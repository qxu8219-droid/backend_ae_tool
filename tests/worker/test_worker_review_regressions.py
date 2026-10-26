import sys
from pathlib import Path
from types import SimpleNamespace
import pytest,zmq
sys.path.insert(0,str(Path(__file__).parent))
from test_engine import setup,request,frame,Runtime
from replay_protocol import TargetMessage
from sim_worker.engine import WorkerEngine
from sim_worker.transport import WorkerServer
from sim_worker.runtime_62 import Runtime62
from sim_worker.media import SnapshotRegistry
from test_media import Image


def test_pause_fences_pending_and_floor_equal_stream_until_new_keyframe():
    runtime,engine,replies,events=setup();engine.rpc(b'c',request('load_log',log_id='log'))
    engine.rpc(b'c',request('apply_keyframe',n=2,frame=frame(1)))
    engine.target(TargetMessage(frame=frame(2,t=.5)))
    engine.rpc(b'c',request('pause_barrier',n=3,target_seq=2))
    engine.step();engine.step()
    assert runtime.applied==[]
    assert any(not r.ok and r.request_id=='r2' and r.error.code=='SUPERSEDED' for r in replies)
    engine.rpc(b'c',request('apply_keyframe',n=4,frame=frame(3,t=1).model_copy(update={'state':'ended'})))
    engine.step();assert [f.target_seq for f in runtime.applied]==[3]


def test_stream_cannot_duplicate_a_reserved_reliable_identity():
    runtime,engine,replies,events=setup();engine.rpc(b'c',request('load_log',log_id='log'))
    engine.rpc(b'c',request('apply_keyframe',n=2,frame=frame(1)))
    assert not engine.target(TargetMessage(frame=frame(1,t=.5)))
    engine.step();engine.step();assert len(runtime.applied)==1


def test_lost_reliable_reply_is_terminal_before_any_target_is_applied():
    class Lost:
        def send_multipart(self,*args,**kwargs):raise zmq.ZMQError(zmq.EHOSTUNREACH)
    server=WorkerServer.__new__(WorkerServer);server.rpc=Lost();server.send_failures=0
    runtime=Runtime();events=[]
    server.engine=WorkerEngine('s',runtime,server.reply,events.append);server.engine.subscriptions_ready=True
    server.engine.rpc(b'missing',request('load_log',log_id='log'))
    server.engine.rpc(b'missing',request('apply_keyframe',n=2,frame=frame()))
    server.engine.step()
    assert server.engine.failed and not runtime.applied
    assert events[-1].event.error.code=='RPC_SEND_FAILED'


def test_cleanup_releases_every_resource_even_if_report_and_detach_fail(tmp_path):
    calls=[]
    class Writer:
        sync_stats={}
        def detach(self):calls.append('detach');raise RuntimeError('detach failed')
        def _stop_server(self):calls.append('stop')
    runtime=Runtime62.__new__(Runtime62);runtime.output_dir=tmp_path/'missing';runtime.report={}
    runtime.registry=SimpleNamespace(stats=lambda:{});runtime.writer=Writer()
    runtime.trace=SimpleNamespace(close=lambda:calls.append('trace'));runtime.app=SimpleNamespace(close=lambda **kwargs:calls.append('app'))
    errors=runtime.close()
    assert calls==['detach','stop','trace','app'] and errors
    assert runtime.writer is None and runtime.trace is None and runtime.app is None


