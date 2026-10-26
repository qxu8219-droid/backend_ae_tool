#!/usr/bin/env python3
"""Real HTTP/WS/SDK validation, synthetic log only; never accesses remote robots."""
import argparse
import asyncio
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from app.core.ports import PortPool
from app.core.processes import ManagedProcess, process_info
from app.schemas.rest import RESTPlaybackResponse, UploadResult
from app.schemas.ws import WS_SERVER_ADAPTER
from replay_protocol import CANONICAL_JOINTS, SnapshotCatalog
from scripts.spike.smoke import sample_pose
from sim_worker.media import SEI_METADATA_UUID
from sim_worker.resources import listening_addresses


async def until(predicate, timeout=150.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        await asyncio.sleep(.02)
    raise TimeoutError('API validation condition timed out')


class Observer:
    def __init__(self, ws, path):
        self.ws, self.path = ws, path
        self.messages, self.frames = [], {}
        self.task = asyncio.create_task(self.collect())

    async def collect(self):
        seq = 0; connection_id = None
        try:
            async for raw in self.ws:
                message = WS_SERVER_ADAPTER.validate_json(raw).model_dump(mode='json')
                assert message['seq'] == seq + 1, message
                seq += 1
                if connection_id is None:
                    assert message['type'] == 'connection_snapshot'
                    connection_id = message['connection_id']
                assert message['connection_id'] == connection_id
                self.messages.append(message)
                if message['type'] == 'frame_snapshot':
                    frame = message['data']; self.frames[frame['capture_id']] = frame
                if message['type'] == 'connection_snapshot':
                    assert message['data']['sync']['state'] != 'paired'
        except ConnectionClosed as exc:
            assert exc.code in (1000, 1001), str(exc)
        finally:
            self.path.write_text(''.join(json.dumps(row) + '\n' for row in self.messages))

    async def frame(self, control):
        def observed():
            if self.task.done():
                self.task.result()
                raise RuntimeError('WebSocket ended while waiting for evidence')
            return next((row for row in reversed(self.frames.values()) if row['control_seq'] == control), None)
        return await until(observed, 20.)

    async def close(self):
        await self.ws.close()
        await self.task


def synthetic_csv():
    stream = io.StringIO(); writer = csv.writer(stream)
    writer.writerow(['timestamp', *CANONICAL_JOINTS])
    for index in range(121):
        t = index / 20
        values = sample_pose(0., list(CANONICAL_JOINTS))
        values[0] = .1 * t
        for joint in (1, 3, 5): values[joint] = t
        values[10] += .1 * t
        for joint in (17, 26): values[joint] = .025 + .002 * t
        for joint in (18, 27): values[joint] = -.025 - .002 * t
        writer.writerow([f'{t:.2f}', *values])
    return stream.getvalue().encode()


def verify_stream(output, observer, controls, source, sid, lid):
    # Independent oracle interpolates the saved input rows; no ReplaySampler use.
    import bisect
    table = [[float(cell) for cell in row] for row in list(csv.reader(io.StringIO(source.decode())))[1:]]
    times = [row[0] for row in table]
    rows = [json.loads(line) for line in (output / 'decoder.json.frames.jsonl').read_text().splitlines()]
    matched = missed = 0; states = set(); epochs = set(); final_matches = 0
    for row in rows:
        assert row['confident'] and row['crc'] == row['expected_crc']
        if row['capture_id'] == 0: continue
        assert row['uuid_hex'] == SEI_METADATA_UUID.hex()
        catalog = SnapshotCatalog.model_validate_json(row['metadata_json'].rstrip('\0'))
        candidates = [frame for frame in catalog.snapshots if frame.capture_id == row['capture_id']]
        assert len(candidates) == 1
        frame = candidates[0]; t = frame.log_time_s
        assert (frame.session_id, frame.log_id) == (sid, lid)
        control = controls[frame.control_seq]
        assert frame.playback_epoch == control.playback_epoch and frame.speed == control.playback.speed
        assert frame.state == control.playback.state or frame.state == 'ended' and t == 6.
        assert frame.duration_s == 6. and abs(frame.progress - t / 6.) < 1e-10
        right = min(bisect.bisect_right(times, t), len(table) - 1); left = max(0, right - 1)
        weight = (t - times[left]) / (times[right] - times[left])
        expected = [a + (b - a) * weight for a, b in zip(table[left][1:], table[right][1:])]
        for index, (value, wanted) in enumerate(zip(frame.source_joints, expected)):
            assert abs(value - wanted) < 1e-9, (index, value, wanted)
            difference = frame.joints[index] - wanted
            if index in (1, 3, 5): difference = math.remainder(difference, 2 * math.pi)
            assert abs(difference) < 1e-5
        assert all(quality.origin == 'commanded' for quality in frame.quality)
        assert frame.chassis is None
        states.add(frame.state); epochs.add(frame.playback_epoch)
        ws_frame = observer.frames.get(frame.capture_id)
        if ws_frame is not None:
            assert frame.model_dump(mode='json') == ws_frame, f'WS and H264 differ for {frame.capture_id}'
            matched += 1
            final_matches += frame.control_seq == max(controls)
        else:
            # Superseded control frames may still be encoded during a barrier.
            # Never match those to the latest WS values or hide the count.
            missed += 1
    assert matched >= 30 and final_matches >= 10, (matched, final_matches)
    assert 'playing' in states and 'paused' in states and len(epochs) >= 2, (states, epochs)
    return {'decoded_frames': len(rows), 'exact_ws_video_matches': matched, 'video_frames_without_ws_match': missed,
            'final_stop_matches': final_matches, 'states': sorted(states), 'epochs': sorted(epochs),
            'all_video_values_checked_against_saved_csv': True, 'pixel_crc_errors': 0}


async def run(args):
    output = args.output_dir.absolute(); output.mkdir(parents=True, exist_ok=False)
    with PortPool(6600, 6699).lease(['tcp', 'tcp', 'tcp', 'udp', 'tcp']) as lease:
        api, mtx, whep, ice, preview = lease.ports
    env = dict(os.environ, ISAACSIM_PYTHON=str(args.isaac_python.absolute()), REPLAY_API_PORT=str(api),
        REPLAY_MTX_API_PORT=str(mtx), REPLAY_WHEP_PORT=str(whep), REPLAY_ICE_PORT=str(ice),
        REPLAY_ALLOWED_ORIGINS=f'http://127.0.0.1:{preview}', REPLAY_STORAGE_ROOT=str(output / 'service'))
    processes = []; observers = []; worker_pids = []; worker_ports = []
    report = {'scope': 'Actual configured HTTP/WS + SDK 6.2 + H264 + native WHEP observation; synthetic commands; formal paired UI deferred to T15'}
    server = await ManagedProcess.spawn(['bash', ROOT / 'scripts/run_backend.sh'], output / 'api.log', cwd=ROOT, env=env)
    processes.append(server)
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{api}', trust_env=False, timeout=30.) as client:
        async def request(method, path, status=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            assert response.status_code == status, (method, path, response.status_code, response.text)
            return response.json() if response.content else None
        async def status_ready(sid):
            value = await request('GET', f'/api/v1/sessions/{sid}')
            assert value['status'] != 'error', value
            return value if value['status'] == 'ready' else None
        async def create():
            created = await request('POST', '/api/v1/sessions', 201, json={'scene_id': 'empty', 'robot_id': 'galaxea_r1pro_2026'})
            sid = created['session_id']
            ws = await connect(f'ws://127.0.0.1:{api}' + created['ws_url'], proxy=None, max_size=2**20)
            observer = Observer(ws, output / f'{sid}-ws.jsonl'); observers.append(observer)
            ready = await until(lambda: status_ready(sid))
            worker_pids.append(ready['worker']['pid'])
            print('T14_HTTP_READY', sid, flush=True)
            return sid, observer
        async def upload(sid, source):
            value = await request('POST', f'/api/v1/sessions/{sid}/logs', 201,
                files={'file': ('synthetic.csv', source, 'text/csv')}, data={'mapping_config': json.dumps({'joints': {'origin': 'commanded'}})})
            log = UploadResult.model_validate(value)
            assert log.raw_file_sha256 == hashlib.sha256(source).hexdigest()
            assert (output / 'service/logs' / log.raw_file_ref).read_bytes() == source
            return log
        try:
            async def api_up():
                assert server.returncode is None, 'API server exited'
                try: return (await client.get('/api/v1/scenes')).status_code == 200
                except httpx.ConnectError: return False
            await until(api_up, 30.)
            assert (await request('GET', '/api/v1/robots'))['robots'][0]['dof'] == 28
            assert len((await request('GET', '/api/v1/robots/galaxea_r1pro_2026/joints'))['joints']) == 28
            sid, observer = await create(); base = f'/api/v1/sessions/{sid}'
            source = synthetic_csv(); (output / 'synthetic.csv').write_bytes(source)
            log = await upload(sid, source)
            assert (await request('GET', base + '/playback'))['state'] == 'idle'
            assert len((await request('GET', base + f'/logs/{log.log_id}/channels'))['channels']) == 28
            curve = await request('GET', base + f'/logs/{log.log_id}/curve', params={'channels': CANONICAL_JOINTS[0], 'max_points': 121})
            assert abs(curve['series'][0]['points'][-1]['value'] - .6) < 1e-12
            controls = {}
            async def control(name, **payload):
                payload['request_id'] = f'{name}-{len(controls) + 1}'
                response = RESTPlaybackResponse.model_validate(await request('POST', base + '/playback/' + name, json=payload))
                assert response.request_id == payload['request_id'] and response.ack_stage == 'accepted'
                controls[response.control_seq] = response
                frame = await observer.frame(response.control_seq)
                print('T14_CONTROL', name, frame['log_time_s'], frame['capture_id'], flush=True)
                return frame
            initial = await control('load', log_id=log.log_id)
            assert initial['log_time_s'] == 0 and initial['state'] == 'ready'
            runtime_path = output / 'service/state' / sid / 'worker/runtime.json'
            runtime = json.loads(runtime_path.read_text())
            rtsp_port = int(runtime['rtsp_url'].split(':')[2].split('/')[0]); worker_ports.append(rtsp_port)
            assert listening_addresses(rtsp_port) == ['127.0.0.1']
            async def video_ready():
                value = await request('GET', base + '/video')
                return value if value['publishing'] else None
            video = await until(video_ready, 30.)
            decoder = await ManagedProcess.spawn([args.decoder.absolute(), runtime['rtsp_url'], '14', output / 'decoder.json', output / 'decoded-first.ppm'], output / 'decoder.log')
            processes.append(decoder)
            # Allow the independent decoder to acquire the next IDR before controls.
            await asyncio.sleep(2.)
            await control('play', speed=1.); await asyncio.sleep(1.2)
            await control('speed', speed=10.); await asyncio.sleep(.15)
            await control('pause'); await asyncio.sleep(.6)
            assert (await control('seek', time_s=1.5))['log_time_s'] == 1.5
            assert (await control('step', direction='forward'))['log_time_s'] == 1.55
            assert (await control('step', direction='backward'))['log_time_s'] == 1.5
            stopped = await control('stop'); assert stopped['log_time_s'] == 0
            page_dir = output / 'page'; page_dir.mkdir()
            page = await ManagedProcess.spawn([sys.executable, ROOT / 'scripts/spike/whep_preview.py', '--output-dir', page_dir, '--port', preview], output / 'page.log')
            processes.append(page)
            async def page_up():
                try: return (await client.get(f'http://127.0.0.1:{preview}/status')).status_code == 200
                except httpx.ConnectError: return False
            await until(page_up, 10.)
            url = f'http://127.0.0.1:{preview}/?' + urlencode({'whep': video['whep_url'], 'autoconnect': '1'})
            browser = await ManagedProcess.spawn(['node', ROOT / 'scripts/observe_api_video.mjs', url, runtime_path, output / 'browser'], output / 'browser.log')
            processes.append(browser)
            await asyncio.wait_for(browser.process.wait(), 55.); assert browser.returncode == 0, (output / 'browser.log').read_text()
            await asyncio.wait_for(decoder.process.wait(), 25.); assert decoder.returncode == 0
            await asyncio.sleep(.2)
            report['identity'] = verify_stream(output, observer, controls, source, sid, log.log_id)
            observations = json.loads((output / 'browser.json').read_text())['observations']
            for sample in observations:
                frame = observer.frames[sample['captureId']]
                assert frame['control_seq'] == max(controls) and frame['log_time_s'] == 0.
            report['native_browser_pixel_matches'] = len(observations)
            (output / 'controls.json').write_text(json.dumps({key: value.model_dump(mode='json') for key, value in controls.items()}, indent=2))
            status = await request('GET', base); assert status['render_fps'] >= 15
            report['raw_capture_fps'] = status['render_fps']
            started = time.monotonic(); os.kill(status['worker']['pid'], signal.SIGKILL)
            async def failed():
                value = await request('GET', base)
                return value if value['status'] == 'error' else None
            failed_status = await until(failed, 3.)
            assert failed_status['worker']['error']['code'] == 'WORKER_EXITED'
            report['worker_exit_detection_s'] = time.monotonic() - started
            await until(lambda: any(row['type'] == 'system' and row['data']['event'] in ('worker_exited', 'session_error') for row in observer.messages), 3.)
            await request('DELETE', base, 204); await request('DELETE', base, 204)
            await observer.close()
            sid2, observer2 = await create(); assert sid2 != sid
            log2 = await upload(sid2, source)
            response = await request('POST', f'/api/v1/sessions/{sid2}/playback/load', json={'log_id': log2.log_id})
            fresh = await observer2.frame(response['control_seq'])
            assert fresh['log_time_s'] == 0 and fresh['capture_id'] == 1
            assert observer2.messages[0]['connection_id'] != observer.messages[0]['connection_id']
            report['new_session_reload'] = {'new_sid': sid2, 'capture_id': fresh['capture_id'], 'log_time_s': fresh['log_time_s']}
            await request('DELETE', f'/api/v1/sessions/{sid2}', 204)
            await observer2.close()
        finally:
            for observer in observers:
                await observer.ws.close()
                await asyncio.gather(observer.task, return_exceptions=True)
            for process in reversed(processes):
                await process.terminate()
    assert all(not (info := process_info(pid)) or info['state'] == 'Z' for pid in worker_pids)
    assert all(not listening_addresses(port) for port in [api, mtx, whep, preview, *worker_ports])
    with PortPool(ice, ice).lease(['udp']): pass
    report.update(passed=True, owned_resources_released=True, original_source_sha256=hashlib.sha256(source).hexdigest())
    (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--isaac-python', type=Path, required=True)
    parser.add_argument('--decoder', type=Path, default=ROOT / 'outputs/t11/rtsp_identity')
    asyncio.run(run(parser.parse_args()))
