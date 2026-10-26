"""CPU fixtures use real ZMQ and processes, with only the GPU runtime replaced."""
from contextlib import contextmanager
import threading
import time

from replay_protocol import AppliedFrame, RenderedFrame, TargetFrame
from sim_worker.config import WorkerConfig
from sim_worker.transport import WorkerServer


class CpuRuntime:
    def __init__(self):
        self.capture = 0

    def apply(self, frame):
        return AppliedFrame(**frame.model_dump(), source_joints=frame.joints, applied_wall_s=time.time())

    def render(self, frame):
        self.capture += 1
        return RenderedFrame(**frame.model_dump(), capture_id=self.capture,
                             render_seq=self.capture, captured_wall_s=time.time(), raw_identity_verified=True)


def target(sid, *, seq=1, epoch=1, control=1):
    return TargetFrame(session_id=sid, log_id='fixture', playback_epoch=epoch, control_seq=control,
                       target_seq=seq, log_time_s=0., duration_s=1., progress=0.,
                       joints=[0.] * 28, quality=[{'origin': 'missing', 'valid': False, 'processing': ['defaulted'], 'source_indices': []}] * 28,
                       chassis=None, state='paused', speed=1.)


@contextmanager
def zmq_worker():
    from app.core.ports import PortPool
    lease = PortPool(6300, 6399).lease(['tcp'] * 4)
    config = WorkerConfig('cpu-client', *lease.ports)
    lease.close()
    started, stop = threading.Event(), threading.Event()
    holder = {}
    def run():
        with WorkerServer(config, CpuRuntime()) as server:
            holder['server'] = server
            started.set()
            while not stop.is_set():
                server.poll(2)
                server.engine.step()
    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(2)
    try:
        yield config, holder
    finally:
        stop.set(); thread.join(timeout=2)
        assert not thread.is_alive()
