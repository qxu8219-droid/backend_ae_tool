#!/usr/bin/env python3
"""Prove source-peak preservation; optionally prove actual SDK/SEI limit masks."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analysis.cursor import RenderedEventCursor
from app.analysis.service import ReplayAnalysis
from app.replay.interpolator import ReplaySampler
from app.services.log_store import LogStore
from replay_protocol import AppliedFrame, CANONICAL_JOINTS, ChannelQuality, RenderedFrame, RPCRequest, SnapshotCatalog, TargetFrame


async def cpu_probe(output):
    name = CANONICAL_JOINTS[0]
    content = ('timestamp,' + name + '\n' + '\n'.join(
        f'{i / 1000:.3f},{4.2 if i == 501 else -4.2 if i == 503 else 0.}' for i in range(2000)) + '\n').encode()
    source_path = output / 'synthetic-source.csv'; source_path.write_bytes(content)
    source_hash = hashlib.sha256(content).hexdigest()
    async def chunks():
        for start in range(0, len(content), 4096):
            yield content[start:start + 4096]
    store = LogStore(output / 'archive')
    started = time.monotonic()
    imported = await store.import_file('analysis-probe', chunks(), 'fixture.csv')
    data = await store.get('analysis-probe', imported.log_id)
    import_s = time.monotonic() - started
    started = time.monotonic()
    analysis = ReplayAnalysis(data)
    prepared_s = time.monotonic() - started
    events = analysis.history.overlapping(0., 1.999)
    assert [(e.from_s, e.peak_value, e.peak_quality.origin) for e in events] == [(.501, 4.2, 'measured'), (.503, -4.2, 'measured')]
    started = time.monotonic()
    curve = analysis.curve([name], max_points=2)
    query_s = time.monotonic() - started
    assert [(point.time_s, point.value) for point in curve.series[0].points] == [(.501, 4.2), (.503, -4.2)]
    cursor = RenderedEventCursor(analysis.history, session_id='analysis-probe')
    sampler = ReplaySampler(data, session_id='analysis-probe')
    def synthetic_receipt(t, seq, epoch):
        # Explicit CPU test double only; this section does not prove a captured image.
        target = sampler.sample_at(t, target_seq=seq, control_seq=epoch, playback_epoch=epoch)
        applied = AppliedFrame(**target.model_dump(), source_joints=target.joints, applied_wall_s=1000.)
        return RenderedFrame(**applied.model_dump(), capture_id=seq, render_seq=seq, captured_wall_s=1001., raw_identity_verified=True)
    assert not cursor.advance(synthetic_receipt(0., 1, 1))
    alerts = cursor.advance(synthetic_receipt(1., 2, 1))
    assert [a.log_time_s for a in alerts] == [.501, .503]
    assert not cursor.advance(synthetic_receipt(1., 3, 1))
    assert not cursor.advance(synthetic_receipt(0., 4, 2))
    replayed = cursor.advance(synthetic_receipt(1., 5, 2))
    assert [a.details['history_event_id'] for a in alerts] == [a.details['history_event_id'] for a in replayed]
    assert all(a.alert_id != b.alert_id for a, b in zip(alerts, replayed))
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256((store.root / data.raw_file_ref).read_bytes()).hexdigest() == source_hash
    assert data.raw_file_sha256 == source_hash
    (output / 'curve.json').write_text(curve.model_dump_json(indent=2))
    (output / 'events.json').write_text(json.dumps([e.model_dump(mode='json') for e in events], indent=2))
    report = {'passed': True, 'scope': 'Actual CSV import/archive and CPU analysis; synthetic observations and CPU receipt fixture, no image proof',
              'source_samples': 2000, 'narrow_events': 2, 'curve_budget': 2, 'retained_peak_values': [4.2, -4.2],
              'source_sha256_unchanged': source_hash, 'event_ids_stable_on_replay': True,
              'import_s': import_s, 'prepare_s': prepared_s, 'curve_query_s': query_s}
    (output / 'cpu-evidence.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


async def gpu_probe(output, isaac_python, decoder_path):
    from app.core.config import Settings
    from app.core.gateway import GatewayConfig
    from app.core.ports import PortPool
    from app.core.processes import ManagedProcess
    from app.core.session_manager import SessionManager
    from app.schemas.rest import CreateSessionRequest
    from scripts.spike.smoke import sample_pose
    from sim_worker.media import SEI_METADATA_UUID
    gpu = output / 'gpu'; gpu.mkdir()
    with PortPool(6500, 6599).lease(['tcp', 'tcp', 'udp']) as lease:
        gateway = GatewayConfig(api_port=lease.ports[0], http_port=lease.ports[1], ice_port=lease.ports[2])
    settings = Settings(isaac_python=isaac_python.absolute(), state_dir=gpu / 'state', gateway=gateway)
    cases = [(0.06, 'measured', True, ['original'], 131072, 0),
             (0.049, 'commanded', True, ['original'], 0, 131072),
             (0.06, 'measured', False, ['forward_filled'], 0, 0),
             (0.06, 'derived', True, ['derived'], 0, 0)]
    decoder = None
    issued = {}
    async with SessionManager(settings) as manager:
        created = await manager.create(CreateSessionRequest(scene_id='empty', robot_id='galaxea_r1pro_2026'))
        sid = created.session_id
        deadline = time.monotonic() + 150
        while manager.get(sid).status == 'starting' and time.monotonic() < deadline:
            await asyncio.sleep(.05)
        assert manager.get(sid).status == 'ready', manager.get(sid).model_dump()
        config = manager.sessions[sid].config
        try:
            response = await manager.rpc(sid, RPCRequest(session_id=sid, request_id='load', playback_epoch=1, control_seq=1,
                command={'cmd': 'load_log', 'log_id': 'synthetic-limit-fixture'}))
            assert response.ok
            for seq, (value, origin, valid, processing, flags, warns) in enumerate(cases, 1):
                joints = sample_pose(0., list(CANONICAL_JOINTS)); joints[17] = value
                qualities = [ChannelQuality(origin='derived', valid=True, processing=['derived'], method='fixture.synthetic-not-hardware')] * 28
                qualities[17] = ChannelQuality(origin=origin, valid=valid, processing=processing, source_indices=[seq - 1], method='fixture.synthetic-not-hardware')
                target = TargetFrame(session_id=sid, log_id='synthetic-limit-fixture', playback_epoch=1, control_seq=seq,
                    target_seq=seq, log_time_s=float(seq - 1), duration_s=4., progress=(seq - 1) / 4,
                    source_sample_index=seq - 1, joints=joints, quality=qualities, chassis=None, state='paused', speed=1.)
                issued[seq] = {'target': target.model_dump(mode='json'), 'flags': flags, 'warns': warns}
                result = await manager.rpc(sid, RPCRequest(session_id=sid, request_id=f'case-{seq}', playback_epoch=1,
                    control_seq=seq, command={'cmd': 'apply_keyframe', 'frame': target}))
                assert result.ok and result.frame.raw_identity_verified
                if seq == 1:
                    decoder = await ManagedProcess.spawn([decoder_path.resolve(), f'rtsp://127.0.0.1:{config.rtsp_port}/stream',
                        '12', gpu / 'decoder.json', gpu / 'decoded-first.ppm'], gpu / 'decoder.log')
                await asyncio.sleep(3.)
            await asyncio.wait_for(decoder.process.wait(), 20.)
            assert decoder.returncode == 0
            await decoder.terminate(); decoder = None
            counts = {seq: 0 for seq in issued}
            rows = [json.loads(line) for line in (gpu / 'decoder.json.frames.jsonl').read_text().splitlines()]
            for row in rows:
                assert row['confident'] and row['crc'] == row['expected_crc']
                assert row['uuid_hex'] == SEI_METADATA_UUID.hex()
                catalog = SnapshotCatalog.model_validate_json(row['metadata_json'].rstrip('\0'))
                matching = [s for s in catalog.snapshots if s.capture_id == row['capture_id']]
                assert len(matching) == 1
                snapshot = matching[0]; expected = issued[snapshot.target_seq]
                target = TargetFrame.model_validate(expected['target'])
                assert snapshot.session_id == sid and snapshot.source_joints == target.joints
                assert snapshot.quality == target.quality and snapshot.log_time_s == target.log_time_s
                assert (snapshot.limit_flags, snapshot.limit_warn_flags) == (expected['flags'], expected['warns'])
                assert abs(snapshot.joints[17] - target.joints[17]) < 1e-7
                counts[snapshot.target_seq] += 1
            assert len(rows) >= 60 and all(n >= 5 for n in counts.values()), counts
            report = {'passed': True, 'scope': 'Real SDK raw capture and independent H264 decode; synthetic quality-class fixtures, not robot feedback',
                      'decoded_frames': len(rows), 'matched_frames': len(rows), 'cases': counts,
                      'expected_masks': {seq: [item['flags'], item['warns']] for seq, item in issued.items()},
                      'raw_capture_fps': manager.get(sid).render_fps}
            (gpu / 'issued.json').write_text(json.dumps(issued, indent=2))
        finally:
            if decoder is not None:
                await decoder.terminate()
    assert manager.get(sid).status == 'stopped'
    from sim_worker.resources import listening_addresses
    assert all(not listening_addresses(getattr(config, name + '_port')) for name in ('rpc', 'target', 'event', 'rtsp'))
    assert manager.gateway.process.returncode is not None
    report['owned_resources_released'] = True
    (gpu / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


async def main(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    await cpu_probe(args.output_dir)
    if args.isaac_python:
        await gpu_probe(args.output_dir, args.isaac_python, args.decoder)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--isaac-python', type=Path)
    parser.add_argument('--decoder', type=Path, default=ROOT / 'outputs/t11/rtsp_identity')
    asyncio.run(main(parser.parse_args()))
