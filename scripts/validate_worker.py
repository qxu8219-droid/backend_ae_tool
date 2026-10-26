#!/usr/bin/env python3
"""Real IsaacSim/ZMQ worker validation; owns and cleans only its child process."""
import argparse,json,math,os,signal,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from replay_protocol import RPCRequest,TargetMessage,TargetFrame
from app.replay.interpolator import ReplaySampler
from sim_worker.config import WorkerConfig
from sim_worker.transport import WorkerClient
from scripts.spike.smoke import sample_pose
from replay_protocol import CANONICAL_JOINTS,ChannelSeries,ReplayData


def source():
    times=[i/60 for i in range(361)]
    samples=[sample_pose(t,list(CANONICAL_JOINTS)) for t in times]
    channels={name:ChannelSeries(timestamps_s=times,values=[q[i] for q in samples],unit='m' if 'gripper' in name else 'rad',angle_semantics='linear' if 'gripper' in name else 'angular_unwrapped' if name.startswith('wheel') else 'bounded',quality=[{'origin':'derived','method':'synthetic.worker-validation.v1','valid':True,'processing':['derived'],'source_indices':[j]} for j in range(len(times))]) for i,name in enumerate(CANONICAL_JOINTS)}
    for axis,values in [('x',[t*.05 for t in times]),('y',[math.sin(t)*.1 for t in times]),('yaw',[t*.1 for t in times])]:
        channels['chassis.'+axis]=ChannelSeries(timestamps_s=times,values=values,unit='rad' if axis=='yaw' else 'm',angle_semantics='angular_unwrapped' if axis=='yaw' else 'linear',quality=[{'origin':'derived','method':'synthetic.worker-validation.v1','valid':True,'processing':['derived'],'source_indices':[j]} for j in range(len(times))])
    return ReplayData(log_id='synthetic-log',robot_id='galaxea_r1pro_2026',raw_file_ref='generated-in-memory',raw_file_sha256='0'*64,mapping_snapshot_id='synthetic-map',mapping_snapshot={},time_basis={'source_origin':0.},canonical_order=list(CANONICAL_JOINTS),sample_times_s=times,channels=channels)


def free_ports():
    sockets=[socket.socket() for _ in range(4)]
    for s in sockets:s.bind(('127.0.0.1',0))
    ports=[s.getsockname()[1] for s in sockets]
    for s in sockets:s.close()
    return ports


