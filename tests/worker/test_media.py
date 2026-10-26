import sys,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'replay'))
from test_engine import frame
from timeline_support import applied_frame
from replay_protocol import SnapshotCatalog
from sim_worker.media import SnapshotRegistry


class Image:
    shape=(2,32,3);dtype='uint8'
    def __init__(self,id):self.bits=id|(zlib.crc32(id.to_bytes(4,'big'))<<32)
    def __getitem__(self,key):
        y,x,c=key;bit=y*32+x;return (0,255,0)[c] if self.bits&(1<<bit) else (255,0,0)[c]


def test_formal_catalog_admits_only_actual_raw_identity_and_never_assumes_encoder_current_frame():
    pixels=[(i%32,i//32) for i in range(64)];r=SnapshotRegistry(pixels,[],clock=lambda:1001.)
    r.arm(1,applied_frame(frame()))
    assert r.prepare_frame({'Attribute':1,'rgb':Image(2),'LdrColor':b'encoded','reference_time':(1,20)}) is None
    assert r.rendered(1) is None
    out=r.prepare_frame({'Attribute':1,'rgb':Image(1),'LdrColor':b'older encoded bytes','reference_time':(2,20)})
    catalog=SnapshotCatalog.model_validate_json(out.metadata)
    assert catalog.snapshots[0].capture_id==1 and r.rendered(1).raw_identity_verified
    r.arm(2,applied_frame(frame(2,t=.5)))
    out=r.prepare_frame({'Attribute':2,'rgb':Image(2),'LdrColor':b'possibly capture1','reference_time':(3,20)})
    assert [s.capture_id for s in SnapshotCatalog.model_validate_json(out.metadata).snapshots]==[1,2]
    assert r.rendered(2).log_time_s==.5


def test_bad_raw_crc_never_produces_render_proof_and_cache_is_bounded():
    pixels=[(i%32,i//32) for i in range(64)];r=SnapshotRegistry(pixels,[],capacity=2,clock=lambda:1001.)
    for i in range(1,8):
        r.arm(i,applied_frame(frame(i)))
        image=Image(i)
        if i==7:image.bits^=1<<40
        r.prepare_frame({'Attribute':i,'rgb':image,'LdrColor':b'bytes','reference_time':(i,20)})
    assert r.rendered(7) is None
    assert r.stats()['catalog_size']<=2 and r.stats()['raw_errors']==1
