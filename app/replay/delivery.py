"""Bounded, transport-independent worker delivery and evidence accounting."""
from __future__ import annotations

from collections import OrderedDict, deque
import math
import time
from typing import Callable, Literal

from pydantic import TypeAdapter
from replay_protocol import (
    AppliedFrame, FrameIdentity, Identifier, PositionRef, RPCRequest, RPCResponse,
    RenderedFrame, SafeInt, TargetFrame, TargetMessage, WorkerEvent,
)

from .timeline_base import TimelineError

_IDENTIFIER = TypeAdapter(Identifier)
_SAFE_INT = TypeAdapter(SafeInt)


class DeliveryTracker:
    """Separate reliable keyframes from the replaceable streaming outbox.

    A target becomes eligible as evidence only when a consumer takes it. Reliable
    work remains pending until the worker proves that very target was rendered.
    """

    def __init__(self, session_id: str, *, clock: Callable[[], float] = time.monotonic,
                 timeout_s: float = 10.0, capacity: int = 64):
        self.session_id = _IDENTIFIER.validate_python(session_id)
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be a finite positive number")
        self._clock = clock
        self._timeout_s = timeout_s
        self._capacity = capacity
        self._last_clock: float | None = None
        self._now()
        self._log_id: str | None = None
        self._epoch = self._control = 0
        self._rpc_counter = 0
        self._target_floor = -1
        self._queue: deque[RPCRequest] = deque()
        self._active: RPCRequest | None = None
        self._stage = 0
        self._critical: TargetFrame | None = None
        self._deadline: float | None = None
        self._stream: TargetFrame | None = None
        self._issued: OrderedDict[int, TargetFrame] = OrderedDict()
        self._worker_seq = -1
        self._capture_floor = 0
        self._closed = False
        self.applied_position: PositionRef | None = None
        self.rendered_identity: FrameIdentity | None = None
        self.loaded_log_id: str | None = None
        self.error: TimelineError | None = None

    @property
    def critical_pending(self) -> bool:
        return self._critical is not None

    def _clear_outbox(self) -> None:
        self._queue.clear()
        self._active = self._critical = self._stream = None
        self._deadline = None
        self._stage = 0

    def _ensure_open(self) -> None:
        if self._closed:
            raise TimelineError("DELIVERY_CLOSED", "Delivery tracker is closed")

    def _now(self) -> float:
        now = self._clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or (self._last_clock is not None and now < self._last_clock)):
            raise TimelineError("INVALID_CLOCK", "Clock must be finite and nondecreasing", 422)
        self._last_clock = now
        return now

    def _validate_target(self, frame: TargetFrame) -> TargetFrame:
        self._ensure_open()
        if self.error is not None:
            raise self.error
        frame = TargetFrame.model_validate(frame)
        if not self._current(frame):
            raise ValueError("target belongs to another delivery context")
        return frame

    def reset(self, log_id: str, playback_epoch: int, control_seq: int) -> None:
        self._ensure_open()
        log_id = _IDENTIFIER.validate_python(log_id)
        playback_epoch = _SAFE_INT.validate_python(playback_epoch)
        control_seq = _SAFE_INT.validate_python(control_seq)
        if (log_id, playback_epoch) != (self._log_id, self._epoch):
            self.applied_position = self.rendered_identity = None
        if log_id != self._log_id:
            self.loaded_log_id = None
        self._log_id, self._epoch, self._control = log_id, playback_epoch, control_seq
        self._clear_outbox()
        self._issued.clear()
        self.error = None

    def _request(self, command: dict) -> RPCRequest:
        self._rpc_counter += 1
        return RPCRequest(session_id=self.session_id, request_id=f"delivery-{self._rpc_counter}",
                          playback_epoch=self._epoch, control_seq=self._control,
                          command=command)

    def keyframe(self, frame: TargetFrame, *,
                 prefix: Literal["load_log", "pause_barrier"] | None = None) -> None:
        frame = self._validate_target(frame)
        if prefix not in (None, "load_log", "pause_barrier"):
            raise ValueError("unsupported keyframe prefix")
        if frame.target_seq <= self._target_floor:
            raise ValueError("a keyframe requires a new target sequence")
        deadline = self._now() + self._timeout_s
        if self._critical is not None:
            self._issued.pop(self._critical.target_seq, None)
        self._clear_outbox()
        self._critical = frame
        self._target_floor = frame.target_seq
        self._deadline = deadline
        if prefix == "load_log":
            self._queue.append(self._request({"cmd": prefix, "log_id": frame.log_id}))
        elif prefix == "pause_barrier":
            self._queue.append(self._request({"cmd": prefix, "target_seq": frame.target_seq}))
        self._queue.append(self._request({"cmd": "apply_keyframe", "frame": frame}))

    def queue_target(self, frame: TargetFrame) -> None:
        frame = self._validate_target(frame)
        if frame.target_seq > self._target_floor:
            self._stream = frame
            self._target_floor = frame.target_seq

    def _register(self, frame: TargetFrame) -> None:
        self._issued[frame.target_seq] = TargetFrame.model_validate(frame)
        while len(self._issued) > self._capacity:
            self._issued.popitem(last=False)

    def take_rpc(self) -> RPCRequest | None:
        if self._closed or self.error or self.check_timeout() or self._active or not self._queue:
            return None
        self._active = self._queue.popleft()
        self._stage = 0
        if self._active.command.cmd == "apply_keyframe":
            self._register(self._active.command.frame)
        return RPCRequest.model_validate(self._active)

    def take_target(self) -> TargetMessage | None:
        if self._closed or self.error or self.check_timeout() or self.critical_pending or not self._stream:
            return None
        frame, self._stream = self._stream, None
        self._register(frame)
        return TargetMessage(frame=frame)

    def _current(self, frame: TargetFrame) -> bool:
        return (frame.session_id, frame.log_id, frame.playback_epoch, frame.control_seq) == (
            self.session_id, self._log_id, self._epoch, self._control)

    def _observe(self, frame: AppliedFrame) -> bool:
        issued = self._issued.get(frame.target_seq)
        if not self._current(frame) or issued is None:
            return False
        if any(getattr(frame, field) != getattr(issued, field) for field in (
            "log_time_s", "duration_s", "progress", "source_sample_index", "state", "speed", "quality"
        )) or frame.source_joints != issued.joints:
            return False
        if getattr(frame.chassis, 'quality', None) != getattr(issued.chassis, 'quality', None):
            return False
        rendered = isinstance(frame, RenderedFrame)
        if rendered:
            if frame.capture_id <= self._capture_floor or (
                self.rendered_identity is not None and
                frame.target_seq < self.rendered_identity.target_seq
            ):
                return False
        elif self.applied_position is not None and frame.target_seq <= self.applied_position.target_seq:
            return False
        if self.applied_position is None or frame.target_seq > self.applied_position.target_seq:
            self.applied_position = PositionRef(log_time_s=frame.log_time_s,
                                               source_sample_index=frame.source_sample_index,
                                               target_seq=frame.target_seq)
        if rendered:
            self.rendered_identity = FrameIdentity.model_validate({
                key: getattr(frame, key) for key in FrameIdentity.model_fields})
            self._capture_floor = frame.capture_id
            if self._critical is not None and frame.target_seq == self._critical.target_seq:
                self._active = self._critical = None
                self._deadline = None
                self._queue.clear()
        return True

    def receive_rpc(self, response: RPCResponse) -> bool:
        if self._closed or self.error or self.check_timeout():
            return False
        try:
            response = RPCResponse.model_validate(response)
        except (ValueError, TypeError, AttributeError):
            return False
        active = self._active
        if active is None or any(getattr(response, field) != getattr(active, field) for field in (
            "session_id", "request_id", "playback_epoch", "control_seq"
        )):
            return False
        if not response.ok:
            self.error = TimelineError(response.error.code, response.error.message, 502)
            self._clear_outbox()
            return True
        if active.command.cmd != "apply_keyframe":
            if response.stage != "accepted":
                return False
            if active.command.cmd == "load_log":
                self.loaded_log_id = active.command.log_id
            self._active = None
            return True
        stage = {"accepted": 1, "applied": 2, "rendered": 3}[response.stage]
        if stage <= self._stage:
            return False
        if response.frame is not None:
            if response.frame.target_seq != active.command.frame.target_seq or not self._observe(response.frame):
                return False
        self._stage = stage
        return True

    def receive_event(self, event: WorkerEvent) -> bool:
        if self._closed or self.error or self.check_timeout():
            return False
        try:
            event = WorkerEvent.model_validate(event)
        except (ValueError, TypeError, AttributeError):
            return False
        if event.session_id != self.session_id or event.seq <= self._worker_seq:
            return False
        if event.event.type in ("applied", "rendered"):
            if not self._observe(event.event.frame):
                return False
        elif event.event.type == "error":
            self.error = TimelineError(event.event.error.code, event.event.error.message, 502)
            self._clear_outbox()
        self._worker_seq = event.seq
        return True

    def check_timeout(self) -> bool:
        if self._deadline is not None and self._now() >= self._deadline:
            self.error = TimelineError("WORKER_TIMEOUT", "Worker keyframe confirmation timed out", 504)
            self._clear_outbox()
            return True
        return False

    def close(self) -> None:
        self._closed = True
        self._clear_outbox()
        self._issued.clear()

    def diagnostics(self) -> dict:
        return {"pending_rpc": int(self._active is not None), "queued_rpc": len(self._queue),
                "queued_targets": int(self._stream is not None), "issued_targets": len(self._issued),
                "critical_pending": self.critical_pending, "closed": self._closed,
                "capture_floor": self._capture_floor, "worker_seq": self._worker_seq,
                "rpc_requests_created": self._rpc_counter, "target_seq": self._target_floor}
