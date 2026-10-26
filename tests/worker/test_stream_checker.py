import json,sys,zlib
from pathlib import Path
import pytest
from replay_protocol import SnapshotCatalog,FrameSnapshot
from scripts.validate_worker import source
from app.replay.interpolator import ReplaySampler
from sim_worker.media import SEI_METADATA_UUID
from scripts.worker_validation.check_stream import check


def fixture(path):
    sampler=ReplaySampler(source(),session_id='s');rows=[];targets=[]
    for i in range(30):
        target=sampler.sample_at(i/20,target_seq=i+1,control_seq=1 if i<15 else 2,playback_epoch=1 if i<15 else 2).model_copy(update={'state':'playing' if i<15 else 'paused','speed':2.})
        targets.append(target.model_dump(mode='json'))
        payload={k:getattr(target,k) for k in FrameSnapshot.model_fields if hasattr(target,k)}
        snapshot=FrameSnapshot(**payload,capture_id=i+1,render_seq=i+1,source_joints=target.joints,captured_wall_s=1000.+i,limit_flags=0,limit_warn_flags=0)
        catalog=SnapshotCatalog(session_id='s',marker_pixels=[(j%32,j//32) for j in range(64)],snapshots=[snapshot],encoder_poll_capture_id=i+1)
        crc=zlib.crc32((i+1).to_bytes(4,'big'))
        rows.append({'capture_id':i+1,'crc':crc,'expected_crc':crc,'confident':True,'uuid_hex':SEI_METADATA_UUID.hex(),'metadata_json':catalog.model_dump_json()})
    (path/'issued.json').write_text(json.dumps({'targets':targets,'expected_rejected_target_seqs':[]}))
    return rows


@pytest.mark.parametrize('corruption',['crc','confidence','metadata','uuid','unissued','context'])
def test_decodable_frames_with_wrong_identity_or_unissued_context_fail_validation(tmp_path,corruption):
    rows=fixture(tmp_path)
    if corruption=='crc':rows[0]['crc']^=1
    elif corruption=='confidence':rows[0]['confident']=False
    elif corruption=='metadata':rows[0]['metadata_json']=None
    elif corruption=='uuid':rows[0]['uuid_hex']='0'*32
    else:
        catalog=json.loads(rows[0]['metadata_json']);snapshot=catalog['snapshots'][0]
        if corruption=='unissued':snapshot['target_seq']=999
        else:snapshot['state']='ended'
        rows[0]['metadata_json']=json.dumps(catalog)
    (tmp_path/'decoder.json.frames.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    with pytest.raises((AssertionError,ValueError,KeyError)):check(tmp_path)


def test_complete_identity_trace_checks_against_recorded_issued_targets(tmp_path):
    rows=fixture(tmp_path);(tmp_path/'decoder.json.frames.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    check(tmp_path);assert json.loads((tmp_path/'encoded-identity-evidence.json').read_text())['matched_frames']==30
