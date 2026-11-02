import asyncio

import pytest
from replay_protocol import RPCRequest

from core_support import zmq_worker, target


def command(sid, request_id, name='heartbeat', **fields):
    return RPCRequest(session_id=sid, request_id=request_id, control_seq=1, playback_epoch=1,
                      command={'cmd': name, **fields})


def test_real_subscription_and_concurrent_requests_keep_their_own_stages():
    from app.core.worker_client import AsyncWorkerClient
    async def run(config):
        async with AsyncWorkerClient(config) as client:
            await client.handshake(timeout_s=2)
            assert (await client.request(command(config.session_id, 'load', 'load_log', log_id='fixture'))).ok
            requests = [client.request(command(config.session_id, 'frame', 'apply_keyframe', frame=target(config.session_id)))]
            requests += [client.request(command(config.session_id, f'ping-{n}')) for n in range(8)]
            results = await asyncio.gather(*requests)
            assert results[0].stage == 'rendered' and results[0].frame.target_seq == 1
            assert [r.request_id for r in results[1:]] == [f'ping-{n}' for n in range(8)]
            assert all(r.ok for r in results)
            assert client.pending_count == 0
    with zmq_worker() as (config, _):
        asyncio.run(run(config))


def test_wrong_response_identity_is_ignored_and_timeout_does_not_poison_next_rpc():
    from app.core.worker_client import AsyncWorkerClient
    async def run(config, holder):
        async with AsyncWorkerClient(config) as client:
            await client.handshake(timeout_s=2)
            server = holder['server']
            original = server.reply
            def wrong(route, response):
                if response.request_id == 'bad':
                    original(route, response.model_copy(update={'control_seq': 999}))
                else:
                    original(route, response)
            server.engine.reply = wrong
            with pytest.raises(TimeoutError):
                await client.request(command(config.session_id, 'bad'), timeout_s=.1)
            assert (await client.request(command(config.session_id, 'good'), timeout_s=1)).ok
            assert client.pending_count == 0 and client.invalid_responses >= 1
    with zmq_worker() as (config, holder):
        asyncio.run(run(config, holder))


def test_close_fails_pending_and_releases_bound_target_port():
    from app.core.worker_client import AsyncWorkerClient, WorkerClosed
    from app.core.ports import PortPool
    from sim_worker.config import WorkerConfig
    async def run():
        lease = PortPool(6300, 6399).lease(['tcp'] * 4)
        config = WorkerConfig('no-worker', *lease.ports); lease.close()
        client = AsyncWorkerClient(config)
        pending = asyncio.create_task(client.request(command(config.session_id, 'waiting'), timeout_s=10))
        await asyncio.sleep(.02)
        await client.close()
        with pytest.raises(WorkerClosed):
            await pending
        with PortPool(config.target_port, config.target_port).lease(['tcp']):
            pass
        assert client.pending_count == 0
    asyncio.run(run())
