import asyncio
from pathlib import Path
import signal
import sys
import time

import pytest
from app.schemas.rest import CreateSessionRequest
from replay_protocol import RPCRequest
from core_support import target
from test_gateway import gateway_config
from test_resources import alive

ROOT = Path(__file__).resolve().parents[2]
REQUEST = CreateSessionRequest(scene_id='empty', robot_id='galaxea_r1pro_2026')


def build_manager(tmp_path, mode='normal', **options):
    from app.core.config import Settings, Launch
    from app.core.session_manager import SessionManager
    settings = Settings(isaac_python=Path(sys.executable), state_dir=tmp_path,
                        gateway=options.pop('gateway', None) or gateway_config(), startup_timeout_s=2., load_timeout_s=1.,
                        heartbeat_interval_s=.05, heartbeat_timeout_s=.3, shutdown_grace_s=.1,
                        **options)
    def launcher(settings, config, output):
        args = [sys.executable, str(ROOT / 'tests/core/worker_process.py'), '--session-id', config.session_id,
                '--output-dir', str(output), '--fixture-mode', mode]
        for name in ('rpc', 'target', 'event', 'rtsp'):
            args += ['--' + name + '-port', str(getattr(config, name + '_port'))]
        return Launch(args, {})
    return SessionManager(settings, worker_launcher=launcher)


async def wait_status(manager, sid, wanted, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.get(sid)
        if status.status == wanted:
            return status
        if status.status == 'error' and wanted != 'error':
            raise AssertionError(status.model_dump())
        await asyncio.sleep(.01)
    raise AssertionError(manager.get(sid).model_dump())


def test_create_is_async_single_slot_and_delete_is_idempotent(tmp_path):
    from app.core.session_manager import SessionError
    async def run():
        async with build_manager(tmp_path, mode='slow', rpc_timeout_s=.05) as manager:
            created = await manager.create(REQUEST)
            assert created.ws_url == f'/ws/sessions/{created.session_id}'
            assert manager.get(created.session_id).status == 'starting'
            with pytest.raises(SessionError) as conflict:
                await manager.create(REQUEST)
            assert conflict.value.status_code == 409
            status = await wait_status(manager, created.session_id, 'ready')
            assert not status.video.publishing  # No log/image has been sent.
            phases = [event.status.worker.phase for event in manager.drain_events(created.session_id)]
            assert phases == ['booting', 'loading_scene', 'loading_robot', 'rtsp_publishing', 'ready']
            pid = status.worker.pid
            await manager.delete(created.session_id)
            await manager.delete(created.session_id)
            await manager.delete('unknown-session')
            assert manager.get(created.session_id).status == 'stopped'
            assert not alive(pid)
    asyncio.run(run())


def test_worker_kill_is_reported_before_cleanup_and_reload_uses_new_identity(tmp_path):
    async def run():
        async with build_manager(tmp_path) as manager:
            first = await manager.create(REQUEST)
            status = await wait_status(manager, first.session_id, 'ready')
            import os
            started = time.monotonic(); os.kill(status.worker.pid, signal.SIGKILL)
            error = await wait_status(manager, first.session_id, 'error')
            assert time.monotonic() - started < 3
            assert error.worker.error.code == 'WORKER_EXITED'
            await manager.delete(first.session_id)
            second = await manager.create(REQUEST)
            assert second.session_id != first.session_id
            await wait_status(manager, second.session_id, 'ready')
            for rid, cmd in [('load', {'cmd': 'load_log', 'log_id': 'fixture'}),
                             ('first-frame', {'cmd': 'apply_keyframe', 'frame': target(second.session_id)})]:
                response = await manager.rpc(second.session_id, RPCRequest(session_id=second.session_id, request_id=rid,
                                                                          control_seq=1, playback_epoch=1, command=cmd))
                assert response.ok
            assert response.frame.log_time_s == 0 and response.frame.capture_id == 1
    asyncio.run(run())


@pytest.mark.parametrize('mode,code', [('exit', 'WORKER_EXITED'), ('hang', 'WORKER_STARTUP_TIMEOUT'), ('no-heartbeat', 'WORKER_HEARTBEAT_TIMEOUT')])
def test_startup_and_ready_failures_have_separate_deadlines_and_release_resources(tmp_path, mode, code):
    async def run():
        manager = build_manager(tmp_path, mode=mode)
        async with manager:
            first = await manager.create(REQUEST)
            error = await wait_status(manager, first.session_id, 'error')
            assert error.worker.error.code == code
            await manager.delete(first.session_id)
            assert manager.get(first.session_id).status == 'stopped'
            if error.worker.pid:
                assert not alive(error.worker.pid)
    asyncio.run(run())


def test_delete_during_start_and_service_close_leave_no_live_worker(tmp_path):
    async def run():
        manager = build_manager(tmp_path, mode='hang')
        await manager.start()
        first = await manager.create(REQUEST)
        await asyncio.gather(manager.delete(first.session_id), manager.delete(first.session_id))
        assert manager.get(first.session_id).status == 'stopped'
        second = await manager.create(REQUEST)
        for _ in range(100):
            pid = manager.get(second.session_id).worker.pid
            if pid:
                break
            await asyncio.sleep(.01)
        await manager.close()
        assert pid and not alive(pid)
        assert manager.get(second.session_id).status == 'stopped'
    asyncio.run(run())


def test_gateway_failure_and_recovery_do_not_recreate_worker(tmp_path):
    async def run():
        async with build_manager(tmp_path, gateway_poll_s=.05) as manager:
            created = await manager.create(REQUEST)
            before = await wait_status(manager, created.session_id, 'ready')
            manager.gateway.process.signal_group(signal.SIGKILL)
            found = []
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                found += [e.kind for e in manager.drain_events(created.session_id)]
                if 'gateway_recovered' in found:
                    break
                await asyncio.sleep(.02)
            assert 'gateway_unreachable' in found and 'gateway_recovered' in found
            after = manager.get(created.session_id)
            assert after.status == 'ready' and after.worker.pid == before.worker.pid
    asyncio.run(run())


def test_two_managers_cannot_share_state_root_and_closed_manager_rejects_creation(tmp_path):
    from app.core.session_manager import SessionError
    async def run():
        one, two = build_manager(tmp_path), build_manager(tmp_path)
        await one.start()
        try:
            with pytest.raises(SessionError, match='owner'):
                await two.start()
        finally:
            await one.close(); await two.close()
        with pytest.raises(SessionError):
            await one.create(REQUEST)
    asyncio.run(run())
