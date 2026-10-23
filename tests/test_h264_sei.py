from pathlib import Path
import subprocess
import unittest


class H264SEITests(unittest.TestCase):
    def test_real_sei_nal_parsing_and_rejection_boundaries(self):
        module = (Path(__file__).resolve().parents[1]/'scripts/spike/h264_sei.js').as_uri()
        script = r'''
import assert from 'node:assert/strict';
import {extractSnapshot, splitNalUnits} from '__MODULE__';
const uuid=Buffer.from('3654537366c659f396416e41d4c037f5','hex');
const value={sei_schema:'p0.snapshot.v1',capture_id:17,render_seq:17,epoch:2,
 log_time_s:3.25,joints:Array(28).fill(0),chassis:{x:1,y:2,yaw:0},state:'paused',speed:1};
const payload=Buffer.concat([uuid,Buffer.from(JSON.stringify(value))]);
const size=[];let n=payload.length;while(n>=255){size.push(255);n-=255;}size.push(n);
const nal=Buffer.concat([Buffer.from([6,5,...size]),payload,Buffer.from([128])]);
const annex=Buffer.concat([Buffer.from([0,0,0,1]),nal,Buffer.from([0,0,1,0x65,0x88])]);
assert.equal(extractSnapshot(annex).capture_id,17);
assert.equal(extractSnapshot(annex).log_time_s,3.25);
assert.equal(splitNalUnits(annex).length,2);
const length=Buffer.alloc(4);length.writeUInt32BE(nal.length);
assert.equal(extractSnapshot(Buffer.concat([length,nal])).epoch,2);
const other=Buffer.from(annex);other[4+1+1+size.length]=0xff;
assert.equal(extractSnapshot(other),null);
assert.equal(extractSnapshot(Buffer.from([0,0,1,0x65,0x88])),null);
assert.throws(()=>extractSnapshot(Buffer.concat([annex,annex])),/multiple/i);
assert.throws(()=>extractSnapshot(Buffer.concat([Buffer.from([0,0,1,6,5,255]),Buffer.alloc(3)])),/truncat|size/i);
// A malformed JSON payload under our UUID must fail closed, not choose another state.
const bad=Buffer.concat([Buffer.from([0,0,1,6,5,17]),uuid,Buffer.from([123,128])]);
assert.throws(()=>extractSnapshot(bad));
'''.replace('__MODULE__', module)
        result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_catalog_preserves_candidates_and_rejects_ambiguous_identity(self):
        module = (Path(__file__).resolve().parents[1]/'scripts/spike/h264_sei.js').as_uri()
        script = self.catalog_fixture() + r'''
const {extractCatalog}=await import('__MODULE__');
assert.equal(typeof extractCatalog,'function','catalog parser must exist');
const value=catalog(0xffffffff,[1,4097,0xffffffff]);
const parsed=extractCatalog(access(value));
assert.deepEqual(parsed.snapshots.map(s=>s.capture_id),[1,4097,0xffffffff]);
assert.equal(parsed.encoder_poll_capture_id,0xffffffff);
assert.equal(parsed.snapshots[0].log_time_s,0.25);
for(const state of ['playing','paused','ready','ended']){
 const candidate={...value,snapshots:[{...value.snapshots[0],state}]};
 assert.equal(extractCatalog(access(candidate)).snapshots[0].state,state);
}
assert.equal(extractCatalog(access({...value,encoder_poll_capture_id:null})).encoder_poll_capture_id,null);
assert.equal(extractCatalog(Buffer.from([0,0,1,0x65,0x88])),null);
const invalid=[
 {...value,snapshots:[]}, {...value,snapshots:Array(17).fill(value.snapshots[0])},
 {...value,snapshots:[value.snapshots[0],value.snapshots[0]]},
 {...value,snapshots:[{...value.snapshots[0],session_id:'other'}]},
 {...value,snapshots:[{...value.snapshots[0],capture_id:0x100000001,render_seq:0x100000001}]},
 {...value,snapshots:[{...value.snapshots[0],render_seq:2}]},
 {...value,snapshots:[{...value.snapshots[0],sei_schema:'p0.snapshot_catalog.v1'}]},
 {...value,snapshots:[{...value.snapshots[0],joints:Array(28).fill(null)}]},
 {...value,marker_bits:12}, {...value,marker_pixels:Array(64).fill([0,0])},
 {...value,marker_pixels:value.marker_pixels.map((p,i)=>i===0?[-1,0]:p)},
 {...value,encoder_poll_capture_id:0}, {...value,session_id:''},
 {...value,snapshots:[{...value.snapshots[0],marker_pixels:value.marker_pixels.map((p,i)=>i===0?[99,99]:p)}]},
];
for(const bad of invalid)assert.throws(()=>extractCatalog(access(bad)),/invalid/i);
assert.throws(()=>extractCatalog(Buffer.concat([access(value),access(value)])),/multiple/i);
const annex=access(value);
const nal=annex.subarray(4,annex.length-13);
const length=Buffer.alloc(4);length.writeUInt32BE(nal.length);
assert.equal(extractCatalog(Buffer.concat([length,nal])).snapshots[2].capture_id,0xffffffff);
'''.replace('__MODULE__',module)
        self.run_node(script)

    def test_worker_uses_unique_packet_ids_and_bounds_pending_decodes(self):
        module = (Path(__file__).resolve().parents[1]/'scripts/spike/sei_transform.js').as_uri()
        script = self.catalog_fixture() + r'''
const messages=[],decoders=[];
globalThis.self={postMessage:value=>messages.push(value)};
globalThis.EncodedVideoChunk=class {constructor(options){Object.assign(this,options);}};
// Node has no WebCodecs. This boundary double controls asynchronous output;
// the real worker parser, TransformStream, packet registry and recovery run.
globalThis.VideoDecoder=class {
 constructor(callbacks){this.callbacks=callbacks;this.decodeQueueSize=0;this.state='unconfigured';this.chunks=[];decoders.push(this);}
 configure(){this.state='configured';}
 close(){this.state='closed';}
 decode(chunk){this.chunks.push(chunk);}
};
await import('__MODULE__');
async function feed(values){
 const output=[];
 const readable=new ReadableStream({start(controller){for(const value of values)controller.enqueue(value);controller.close();}});
 const writable=new WritableStream({write(frame){output.push(frame);}});
 self.onmessage({data:{readable,writable}});
 for(let i=0;i<100&&output.length<values.length;i++)await new Promise(resolve=>setTimeout(resolve,1));
 assert.equal(output.length,values.length,'native encoded frames must always be forwarded');
}
const frame=(poll,rtp=10,key=true)=>({timestamp:rtp,data:access(catalog(poll,[1,2]),key)});
await feed([frame(2,10),frame(2,11)]);
const metadata=messages.filter(m=>m.type==='metadata');
assert.equal(metadata.length,2,'each access unit has a catalog');
assert.notEqual(metadata[0].packet_id,metadata[1].packet_id,'repeated poll ID must not overwrite decoded association');
assert.equal(metadata[0].session_id,'session-a');
assert.equal(metadata[0].envelope.encoder_poll_capture_id,2);
assert.equal(metadata[0].capture_id,undefined);
assert.equal(decoders[0].chunks[0].timestamp,metadata[0].packet_id);
function emit(decoder,chunk){
 const image={timestamp:chunk.timestamp,closed:false,close(){this.closed=true;}};
 decoder.callbacks.output(image);return image;
}
// Decoder completion order must not choose a different RTP/catalog entry.
emit(decoders[0],decoders[0].chunks[1]);emit(decoders[0],decoders[0].chunks[0]);
const images=messages.filter(m=>m.type==='decoded_frame');
assert.deepEqual(images.map(m=>m.packet_id),[metadata[1].packet_id,metadata[0].packet_id]);
assert.deepEqual(images.map(m=>m.rtpTimestamp),[11,10]);
assert(images.every(m=>m.capture_id===undefined));
await feed(Array.from({length:100},(_,i)=>frame(2,100+i)));
assert.equal(emit(decoders[0],decoders[0].chunks[2]).closed,true,'unbounded pending entries retain old decoder surfaces');
assert.equal(emit(decoders[0],decoders[0].chunks.at(-1)).closed,false);
// A failed decoder must wait for an IDR before restarting, then recover.
decoders[0].state='closed';decoders[0].callbacks.error(new Error('decoder failed'));
const before=messages.filter(m=>m.type==='decoded_frame').length;
await feed([frame(2,500,false),frame(2,501,true)]);
assert.equal(decoders.length,2);
assert.equal(decoders[1].chunks.length,1);
assert.equal(decoders[1].chunks[0].type,'key');
emit(decoders[1],decoders[1].chunks[0]);
assert.equal(messages.filter(m=>m.type==='decoded_frame').length,before+1);
'''.replace('__MODULE__',module)
        self.run_node(script)

    @staticmethod
    def catalog_fixture():
        return r'''
import assert from 'node:assert/strict';
const uuid=Buffer.from('3654537366c659f396416e41d4c037f5','hex');
const pixels=Array.from({length:64},(_,i)=>[i%32,Math.floor(i/32)]);
function catalog(poll,ids){return {sei_schema:'p0.snapshot_catalog.v1',session_id:'session-a',
 encoder_poll_capture_id:poll,marker_layout:'capture32_crc32',marker_bits:64,marker_pixels:pixels,
 encoder_poll_reference_time:[1,30],rp_fabric_time:[1,30],video_timestamp_ns:33333333,
 snapshots:ids.map((id,i)=>({sei_schema:'p0.snapshot.v1',session_id:'session-a',capture_id:id,render_seq:id,
 epoch:0,log_time_s:i+0.25,joints:Array(28).fill(i),chassis:{x:1,y:2,yaw:0},
 state:'paused',speed:1,request_id:0,captured_wall_s:100,
 marker_layout:'capture32_crc32',marker_bits:64,marker_pixels:pixels}))};}
function access(value,key=true){
 const payload=Buffer.concat([uuid,Buffer.from(JSON.stringify(value))]);
 const sizes=[];let n=payload.length;while(n>=255){sizes.push(255);n-=255;}sizes.push(n);
 const raw=Buffer.concat([Buffer.from([5,...sizes]),payload,Buffer.from([128])]);
 const escaped=[];let zeroes=0;
 for(const v of raw){if(zeroes>=2&&v<=3){escaped.push(3);zeroes=0;}escaped.push(v);zeroes=v===0?zeroes+1:0;}
 const sei=Buffer.from([0,0,0,1,6,...escaped]);
 // SPS gives avc1.640028. Each fixture is a synthetic access unit; pixel decode
 // is intentionally outside these parser/packet-association CPU tests.
 return Buffer.concat([sei,key?Buffer.from([0,0,1,7,100,0,40,0,0,1,0x65,0x88,0]):Buffer.from([0,0,1,0x41,0x88,0])]);
}
'''

    def run_node(self, script):
        result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
