import {decodeCaptureStamp,selectFrameSnapshot} from './frame_pair.js';
const video = document.querySelector('#source');
const status = document.querySelector('#status');
const events = document.querySelector('#events');
const params = new URLSearchParams(location.search);
const syncMode=params.get('sync')==='1';
const endpoint = new URL(params.get('whep') || 'http://127.0.0.1:8889/stream/whep');
if (!['127.0.0.1','localhost'].includes(endpoint.hostname)) throw new Error('P0 endpoint must be local');
let pc = null, resource = null, generation = 0, frameCallback = null;
let transformWorker=null, paintScheduled=false, barrier=0;
let currentSession=null, lastCapture=0, lastEpoch=-1;
const metadata=new Map(), frames=new Map(), delays=new Map();
const canvas=document.querySelector('#syncCanvas'), draw=canvas.getContext('2d');
const scratch=document.createElement('canvas'), pixels=scratch.getContext('2d',{willReadFrequently:true});
const delayMs=name=>Math.min(1500,Math.max(0,Number(params.get(name))||0));
let firstFrameAt = null, connectedAt = null;
const stats = window.p0Stats = {mode:'video-only',presented_frames:0,connections:0,
  errors:[],last_video_frame:null,rtc:null,metadata_frames:0,sync_presented_frames:0,
  marker_mismatches:0,stale_dropped:0,missing_pairs:0,latencies_ms:[]};
