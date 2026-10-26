"""CPU-only test doubles; synthetic captures are not Isaac/browser proof."""
from __future__ import annotations

from replay_protocol import (
    AppliedFrame, CANONICAL_JOINTS, ChannelSeries, RenderedFrame, ReplayData,
    RPCRequest, RPCResponse, TargetFrame, TargetMessage, WorkerEvent,
)

from app.replay.interpolator import ReplaySampler


class ManualClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> float:
        self.now += delta
        return self.now


def build_sampler(session='s', log_id='log', times=(0., .5, 1.)):
    """Real prepared sampler; torso_joint1 = 2 * original source time."""
    values = [2. * time for time in times]
    channel = ChannelSeries(
        timestamps_s=list(times), values=values, unit='rad', angle_semantics='bounded',
        quality=[{'origin': 'measured', 'valid': True, 'processing': ['original'],
                  'source_indices': [index]} for index in range(len(times))],
    )
    data = ReplayData(
        log_id=log_id, robot_id='galaxea_r1pro_2026', raw_file_ref='synthetic-timeline.csv',
        raw_file_sha256='0' * 64, mapping_snapshot_id='synthetic-timeline-mapping',
        mapping_snapshot={}, time_basis={'source_origin': 1000., 'relative_origin_s': times[0]},
        canonical_order=list(CANONICAL_JOINTS), sample_times_s=list(times),
        channels={'torso_joint1': channel},
    )
    return ReplaySampler(data, session_id=session)


def applied_frame(frame: TargetFrame, *, wall_s=1000., **changes) -> AppliedFrame:
    """Build synthetic readback, retaining the original issued source joints."""
    data = dict(frame.model_dump(mode='python'), source_joints=frame.joints,
                applied_wall_s=wall_s)
    data.update(changes)
    return AppliedFrame.model_validate(data)


def rendered_frame(frame: TargetFrame, *, capture_id=1, wall_s=1000., **changes) -> RenderedFrame:
    """Explicit test-only raw-identity assertion; no real image was captured."""
    data = dict(applied_frame(frame, wall_s=wall_s).model_dump(mode='python'),
                capture_id=capture_id, render_seq=capture_id, captured_wall_s=wall_s,
                raw_identity_verified=True)
    data.update(changes)
    return RenderedFrame.model_validate(data)


def rpc_response(request: RPCRequest, *, stage='accepted', frame=None, error=None) -> RPCResponse:
    return RPCResponse(
        session_id=request.session_id, request_id=request.request_id,
        control_seq=request.control_seq, playback_epoch=request.playback_epoch,
        stage=stage, ok=error is None, frame=frame, error=error,
    )


def frame_event(frame: AppliedFrame | RenderedFrame, *, seq=1) -> WorkerEvent:
    return WorkerEvent(session_id=frame.session_id, seq=seq,
                       event={'type': 'rendered' if isinstance(frame, RenderedFrame) else 'applied',
                              'frame': frame})


class FakeWorker:
    """Minimal worker contract fake with synthetic monotonic captures and fences.

    It models protocol sequencing only: it does not run IsaacSim, physics,
    rendering, an encoder, a browser, or a ZMQ transport.
    """
    def __init__(self, session_id, clock):
        self.session_id = session_id
        self.clock = clock
        self.loaded_log_id = None
        self.context = (-1, -1)
        self.target_floor = -1
        self.last_target_seq = -1
        self.capture_id = 0
        self.event_seq = 0
        self.last_frame = None
        self.requests = []
        self.targets = []
        self.frames = []

    def _failure(self, request, code, message):
        return rpc_response(request, error={'code': code, 'message': message})

    def _render(self, frame):
        self.last_target_seq = frame.target_seq
        self.target_floor = max(self.target_floor, frame.target_seq)
        self.capture_id += 1
        result = rendered_frame(frame, capture_id=self.capture_id, wall_s=1000. + self.clock())
        self.last_frame = result
        self.frames.append(result)
        return result

    def handle_rpc(self, request: RPCRequest) -> RPCResponse:
        self.requests.append(request)
        context = (request.playback_epoch, request.control_seq)
        if request.session_id != self.session_id or context < self.context:
            return self._failure(request, 'STALE_COMMAND', 'Synthetic worker rejected stale command')
        self.context = context
        command = request.command
        if command.cmd == 'load_log':
            self.loaded_log_id = command.log_id
            return rpc_response(request)
        if command.cmd == 'pause_barrier':
            self.target_floor = max(self.target_floor, command.target_seq)
            return rpc_response(request)
        if command.cmd == 'apply_keyframe':
            frame = command.frame
            if frame.log_id != self.loaded_log_id:
                return self._failure(request, 'LOG_NOT_LOADED', 'Synthetic worker requires load_log first')
            if frame.target_seq < self.target_floor or frame.target_seq <= self.last_target_seq:
                return self._failure(request, 'STALE_TARGET', 'Synthetic worker rejected stale target')
            return rpc_response(request, stage='rendered', frame=self._render(frame))
        raise AssertionError(f'Unsupported synthetic worker command: {command.cmd}')

    def publish_target(self, message: TargetMessage) -> WorkerEvent | None:
        frame = message.frame
        context = (frame.playback_epoch, frame.control_seq)
        if (frame.session_id != self.session_id or frame.log_id != self.loaded_log_id
                or context < self.context or frame.target_seq < self.target_floor
                or frame.target_seq <= self.last_target_seq):
            return None
        self.context = context
        self.targets.append(message)
        result = self._render(frame)
        self.event_seq += 1
        return frame_event(result, seq=self.event_seq)

    rpc = handle_rpc
    publishTarget = publish_target


def pump(timeline, worker: FakeWorker, *, limit=100):
    """Drain currently available work without ticking or advancing the clock."""
    for _ in range(limit):
        request = timeline.take_rpc()
        if request is not None:
            response = worker.handle_rpc(request)
            assert timeline.receive_rpc(response), f'Valid synthetic RPC receipt rejected: {response}'
            continue
        message = timeline.take_target()
        if message is not None:
            event = worker.publish_target(message)
            assert event is not None, f'Current synthetic target rejected: {message}'
            assert timeline.receive_event(event), f'Valid synthetic target receipt rejected: {event}'
            continue
        return timeline.snapshot()
    raise AssertionError('Synthetic worker pump exceeded its bounded command drain')
