import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'replay'))
from replay_protocol import RPCRequest,TargetMessage
from timeline_support import build_sampler,applied_frame,rendered_frame
from sim_worker.engine import WorkerEngine


class Runtime:
    def __init__(self):self.applied=[];self.capture=0
    def apply(self,frame):self.applied.append(frame);return applied_frame(frame)
    def render(self,frame):self.capture+=1;return rendered_frame(frame,capture_id=self.capture)


def setup():
    r=Runtime();replies=[];events=[];e=WorkerEngine('s',r,lambda route,response:replies.append(response),events.append)
    e.subscriptions_ready=True
    return r,e,replies,events


def request(cmd,n=1,epoch=1,control=1,**fields):
    return RPCRequest(session_id='s',request_id=f'r{n}',control_seq=control,playback_epoch=epoch,command={'cmd':cmd,**fields})


def frame(seq=1,epoch=1,control=1,t=0):
    return build_sampler().sample_at(t,target_seq=seq,playback_epoch=epoch,control_seq=control).model_copy(update={"state":"playing"})


def test_keyframe_confirms_application_and_render_with_immutable_context():
    r,e,replies,events=setup();e.rpc(b'c',request('load_log',log_id='log'))
    f=frame();e.rpc(b'c',request('apply_keyframe',n=2,frame=f))
    assert replies[-1].stage=='accepted' and not r.applied
    e.step();assert [x.stage for x in replies[-3:]]==['accepted','applied','rendered']
    assert replies[-1].frame.source_joints==f.joints
    assert [x.event.type for x in events]==['applied','rendered']
    first=replies[-1].frame.capture_id;e.step()
    assert events[-1].event.frame.capture_id>first
    assert events[-1].event.frame.log_time_s==0


def test_new_epoch_discards_old_stream_and_futures_require_reliable_control():
    r,e,replies,events=setup();e.rpc(b'c',request('load_log',log_id='log'))
    e.rpc(b'c',request('apply_keyframe',n=10,frame=frame(1)))
    for i in range(2,101):e.target(TargetMessage(frame=frame(i,t=.5)))
    assert e.diagnostics()['queued_targets']==1
    e.rpc(b'c',request('apply_keyframe',n=2,epoch=2,control=2,frame=frame(101,2,2,t=.25)))
    e.target(TargetMessage(frame=frame(102,1,1,t=1)))
    e.target(TargetMessage(frame=frame(103,3,3,t=1)))
    e.step();assert len(r.applied)==1 and r.applied[0].log_time_s==.25
    e.target(TargetMessage(frame=frame(105,2,2,t=.5)));e.target(TargetMessage(frame=frame(104,2,2,t=1)))
    e.step();assert r.applied[-1].target_seq==105


def test_retry_is_idempotent_and_conflicting_request_id_does_not_apply_again():
    r,e,replies,_=setup();e.rpc(b'c',request('load_log',log_id='log'))
    req=request('apply_keyframe',n=2,frame=frame());e.rpc(b'c',req);e.step()
    e.rpc(b'c',req);assert len(r.applied)==1 and replies[-1].stage=='rendered'
    e.rpc(b'c',request('apply_keyframe',n=2,frame=frame(2,t=.5)))
    assert not replies[-1].ok and replies[-1].error.code=='REQUEST_ID_CONFLICT'


def test_pause_barrier_rejects_old_targets_and_foreign_log_is_not_applied():
    r,e,replies,_=setup();e.rpc(b'c',request('load_log',log_id='log'))
    e.target(TargetMessage(frame=frame(1,t=.5)))
    e.rpc(b'c',request('pause_barrier',n=2,control=2,target_seq=5))
    e.target(TargetMessage(frame=frame(3,control=2,t=.8)))
    e.step();assert not r.applied
    wrong=frame(6,control=2).model_copy(update={'log_id':'other'})
    e.rpc(b'c',request('apply_keyframe',n=3,control=2,frame=wrong))
    assert not replies[-1].ok and not r.applied


def test_subscription_readiness_and_runtime_failure_are_explicit():
    r,e,replies,events=setup();e.subscriptions_ready=False
    e.rpc(b'c',request('subscribe_ready',channels=['target','telemetry']))
    assert not replies[-1].ok
    e.subscriptions_ready=True;e.rpc(b'c',request('load_log',n=2,log_id='log'))
    def bad(frame):raise RuntimeError('readback mismatch')
    r.apply=bad;e.rpc(b'c',request('apply_keyframe',n=3,frame=frame()));e.step()
    assert e.failed and events[-1].event.type=='error' and not replies[-1].ok


def test_stream_cannot_start_or_change_playback_context_without_reliable_control():
    r,e,replies,_=setup();e.rpc(b'c',request('load_log',log_id='log'))
    assert not e.target(TargetMessage(frame=frame(1).model_copy(update={'state':'playing'})))
    e.rpc(b'c',request('apply_keyframe',n=2,frame=frame(2).model_copy(update={'state':'paused'})));e.step()
    assert not e.target(TargetMessage(frame=frame(3).model_copy(update={'state':'playing'})))
    e.rpc(b'c',request('apply_keyframe',n=3,control=2,frame=frame(4,control=2).model_copy(update={'state':'playing','speed':2.})));e.step()
    assert not e.target(TargetMessage(frame=frame(5,control=2).model_copy(update={'state':'playing','speed':1.})))
    assert e.target(TargetMessage(frame=frame(6,control=2).model_copy(update={'state':'playing','speed':2.})))


def test_runtime_cannot_relabel_an_applied_target_or_reuse_a_capture_id():
    r,e,replies,events=setup();e.rpc(b'c',request('load_log',log_id='log'))
    r.apply=lambda f:applied_frame(f).model_copy(update={'speed':3.})
    e.rpc(b'c',request('apply_keyframe',n=2,frame=frame()));e.step()
    assert e.failed and not replies[-1].ok
    assert not any(x.event.type=='rendered' for x in events)
    r,e,replies,events=setup();e.rpc(b'c',request('load_log',log_id='log'))
    r.render=lambda f:rendered_frame(f,capture_id=1)
    e.rpc(b'c',request('apply_keyframe',n=2,frame=frame()));e.step();e.step()
    assert e.failed and sum(x.event.type=='rendered' for x in events)==1


def test_runtime_cannot_return_rendered_time_from_another_target():
    r,e,replies,events=setup();e.rpc(b'c',request('load_log',log_id='log'))
    r.render=lambda f:rendered_frame(f,capture_id=1,log_time_s=.5,progress=.5,source_sample_index=1)
    e.rpc(b'c',request('apply_keyframe',n=2,frame=frame()));e.step()
    assert e.failed and not any(x.event.type=='rendered' for x in events)