if(syncMode){
  stats.mode='SEI + VideoDecoder paired canvas';video.style.cssText='position:absolute;width:1px;height:1px;opacity:0.01';
  canvas.style.display='block';
  document.querySelector('#notice').textContent='等待具有同帧元数据且通过像素标记核对的解码帧。';
}
function clearPairs(){
  for(const item of frames.values())item.frame.close();frames.clear();metadata.clear();
  for(const [timer,item] of delays){clearTimeout(timer);item?.frame?.close();}delays.clear();
  currentSession=null;lastCapture=0;lastEpoch=-1;paintScheduled=false;
}
function cappedPut(map,key,value){
  map.get(key)?.frame?.close();
  map.set(key,value);
  while(map.size>90){const old=map.keys().next().value;map.get(old)?.frame?.close();map.delete(old);stats.stale_dropped++;}
}
function queuePaint(){
  if(paintScheduled)return;paintScheduled=true;
  const token=generation;
  requestAnimationFrame(()=>{
    if(token!==generation)return;
    paintScheduled=false;
    const available=[...frames.keys()].filter(key=>metadata.has(key));
    if(!available.length){stats.missing_pairs++;return;}
    available.sort((a,b)=>frames.get(b).packet_id-frames.get(a).packet_id);
    const key=available[0], item=frames.get(key), envelope=metadata.get(key);
    frames.delete(key);metadata.delete(key);
    scratch.width=item.frame.displayWidth;scratch.height=item.frame.displayHeight;
    pixels.drawImage(item.frame,0,0);item.frame.close();
    let selected,samples=[];
    try{
      samples=envelope.marker_pixels.map(([x,y])=>{
        if(x>=scratch.width||y>=scratch.height)throw new Error('Image stamp is outside decoded frame');
        return pixels.getImageData(x,y,1,1).data;
      });
      selected=selectFrameSnapshot(envelope,samples);
    }catch(error){
      stats.marker_mismatches++;stats.last_marker_failure={message:error.message,packet_id:item.packet_id,
        encoder_poll_capture_id:envelope.encoder_poll_capture_id,stamp:decodeCaptureStamp(samples),
        pixels:samples.map(pixel=>[...pixel])};
      queuePaintIfReady();return;
    }
    const meta=selected.snapshot,code=selected.stamp.capture_id;
    if(meta.request_id<barrier){stats.stale_dropped++;queuePaintIfReady();return;}
    // A restarted worker needs an explicit new connection. Do not let an old
    // stream switch the displayed session while this connection is active.
    if(currentSession!==null&&meta.session_id!==currentSession){
      stats.stale_dropped++;queuePaintIfReady();return;
    }
    if(meta.session_id===currentSession&&(meta.capture_id<=lastCapture||meta.epoch<lastEpoch)){
      stats.stale_dropped++;queuePaintIfReady();return;
    }
    const frameWidth=1240, frameHeight=Math.round(frameWidth*scratch.height/scratch.width);
    draw.fillStyle='#101722';draw.fillRect(0,0,canvas.width,canvas.height);
    draw.drawImage(scratch,0,0,frameWidth,frameHeight);
    draw.font='15px monospace';draw.fillStyle='#e7edf5';
    meta.joints.forEach((value,index)=>{
      const name=meta.joint_names?.[index]||`joint${index}`;
      const label=name.replace('_motor_joint','').replace('_gripper_finger_joint','_finger').replace('_arm_joint','_arm').replace('torso_joint','torso');
      draw.fillText(`${label}: ${value.toFixed(5)} ${name.includes('finger')?'m':'rad'}`,1260,30+index*25);
    });
    draw.font='18px monospace';draw.fillStyle='#8fdfb2';
    draw.fillText(`capture ${meta.capture_id} | epoch ${meta.epoch} | log ${meta.log_time_s.toFixed(3)}s | ${meta.state} ${meta.speed}x`,18,750);
    draw.fillText(`x ${meta.chassis.x.toFixed(3)}  y ${meta.chassis.y.toFixed(3)}  yaw ${meta.chassis.yaw.toFixed(3)}`,18,779);
    draw.fillRect(18,714,Math.min(1200,1200*meta.log_time_s/12),5);
    currentSession=meta.session_id;lastCapture=meta.capture_id;lastEpoch=meta.epoch;
    stats.sync_presented_frames++;stats.last_metadata=meta;stats.last_marker=code;
    stats.last_encoder_poll_capture_id=envelope.encoder_poll_capture_id;
    stats.last_presentation_wall_ms=Date.now();
    stats.latencies_ms.push(Date.now()-meta.captured_wall_s*1000);if(stats.latencies_ms.length>2000)stats.latencies_ms.shift();
    stats.presentations??=[];stats.presentations.push({capture_id:meta.capture_id,epoch:meta.epoch,log_time_s:meta.log_time_s,
      request_id:meta.request_id,marker:code,wall_ms:Date.now()});if(stats.presentations.length>2000)stats.presentations.shift();
    document.querySelector('#notice').textContent=`同帧呈现 ${meta.capture_id} · 像素标记 ${code} · log ${meta.log_time_s.toFixed(3)}s`;
    for(const [old,entry] of frames)if(entry.session_id===currentSession&&entry.packet_id<=item.packet_id){entry.frame.close();frames.delete(old);metadata.delete(old);}
    queuePaintIfReady();
  });
}
function queuePaintIfReady(){if([...frames.keys()].some(key=>metadata.has(key)))queuePaint();}
function transformMessage(message,token){
  if(token!==generation){message.frame?.close();return;}
  if(message.type==='metadata'||message.type==='decoded_frame'){
    const isMeta=message.type==='metadata', value=isMeta?message.envelope:message;
    const key=`${message.session_id}:${message.packet_id}`;
    const accept=()=>{cappedPut(isMeta?metadata:frames,key,value);if(isMeta)stats.metadata_frames++;queuePaint();};
    const ms=delayMs(isMeta?'metadata_delay_ms':'video_delay_ms');
    if(ms){const timer=setTimeout(()=>{delays.delete(timer);if(token===generation)accept();else value.frame?.close();},ms);delays.set(timer,value);
      while(delays.size>90){const first=delays.keys().next().value;clearTimeout(first);delays.get(first)?.frame?.close();delays.delete(first);stats.stale_dropped++;}}
    else accept();
  }else if(message.type.endsWith('error')){stats.errors.push(message.message);stats.errors=stats.errors.slice(-30);log(message.message);}
  else stats[message.type]=message;
}
function log(message) {
  events.textContent = `${new Date().toISOString()} ${message}\n${events.textContent}`.slice(0,5000);
}
function iceReady(peer, signal) {
  if(peer.iceGatheringState==='complete') return Promise.resolve();
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>finish(new Error('ICE gathering timeout')),5000);
    const change=()=>{if(peer.iceGatheringState==='complete')finish();};
    const abort=()=>finish(new Error('Connection superseded'));
    function finish(error){clearTimeout(timer);peer.removeEventListener('icegatheringstatechange',change);signal.removeEventListener('abort',abort);error?reject(error):resolve();}
    peer.addEventListener('icegatheringstatechange',change);signal.addEventListener('abort',abort,{once:true});
  });
}
let aborter = null;
async function disconnect() {
  generation++;
  aborter?.abort(); aborter=null;
  transformWorker?.terminate();transformWorker=null;clearPairs();barrier=0;
  if(frameCallback!==null)video.cancelVideoFrameCallback(frameCallback);
  frameCallback=null;
  const oldPc=pc, oldResource=resource;pc=null;resource=null;
  video.srcObject=null;
  status.textContent='已断开';
  try{
    // Closing the PC first can make MediaMTX remove the resource before DELETE.
    if(oldResource){const response=await fetch(oldResource,{method:'DELETE',signal:AbortSignal.timeout(3000)});
      if(!response.ok&&response.status!==404)throw new Error(`WHEP DELETE HTTP ${response.status}`);}
  }catch(error){log(`资源释放: ${error.message}`);}
  finally{oldPc?.close();}
}
function observeFrame(token) {
  frameCallback=video.requestVideoFrameCallback((now, meta)=>{
    if(token!==generation)return;
    if(firstFrameAt===null)firstFrameAt=performance.now();
    stats.presented_frames++;
    stats.last_video_frame={width:meta.width,height:meta.height,rtpTimestamp:meta.rtpTimestamp??null,
      mediaTime:meta.mediaTime,presentedFrames:meta.presentedFrames,receivedAt:now};
    stats.connect_to_first_frame_ms=firstFrameAt-connectedAt;
    status.textContent=`视频已接收 ${meta.width}×${meta.height} · ${stats.presented_frames} 帧`;
    observeFrame(token);
  });
}
async function connect() {
  const token=generation+1;
  await disconnect();
  if(token!==generation)return;
  const controller=aborter=new AbortController();
  connectedAt=performance.now();firstFrameAt=null;
  stats.presented_frames=0;stats.last_video_frame=null;
  status.textContent='连接中';
  const peer=pc=new RTCPeerConnection({iceServers:[],encodedInsertableStreams:syncMode});
  const transceiver=peer.addTransceiver('video',{direction:'recvonly'});
  if(syncMode){
    transformWorker=new Worker(new URL('./sei_transform.js',import.meta.url),{type:'module'});
    transformWorker.onmessage=event=>transformMessage(event.data,token);
    transformWorker.onerror=event=>{stats.errors.push(event.message);log(event.message);};
    if(typeof RTCRtpScriptTransform==='function')transceiver.receiver.transform=new RTCRtpScriptTransform(transformWorker,{});
    else {const streams=transceiver.receiver.createEncodedStreams();transformWorker.postMessage(streams,[streams.readable,streams.writable]);}
  }
  peer.ontrack=event=>{
    if(token!==generation)return;
    video.srcObject=event.streams[0]||new MediaStream([event.track]);
    video.play().catch(error=>log(error.message));
    observeFrame(token);
  };
  peer.onconnectionstatechange=()=>{if(token===generation){stats.connection_state=peer.connectionState;log(`PC ${peer.connectionState}`);}};
  try {
    await peer.setLocalDescription(await peer.createOffer());
    await iceReady(peer,controller.signal);
    if(token!==generation)return;
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/sdp'},
      body:peer.localDescription.sdp,signal:AbortSignal.any([controller.signal,AbortSignal.timeout(8000)])});
    if(response.status!==201)throw new Error(`WHEP HTTP ${response.status}: ${(await response.text()).slice(0,200)}`);
    const nextResource=response.headers.get('Location');
    let issuedResource=null;
    if(nextResource){const url=new URL(nextResource,endpoint);if(url.origin!==endpoint.origin)throw new Error('Unexpected WHEP resource origin');issuedResource=url.href;}
    if(token!==generation){if(issuedResource)await fetch(issuedResource,{method:'DELETE'});return;}
    resource=issuedResource;
    const answer=await response.text();
    if(token!==generation)return;
    await peer.setRemoteDescription({type:'answer',sdp:answer});
    stats.connections++;log('WHEP answer accepted');
  } catch(error) {
    if(token!==generation)return;
    stats.errors.push(error.message);log(error.message);await disconnect();status.textContent=`连接失败: ${error.message}`;
  }
}
setInterval(async()=>{
  const current=pc;
  if(current)try{
    const reports=await current.getStats();
    const inbound=[...reports.values()].find(r=>r.type==='inbound-rtp'&&r.kind==='video');
    if(inbound)stats.rtc={framesDecoded:inbound.framesDecoded,framesDropped:inbound.framesDropped,
      framesPerSecond:inbound.framesPerSecond,bytesReceived:inbound.bytesReceived,
      jitter:inbound.jitter,codec:reports.get(inbound.codecId)?.mimeType};
  }catch(error){log(error.message);}
  document.querySelector('#stats').textContent=JSON.stringify(stats,null,2);
  if(syncMode&&pc&&stats.last_presentation_wall_ms&&Date.now()-stats.last_presentation_wall_ms>1500)
    document.querySelector('#notice').textContent='等待可配对画面，当前图像和数据共同保持。';
},500);
async function control(command){
  try{const response=await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});
    const result=await response.json();if(!response.ok)throw new Error(JSON.stringify(result));
    stats.last_control=result;log(`指令已接收 ${JSON.stringify(result.control)}，等待呈现帧确认`);
    if(syncMode){barrier=Math.max(barrier,result.control.request_id);stats.barrier_request_id=barrier;}
  }catch(error){stats.errors.push(error.message);log(error.message);}
}
document.querySelector('#connect').onclick=connect;
document.querySelector('#disconnect').onclick=disconnect;
document.querySelectorAll('[data-action]').forEach(button=>button.onclick=()=>control({action:button.dataset.action}));
document.querySelectorAll('[data-step]').forEach(button=>button.onclick=()=>control({action:'step',direction:Number(button.dataset.step)}));
document.querySelectorAll('[data-speed]').forEach(button=>button.onclick=()=>control({action:'speed',speed:Number(button.dataset.speed)}));
document.querySelector('#seek').onclick=()=>control({action:'seek',time_s:Number(document.querySelector('#seekTime').value)});
window.addEventListener('pagehide',()=>{if(resource)fetch(resource,{method:'DELETE',keepalive:true}).catch(()=>{});pc?.close();});
window.p0Connect=connect;window.p0Disconnect=disconnect;window.p0Control=control;
if(params.get('autoconnect')==='1')connect();