def run(args):
    args.output_dir.mkdir(parents=True,exist_ok=False)
    config=WorkerConfig('worker-'+args.output_dir.name,*free_ports(),allow_native_rtsp_wildcard=True)
    (args.output_dir/'config.json').write_text(json.dumps(config.__dict__,indent=2))
    client=WorkerClient(config);worker=None;decoder=None
    env=dict(os.environ);env['ISAACSIM_PYTHON']=str(args.isaac_python)
    log=(args.output_dir/'worker.log').open('w');proofs=[];events=[];issued={};sampler=ReplaySampler(source(),session_id=config.session_id)
    def target(seq,t,control,epoch,state):
        frame=sampler.sample_at(t,target_seq=seq,control_seq=control,playback_epoch=epoch).model_copy(update={'state':state,'speed':2.})
        issued[seq]=frame
        return frame
    def rpc(cmd,n,control,epoch,**fields):
        req=RPCRequest(session_id=config.session_id,request_id=f'probe-{n}',control_seq=control,playback_epoch=epoch,command={'cmd':cmd,**fields})
        response=client.request(req)
        if not response.ok:raise RuntimeError(response.error.model_dump_json())
        if response.frame is not None:proofs.append(response.frame.model_dump(mode='json'))
        return response
    try:
        worker=subprocess.Popen(['bash','scripts/run_worker.sh','--session-id',config.session_id,'--output-dir',str(args.output_dir/'worker'),'--allow-native-rtsp-wildcard',*sum(([f'--{name}-port',str(getattr(config,name+'_port'))] for name in ('rpc','target','event','rtsp')),[])],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        deadline=time.monotonic()+150
        while True:
            if worker.poll() is not None:raise RuntimeError(f'Worker startup exited {worker.returncode}; inspect worker.log')
            try:client.handshake(timeout_s=2);break
            except TimeoutError:
                if time.monotonic()>deadline:raise
        listeners=subprocess.check_output(['ss','-ltnH',f'sport = :{config.rtsp_port}'],text=True)
        (args.output_dir/'rtsp-listener.txt').write_text(listeners)
        rpc('load_log',1,1,1,log_id='synthetic-log')
        initial=rpc('apply_keyframe',2,1,1,frame=target(1,0,1,1,'ready'))
        print('REAL_INITIAL_CAPTURE',initial.frame.capture_id,flush=True)
        if args.decoder:
            decoder=subprocess.Popen([str(args.decoder),f'rtsp://127.0.0.1:{config.rtsp_port}/stream','8',str(args.output_dir/'decoder.json'),str(args.output_dir/'decoded-first.ppm')],stdout=subprocess.DEVNULL,stderr=(args.output_dir/'decoder.log').open('w'))
        rpc('apply_keyframe',20,2,1,frame=target(2,0,2,1,'playing'))
        for n in range(3,43):client.publish(TargetMessage(frame=target(n,n/20,2,1,'playing')))
        until=time.monotonic()+2
        while time.monotonic()<until:events.extend(client.poll_events(20))
        if not any(e.event.type=='rendered' and e.event.frame.target_seq==42 for e in events):raise RuntimeError('Latest burst target was not rendered')
        rpc('pause_barrier',3,3,1,target_seq=100)
        paused=rpc('apply_keyframe',4,3,1,frame=target(100,2,3,1,'paused'))
        until=time.monotonic()+1
        while time.monotonic()<until:events.extend(client.poll_events(20))
        seek=rpc('apply_keyframe',5,4,2,frame=target(200,.5,4,2,'paused'))
        client.publish(TargetMessage(frame=target(999,5,1,1,'playing')))
        until=time.monotonic()+2
        while time.monotonic()<until:events.extend(client.poll_events(20))
        bad=[e for e in events if e.event.type=='rendered' and e.event.frame.target_seq==999]
        assert not bad
        for proof in proofs:
            assert proof['raw_identity_verified'] and proof['capture_id']==proof['render_seq']
            want=issued[proof['target_seq']]
            for key in ('session_id','log_id','playback_epoch','control_seq','target_seq','state','speed','log_time_s','source_sample_index'):
                assert proof[key]==getattr(want,key)
            for i,(a,b) in enumerate(zip(proof['joints'],want.joints)):
                error=abs(math.remainder(a-b,2*math.pi)) if CANONICAL_JOINTS[i].startswith('wheel') else abs(a-b)
                assert error<1e-5,(i,a,b)
        paused_frames=[e.event.frame for e in events if e.event.type=='rendered' and e.event.frame.target_seq==100]
        assert len(paused_frames)>1 and all(f.log_time_s==2. and f.state=='paused' and f.speed==2. for f in paused_frames)
        raw_fps=(len(paused_frames)-1)/(paused_frames[-1].captured_wall_s-paused_frames[0].captured_wall_s)
        assert raw_fps>=15,raw_fps
        rpc('apply_keyframe',21,5,3,frame=target(300,5,5,3,'paused'))
        (args.output_dir/'issued.json').write_text(json.dumps({'targets':[f.model_dump(mode='json') for f in issued.values()],'expected_rejected_target_seqs':[999]},indent=2))
        if decoder:
            decoder.wait(timeout=20)
            if decoder.returncode:raise RuntimeError('Independent RTSP decoder failed')
            from scripts.worker_validation.check_stream import check
            check(args.output_dir)
        rpc('shutdown',6,6,3)
        worker.wait(timeout=15)
        if worker.returncode!=0:raise RuntimeError(f'Worker shutdown exit {worker.returncode}')
        report={'scope':'Actual IsaacSim 6.2 worker, actual local ZMQ, actual raw capture IDs; browser not tested','passed':True,'proofs':proofs,'event_count':len(events),'paused_recaptures':len(paused_frames),'stale_target_rejected':True,'worker_exit_code':worker.returncode,'paused_raw_capture_fps':raw_fps,'media_decode_verified':bool(decoder),'rtsp_listening_address':listeners.strip()}
        (args.output_dir/'evidence.json').write_text(json.dumps(report,indent=2))
        (args.output_dir/'events.jsonl').write_text('\n'.join(e.model_dump_json() for e in events)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='proofs'}),flush=True)
    finally:
        if decoder and decoder.poll() is None:decoder.terminate();decoder.wait(timeout=5)
        if worker and worker.poll() is None:
            os.killpg(worker.pid,signal.SIGTERM)
            try:worker.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(worker.pid,signal.SIGKILL);worker.wait(timeout=5)
        client.close();log.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--isaac-python',type=Path,required=True);p.add_argument('--decoder',type=Path)
    run(p.parse_args())
