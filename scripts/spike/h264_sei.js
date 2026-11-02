// P0 metadata UUID shared with rtsp_snapshot_writer.py. No latest-state fallback.
const UUID = Uint8Array.from('3654537366c659f396416e41d4c037f5'.match(/../g),v=>parseInt(v,16));
export function splitNalUnits(input) {
  const bytes=input instanceof Uint8Array?input:new Uint8Array(input);
  const starts=[];
  for(let i=0;i+2<bytes.length;i++){
    if(bytes[i]===0&&bytes[i+1]===0){
      const n=bytes[i+2]===1?3:(bytes[i+2]===0&&bytes[i+3]===1?4:0);
      if(n){starts.push([i,i+n]);i+=n-1;}
    }
  }
  if(starts.length&&starts[0][0]===0)return starts.map((s,i)=>bytes.subarray(s[1],starts[i+1]?.[0]??bytes.length)).filter(n=>n.length);
  const avcc=[];let offset=0;
  while(offset+4<=bytes.length){
    const n=new DataView(bytes.buffer,bytes.byteOffset+offset,4).getUint32(0);
    if(n===0||offset+4+n>bytes.length)break;
    avcc.push(bytes.subarray(offset+4,offset+4+n));offset+=4+n;
  }
  if(avcc.length&&offset===bytes.length)return avcc;
  if(bytes.length&&(bytes[0]&31)>0&&(bytes[0]&31)<24)return [bytes];
  throw new Error('Invalid H264 NAL framing');
}
function rbsp(nal){
  const out=[];let zeros=0;
  for(let i=1;i<nal.length;i++){
    const value=nal[i];
    if(zeros>=2&&value===3){if(i+1>=nal.length||nal[i+1]>3)throw new Error('Invalid emulation prevention');zeros=0;continue;}
    out.push(value);zeros=value===0?zeros+1:0;
  }
  return Uint8Array.from(out);
}
function validateSnapshot(value){
  if(value?.sei_schema!=='p0.snapshot.v1'||!Number.isSafeInteger(value.capture_id)||value.capture_id<=0||value.capture_id>0xffffffff||
     value.render_seq!==value.capture_id||!Number.isSafeInteger(value.epoch)||value.epoch<0||
     !Number.isFinite(value.log_time_s)||!Array.isArray(value.joints)||value.joints.length!==28||
     !value.joints.every(Number.isFinite)||!['x','y','yaw'].every(k=>Number.isFinite(value.chassis?.[k])))
    throw new Error('Invalid snapshot metadata');
  return value;
}
function extractMetadata(input,validate){
  const found=[];
  for(const nal of splitNalUnits(input)){
    if((nal[0]&31)!==6)continue;
    const data=rbsp(nal);let i=0;
    while(i<data.length&&data[i]!==128){
      let type=0,size=0;
      while(i<data.length&&data[i]===255){type+=255;i++;}
      if(i>=data.length)throw new Error('Truncated SEI type');type+=data[i++];
      while(i<data.length&&data[i]===255){size+=255;i++;}
      if(i>=data.length)throw new Error('Truncated SEI size');size+=data[i++];
      if(i+size>data.length)throw new Error('Truncated SEI payload');
      const payload=data.subarray(i,i+size);i+=size;
      if(type===5&&size>=16&&UUID.every((b,k)=>payload[k]===b))
        found.push(validate(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(payload.subarray(16)))));
    }
  }
  if(found.length>1)throw new Error('Multiple metadata envelopes in one access unit');
  return found[0]??null;
}
export function extractSnapshot(input){return extractMetadata(input,validateSnapshot);}
function isUint32(value){return Number.isInteger(value)&&value>0&&value<=0xffffffff;}
function validMarker(value){
  return value?.marker_layout==='capture32_crc32'&&value.marker_bits===64&&
    Array.isArray(value.marker_pixels)&&value.marker_pixels.length===64&&
    value.marker_pixels.every(p=>Array.isArray(p)&&p.length===2&&p.every(v=>Number.isSafeInteger(v)&&v>=0))&&
    new Set(value.marker_pixels.map(p=>p.join(','))).size===64;
}
function validateCatalog(value){
  if(value?.sei_schema!=='p0.snapshot_catalog.v1'||typeof value.session_id!=='string'||!value.session_id||
     !(value.encoder_poll_capture_id===null||isUint32(value.encoder_poll_capture_id))||
     !Array.isArray(value.snapshots)||value.snapshots.length<1||value.snapshots.length>16||
     !validMarker(value)||!Number.isSafeInteger(value.video_timestamp_ns)||value.video_timestamp_ns<0)
    throw new Error('Invalid snapshot catalog');
  const ids=new Set(),pixels=JSON.stringify(value.marker_pixels);
  for(const snapshot of value.snapshots){
    validateSnapshot(snapshot);
    if(snapshot.session_id!==value.session_id||ids.has(snapshot.capture_id)||!validMarker(snapshot)||
       JSON.stringify(snapshot.marker_pixels)!==pixels||
       !Number.isSafeInteger(snapshot.request_id)||snapshot.request_id<0||
       !Number.isFinite(snapshot.captured_wall_s)||!Number.isFinite(snapshot.speed)||snapshot.speed<=0||
       !['playing','paused','ready','ended'].includes(snapshot.state))
      throw new Error('Invalid catalog snapshot identity or presentation fields');
    ids.add(snapshot.capture_id);
  }
  // This is a candidate set. encoder_poll_capture_id is deliberately never
  // used to select an entry: only the decoded image's ID + CRC can do that.
  return value;
}
export function extractCatalog(input){return extractMetadata(input,validateCatalog);}
export function codecFromAccessUnit(input){
  const sps=splitNalUnits(input).find(n=>(n[0]&31)===7);
  return sps&&sps.length>=4?'avc1.'+[sps[1],sps[2],sps[3]].map(v=>v.toString(16).padStart(2,'0')).join(''):null;
}
