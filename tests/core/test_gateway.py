import asyncio
from pathlib import Path
import signal

import pytest

ROOT = Path(__file__).resolve().parents[2]


def gateway_config():
    from app.core.gateway import GatewayConfig
    from app.core.ports import PortPool
    with PortPool(6400, 6499).lease(['tcp', 'tcp', 'udp']) as lease:
        return GatewayConfig(api_port=lease.ports[0], http_port=lease.ports[1], ice_port=lease.ports[2])


def test_managed_gateway_preserves_paths_across_its_own_restart(tmp_path):
    from app.core.gateway import MediaGateway
    async def run():
        gateway = MediaGateway(gateway_config(), tmp_path)
        try:
            await gateway.start()
            first_pid = gateway.process.pid
            await gateway.add_path('s-owned', 65000)
            health = await gateway.health('s-owned')
            assert health.reachable and not health.publishing
            config = await gateway.request('GET', '/v3/config/global/get')
            assert config['webrtcAddress'].startswith('127.0.0.1:')
            gateway.process.signal_group(signal.SIGKILL)
            await asyncio.sleep(.05)
            assert not (await gateway.health('s-owned')).reachable
            await gateway.recover()
            assert gateway.process.pid != first_pid
            path = await gateway.request('GET', '/v3/config/paths/get/s-owned')
            assert path['source'] == 'rtsp://127.0.0.1:65000/stream'
            await gateway.remove_path('s-owned')
            assert await gateway.request('GET', '/v3/config/paths/get/s-owned', missing_ok=True) is None
        finally:
            await gateway.close()
        assert gateway.process.returncode is not None
    asyncio.run(run())


def test_external_gateway_never_replaces_foreign_path_or_stops_the_server(tmp_path):
    from app.core.gateway import GatewayConfig, MediaGateway, GatewayError
    async def run():
        owner = MediaGateway(gateway_config(), tmp_path / 'owner')
        await owner.start()
        external = MediaGateway(GatewayConfig(mode='external', api_url=owner.api_url, whep_origin=owner.whep_origin), tmp_path / 'external')
        try:
            await owner.add_path('foreign', 65000)
            await external.start()
            with pytest.raises(GatewayError, match='exists'):
                await external.add_path('foreign', 65000)
            await external.add_path('own', 65001)
            await external.close()
            assert owner.process.returncode is None
            assert await owner.request('GET', '/v3/config/paths/get/foreign') is not None
            assert await owner.request('GET', '/v3/config/paths/get/own', missing_ok=True) is None
        finally:
            await external.close()
            await owner.close()
    asyncio.run(run())


def test_external_start_cleans_journaled_paths_from_previous_owner_before_reuse(tmp_path):
    from app.core.gateway import GatewayConfig, MediaGateway
    async def run():
        owner = MediaGateway(gateway_config(), tmp_path / 'owner')
        await owner.start()
        config = GatewayConfig(mode='external', api_url=owner.api_url, whep_origin=owner.whep_origin)
        first = MediaGateway(config, tmp_path / 'external')
        second = MediaGateway(config, tmp_path / 'external')
        try:
            await first.start()
            await first.add_path('orphan-from-previous-service', 65001)
            # Simulate loss of the application object while the external server stays alive.
            await second.start()
            assert await owner.request('GET', '/v3/config/paths/get/orphan-from-previous-service', missing_ok=True) is None
        finally:
            await second.close()
            await first.close()
            await owner.close()
    asyncio.run(run())
