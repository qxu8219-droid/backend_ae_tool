"""Review regressions exercise actual sessions and real local MediaMTX ownership."""
import asyncio
import signal
import time

import pytest
from test_gateway import gateway_config
from test_session_manager import REQUEST, build_manager, wait_status


async def until(predicate, timeout=3.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(.01)
    raise AssertionError('Expected lifecycle transition was not observed')


def test_canceled_delete_can_be_retried_to_release_the_single_session_slot(tmp_path):
    async def run():
        async with build_manager(tmp_path, mode='hang') as manager:
            created = await manager.create(REQUEST)
            sid = created.session_id
            await until(lambda: manager.get(sid).worker.pid is not None)
            deletion = asyncio.create_task(manager.delete(sid))
            await until(lambda: manager.get(sid).status == 'stopping')
            deletion.cancel()
            with pytest.raises(asyncio.CancelledError):
                await deletion
            await until(lambda: manager.get(sid).status == 'stopped')
            await manager.delete(sid)
            replacement = await manager.create(REQUEST)
            assert replacement.session_id != sid
    asyncio.run(run())


def test_external_gateway_recovery_restores_owned_path_before_recovery_event(tmp_path):
    from app.core.gateway import GatewayConfig, MediaGateway
    async def run():
        owner = MediaGateway(gateway_config(), tmp_path / 'owner')
        await owner.start()
        external = GatewayConfig(mode='external', api_url=owner.api_url, whep_origin=owner.whep_origin)
        manager = build_manager(tmp_path / 'manager', gateway=external, gateway_poll_s=.05)
        try:
            async with manager:
                created = await manager.create(REQUEST)
                sid = created.session_id
                before = await wait_status(manager, sid, 'ready')
                owner.process.signal_group(signal.SIGKILL)
                events = []
                def saw(kind):
                    events.extend(manager.drain_events(sid))
                    return any(event.kind == kind for event in events)
                await until(lambda: saw('gateway_unreachable'))
                await owner.recover()
                await until(lambda: saw('gateway_recovered'))
                path = await owner.request('GET', '/v3/config/paths/get/' + sid, missing_ok=True)
                assert path is not None
                assert path['source'].startswith('rtsp://127.0.0.1:')
                assert manager.get(sid).worker.pid == before.worker.pid
        finally:
            await owner.close()
    asyncio.run(run())


@pytest.mark.parametrize('conflicting', [False, True])
def test_external_path_loss_or_conflict_is_detected_without_api_outage(tmp_path, conflicting):
    from app.core.gateway import GatewayConfig, MediaGateway
    async def run():
        owner = MediaGateway(gateway_config(), tmp_path / 'owner')
        await owner.start()
        external = GatewayConfig(mode='external', api_url=owner.api_url, whep_origin=owner.whep_origin)
        try:
            async with build_manager(tmp_path / 'manager', gateway=external, gateway_poll_s=.05) as manager:
                created = await manager.create(REQUEST)
                sid = created.session_id
                before = await wait_status(manager, sid, 'ready')
                expected = manager.gateway.owned_paths[sid].copy()
                try:
                    if conflicting:
                        await owner.request('PATCH', '/v3/config/paths/patch/' + sid, {'source': 'rtsp://127.0.0.1:65002/stream'})
                    else:
                        await owner.request('DELETE', '/v3/config/paths/delete/' + sid)
                    events = []
                    def observed():
                        events.extend(manager.drain_events(sid))
                        kinds = {event.kind for event in events}
                        return 'gateway_unreachable' in kinds and (conflicting or 'gateway_recovered' in kinds)
                    await until(observed)
                    current = await owner.request('GET', '/v3/config/paths/get/' + sid)
                    if conflicting:
                        assert current['source'] == 'rtsp://127.0.0.1:65002/stream'
                        assert not manager.get(sid).video.publishing
                        assert not any(event.kind == 'gateway_recovered' for event in events)
                    else:
                        assert current['source'] == expected['source']
                    assert manager.get(sid).worker.pid == before.worker.pid
                finally:
                    if conflicting:
                        await owner.request('PATCH', '/v3/config/paths/patch/' + sid, expected)
        finally:
            await owner.close()
    asyncio.run(run())