def test_diagnostic_callback_failure_is_visible_and_does_not_abort_encoding():
    def bad(*args):raise OSError('disk full')
    registry=SnapshotRegistry([(i%32,i//32) for i in range(64)],[],clock=lambda:1001.,on_verified=bad)
    from timeline_support import applied_frame
    registry.arm(1,applied_frame(frame()))
    output=registry.prepare_frame({'Attribute':1,'rgb':Image(1),'LdrColor':b'encoded','reference_time':(1,20)})
    assert output is not None and registry.rendered(1) is not None
    assert registry.stats()['diagnostic_errors']==1


def test_post_shutdown_commands_are_explicitly_rejected():
    _,engine,replies,_=setup();engine.rpc(b'c',request('shutdown'))
    engine.rpc(b'c',request('load_log',n=2,log_id='log'))
    assert not replies[-1].ok and replies[-1].error.code=='SHUTTING_DOWN'


def test_failed_rtsp_start_and_partial_attach_release_owned_resources():
    from sim_worker.resources import start_checked,attach_checked
    calls=[]
    class Server:
        def start(self,config):calls.append('start');return False
        def stop(self):calls.append('stop');raise RuntimeError('stop failed')
        def close(self):calls.append('close')
    with pytest.raises(RuntimeError,match='refused'):start_checked(Server(),object())
    assert calls==['start','stop','close']
    calls.clear()
    class Writer:
        def attach(self,products):calls.append('attach')
        def _start_server(self,*args):calls.append('start');raise RuntimeError('bind failed')
        def detach(self):calls.append('detach');raise RuntimeError('detach failed')
        def _stop_server(self):calls.append('stop')
    with pytest.raises(RuntimeError,match='bind failed'):attach_checked(Writer(),'product',1280,720)
    assert calls==['attach','start','detach','stop']


def test_missing_shutdown_report_directory_cannot_skip_server_or_runtime_close(tmp_path):
    from sim_worker.main import finish_worker
    calls=[]
    server=SimpleNamespace(engine=SimpleNamespace(diagnostics=lambda:{}),invalid=0,send_failures=0,close=lambda:calls.append('server'))
    runtime=SimpleNamespace(close=lambda **kwargs:calls.append('runtime'))
    assert finish_worker(server,runtime,tmp_path/'missing')
    assert calls==['server','runtime']


def test_preoccupied_rtsp_port_fails_before_runtime_initialization():
    import socket
    from sim_worker.config import WorkerConfig,ensure_ports_available
    from test_transport import ports
    p=ports()
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',p[3]));occupied.listen()
        config=WorkerConfig('s',*p)
        with pytest.raises(OSError,match='rtsp'):ensure_ports_available(config)


def test_native_rtsp_binding_is_closed_by_default_unless_loopback_or_explicit_override(monkeypatch):
    import sim_worker.resources as resources
    monkeypatch.setattr(resources,'listening_addresses',lambda port:['0.0.0.0'],raising=False)
    with pytest.raises(RuntimeError,match='non-loopback'):resources.verify_rtsp_binding(19000)
    assert resources.verify_rtsp_binding(19000,allow_native_wildcard=True)==['0.0.0.0']
    monkeypatch.setattr(resources,'listening_addresses',lambda port:['127.0.0.1','::1'])
    assert resources.verify_rtsp_binding(19000)==['127.0.0.1','::1']


def test_sdk_shutdown_receives_failure_status_before_fast_exit(tmp_path,capsys):
    calls=[]
    runtime=Runtime62.__new__(Runtime62);runtime.output_dir=tmp_path;runtime.report={}
    runtime.writer=None;runtime.trace=None
    runtime.app=SimpleNamespace(close=lambda **kwargs:calls.append(kwargs))
    assert runtime.close(exit_code=1)==[]
    assert calls==[{'exit_code':1}]


def test_startup_error_is_reported_before_sdk_close_can_exit(monkeypatch,tmp_path,capsys):
    import sim_worker.runtime_62 as module
    monkeypatch.setattr(module,'load_asset_profile',lambda _: {'asset_path':tmp_path})
    monkeypatch.setattr(module,'audit_asset',lambda _: {'passed':True})
    def fail(*args):raise RuntimeError('binding rejected')
    monkeypatch.setattr(Runtime62,'_start',fail)
    def close(self,**kwargs):
        assert kwargs=={'exit_code':1}
        assert 'binding rejected' in capsys.readouterr().err
        raise SystemExit(1)
    monkeypatch.setattr(Runtime62,'close',close)
    with pytest.raises(SystemExit) as caught:Runtime62(SimpleNamespace(rtsp_port=19554),tmp_path,tmp_path)
    assert caught.value.code==1
