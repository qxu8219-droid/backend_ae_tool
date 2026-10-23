from pathlib import Path
import subprocess
import unittest


class FramePairTests(unittest.TestCase):
    def test_pixel_identity_selects_exact_catalog_entry(self):
        module = (Path(__file__).resolve().parents[1] / 'scripts/spike/frame_pair.js').as_uri()
        script = r'''
import assert from 'node:assert/strict';
import {decodeCaptureStamp, selectFrameSnapshot} from '__MODULE__';
const samples=(id,crc)=>Array.from({length:64},(_,i)=>
  ((i<32?id:crc)>>> (i%32))&1 ? [0,220,0,255] : [220,0,0,255]);
for(const [id,crc] of [[1,0x5643ef8a],[0x12345678,0x4a090e98],[0x80000000,0xcc1d6927],[0xffffffff,0xffffffff]]){
  const stamp=decodeCaptureStamp(samples(id,crc));
  assert.equal(stamp.capture_id,id);assert.equal(stamp.valid,true);
}
const envelope={session_id:'s',encoder_poll_capture_id:9,
  snapshots:[{session_id:'s',capture_id:1},{session_id:'s',capture_id:8},{session_id:'s',capture_id:9}]};
assert.equal(selectFrameSnapshot(envelope,samples(1,0x5643ef8a)).snapshot.capture_id,1);
assert.equal(selectFrameSnapshot({...envelope,encoder_poll_capture_id:100},samples(1,0x5643ef8a)).snapshot.capture_id,1);
assert.throws(()=>selectFrameSnapshot({...envelope,snapshots:envelope.snapshots.slice(1)},samples(1,0x5643ef8a)),/catalog/);
assert.throws(()=>selectFrameSnapshot(envelope,samples(1,0x5643ef8b)),/stamp/);
assert.equal(decodeCaptureStamp(Array(64).fill([100,100,0])).valid,false);
assert.equal(decodeCaptureStamp(samples(0,0x2144df1c)).valid,false);
assert.throws(()=>selectFrameSnapshot({...envelope,snapshots:[...envelope.snapshots,envelope.snapshots[0]]},samples(1,0x5643ef8a)),/catalog/);
assert.throws(()=>selectFrameSnapshot({...envelope,session_id:'other'},samples(1,0x5643ef8a)),/catalog/);
'''.replace('__MODULE__', module)
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
