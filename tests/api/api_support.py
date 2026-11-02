"""Real processes/ZMQ/importer, CPU runtime instead of GPU in API unit tests."""
from pathlib import Path
import sys
import asyncio
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from test_session_manager import build_manager, REQUEST

CSV = b'timestamp,steer_motor_joint1\n0,0\n0.1,4.2\n0.2,0\n0.3,0\n1,0\n2,0\n'


async def chunks(content=CSV):
    yield content


def make_service(tmp_path, *, mode='normal', limits=None):
    from app.services.replay_service import ReplayService
    from app.services.log_store import LogStore
    from app.adapters.base import ParseLimits
    manager = build_manager(tmp_path / 'state', mode=mode)
    return ReplayService(manager, LogStore(tmp_path / 'logs', limits or ParseLimits()))


async def until(predicate, timeout=5.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(.01)
    raise AssertionError('Expected service transition was not observed')


async def session(service):
    result = await service.create(REQUEST)
    sid = result.session_id
    await until(lambda: service.manager.get(sid).status == 'ready')
    return sid
