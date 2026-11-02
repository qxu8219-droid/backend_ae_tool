"""Three actual ZMQ channels; all sockets belong to the calling thread."""
import time,math
from collections import deque
import zmq
from replay_protocol import RPCRequest,RPCResponse,TargetMessage,WorkerEvent
from .engine import WorkerEngine

MAX_MESSAGE_BYTES=1024*1024


def _deadline(timeout_s):
    if type(timeout_s) not in (int,float) or not math.isfinite(timeout_s) or timeout_s<=0:
        raise ValueError('Timeout must be a positive finite number')
    deadline=time.monotonic()+timeout_s
    if not math.isfinite(deadline):raise ValueError('Timeout deadline is not representable')
    return deadline


def _socket(context,kind):
    s=context.socket(kind);s.setsockopt(zmq.LINGER,0);s.setsockopt(zmq.SNDHWM,64);s.setsockopt(zmq.RCVHWM,64)
    s.setsockopt(zmq.MAXMSGSIZE,MAX_MESSAGE_BYTES)
    return s


class WorkerServer:
    def __init__(self,config,runtime):
        self.config=config;self.context=zmq.Context();self.sockets=[];self.invalid=0;self.send_failures=0;self.subscribers=0
        try:
            self.rpc=_socket(self.context,zmq.ROUTER);self.sockets.append(self.rpc)
            self.rpc.setsockopt(zmq.ROUTER_MANDATORY,1);self.rpc.bind(config.endpoint('rpc'))
            self.targets=_socket(self.context,zmq.SUB);self.sockets.append(self.targets)
            self.targets.setsockopt(zmq.SUBSCRIBE,config.topic('target'));self.targets.connect(config.endpoint('target'))
            self.events=_socket(self.context,zmq.XPUB);self.sockets.append(self.events)
            self.events.setsockopt(zmq.XPUB_VERBOSE,1);self.events.bind(config.endpoint('event'))
            self.engine=WorkerEngine(config.session_id,runtime,self.reply,self.emit)
            self.poller=zmq.Poller()
            for s in self.sockets:self.poller.register(s,zmq.POLLIN)
        except Exception:self.close();raise
    def reply(self,route,response):
        try:self.rpc.send_multipart([route,response.model_dump_json().encode()],zmq.NOBLOCK)
        except zmq.ZMQError as exc:
            self.send_failures+=1
            self.engine.fail('RPC_SEND_FAILED',str(exc))
    def emit(self,event):
        try:self.events.send_multipart([self.config.topic('events'),event.model_dump_json().encode()],zmq.NOBLOCK)
        except zmq.ZMQError:self.send_failures+=1
    def poll(self,timeout_ms=5):
        ready=dict(self.poller.poll(timeout_ms))
        if self.events in ready:
            for _ in range(64):
                try:message=self.events.recv(zmq.NOBLOCK)
                except zmq.Again:break
                if len(message)>1 and message[1:]==self.config.topic('events'):
                    self.subscribers=max(0,self.subscribers+(1 if message[0] else -1))
            self.engine.subscriptions_ready=self.subscribers>0
        if self.rpc in ready:
            for _ in range(64):
                try:parts=self.rpc.recv_multipart(zmq.NOBLOCK)
                except zmq.Again:break
                try:
                    if len(parts)!=2 or len(parts[1])>MAX_MESSAGE_BYTES:raise ValueError('Invalid RPC envelope')
                    self.engine.rpc(parts[0],RPCRequest.model_validate_json(parts[1]))
                except (ValueError,TypeError):self.invalid+=1
        if self.targets in ready:
            for _ in range(64):
                try:parts=self.targets.recv_multipart(zmq.NOBLOCK)
                except zmq.Again:break
                try:
                    if len(parts)!=2 or parts[0]!=self.config.topic('target') or len(parts[1])>MAX_MESSAGE_BYTES:raise ValueError('Invalid target envelope')
                    self.engine.target(TargetMessage.model_validate_json(parts[1]))
                except (ValueError,TypeError):self.invalid+=1
    def close(self):
        for s in self.sockets:s.close(0)
        self.sockets=[];self.context.term()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class WorkerClient:
    """Backend-side client. Startup handshake and ordinary RPC have distinct deadlines."""
    def __init__(self,config):
        self.config=config;self.context=zmq.Context();self.sockets=[];self.events=deque(maxlen=128);self.target_subscribed=False;self.counter=0
        try:
            self.targets=_socket(self.context,zmq.XPUB);self.sockets.append(self.targets)
            self.targets.setsockopt(zmq.XPUB_VERBOSE,1);self.targets.bind(config.endpoint('target'))
            self.rpc=_socket(self.context,zmq.DEALER);self.sockets.append(self.rpc)
            self.rpc.connect(config.endpoint('rpc'))
            self.telemetry=_socket(self.context,zmq.SUB);self.sockets.append(self.telemetry)
            self.telemetry.setsockopt(zmq.SUBSCRIBE,config.topic('events'));self.telemetry.connect(config.endpoint('event'))
        except Exception:self.close();raise
    def _subscriptions(self):
        for _ in range(64):
            try:message=self.targets.recv(zmq.NOBLOCK)
            except zmq.Again:break
            if len(message)>1 and message[1:]==self.config.topic('target'):self.target_subscribed=bool(message[0])
    def poll_events(self,timeout_ms=0):
        self._subscriptions()
        if timeout_ms:self.telemetry.poll(timeout_ms)
        for _ in range(64):
            try:parts=self.telemetry.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:break
            if len(parts)==2 and parts[0]==self.config.topic('events'):
                try:
                    event=WorkerEvent.model_validate_json(parts[1])
                    if event.session_id==self.config.session_id:self.events.append(event)
                except ValueError:pass
        result=list(self.events);self.events.clear();return result
    def publish(self,target):
        target=TargetMessage.model_validate(target)
        if target.frame.session_id!=self.config.session_id:raise ValueError('Wrong target session')
        self._subscriptions()
        if not self.target_subscribed:raise RuntimeError('Worker target subscription has not been observed')
        self.targets.send_multipart([self.config.topic('target'),target.model_dump_json().encode()],zmq.NOBLOCK)
    def request(self,request,*,timeout_s=10.,expected_stage=None):
        deadline=_deadline(timeout_s)
        request=RPCRequest.model_validate(request)
        if request.session_id!=self.config.session_id:raise ValueError('Wrong request session')
        expected_stage=expected_stage or ('rendered' if request.command.cmd=='apply_keyframe' else 'accepted')
        if expected_stage not in ('accepted','applied','rendered'):raise ValueError('Unknown RPC confirmation stage')
        self.rpc.send(request.model_dump_json().encode(),zmq.NOBLOCK)
        while time.monotonic()<deadline:
            self._subscriptions()
            remaining=deadline-time.monotonic()
            wait_ms=20 if remaining>=.02 else max(0,int(remaining*1000))
            if self.rpc.poll(wait_ms):
                try:response=RPCResponse.model_validate_json(self.rpc.recv())
                except ValueError:continue
                if (response.session_id,response.request_id,response.control_seq,response.playback_epoch)!=(request.session_id,request.request_id,request.control_seq,request.playback_epoch):continue
                if not response.ok or response.stage==expected_stage:return response
        raise TimeoutError(f'Worker RPC timeout: {request.command.cmd}')
    def handshake(self,*,timeout_s=120.):
        deadline=_deadline(timeout_s)
        while time.monotonic()<deadline:
            self._subscriptions()
            if not self.target_subscribed:
                self.targets.poll(20);continue
            self.counter+=1
            request=RPCRequest(session_id=self.config.session_id,request_id=f'handshake-{self.counter}',control_seq=0,playback_epoch=0,command={'cmd':'subscribe_ready','channels':['target','telemetry']})
            try:
                response=self.request(request,timeout_s=min(1.,max(.01,deadline-time.monotonic())))
                if response.ok:return response
            except TimeoutError:pass
            self.telemetry.poll(20)
        raise TimeoutError('Worker startup/subscription handshake timed out')
    def close(self):
        for s in self.sockets:s.close(0)
        self.sockets=[];self.context.term()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
