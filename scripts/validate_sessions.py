#!/usr/bin/env python3
"""Actual local SDK/gateway lifecycle proof. Only synthetic targets are generated."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import Settings
from app.core.gateway import GatewayConfig
from app.core.ports import PortPool
from app.core.processes import ManagedProcess, process_info
from app.core.session_manager import SessionManager
from app.replay.interpolator import ReplaySampler
from app.schemas.rest import CreateSessionRequest
from replay_protocol import RPCRequest
from scripts.validate_worker import source
from scripts.worker_validation.check_stream import check
from sim_worker.resources import listening_addresses

REQUEST = CreateSessionRequest(scene_id='empty', robot_id='galaxea_r1pro_2026')


async def until(predicate, timeout=150.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(.02)
    raise TimeoutError('Lifecycle validation condition timed out')


async def ready(manager, sid):
    def condition():
        status = manager.get(sid)
        if status.status == 'error':
            raise RuntimeError(status.model_dump_json())
        return status if status.status == 'ready' else None
    return await until(condition)


def owned_alive(pid):
    info = process_info(pid)
    return bool(info and info['state'] != 'Z')


async def run(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with PortPool(6400, 6499).lease(['tcp', 'tcp', 'udp']) as lease:
        gateway = GatewayConfig(api_port=lease.ports[0], http_port=lease.ports[1], ice_port=lease.ports[2])
    # Resolving a venv's python symlink selects its base interpreter and loses
    # the SDK environment. Make the path absolute without dereferencing it.
    settings = Settings(isaac_python=args.isaac_python.absolute(), state_dir=output / 'state', gateway=gateway, gateway_poll_s=.25)
    report = {'scope': 'Real IsaacSim 6.2 / local ZMQ / real MediaMTX; synthetic trajectories; no browser presentation claim',
              'sessions': [], 'owned_pids': [], 'media_ports': [gateway.api_port, gateway.http_port, gateway.ice_port]}
    decoder = None
    try:
        async with SessionManager(settings) as manager:
            report['owned_pids'].append(manager.gateway.process.pid)
            created = await manager.create(REQUEST)
            sid = created.session_id
            status = await ready(manager, sid)
            report['owned_pids'].append(status.worker.pid)
            events = manager.drain_events(sid)
            phases = [e.status.worker.phase for e in events]
            assert phases == ['booting', 'loading_scene', 'loading_robot', 'rtsp_publishing', 'ready'], phases
            assert not status.video.publishing
            record = manager.sessions[sid]
            assert listening_addresses(record.config.rtsp_port) == ['127.0.0.1']
            print('T12_REAL_READY', sid, flush=True)
            sampler = ReplaySampler(source(), session_id=sid)
            issued = []
            async def rpc(name, control, epoch, **fields):
                result = await manager.rpc(sid, RPCRequest(session_id=sid, request_id=f'{name}-{control}-{len(issued)}',
                    control_seq=control, playback_epoch=epoch, command={'cmd': name, **fields}))
                assert result.ok, result.model_dump()
                return result
            async def frame(seq, t, control, epoch, state):
                value = sampler.sample_at(t, target_seq=seq, control_seq=control, playback_epoch=epoch).model_copy(update={'state': state, 'speed': 2.})
                issued.append(value)
                return await rpc('apply_keyframe', control, epoch, frame=value)
            await rpc('load_log', 1, 1, log_id='synthetic-log')
            initial = await frame(1, 0., 1, 1, 'ready')
            assert initial.frame.capture_id == 1 and initial.frame.log_time_s == 0
            await until(lambda: manager.get(sid).video.publishing, timeout=25.)
            proof = output / 'identity'; proof.mkdir()
            decoder = await ManagedProcess.spawn([
                args.decoder.resolve(), f'rtsp://127.0.0.1:{record.config.rtsp_port}/stream', '8',
                proof / 'decoder.json', proof / 'decoded-first.ppm'], proof / 'decoder.log')
            await frame(2, .5, 2, 1, 'playing')
            await asyncio.sleep(2.)
            await frame(3, 1., 3, 2, 'paused')
            await asyncio.sleep(2.)
            await frame(4, 5., 4, 3, 'paused')
            (proof / 'issued.json').write_text(json.dumps({'targets': [value.model_dump(mode='json') for value in issued], 'expected_rejected_target_seqs': []}, indent=2))
            await asyncio.wait_for(decoder.process.wait(), 20.)
            assert decoder.returncode == 0
            await decoder.terminate(); decoder = None
            check(proof)
            raw_fps = manager.get(sid).render_fps
            assert raw_fps is not None and raw_fps >= 15, raw_fps
            before_pid = status.worker.pid
            manager.gateway.process.signal_group(signal.SIGKILL)
            recovery_events = []
            def recovered():
                recovery_events.extend(manager.drain_events(sid))
                kinds = {event.kind for event in recovery_events}
                current = manager.get(sid)
                assert current.status == 'ready' and current.worker.pid == before_pid
                return 'gateway_unreachable' in kinds and 'gateway_recovered' in kinds and current.video.publishing
            await until(recovered, timeout=30.)
            report['owned_pids'].append(manager.gateway.process.pid)
            print('T12_GATEWAY_RECOVERED', flush=True)
            started = time.monotonic(); os.kill(before_pid, signal.SIGKILL)
            await until(lambda: manager.get(sid).status == 'error', timeout=3.)
            detected_s = time.monotonic() - started
            assert manager.get(sid).worker.error.code == 'WORKER_EXITED'
            await manager.delete(sid)
            assert not manager.drain_worker_events(sid)
            assert await manager.gateway.request('GET', '/v3/config/paths/get/' + sid, missing_ok=True) is None
            ports = [record.config.rpc_port, record.config.target_port, record.config.event_port, record.config.rtsp_port]
            assert all(not listening_addresses(port) for port in ports)
            report['sessions'].append({'sid': sid, 'phases': phases, 'raw_capture_fps': raw_fps,
                'worker_kill_detection_s': detected_s, 'worker_pid_unchanged_on_gateway_recovery': True,
                'native_rtsp_loopback_only': True, 'ports_released': ports})
            print('T12_WORKER_EXIT_DETECTED', detected_s, flush=True)

            created = await manager.create(REQUEST)
            assert created.session_id != sid
            second = await ready(manager, created.session_id)
            report['owned_pids'].append(second.worker.pid)
            sid = created.session_id
            for rid, command in [
                ('reload', {'cmd': 'load_log', 'log_id': 'synthetic-log'}),
                ('first', {'cmd': 'apply_keyframe', 'frame': ReplaySampler(source(), session_id=sid).sample_at(0., target_seq=1, control_seq=1, playback_epoch=1).model_copy(update={'state': 'ready', 'speed': 2.})}),
            ]:
                response = await manager.rpc(sid, RPCRequest(session_id=sid, request_id=rid, control_seq=1, playback_epoch=1, command=command))
                assert response.ok
            assert response.frame.capture_id == 1 and response.frame.log_time_s == 0.
            await until(lambda: manager.get(sid).video.publishing, timeout=25.)
            report['sessions'].append({'sid': sid, 'reload_log_time_s': response.frame.log_time_s, 'reload_capture_id': response.frame.capture_id})
            print('T12_NEW_SESSION_RELOADED', sid, flush=True)
        assert manager.get(sid).status == 'stopped'
        report['application_close_cleaned_ready_session'] = True

        async with SessionManager(settings) as next_manager:
            report['owned_pids'].append(next_manager.gateway.process.pid)
            created = await next_manager.create(REQUEST)
            sid = created.session_id
            await until(lambda: next_manager.get(sid).worker.pid, timeout=5.)
            status = next_manager.get(sid)
            report['owned_pids'].append(status.worker.pid)
            assert status.status == 'starting'
            await next_manager.delete(sid)
            await next_manager.delete(sid)
            assert next_manager.get(sid).status == 'stopped' and not owned_alive(status.worker.pid)
            report['sessions'].append({'sid': sid, 'delete_during_start': True, 'delete_idempotent': True})
        assert all(not owned_alive(pid) for pid in report['owned_pids'])
        assert not listening_addresses(gateway.api_port) and not listening_addresses(gateway.http_port)
        with PortPool(gateway.ice_port, gateway.ice_port).lease(['udp']):
            pass
        report['passed'] = True
        report['all_owned_processes_exited'] = True
        (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if decoder is not None:
            await decoder.terminate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--isaac-python', type=Path, required=True)
    parser.add_argument('--decoder', type=Path, default=ROOT / 'outputs/t11/rtsp_identity')
    asyncio.run(run(parser.parse_args()))
