import sys,socket,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_engine import Runtime,frame
from replay_protocol import RPCRequest,TargetMessage
from sim_worker.config import WorkerConfig
from sim_worker.transport import WorkerServer,WorkerClient


def ports():
    sockets=[socket.socket() for _ in range(4)]
    for s in sockets:s.bind(('127.0.0.1',0))
    result=[s.getsockname()[1] for s in sockets]
    for s in sockets:s.close()
    return result


def test_real_zmq_subscription_handshake_rpc_fences_and_cleanup():
    p=ports();config=WorkerConfig(session_id='s',rpc_port=p[0],target_port=p[1],event_port=p[2],rtsp_port=p[3])
    client=WorkerClient(config);stop=threading.Event();errors=[]
    def serve():
        try:
            with WorkerServer(config,Runtime()) as server:
                while not stop.is_set() and server.engine.running:
                    server.poll(2);server.engine.step()
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=serve);thread.start()
    try:
        client.handshake(timeout_s=3)
        load=RPCRequest(session_id='s',request_id='load',control_seq=1,playback_epoch=1,command={'cmd':'load_log','log_id':'log'})
        assert client.request(load).ok
        apply=RPCRequest(session_id='s',request_id='apply',control_seq=1,playback_epoch=1,command={'cmd':'apply_keyframe','frame':frame()})
        proof=client.request(apply);assert proof.stage=='rendered' and proof.frame.raw_identity_verified
        client.publish(TargetMessage(frame=frame(2,t=.5)))
        end=time.monotonic()+2;observed=[]
        while time.monotonic()<end:
            observed.extend(client.poll_events(20))
            if any(e.event.type=='rendered' and e.event.frame.target_seq==2 for e in observed):break
        assert any(e.event.type=='rendered' and e.event.frame.target_seq==2 for e in observed)
        shutdown=RPCRequest(session_id='s',request_id='shutdown',control_seq=2,playback_epoch=1,command={'cmd':'shutdown'})
        assert client.request(shutdown).ok
    finally:
        stop.set();thread.join(3);client.close()
    assert not thread.is_alive() and not errors
    with WorkerClient(config):pass


def test_invalid_rpc_deadlines_and_stage_are_rejected_before_send():
    import pytest
    config=WorkerConfig('s',*ports())
    with WorkerClient(config) as client:
        req=RPCRequest(session_id='s',request_id='never-sent',control_seq=0,playback_epoch=0,command={'cmd':'heartbeat'})
        for timeout in (0,-1,True,float('nan'),float('inf')):
            with pytest.raises(ValueError):client.request(req,timeout_s=timeout)
            with pytest.raises(ValueError):client.handshake(timeout_s=timeout)
        with pytest.raises(ValueError):client.request(req,expected_stage='presented')


def test_partial_socket_binding_failure_releases_the_owned_rpc_socket():
    import pytest,zmq
    p=ports();config=WorkerConfig('s',*p)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',p[2]));occupied.listen()
        with pytest.raises(zmq.ZMQError):WorkerServer(config,Runtime())
    with WorkerServer(config,Runtime()):pass
