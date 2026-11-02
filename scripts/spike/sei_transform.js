import {extractCatalog, splitNalUnits, codecFromAccessUnit} from './h264_sei.js';

let decoder=null, configuredCodec=null, awaitingKeyframe=true;
const pending=new Map();
const MAX_PENDING=90;
let packets=0, missing=0, decodeDrops=0, nextPacketId=0;
function report(type, detail){self.postMessage({type,...detail});}
function closeDecoder(){
  if(decoder?.state!=='closed')decoder?.close();
  decoder=null;pending.clear();awaitingKeyframe=true;
}
function ensureDecoder(codec,keyframe){
  if(decoder?.state==='closed')closeDecoder();
  if(decoder&&configuredCodec!==codec)closeDecoder();
  if(awaitingKeyframe&&!keyframe)return false;
  if(decoder)return true;
  const current=new VideoDecoder({output:frame=>{
    // A reset can leave callbacks from the old decoder in flight.
    if(decoder!==current){frame.close();return;}
    const item=pending.get(frame.timestamp);pending.delete(frame.timestamp);
    if(!item){frame.close();return;}
    // packet_id joins this access unit and its catalog, not the source image.
    // The main thread reads the image stamp before selecting a snapshot.
    self.postMessage({type:'decoded_frame',...item,frame},[frame]);
  },error:error=>{
    if(decoder===current)closeDecoder();
    report('decoder_error',{message:error.message});
  }});
  decoder=current;
  decoder.configure({codec,hardwareAcceleration:'prefer-software',optimizeForLatency:true});
  configuredCodec=codec;awaitingKeyframe=false;report('decoder_configured',{codec});
  return true;
}
async function transform(readable,writable){
  await readable.pipeThrough(new TransformStream({transform(frame,controller){
    packets++;
    try{
      const bytes=new Uint8Array(frame.data),nals=splitNalUnits(bytes);
      const envelope=extractCatalog(bytes);
      if(packets<=2)report('encoded_sample',{length:bytes.length,nal_types:nals.map(n=>n[0]&31),
        first_bytes:[...bytes.subarray(0,24)],rtpTimestamp:frame.timestamp});
      if(envelope){
        if(nextPacketId===Number.MAX_SAFE_INTEGER)throw new Error('Packet ID exhausted');
        const packet_id=++nextPacketId,session_id=envelope.session_id,rtpTimestamp=frame.timestamp;
        report('metadata',{envelope,packet_id,session_id,rtpTimestamp});
        const codec=codecFromAccessUnit(bytes)||configuredCodec;
        const keyframe=nals.some(n=>(n[0]&31)===5);
        if(codec&&ensureDecoder(codec,keyframe)&&decoder.decodeQueueSize<4){
          pending.set(packet_id,{packet_id,session_id,rtpTimestamp});
          while(pending.size>MAX_PENDING){pending.delete(pending.keys().next().value);decodeDrops++;}
          try{
            decoder.decode(new EncodedVideoChunk({type:keyframe?'key':'delta',timestamp:packet_id,data:bytes}));
          }catch(error){
            closeDecoder();throw error;
          }
        }else{
          decodeDrops++;
          // A dropped delta frame invalidates later dependent frames. Recover
          // on the next IDR instead of presenting an incomplete decode chain.
          if(decoder?.decodeQueueSize>=4)closeDecoder();
        }
      }else{
        missing++;
        // Missing metadata also means skipping an encoded reference frame.
        // Never continue a potentially invalid chain using another catalog.
        closeDecoder();
      }
      if(packets%30===0)report('encoded_stats',{packets,missing_metadata:missing,decode_drops:decodeDrops,
        pending_decodes:pending.size});
    }catch(error){
      closeDecoder();report('transform_error',{message:error.message});
    }
    // Native receiver continues normally; only the paired canvas is authoritative.
    controller.enqueue(frame);
  }})).pipeTo(writable);
}
self.onrtctransform=event=>transform(event.transformer.readable,event.transformer.writable)
  .catch(error=>report('transform_error',{message:error.message}));
self.onmessage=event=>{if(event.data.readable)transform(event.data.readable,event.data.writable)
  .catch(error=>report('transform_error',{message:error.message}));};
