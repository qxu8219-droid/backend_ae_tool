"""Standalone local worker process. No production fake runtime option."""
import argparse,json,os,signal,time,sys
from pathlib import Path
from .config import WorkerConfig,ensure_ports_available
from .transport import WorkerServer


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--session-id',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    for name,default in [('rpc',19601),('target',19602),('event',19603),('rtsp',19554)]:parser.add_argument('--'+name+'-port',type=int,default=default)
    parser.add_argument('--width',type=int,default=1280);parser.add_argument('--height',type=int,default=720)
    parser.add_argument('--allow-native-rtsp-wildcard',action='store_true',help='Only for synthetic validation or an isolated deployment network')
    args=parser.parse_args();config=WorkerConfig(args.session_id,args.rpc_port,args.target_port,args.event_port,args.rtsp_port,args.width,args.height,allow_native_rtsp_wildcard=args.allow_native_rtsp_wildcard)
    if os.environ.get('REPLAY_RTSP_LOOPBACK_PORT') not in (None,str(config.rtsp_port)):
        raise ValueError('RTSP bind guard port does not match the worker configuration')
    ensure_ports_available(config)
    args.output_dir.mkdir(parents=True,exist_ok=False)
    alive=True
    def stop(*_):
        nonlocal alive;alive=False
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    runtime=None;server=None
    code=1
    try:
        from .runtime_62 import Runtime62
        runtime=Runtime62(config,Path(__file__).resolve().parents[1],args.output_dir)
        server=WorkerServer(config,runtime)
        print('WORKER_READY',json.dumps({'session_id':config.session_id,**runtime.report}),flush=True)
        deadline=time.monotonic()
        while alive and server.engine.running and not server.engine.failed:
            server.poll(2)
            if time.monotonic()>=deadline or server.engine.pending:
                deadline=time.monotonic()+1/config.render_hz
                server.engine.step()
        code=1 if server.engine.failed else 0
    except BaseException as exc:
        print('WORKER_ERROR',f'{type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
        raise
    finally:
        errors=finish_worker(server,runtime,args.output_dir,exit_code=code)
        if errors:
            print('WORKER_CLEANUP_ERRORS',json.dumps(errors),file=sys.stderr,flush=True);code=1
    return code


def finish_worker(server,runtime,output_dir,exit_code=0):
    from .resources import release_all
    actions=[]
    if server is not None:
        def report():
            (output_dir/'shutdown.json').write_text(json.dumps({'engine':server.engine.diagnostics(),'invalid_messages':server.invalid,'send_failures':server.send_failures},indent=2))
        actions.extend([('shutdown-report',report),('zmq-close',server.close)])
    errors=release_all(actions)
    if errors:print('WORKER_CLEANUP_ERRORS',json.dumps(errors),file=sys.stderr,flush=True)
    if runtime is not None:
        try:errors.extend(runtime.close(exit_code=1 if errors else exit_code) or [])
        except BaseException as exc:errors.append(f'runtime-close: {exc}')
    return errors


if __name__=='__main__':raise SystemExit(main())
