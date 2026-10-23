#!/usr/bin/env python3
"""Own one isolated Isaac/MediaMTX/browser P0 run and clean up its process groups.

Example (from backend/):
  ISAACSIM_PYTHON=/absolute/path/to/python ./scripts/run_p0_browser_test.py \
      --run-dir outputs/p0/sync-run --test sync --delay-modes metadata,video
No services are reused. Logs, worker data and browser evidence have separate dirs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PORTS = [('tcp', 8554), ('tcp', 8889), ('tcp', 8768), ('tcp', 9997), ('udp', 8189)]
LOCAL_HTTP = build_opener(ProxyHandler({}))


class SupervisorError(RuntimeError):
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def prepare_run_dir(path: Path):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise SupervisorError(f'Run directory must be empty; refusing to overwrite: {path}', 2)
    path.mkdir(parents=True, exist_ok=True)


def preflight_ports(ports=PORTS):
    """Reject active services, allowing TCP listeners to replace TIME_WAIT sockets."""
    held = []
    try:
        for protocol, port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM)
            held.append(sock)
            try:
                if protocol == 'tcp':
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', port))
                if protocol == 'tcp':
                    sock.listen(1)
            except OSError as exc:
                raise SupervisorError(f'Port unavailable: {protocol.upper()} 127.0.0.1:{port}; '
                                      'existing processes will not be touched', 2) from exc
    finally:
        for sock in held:
            sock.close()


def process_snapshot():
    processes = {}
    for entry in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = entry.read_text().rsplit(')', 1)[1].split()
            processes[int(entry.parent.name)] = {'state': fields[0], 'parent': int(fields[1]),
                'group': int(fields[2]), 'start': fields[19]}
        except (FileNotFoundError, ProcessLookupError):
            pass
    return processes


class Supervisor:
    def __init__(self, run_dir: Path, shutdown_timeout=5):
        self.run_dir = run_dir.resolve()
        self.shutdown_timeout = shutdown_timeout
        self.processes = {}
        self.logs = {}
        self.owned = {}
        self.excluded = {}
        self.report = {'schema_version': 1, 'started_at': utc_now(), 'run_dir': str(self.run_dir),
                       'status': 'running', 'exit_code': 0, 'test_exit_code': None,
                       'stages': [], 'processes': {}, 'cleanup': {'complete': False}}
        prepare_run_dir(self.run_dir)
        self.report_path = self.run_dir/'supervisor.json'
        # Reserve this run before creating outputs; a competing invocation must fail.
        with self.report_path.open('x') as output:
            json.dump(self.report, output, indent=2)
        for name in ['worker', 'logs', 'browser']:
            (self.run_dir/name).mkdir()

    def __enter__(self):
        # Playwright starts Chrome in a separate session. Linux subreaping keeps
        # orphaned descendants attached here even if their npm/Node parent exits
        # between polls. Pre-existing children and their descendants are excluded.
        before = process_snapshot()
        parents = {os.getpid()}
        while parents:
            children = {pid for pid, info in before.items() if info['parent'] in parents}
            self.excluded.update({pid: before[pid]['start'] for pid in children})
            parents = children
        self.libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        if self.libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or self.libc.prctl(36, 1, 0, 0, 0):
            raise SupervisorError('Linux child subreaper setup failed; refusing an uncontained run', 2)
        self.previous_subreaper = previous.value
        return self

    def __exit__(self, kind, error, traceback):
        if error is not None:
            self.report['exit_code'] = getattr(error, 'exit_code', 130 if kind is KeyboardInterrupt else 1)
            self.report['error'] = str(error) or kind.__name__
        def defer_interrupt(signum, frame):
            self.report.setdefault('cleanup_signals', []).append(signal.Signals(signum).name)
            if self.report['exit_code'] == 0:
                self.report['exit_code'] = 128+signum
        handlers = {sig: signal.signal(sig, defer_interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            self.cleanup()
            self.report['status'] = 'passed' if self.report['exit_code'] == 0 else 'failed'
            self.report['finished_at'] = utc_now()
            self.save()
        finally:
            self.libc.prctl(36, self.previous_subreaper, 0, 0, 0)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        return False

    def save(self):
        temporary = self.report_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.report, indent=2) + '\n')
        temporary.replace(self.report_path)

    @contextmanager
    def stage(self, name):
        stage = {'name': name, 'started_at': utc_now(), 'status': 'running'}
        self.report['stages'].append(stage)
        started = time.monotonic()
        try:
            yield stage
            if stage['status'] == 'running':
                stage['status'] = 'passed'
        except BaseException as exc:
            stage.update(status='failed', error=str(exc))
            raise
        finally:
            stage['elapsed_seconds'] = round(time.monotonic()-started, 3)
            self.save()

    def start(self, name, command, *, cwd, env=None):
        with self.stage('start_' + name):
            log_path = self.run_dir/'logs'/f'{name}.log'
            log = log_path.open('xb')
            self.logs[name] = log
            command = [str(part) for part in command]
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            self.processes[name] = process
            self.report['processes'][name] = {'pid': process.pid, 'pgid': process.pid,
                'command': command, 'cwd': str(cwd), 'log': str(log_path), 'returncode': None}
            self.capture_descendants()
            return process

    def check_health(self, names):
        self.capture_descendants()
        for name in names:
            code = self.processes[name].poll()
            if code is not None:
                raise SupervisorError(f'{name} exited unexpectedly with code {code}; '
                                      f'see logs/{name}.log')

    def start_shell_script(self, name, script, arguments=(), *, cwd, env=None):
        # Repository shell scripts need not carry an executable git mode.
        return self.start(name, ['bash', script, *arguments], cwd=cwd, env=env)

    def capture_descendants(self):
        snapshot = process_snapshot()
        roots = {process.pid: name for name, process in self.processes.items()}
        changed = True
        while changed:
            changed = False
            for pid, info in snapshot.items():
                if pid in self.excluded or pid in self.owned:
                    continue
                parent = info['parent']
                if parent in self.excluded and snapshot.get(parent, {}).get('start') == self.excluded[parent]:
                    self.excluded[pid] = info['start']
                    changed = True
                elif pid in roots or parent in self.owned or parent == os.getpid():
                    owner = roots.get(pid, self.owned.get(parent, {}).get('owner', 'adopted'))
                    self.owned[pid] = {'start': info['start'], 'owner': owner}
                    changed = True
        return {pid: info for pid, info in snapshot.items() if pid in self.owned
                and info['start'] == self.owned[pid]['start'] and info['state'] != 'Z'}

    def wait_until(self, name, predicate, timeout, required):
        with self.stage(name):
            deadline = time.monotonic() + timeout
            while True:
                self.check_health(required)
                if predicate():
                    self.check_health(required)
                    return
                if time.monotonic() >= deadline:
                    raise SupervisorError(f'Timed out after {timeout:g}s waiting for {name}')
                time.sleep(0.1)

    def run_browser(self, command, *, cwd, env, timeout, required):
        process = self.start('browser', command, cwd=cwd, env=env)
        with self.stage('browser_test') as stage:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                self.check_health(required)
                if time.monotonic() >= deadline:
                    raise SupervisorError(f'Browser test exceeded {timeout:g}s', 124)
                time.sleep(0.1)
            # Record the real test result before health/cleanup can raise an error.
            self.report['test_exit_code'] = process.returncode
            self.report['exit_code'] = process.returncode if process.returncode >= 0 else 128-process.returncode
            stage['returncode'] = process.returncode
            if process.returncode == 0:
                self.check_health(required)
            else:
                stage.update(status='failed', error=f'Browser test exited with code {process.returncode}; '
                                                   'see logs/browser.log')
        return process.returncode

    def cleanup(self):
        groups = {}
        roots = {process.pid for process in self.processes.values()}
        for name, process in self.processes.items():
            self.report['processes'][name]['returncode_before_cleanup'] = process.poll()
        term_deadline = time.monotonic() + self.shutdown_timeout
        final_deadline = term_deadline + 2
        while True:
            # Reap original children with Popen, and adopted descendants only by
            # their recorded PID. Never wait on or signal an unrelated child.
            for process in self.processes.values():
                process.poll()
            live = self.capture_descendants()
            for pid in self.owned.keys() - roots:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
            if not live or time.monotonic() >= final_deadline:
                break
            for pgid in {info['group'] for info in live.values()}:
                result = groups.setdefault(pgid, {'sent_sigterm': False, 'sent_sigkill': False})
                if pgid == os.getpgrp():
                    result['error'] = 'Refusing to signal the supervisor process group'
                    continue
                sig = signal.SIGKILL if time.monotonic() >= term_deadline else signal.SIGTERM
                key = 'sent_sigkill' if sig == signal.SIGKILL else 'sent_sigterm'
                if not result[key]:
                    try:
                        os.killpg(pgid, sig)
                        result[key] = True
                    except ProcessLookupError:
                        pass  # A group naturally exiting during cleanup is success.
                    except OSError as exc:
                        result['error'] = str(exc)
            time.sleep(0.05)
        for name, process in self.processes.items():
            result = dict(groups.get(process.pid, {'sent_sigterm': False, 'sent_sigkill': False}))
            result['remaining_live_pids'] = [pid for pid in live if self.owned[pid]['owner'] == name]
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                result['error'] = 'Owned root process did not finish reaping'
            self.report['processes'][name].update(returncode=process.returncode, cleanup=result)
        for log in self.logs.values():
            log.close()
        complete = not live and all(process.returncode is not None for process in self.processes.values())
        self.report['cleanup'] = {'complete': complete, 'finished_at': utc_now(),
            'groups': groups, 'owned_descendant_pids': sorted(self.owned.keys() - roots),
            'remaining_live_pids': sorted(live)}
        if not complete and self.report['exit_code'] == 0:
            self.report['exit_code'] = 1


def load_ready(path):
    try:
        return isinstance(json.loads(path.read_text()), dict)
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def get_json(url):
    try:
        with LOCAL_HTTP.open(url, timeout=1) as response:
            return json.load(response)
    except (URLError, TimeoutError, json.JSONDecodeError, ConnectionError):
        return None


def stream_ready():
    data = get_json('http://127.0.0.1:9997/v3/paths/list')
    return isinstance(data, dict) and any(item.get('name') == 'stream' and item.get('ready') is True
                                        for item in data.get('items', []))


def executable(value, label):
    path = Path(value or '')
    if not value or not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise SupervisorError(f'{label} must be an absolute path to an executable file', 2)
    return str(path)


def run_pipeline(args):
    supervisor = None
    try:
        with Supervisor(args.run_dir, args.shutdown_timeout) as supervisor:
            root = BACKEND_ROOT
            worker_dir = supervisor.run_dir/'worker'
            browser_dir = root/'scripts/spike/browser_tests'
            url = args.url or ('http://127.0.0.1:8768/?autoconnect=1&sync=1' if args.test == 'sync'
                               else 'http://127.0.0.1:8768/?autoconnect=1')
            with supervisor.stage('preflight'):
                preflight_ports()
                python = executable(os.environ.get('ISAACSIM_PYTHON'), 'ISAACSIM_PYTHON')
                npm = shutil.which('npm')
                if npm is None:
                    raise SupervisorError('npm is required; install browser dependencies before running', 2)
                executable(os.environ.get('CHROME_BIN', '/usr/bin/google-chrome'), 'CHROME_BIN')
                for path in [root/'scripts/run_p0.sh', root/'scripts/run_mediamtx_p0.sh',
                             root/'scripts/spike/whep_preview.py', root/'tools/mediamtx/mediamtx',
                             browser_dir/'node_modules/playwright/package.json']:
                    if not path.is_file():
                        raise SupervisorError(f'Required file is missing: {path}', 2)
            mode = 'rtsp_sync' if args.test == 'sync' else 'rtsp'
            supervisor.report['configuration'] = {'test': args.test, 'worker_mode': mode,
                'worker_seconds': args.seconds, 'width': args.width, 'height': args.height,
                'rt_subframes': args.rt_subframes, 'url': url, 'delay_modes': args.delay_modes,
                'isaacsim_python': python, 'worker_dir': str(worker_dir),
                'browser_output': str(supervisor.run_dir/'browser')}
            env = dict(os.environ, ISAACSIM_PYTHON=python)
            supervisor.start_shell_script('worker', root/'scripts/run_p0.sh', ['--mode', mode,
                '--output-dir', worker_dir, '--seconds', str(args.seconds), '--width', str(args.width),
                '--height', str(args.height), '--rt-subframes', str(args.rt_subframes)], cwd=root, env=env)
            supervisor.wait_until('worker_ready', lambda: load_ready(worker_dir/'ready.json'),
                                  args.startup_timeout, ['worker'])
            supervisor.start_shell_script('mediamtx', root/'scripts/run_mediamtx_p0.sh', cwd=root, env=env)
            supervisor.start('preview', [sys.executable, '-u', root/'scripts/spike/whep_preview.py',
                                        '--output-dir', worker_dir, '--port', '8768'], cwd=root, env=env)
            required = ['worker', 'mediamtx', 'preview']
            supervisor.wait_until('stream_ready', stream_ready, args.stream_timeout, required)
            supervisor.wait_until('preview_ready', lambda: (get_json('http://127.0.0.1:8768/status') or {}).get('ok') is True,
                                  10, required)
            browser_env = dict(env, P0_WHEP_URL=url, P0_BROWSER_OUTPUT=str(supervisor.run_dir/'browser'),
                               P0_EXPECT_WIDTH=str(args.width), P0_EXPECT_HEIGHT=str(args.height),
                               P0_SYNC_DELAY_MODES=args.delay_modes,
                               P0_MEDIAMTX_API_URL='http://127.0.0.1:9997/v3/webrtcsessions/list')
            script = {'whep': 'test:whep', 'lifecycle': 'test:whep:lifecycle', 'sync': 'test:sync'}[args.test]
            supervisor.run_browser([npm, 'run', script], cwd=browser_dir, env=browser_env,
                                   timeout=args.test_timeout, required=required)
        return supervisor.report['exit_code']
    except (SupervisorError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return supervisor.report['exit_code'] if supervisor is not None else getattr(exc, 'exit_code', 2)
    finally:
        if supervisor is not None:
            print(f'Supervisor evidence: {supervisor.report_path}', flush=True)


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be finite and positive')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run-dir', type=Path, default=BACKEND_ROOT/'outputs/p0'/datetime.now().strftime('browser-%Y%m%d-%H%M%S-%f'))
    parser.add_argument('--test', choices=['whep', 'lifecycle', 'sync'], default='whep')
    parser.add_argument('--seconds', '--worker-seconds', type=positive, default=300)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--rt-subframes', type=int, default=1)
    parser.add_argument('--startup-timeout', type=positive, default=180)
    parser.add_argument('--stream-timeout', type=positive, default=30)
    parser.add_argument('--test-timeout', type=positive, default=240)
    parser.add_argument('--shutdown-timeout', type=positive, default=5)
    parser.add_argument('--delay-modes', default='', help='Comma-separated metadata,video sync scenarios')
    parser.add_argument('--url', help='Override the loopback preview page URL')
    args = parser.parse_args(argv)
    if args.seconds > 300 or min(args.width, args.height, args.rt_subframes) < 1:
        parser.error('seconds must be <= 300; width, height and rt-subframes must be positive')
    modes = [mode.strip() for mode in args.delay_modes.split(',') if mode.strip()]
    if any(mode not in ('metadata', 'video') for mode in modes):
        parser.error('delay-modes supports metadata,video only')
    args.delay_modes = ','.join(dict.fromkeys(modes))
    if args.url:
        url = urlsplit(args.url)
        if url.scheme not in ('http', 'https') or url.hostname not in ('127.0.0.1', 'localhost'):
            parser.error('url must be a loopback HTTP(S) URL')
    # First interrupt raises into the context manager; repeated interrupts must
    # not interrupt cleanup halfway through and leave descendants running.
    interrupted = False
    def interrupt(signum, frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise SupervisorError(f'Received {signal.Signals(signum).name}', 128+signum)
    handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return run_pipeline(args)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
