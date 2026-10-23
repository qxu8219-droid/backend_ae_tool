#!/usr/bin/env python3
"""Run real P0 media/worker faults against exclusively owned local processes.

The browser test requests named actions through a private run-directory mailbox.
No HTTP endpoint accepts process IDs, shell commands, or arbitrary file paths.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

try:
    from .run_p0_browser_test import (BACKEND_ROOT, Supervisor, SupervisorError,
        executable, get_json, load_ready, preflight_ports, stream_ready)
except ImportError:
    from run_p0_browser_test import (BACKEND_ROOT, Supervisor, SupervisorError,
        executable, get_json, load_ready, preflight_ports, stream_ready)


class FaultMailbox:
    ACTIONS={'start_worker','stop_gateway','start_gateway','crash_worker','reload_worker'}

    def __init__(self, directory):
        self.directory=Path(directory)
        self.directory.mkdir(exist_ok=True)
        self.previous=None

    def poll(self, handler):
        try:
            request=json.loads((self.directory/'fault-request.json').read_text())
        except FileNotFoundError:
            return None
        if (not isinstance(request,dict) or set(request)!={'request_id','action'} or
            type(request['request_id']) is not int or not 0<request['request_id']<2**53 or
            request['action'] not in self.ACTIONS):
            raise SupervisorError('Invalid fault mailbox request')
        if self.previous is not None:
            if request==self.previous:return None
            if request['request_id']<=self.previous['request_id']:
                raise SupervisorError('Fault request ID reused or out of order')
        self.previous=request
        response={'request_id':request['request_id']}
        try:
            result=handler(request['action']) or {}
            response.update(ok=True,**result)
        except Exception as exc:
            response.update(ok=False,error=str(exc))
            raise
        finally:
            temporary=self.directory/'fault-response.json.tmp'
            temporary.write_text(json.dumps(response,allow_nan=False))
            temporary.replace(self.directory/'fault-response.json')
        return response


class FaultRig:
    def __init__(self, supervisor, env):
        self.supervisor=supervisor
        self.env=env
        self.active={}
        self.serial={}
        self.worker_dir=supervisor.run_dir/'worker'
        self.session_id=None

    def required(self):
        return list(self.active.values())

    def name(self, kind):
        self.serial[kind]=self.serial.get(kind,0)+1
        return f'{kind}_{self.serial[kind]}'

    def stop(self, kind, *, crash=False):
        name=self.active.get(kind)
        if name is None:raise SupervisorError(f'{kind} is not active')
        process=self.supervisor.processes[name]
        if process.poll() is not None:
            raise SupervisorError(f'{kind} already exited unexpectedly')
        self.supervisor.capture_descendants()
        with self.supervisor.stage(('crash_' if crash else 'stop_')+name) as stage:
            sig=signal.SIGKILL if crash else signal.SIGTERM
            os.killpg(process.pid,sig)
            process.wait(timeout=10)
            stage.update(signal=sig.name,returncode=process.returncode,intentional=True)
            del self.active[kind]

    def start_gateway(self):
        if 'gateway' in self.active:raise SupervisorError('Gateway already running')
        name=self.name('gateway')
        self.supervisor.start_shell_script(name,BACKEND_ROOT/'scripts/run_mediamtx_p0.sh',
                                          cwd=BACKEND_ROOT,env=self.env)
        self.active['gateway']=name
        self.supervisor.wait_until(name+'_api',lambda:get_json('http://127.0.0.1:9997/v3/paths/list') is not None,
                                   15,self.required())
        if 'worker' in self.active:
            self.supervisor.wait_until(name+'_source',stream_ready,30,self.required())

    def start_preview(self):
        if 'preview' in self.active:self.stop('preview')
        name=self.name('preview')
        self.supervisor.start(name,[sys.executable,'-u',BACKEND_ROOT/'scripts/spike/whep_preview.py',
            '--output-dir',self.worker_dir,'--port','8768'],cwd=BACKEND_ROOT,env=self.env)
        self.active['preview']=name
        self.supervisor.wait_until(name+'_ready',lambda:(get_json('http://127.0.0.1:8768/status') or {}).get('ok'),
                                   10,self.required())

    def start_worker(self, *, reloading=False):
        if 'worker' in self.active:raise SupervisorError('Worker already running')
        if (self.session_id is not None)!=reloading:
            raise SupervisorError('Use start_worker once, then reload_worker after a crash')
        previous=self.session_id
        name=self.name('worker')
        self.worker_dir=self.supervisor.run_dir/name
        self.worker_dir.mkdir()
        self.supervisor.start_shell_script(name,BACKEND_ROOT/'scripts/run_p0.sh',[
            '--mode','rtsp_sync','--start-paused','--width','1280','--height','720',
            '--rt-subframes','1','--seconds','240','--output-dir',self.worker_dir],
            cwd=BACKEND_ROOT,env=self.env)
        self.active['worker']=name
        self.supervisor.wait_until(name+'_ready',lambda:load_ready(self.worker_dir/'latest_snapshot.json'),
                                   120,self.required())
        snapshot=json.loads((self.worker_dir/'latest_snapshot.json').read_text())
        self.session_id=snapshot['session_id']
        if self.session_id==previous or snapshot['log_time_s']!=0 or snapshot['state']!='ready':
            raise SupervisorError('Reload must create a new session in ready state at log time zero')
        self.start_preview()
        self.supervisor.wait_until(name+'_stream',stream_ready,30,self.required())

    def action(self, action):
        with self.supervisor.stage('fault_'+action):
            if action=='start_worker':self.start_worker()
            elif action=='reload_worker':self.start_worker(reloading=True)
            elif action=='stop_gateway':self.stop('gateway')
            elif action=='start_gateway':self.start_gateway()
            elif action=='crash_worker':self.stop('worker',crash=True)
            else:raise SupervisorError('Unknown fault action')
        return {'worker_dir':str(self.worker_dir),'session_id':self.session_id}


def run(args):
    supervisor=None
    try:
        with Supervisor(args.run_dir,shutdown_timeout=10) as supervisor:
            with supervisor.stage('preflight'):
                preflight_ports()
                python=executable(os.environ.get('ISAACSIM_PYTHON'),'ISAACSIM_PYTHON')
                executable(os.environ.get('CHROME_BIN','/usr/bin/google-chrome'),'CHROME_BIN')
                npm=shutil.which('npm')
                if not npm:raise SupervisorError('npm not installed')
                if not (BACKEND_ROOT/'scripts/spike/browser_tests/node_modules/playwright/package.json').is_file():
                    raise SupervisorError('Install the pinned browser test dependencies first')
            env=dict(os.environ,ISAACSIM_PYTHON=python)
            rig=FaultRig(supervisor,env)
            mailbox=FaultMailbox(supervisor.run_dir/'control')
            rig.start_gateway()
            rig.start_preview()
            env.update(P0_FAULT_DIR=str(mailbox.directory),
                P0_BROWSER_OUTPUT=str(supervisor.run_dir/'browser'),
                P0_WHEP_URL='http://127.0.0.1:8768/?autoconnect=1&sync=1',
                P0_MEDIAMTX_API_URL='http://127.0.0.1:9997/v3/webrtcsessions/list')
            process=supervisor.start('browser',[npm,'run','test:sync:faults'],
                                    cwd=BACKEND_ROOT/'scripts/spike/browser_tests',env=env)
            with supervisor.stage('browser_fault_test') as stage:
                deadline=time.monotonic()+args.timeout
                while process.poll() is None:
                    mailbox.poll(rig.action)
                    supervisor.check_health(rig.required())
                    if time.monotonic()>deadline:raise SupervisorError('Fault browser timeout',124)
                    time.sleep(.05)
                code=process.returncode
                supervisor.report['test_exit_code']=code
                supervisor.report['exit_code']=code if code>=0 else 128-code
                stage.update(returncode=code,status='passed' if code==0 else 'failed')
        return supervisor.report['exit_code']
    except (SupervisorError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        return supervisor.report['exit_code'] if supervisor else 1
    finally:
        if supervisor:print('Fault evidence:',supervisor.report_path,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=300)
    args=parser.parse_args()
    if not 1<=args.timeout<=600:parser.error('timeout must be in [1,600] seconds')
    def interrupted(signum,_):raise SupervisorError(f'Interrupted by {signal.Signals(signum).name}',128+signum)
    signal.signal(signal.SIGINT,interrupted)
    signal.signal(signal.SIGTERM,interrupted)
    return run(args)


if __name__=='__main__':raise SystemExit(main())
