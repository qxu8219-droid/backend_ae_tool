"""Single-owner execution fences and bounded commands, without app or SDK imports."""
from collections import OrderedDict
import hashlib
from replay_protocol import RPCRequest, RPCResponse, TargetMessage, WorkerEvent, AppliedFrame, RenderedFrame


class WorkerEngine:
    def __init__(self, session_id, runtime, reply, emit, *, robot_id='galaxea_r1pro_2026', cache_size=128):
        if type(cache_size) is not int or cache_size<1:raise ValueError('Invalid request cache capacity')
        self.session_id=session_id;self.runtime=runtime;self.reply=reply;self.emit=emit;self.robot_id=robot_id
        self.subscriptions_ready=False;self.running=True;self.failed=False
        self.log_id=None;self.epoch=0;self.control=0;self.floor=0;self.last_sequence=0
        self.pending=None;self.latest=None;self.held=None;self.event_seq=0
        self.stream_enabled=False;self.control_speed=None
        self.last_capture=0
        self.error=None
        self.cache=OrderedDict();self.cache_size=cache_size;self.dropped=0

    def _event(self,event):
        self.event_seq+=1;self.emit(WorkerEvent(session_id=self.session_id,seq=self.event_seq,event=event))

    def ready(self):self._event({'type':'ready'})

    def fail(self,code,message):
        if self.failed:return
        self.failed=True;self.running=False;self.pending=None;self.latest=None;self.stream_enabled=False
        self.error={'code':code,'message':str(message)[:256] or code}
        self._event({'type':'error','error':self.error})

    def _response(self,route,request,stage='accepted',frame=None,error=None,*,cache=True):
        response=RPCResponse(session_id=self.session_id,request_id=request.request_id,
            control_seq=request.control_seq,playback_epoch=request.playback_epoch,stage=stage,
            ok=error is None,error=error,frame=frame)
        if cache:
            digest=hashlib.sha256(request.model_dump_json().encode()).hexdigest()
            self.cache[request.request_id]=(digest,response)
            while len(self.cache)>self.cache_size:self.cache.popitem(last=False)
        self.reply(route,response)
        return response

    def _reject(self,route,request,code,message,*,cache=True):
        return self._response(route,request,error={'code':code,'message':str(message)[:256] or code},cache=cache)

    def _advance(self,request):
        if request.playback_epoch<self.epoch or request.control_seq<self.control:
            return False
        if (request.playback_epoch,request.control_seq)!=(self.epoch,self.control):
            if self.pending:
                route,previous=self.pending
                self._reject(route,previous,'SUPERSEDED','A newer control superseded this target')
                if self.failed:return False
            self.pending=None;self.latest=None
            self.stream_enabled=False
            self.epoch=request.playback_epoch;self.control=request.control_seq
        return True

    def rpc(self,route,request):
        request=RPCRequest.model_validate(request)
        if request.session_id!=self.session_id:
            self._reject(route,request,'WRONG_SESSION','Request belongs to another worker',cache=False);return
        if not self.running and request.command.cmd!='shutdown':
            self._reject(route,request,'WORKER_FAILED' if self.failed else 'SHUTTING_DOWN','Worker is not accepting new work',cache=False);return
        digest=hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        if request.request_id in self.cache:
            old,response=self.cache[request.request_id]
            if digest!=old:self._reject(route,request,'REQUEST_ID_CONFLICT','Request ID was reused with another payload',cache=False)
            else:self.reply(route,response)
            return
        cmd=request.command.cmd
        if cmd=='shutdown':
            self._response(route,request);self.running=False;self.latest=None;self.pending=None;return
        if self.failed:
            self._reject(route,request,'WORKER_FAILED','Worker must be recreated');return
        if cmd=='heartbeat':self._response(route,request);return
        if cmd=='subscribe_ready':
            if set(request.command.channels)!={'target','telemetry'} or not self.subscriptions_ready:
                self._reject(route,request,'NOT_SUBSCRIBED','Both real PUB/SUB subscriptions must be observed');return
            self._response(route,request);self.ready();return
        if cmd in ('load_scene','load_robot'):
            value=request.command.scene_id if cmd=='load_scene' else request.command.robot_id
            if value!=('empty' if cmd=='load_scene' else self.robot_id):
                self._reject(route,request,'UNKNOWN_ASSET','Worker supports only its configured empty scene and robot');return
            self._response(route,request);return
        if not self.subscriptions_ready:
            self._reject(route,request,'NOT_SUBSCRIBED','Subscribe before issuing motion targets');return
        if request.playback_epoch<self.epoch or request.control_seq<self.control:
            self._reject(route,request,'STALE_CONTROL','Control or playback epoch moved backwards');return
        if cmd=='load_log':
            if self.log_id and request.command.log_id!=self.log_id and request.control_seq<=self.control:
                self._reject(route,request,'CONTEXT_CONFLICT','Changing log requires a newer control');return
            if not self._advance(request):return
            self.log_id=request.command.log_id;self.latest=None;self.held=None
            self.stream_enabled=False
            self._response(route,request);return
        if cmd=='reset':
            if request.control_seq<=self.control:
                self._reject(route,request,'STALE_CONTROL','Reset requires a newer control');return
            if not self._advance(request):return
            self.log_id=None;self.held=None;self._response(route,request);return
        if not self.log_id:
            self._reject(route,request,'NO_LOG_LOADED','load_log must precede targets');return
        if cmd=='pause_barrier':
            if not self._advance(request):return
            self.floor=max(self.floor,request.command.target_seq)
            self.stream_enabled=False
            self.latest=None
            if self.pending:
                old_route,old_request=self.pending;self.pending=None
                self._reject(old_route,old_request,'SUPERSEDED','Pause barrier canceled queued reliable work')
                if self.failed:return
            self._response(route,request);return
        if cmd=='apply_keyframe':
            frame=request.command.frame
            if frame.log_id!=self.log_id or frame.target_seq<self.floor or frame.target_seq<=self.last_sequence:
                self._reject(route,request,'STALE_TARGET','Target log or sequence is stale');return
            if not self._advance(request):return
            if self.pending:
                old_route,old_request=self.pending
                self._reject(old_route,old_request,'SUPERSEDED','A newer reliable target superseded this target')
                if self.failed:return
            self.pending=(route,request);self.latest=None;self.floor=max(self.floor,frame.target_seq)
            self.stream_enabled=frame.state=='playing';self.control_speed=frame.speed
            self._response(route,request);return
        self._reject(route,request,'UNSUPPORTED_COMMAND','Unsupported worker command')

    def target(self,message):
        message=TargetMessage.model_validate(message);frame=message.frame
        if (self.failed or not self.running or not self.subscriptions_ready or not self.stream_enabled or
            frame.state!='playing' or frame.speed!=self.control_speed or
            (frame.session_id,frame.log_id,frame.playback_epoch,frame.control_seq)!=(self.session_id,self.log_id,self.epoch,self.control) or
            frame.target_seq<self.floor or frame.target_seq<=self.last_sequence or
            self.pending and frame.target_seq<=self.pending[1].command.frame.target_seq or
            self.latest and frame.target_seq<=self.latest.target_seq):
            self.dropped+=1;return False
        self.latest=frame;return True

    def step(self):
        if self.failed or not self.running:return
        pending=self.pending
        frame=pending[1].command.frame if pending else self.latest
        if frame is not None and (frame.target_seq<self.floor or frame.target_seq<=self.last_sequence or
                (frame.session_id,frame.log_id,frame.playback_epoch,frame.control_seq)!=(self.session_id,self.log_id,self.epoch,self.control) or
                not pending and not self.stream_enabled):
            self.latest=None;self.dropped+=1;return
        try:
            if frame is not None:
                if not pending:self.latest=None
                applied=AppliedFrame.model_validate(self.runtime.apply(frame))
                if any(getattr(applied,key)!=getattr(frame,key) for key in ('session_id','log_id','target_seq','playback_epoch','control_seq','log_time_s','duration_s','progress','source_sample_index','state','speed','quality')) or applied.source_joints!=frame.joints:
                    raise RuntimeError('Runtime application did not identify its exact source target')
                self.last_sequence=frame.target_seq;self.floor=max(self.floor,frame.target_seq);self.held=applied
                self._event({'type':'applied','frame':applied})
                if pending:self._response(*pending,stage='applied',frame=applied)
                if self.failed:return
            if self.held is not None and (self.held.playback_epoch,self.held.control_seq)==(self.epoch,self.control):
                rendered=RenderedFrame.model_validate(self.runtime.render(self.held))
                if any(getattr(rendered,key)!=getattr(self.held,key) for key in ('session_id','log_id','target_seq','playback_epoch','control_seq','log_time_s','duration_s','progress','source_sample_index','state','speed','quality','source_joints')) or rendered.capture_id<=self.last_capture:
                    raise RuntimeError('Rendered evidence belongs to another target')
                self.last_capture=rendered.capture_id
                self._event({'type':'rendered','frame':rendered})
                if pending:self._response(*pending,stage='rendered',frame=rendered);self.pending=None
        except Exception as exc:
            if pending:self._reject(*pending,'RUNTIME_FAILED',str(exc))
            self.fail('RUNTIME_FAILED',str(exc))

    def diagnostics(self):
        return {'queued_targets':int(self.latest is not None),'pending_keyframes':int(self.pending is not None),
                'cached_requests':len(self.cache),'last_target_seq':self.last_sequence,'dropped_targets':self.dropped,
                'epoch':self.epoch,'control_seq':self.control,'failed':self.failed,'error':self.error}
