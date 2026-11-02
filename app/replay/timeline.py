"""Deterministic playback state machine with bounded worker delivery boundaries.

No thread, wall-clock loop, simulator, or transport is started here. A future
service schedules tick() and forwards take_rpc()/take_target() to its worker.
REST acceptance is distinct from worker application/rendering and browser display.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from pydantic import ValidationError
from replay_protocol import PlaybackSnapshot, PositionRef, RPCResponse, WorkerEvent
from app.schemas import LoadRequest, PlayRequest, SeekRequest, SpeedRequest, StepRequest
from .delivery import DeliveryTracker
from .interpolator import ReplaySampler
from .timeline_base import TimelineError

_UNSET = object()
_MAX_SEQUENCE = 2**53 - 1


def _request(model, payload):
    try:
        return model.model_validate(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise TimelineError('INVALID_REQUEST', 'Playback control parameters violate the shared contract', 400) from exc


class Timeline:
    def __init__(self, session_id: str, resolve_log: Callable[[str], ReplaySampler], *,
                 clock: Callable[[], float] = time.monotonic, target_hz: float = 60.0,
                 ack_timeout_s: float = 10.0, delivery: DeliveryTracker | None = None):
        if type(session_id) is not str or not 1 <= len(session_id) <= 256:
            raise TimelineError('INVALID_SESSION_ID', 'Timeline requires a session identifier', 400)
        if not callable(resolve_log) or not callable(clock):
            raise TimelineError('INVALID_CONFIGURATION', 'Log resolver and clock must be callable', 400)
        if type(target_hz) not in (int, float) or not math.isfinite(target_hz) or target_hz <= 0:
            raise TimelineError('INVALID_CONFIGURATION', 'Target rate must be positive and finite', 400)
        self._interval = 1.0 / target_hz
        if not math.isfinite(self._interval) or self._interval <= 0:
            raise TimelineError('INVALID_CONFIGURATION', 'Target interval is not representable', 400)
        self._session_id, self._resolve_log, self._clock = session_id, resolve_log, clock
        self._delivery = delivery or DeliveryTracker(session_id,clock=clock,timeout_s=ack_timeout_s)
        self._state = 'idle'
        self._sampler: ReplaySampler | None = None
        self._log_id: str | None = None
        self._speed = 1.0
        self._epoch = self._control = self._target_sequence = 0
        self._anchor_position = 0.0
        self._anchor_time = 0.0
        self._last_clock: float | None = None
        self._running = False
        self._next_tick = 0.0
        self._target = None
        self.error: TimelineError | None = None
        self._anchor_time = self._now()
        self._next_tick = self._next_deadline(self._anchor_time)

    def _next_deadline(self, now: float, *, preserve_phase=False) -> float:
        """Advance in constant space even when the skipped tick count overflows.

        Floating clocks cannot represent arbitrarily small intervals at every
        magnitude. In that case use the next representable clock value; repeated
        calls at the same clock value must not create additional stream targets.
        """
        if preserve_phase and now < self._next_tick:
            deadline = self._next_tick + self._interval
        else:
            offset = self._interval
            if preserve_phase:
                offset -= math.fmod(now - self._next_tick, self._interval)
            deadline = now + offset
        if deadline <= now:
            deadline = math.nextafter(now, math.inf)
        if not math.isfinite(deadline):
            raise TimelineError('INVALID_CLOCK', 'Next playback tick cannot be represented', 500)
        return deadline

    def _enter_error(self, error: TimelineError):
        if self.error is None:
            self.error = error
            self._state = 'error'
            self._running = False
            self._delivery.close()

    def _now(self) -> float:
        try:
            value = self._clock()
            if type(value) not in (int,float):
                raise ValueError('Clock must return a number')
            value = float(value)
            if not math.isfinite(value) or self._last_clock is not None and value < self._last_clock:
                raise ValueError('Clock must remain finite and monotonic')
        except Exception as exc:
            error = TimelineError('INVALID_CLOCK', 'Playback monotonic clock failed or regressed', 500)
            self._enter_error(error)
            raise error from exc
        self._last_clock = value
        return value

    def _poll(self) -> float:
        now = self._now()
        try:
            self._delivery.check_timeout()
        except TimelineError as exc:
            self._enter_error(exc)
            raise
        if self._delivery.error is not None:
            self._enter_error(self._delivery.error)
            raise self.error
        return now

    def _before_control(self, *, require_log=True) -> float:
        if self._state == 'error':
            raise TimelineError('SESSION_ERROR', 'Worker recovery requires recreating the session', 409)
        now = self._poll()
        if require_log and self._sampler is None:
            raise TimelineError('NO_LOG_LOADED', 'Load a prepared log before playback controls', 409)
        return now

    def _position(self, now: float) -> float:
        if not self._running:
            return self._anchor_position
        duration = self._sampler.duration_s
        return min(duration,self._anchor_position + (now-self._anchor_time)*self._speed)

    def _sequence(self, value: int) -> int:
        if value >= _MAX_SEQUENCE:
            error = TimelineError('SEQUENCE_EXHAUSTED', 'Recreate the session before sequence exhaustion', 500)
            self._enter_error(error)
            raise error
        return value + 1

    def _accept(self, sampler, log_id, position, state, now, *, jump=False, prefix=None, speed=None):
        control = self._sequence(self._control)
        epoch = self._sequence(self._epoch) if jump else self._epoch
        sequence = self._sequence(self._target_sequence)
        try:
            frame = sampler.sample_at(position,playback_epoch=epoch,control_seq=control,target_seq=sequence)
            frame = frame.model_copy(update={'state':state,'speed':speed if speed is not None else self._speed})
        except (ValueError, TypeError, AttributeError) as exc:
            raise TimelineError('INVALID_LOG', 'Prepared sampler could not generate the requested target', 422) from exc
        if frame.session_id != self._session_id or frame.log_id != log_id:
            raise TimelineError('INVALID_LOG', 'Prepared sampler belongs to another session or log', 422)
        self._sampler, self._log_id = sampler, log_id
        self._state = state
        self._control, self._epoch, self._target_sequence = control, epoch, sequence
        self._anchor_position, self._anchor_time = position, now
        self._running = False
        if speed is not None:
            self._speed = speed
        self._target = frame
        try:
            self._delivery.reset(log_id,epoch,control)
            if self._delivery.loaded_log_id != log_id:
                prefix = 'load_log'
            self._delivery.keyframe(frame,prefix=prefix)
        except TimelineError as exc:
            # A late clock read can fail after the control context is reserved.
            # Close the session gate rather than exposing a usable partial load.
            self._enter_error(exc)
            raise
        return self.snapshot()

    def load(self, log_id: str) -> PlaybackSnapshot:
        log_id = _request(LoadRequest,{'log_id':log_id}).log_id
        now = self._before_control(require_log=False)
        try:
            sampler = self._resolve_log(log_id)
        except KeyError as exc:
            raise TimelineError('LOG_NOT_FOUND', 'No prepared log matches this identifier', 404) from exc
        except Exception as exc:
            raise TimelineError('INVALID_LOG', 'Prepared log resolver failed', 422) from exc
        if not isinstance(sampler, ReplaySampler):
            raise TimelineError('INVALID_LOG', 'Log resolver must return a prepared ReplaySampler', 422)
        return self._accept(sampler,log_id,0.,'ready',now,jump=True,prefix='load_log')

    def play(self, speed=None) -> PlaybackSnapshot:
        speed = _request(PlayRequest,{'speed':speed}).speed
        now = self._before_control()
        position = self._position(now)
        restart = self._state == 'ended' or position >= self._sampler.duration_s
        if restart:
            position = 0.0
        state = 'ended' if self._sampler.duration_s == 0 else 'playing'
        return self._accept(self._sampler,self._log_id,position,state,now,jump=restart,speed=speed)

    def pause(self) -> PlaybackSnapshot:
        now = self._before_control()
        return self._accept(self._sampler,self._log_id,self._position(now),'paused',now,prefix='pause_barrier')

    def stop(self) -> PlaybackSnapshot:
        now = self._before_control()
        return self._accept(self._sampler,self._log_id,0.,'ready',now,jump=True,prefix='pause_barrier')

    def seek(self, *, time_s=_UNSET, frame_index=_UNSET) -> PlaybackSnapshot:
        payload = {}
        if time_s is not _UNSET:
            payload['time_s'] = time_s
        if frame_index is not _UNSET:
            payload['frame_index'] = frame_index
        request = _request(SeekRequest,payload)
        now = self._before_control()
        position = self._sampler.frame_time(request.frame_index) if request.frame_index is not None else min(max(request.time_s,0.),self._sampler.duration_s)
        state = 'paused' if self._state == 'ended' else self._state
        if state == 'playing' and position >= self._sampler.duration_s:
            state = 'ended'
        return self._accept(self._sampler,self._log_id,position,state,now,jump=True)

    def step(self, direction: str) -> PlaybackSnapshot:
        direction = _request(StepRequest,{'direction':direction}).direction
        now = self._before_control()
        position = self._sampler.step_time(self._position(now),direction)
        return self._accept(self._sampler,self._log_id,position,'paused',now,jump=True,prefix='pause_barrier')

    def set_speed(self, speed: float) -> PlaybackSnapshot:
        speed = _request(SpeedRequest,{'speed':speed}).speed
        now = self._before_control()
        position = self._position(now)
        state = 'ended' if self._state == 'playing' and position >= self._sampler.duration_s else self._state
        return self._accept(self._sampler,self._log_id,position,state,now,speed=speed)

    def snapshot(self) -> PlaybackSnapshot:
        target = PositionRef(log_time_s=self._target.log_time_s,source_sample_index=self._target.source_sample_index,
                             target_seq=self._target.target_seq) if self._target is not None else None
        return PlaybackSnapshot(session_id=self._session_id,log_id=self._log_id,playback_epoch=self._epoch,
            control_seq=self._control,state=self._state,speed=self._speed,duration_s=self._sampler.duration_s if self._sampler else 0.,
            target_position=target,applied_position=self._delivery.applied_position,rendered_identity=self._delivery.rendered_identity)

    def tick(self) -> PlaybackSnapshot:
        if self._state == 'error':
            return self.snapshot()
        try:
            now = self._poll()
            if self._state != 'playing' or not self._running:
                return self.snapshot()
            position = self._position(now)
            if position >= self._sampler.duration_s:
                sequence = self._sequence(self._target_sequence)
                frame = self._sampler.sample_at(self._sampler.duration_s,playback_epoch=self._epoch,control_seq=self._control,target_seq=sequence)
                frame = frame.model_copy(update={'state':'ended','speed':self._speed})
                self._target_sequence, self._target = sequence, frame
                self._state, self._running = 'ended', False
                self._anchor_position, self._anchor_time = self._sampler.duration_s, now
                self._delivery.keyframe(frame,prefix='pause_barrier')
            elif now + min(1e-12,self._interval*1e-9) >= self._next_tick:
                next_tick = self._next_deadline(now,preserve_phase=True)
                sequence = self._sequence(self._target_sequence)
                frame = self._sampler.sample_at(position,playback_epoch=self._epoch,control_seq=self._control,target_seq=sequence)
                frame = frame.model_copy(update={'state':'playing','speed':self._speed})
                self._target_sequence, self._target = sequence, frame
                self._delivery.queue_target(frame)
                self._next_tick = next_tick
        except TimelineError as exc:
            self._enter_error(exc)
        return self.snapshot()

    def _receive(self, receiver, message) -> bool:
        if self._state == 'error':
            return False
        try:
            now = self._poll()
            pending = self._delivery.critical_pending
            accepted = receiver(message)
            if self._delivery.error is not None:
                self._enter_error(self._delivery.error)
                return accepted
            if pending and not self._delivery.critical_pending and self._state == 'playing':
                self._running = True
                self._anchor_time = now
                self._next_tick = self._next_deadline(now)
            return accepted
        except TimelineError as exc:
            self._enter_error(exc)
            return False

    def receive_rpc(self, response: RPCResponse) -> bool:
        return self._receive(self._delivery.receive_rpc,response)

    def receive_event(self, event: WorkerEvent) -> bool:
        return self._receive(self._delivery.receive_event,event)

    def take_rpc(self):
        if self._state == 'error':
            return None
        try:
            self._poll()
            return self._delivery.take_rpc()
        except TimelineError as exc:
            self._enter_error(exc)
            return None

    def take_target(self):
        if self._state == 'error':
            return None
        try:
            self._poll()
            return self._delivery.take_target()
        except TimelineError as exc:
            self._enter_error(exc)
            return None

    def worker_failed(self, code: str, message: str) -> PlaybackSnapshot:
        self._enter_error(TimelineError(code,message,502))
        return self.snapshot()

    def diagnostics(self) -> dict:
        return {'state':self._state,'running':self._running,'control_seq':self._control,
            'playback_epoch':self._epoch,'target_seq':self._target_sequence,'target_hz':1/self._interval,
            'critical_pending':self._delivery.critical_pending,'delivery':self._delivery.diagnostics(),
            'error':{'code':self.error.code,'message':self.error.message} if self.error else None}
