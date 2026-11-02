import asyncio
import pytest
from api_support import make_service, session, chunks, until


def test_actual_import_timeline_zmq_and_snapshot_chain_preserves_control_evidence(tmp_path):
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            log = await service.upload(sid, chunks(), 'source.csv')
            assert service.playback(sid).state == 'idle'
            response = await service.control(sid, 'load', {'log_id': log.log_id, 'request_id': 'load-1'})
            assert response.ack_stage == 'accepted' and response.request_id == 'load-1'
            await until(lambda: service.playback(sid).rendered_identity is not None)
            frame = service.contexts[sid].frames.latest
            assert frame.log_id == log.log_id and frame.log_time_s == 0
            result = await service.control(sid, 'seek', {'time_s': .1})
            await until(lambda: service.contexts[sid].frames.latest is not None)
            frame = service.contexts[sid].frames.latest
            assert frame.control_seq == result.control_seq and frame.limit_flags & 1
            assert frame.joints[0] == 4.2 and frame.state == 'ready'
            await service.control(sid, 'play', {'speed': 10.})
            await until(lambda: service.playback(sid).state == 'ended')
            await until(lambda: service.contexts[sid].frames.latest.state == 'ended')
            curve = await service.curve(sid, log.log_id, ['steer_motor_joint1'], max_points=2)
            assert max(p.value for p in curve.series[0].points) == 4.2
            await service.delete(sid)
            assert service.manager.get(sid).status == 'stopped'
            assert not await service.logs.list_logs(sid)
    asyncio.run(run())


def test_newer_controls_cancel_old_waits_and_only_latest_context_reaches_cache(tmp_path):
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            log = await service.upload(sid, chunks(), 'source.csv')
            await service.control(sid, 'load', {'log_id': log.log_id})
            for value in (.2, 1., .3):
                result = await service.control(sid, 'seek', {'time_s': value})
                await asyncio.sleep(.005)
            await until(lambda: service.contexts[sid].frames.latest is not None)
            frame = service.contexts[sid].frames.latest
            assert (frame.log_time_s, frame.playback_epoch, frame.control_seq) == (.3, result.playback_epoch, result.control_seq)
            assert service.manager.get(sid).status == 'ready'
            assert len(service.contexts[sid].rpc_tasks) <= 1
    asyncio.run(run())


def test_canceling_delete_does_not_resurrect_context_or_uploaded_archive(tmp_path):
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            await service.upload(sid, chunks(), 'source.csv')
            task = asyncio.create_task(service.delete(sid))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await service.delete(sid)
            assert sid not in service.contexts
            assert not await service.logs.list_logs(sid)
            replacement = await session(service)
            assert replacement != sid
    asyncio.run(run())


def test_video_readiness_changes_are_published_without_an_extra_playback_command(tmp_path):
    from app.core.gateway import GatewayHealth
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            source_ready = [False]
            async def health(_sid=None):
                return GatewayHealth(True, source_ready[0])
            service.manager.gateway.health = health  # Only external media health is substituted.
            box = service.subscribe(sid)
            assert not (await box.next())['data']['video']['publishing']
            source_ready[0] = True
            await until(lambda: service.manager.get(sid).video.publishing)
            async def received():
                while True:
                    packet = await box.next()
                    if packet['type'] == 'connection_snapshot' and packet['data']['video']['publishing']:
                        return packet
            packet = await asyncio.wait_for(received(), .5)
            assert packet['data']['sync']['state'] != 'paired'
    asyncio.run(run())


def test_new_rendered_frames_do_not_clear_an_ongoing_media_stall(tmp_path):
    from app.core.gateway import GatewayHealth
    async def run():
        async with make_service(tmp_path) as service:
            sid = await session(service)
            log = await service.upload(sid, chunks(), 'source.csv')
            await service.control(sid, 'load', {'log_id': log.log_id})
            await until(lambda: service.contexts[sid].frames.latest is not None)
            async def health(_sid=None):
                return GatewayHealth(False, False, 'fixture media outage')
            async def recover():
                pass
            service.manager.gateway.health = health
            service.manager.gateway.recover = recover
            await until(lambda: not service.manager.sessions[sid].gateway_reachable)
            await asyncio.sleep(.2)
            assert service.contexts[sid].frames.latest is not None
            assert service.contexts[sid].sync_state == 'stalled'
    asyncio.run(run())
