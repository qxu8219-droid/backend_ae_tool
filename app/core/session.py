"""Session-owned state and bounded event history, independent of HTTP/WS delivery."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import time

from app.schemas.rest import CreateSessionRequest, ErrorDetail, SessionStatus, VideoInfo, WorkerStatus
from sim_worker.config import WorkerConfig
from sim_worker.media import SEI_METADATA_UUID


@dataclass(frozen=True)
class LifecycleEvent:
    seq: int
    kind: str
    wall_time: float
    status: SessionStatus
    message: str | None = None


@dataclass
class Session:
    sid: str
    request: CreateSessionRequest
    config: WorkerConfig
    output_dir: Path
    lease: object
    whep_origin: str
    status: str = 'starting'
    phase: str = 'booting'
    error: ErrorDetail | None = None
    process: object = None
    client: object = None
    task: object = None
    deletion_task: object = None
    started: float = field(default_factory=time.monotonic)
    phase_started: float = field(default_factory=time.monotonic)
    last_heartbeat: float = 0.
    publishing: bool = False
    gateway_reachable: bool = True
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    runtime_ready: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cleanup_errors: list[str] = field(default_factory=list)
    diagnostic_errors: list[str] = field(default_factory=list)
    events: deque = field(default_factory=lambda: deque(maxlen=128))
    worker_events: deque = field(default_factory=lambda: deque(maxlen=128))
    capture_times: deque = field(default_factory=lambda: deque(maxlen=40))
    event_seq: int = 0
    dropped_events: int = 0
    startup_error: str | None = None

    def snapshot(self):
        fps = None
        if len(self.capture_times) > 1 and self.capture_times[-1] > self.capture_times[0]:
            fps = (len(self.capture_times) - 1) / (self.capture_times[-1] - self.capture_times[0])
        active = self.status in ('starting', 'ready')
        return SessionStatus(
            session_id=self.sid, scene_id=self.request.scene_id, robot_id=self.request.robot_id,
            isaacsim_version='6.2.0', status=self.status, ws_url=f'/ws/sessions/{self.sid}',
            worker=WorkerStatus(phase=self.phase, pid=self.process.pid if self.process else None, error=self.error),
            video=VideoInfo(publishing=self.publishing and active, whep_url=f'{self.whep_origin}/{self.sid}/whep' if active else None,
                            stream_path=self.sid if active else None, sei_uuid_hex=SEI_METADATA_UUID.hex(), render_fps=fps),
            render_fps=fps,
        )

    def emit(self, kind, message=None):
        self.event_seq += 1
        if len(self.events) == self.events.maxlen:
            self.dropped_events += 1
        self.events.append(LifecycleEvent(self.event_seq, kind, time.time(), self.snapshot(), message))

    def set_phase(self, phase):
        if phase != self.phase:
            self.phase = phase
            self.phase_started = time.monotonic()
            self.emit('worker_phase_changed')

    def fail(self, code, message):
        if self.status in ('stopping', 'stopped', 'error'):
            return
        self.error = ErrorDetail(code=code, message=message[:512] or code)
        self.status = 'error'; self.phase = 'error'; self.publishing = False
        self.emit('session_error', message)
        self.stop.set()
