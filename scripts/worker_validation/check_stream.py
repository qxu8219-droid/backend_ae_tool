"""Check independently decoded image IDs against formal SEI and synthetic values."""
import argparse,collections,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from replay_protocol import SnapshotCatalog,TargetFrame
from sim_worker.media import SEI_METADATA_UUID


def check(run):
    rows=[json.loads(line) for line in (run/'decoder.json.frames.jsonl').read_text().splitlines()]
    source=json.loads((run/'issued.json').read_text())
    issued={f.target_seq:f for f in (TargetFrame.model_validate(item) for item in source['targets'])}
    rejected=set(source['expected_rejected_target_seqs'])
    matched=[];warmup=0;different=0
    for row in rows:
        assert row['confident'] and row['crc']==row['expected_crc'],'Decoded marker contrast/CRC failed'
        if row['capture_id']==0:warmup+=1;continue
        assert row['metadata_json'] and row['uuid_hex']==SEI_METADATA_UUID.hex(),'Formal SEI missing'
        catalog=SnapshotCatalog.model_validate_json(row['metadata_json'].rstrip('\0'))
        candidates=[s for s in catalog.snapshots if s.capture_id==row['capture_id']]
        assert len(candidates)==1,'Image identity is not uniquely present in the catalog'
        snapshot=candidates[0];t=snapshot.log_time_s;wave=math.sin(t*math.pi/3)
        expected=issued[snapshot.target_seq]
        assert snapshot.target_seq not in rejected,'A deliberately stale target was rendered'
        for name in ('session_id','log_id','playback_epoch','control_seq','target_seq','log_time_s','duration_s','progress','source_sample_index','state','speed','quality'):
            assert getattr(snapshot,name)==getattr(expected,name),f'Wrong issued context: {name}'
        assert snapshot.source_joints==expected.joints
        for index,(actual,wanted) in enumerate(zip(snapshot.joints,expected.joints)):
            difference=math.remainder(actual-wanted,2*math.pi) if index in (1,3,5) else actual-wanted
            assert abs(difference)<1e-5,'Readback differs from the actual issued target'
        if expected.chassis is None:assert snapshot.chassis is None
        else:
            assert snapshot.chassis is not None
            assert abs(snapshot.chassis.x-expected.chassis.x)<1e-5 and abs(snapshot.chassis.y-expected.chassis.y)<1e-5
            assert abs(math.remainder(snapshot.chassis.yaw-expected.chassis.yaw,2*math.pi))<1e-5
        # Handwritten oracle independent of the producer's sample_pose helper.
        for index in (1,3,5):
            assert abs(snapshot.source_joints[index]-t)<1e-10
            assert abs(math.remainder(snapshot.joints[index]-t,2*math.pi))<1e-5
        for index in (17,26):assert abs(snapshot.joints[index]-(.025+.015*wave))<1e-5
        for index in (18,27):assert abs(snapshot.joints[index]-(-.025-.015*wave))<1e-5
        assert snapshot.speed==2.
        assert abs(snapshot.progress-t/6)<1e-10
        different+=catalog.encoder_poll_capture_id!=snapshot.capture_id
        matched.append(snapshot)
    assert len(matched)>=30
    assert len({s.capture_id for s in matched})>=20
    assert any(s.state=='playing' for s in matched) and any(s.state=='paused' for s in matched)
    assert len({s.playback_epoch for s in matched})>=2
    result={'decoded_frames':len(rows),'matched_frames':len(matched),'warmup_frames':warmup,
        'unique_capture_ids':len({s.capture_id for s in matched}),'pixel_crc_errors':0,'catalog_mismatches':0,
        'encoded_image_differs_from_poll':different,'states':dict(collections.Counter(s.state for s in matched)),
        'epochs':dict(collections.Counter(s.playback_epoch for s in matched)),
        'source_and_readback_values_verified':True,'scope':'Independent software H264 decode of real IsaacSim output; not browser presentation'}
    (run/'encoded-identity-evidence.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run_dir',type=Path);check(parser.parse_args().run_dir)
