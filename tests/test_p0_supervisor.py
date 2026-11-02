import importlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest


def process_is_running(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


class SupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('scripts.run_p0_browser_test') is None:
            raise AssertionError('The P0 browser supervisor is not implemented')
        cls.module = importlib.import_module('scripts.run_p0_browser_test')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_nonempty_run_directory_is_preserved(self):
        run = self.root / 'run'
        run.mkdir()
        marker = run / 'previous-evidence.json'
        marker.write_text('previous result')
        with self.assertRaises(self.module.SupervisorError):
            self.module.prepare_run_dir(run)
        self.assertEqual(marker.read_text(), 'previous result')
        self.assertEqual(list(run.iterdir()), [marker])

    def test_preflight_refuses_owned_tcp_and_udp_without_closing_them(self):
        for protocol, kind in [('tcp', socket.SOCK_STREAM), ('udp', socket.SOCK_DGRAM)]:
            with self.subTest(protocol=protocol), socket.socket(socket.AF_INET, kind) as listener:
                listener.bind(('127.0.0.1', 0))
                if protocol == 'tcp':
                    listener.listen()
                port = listener.getsockname()[1]
                with self.assertRaises(self.module.SupervisorError):
                    self.module.preflight_ports([(protocol, port)])
                self.assertEqual(listener.getsockname(), ('127.0.0.1', port))
            self.module.preflight_ports([(protocol, port)])

    def test_tcp_preflight_allows_real_time_wait_without_an_active_listener(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(('127.0.0.1', port)) as client:
                connection, _ = listener.accept()
                with connection:
                    # The server actively closes first, placing its local port
                    # in TIME_WAIT once the client acknowledges and closes.
                    connection.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b'')
        deadline = time.monotonic() + 2
        while True:
            states = [line.split()[3] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                      if line.split()[1] == f'0100007F:{port:04X}']
            if '06' in states or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        self.assertIn('06', states, 'The fixture must produce real TCP TIME_WAIT')
        self.assertNotIn('0A', states, 'There must be no active TCP listener')
        self.module.preflight_ports([('tcp', port)])
        self.module.preflight_ports([('tcp', port)])

    def test_cli_caps_worker_duration_before_creating_or_starting_anything(self):
        run = self.root/'too-long'
        result = subprocess.run([sys.executable, self.module.__file__, '--run-dir', str(run),
                                 '--test', 'sync', '--seconds', '301'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(run.exists())

    def test_shell_script_without_executable_bit_runs_and_captures_output(self):
        script = self.root/'launcher.sh'
        script.write_text('#!/usr/bin/env bash\nprintf "launched %s\\n" "$1"\n')
        script.chmod(0o644)
        with self.module.Supervisor(self.root/'run') as supervisor:
            process = supervisor.start_shell_script('worker', script, ['argument with spaces'], cwd=self.root)
            supervisor.wait_until('launcher_finished', lambda: process.poll() is not None, 5, [])
            self.assertEqual(process.returncode, 0)
        self.assertEqual((self.root/'run/logs/worker.log').read_text(), 'launched argument with spaces\n')

    def test_cli_preflight_failure_saves_report_without_starting_processes(self):
        env = dict(os.environ)
        env.pop('ISAACSIM_PYTHON', None)
        run = self.root/'missing-runtime'
        result = subprocess.run([sys.executable, self.module.__file__, '--run-dir', str(run)],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        report = json.loads((run/'supervisor.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['stages'][-1]['name'], 'preflight')
        self.assertEqual(report['processes'], {})
        self.assertEqual(list((run/'worker').iterdir()), [])
        self.assertTrue(report['cleanup']['complete'])

    def test_test_failure_survives_cleanup_and_own_descendants_are_killed(self):
        marker = self.root / 'pids.json'
        code = '''
import json, os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], start_new_session=True)
time.sleep(0.1)
open(sys.argv[1], 'w').write(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
'''
        outsider = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        try:
            with self.module.Supervisor(self.root/'run', shutdown_timeout=0.2) as supervisor:
                supervisor.start('worker', [sys.executable, '-c', code, str(marker)], cwd=self.root)
                supervisor.wait_until('worker_ready', marker.exists, 5, ['worker'])
                result = supervisor.run_browser([sys.executable, '-c',
                                                'import sys; print("test output"); print("test error", file=sys.stderr); sys.exit(7)'],
                                                cwd=self.root, env=os.environ.copy(), timeout=5,
                                                required=['worker'])
                self.assertEqual(result, 7)
            report = json.loads((self.root/'run/supervisor.json').read_text())
            self.assertEqual(report['exit_code'], 7)
            self.assertEqual(report['test_exit_code'], 7)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['stages'][-1]['status'], 'failed')
            log = (self.root/'run/logs/browser.log').read_text()
            self.assertIn('test output', log)
            self.assertIn('test error', log)
            self.assertTrue(report['cleanup']['complete'])
            self.assertTrue(report['processes']['worker']['cleanup']['sent_sigkill'])
            self.assertTrue(all(not process_is_running(pid) for pid in json.loads(marker.read_text())))
            self.assertIsNone(outsider.poll(), 'Unrelated processes must remain alive')
        finally:
            outsider.terminate()
            outsider.wait(timeout=5)
            if marker.exists():
                for pid in json.loads(marker.read_text()):
                    if process_is_running(pid):
                        os.kill(pid, signal.SIGKILL)

    def test_first_sigterm_during_cleanup_is_deferred_until_owned_processes_stop(self):
        marker = self.root/'terminating'
        code = '''
import signal, sys
from pathlib import Path
from scripts.run_p0_browser_test import Supervisor, SupervisorError
def interrupt(signum, frame):
    raise SupervisorError('interrupted', 128+signum)
signal.signal(signal.SIGTERM, interrupt)
worker = "import signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM, lambda *_: Path(sys.argv[1]).write_text('terminating')); print('ready', flush=True); time.sleep(60)"
with Supervisor(Path(sys.argv[1])/'run', shutdown_timeout=1) as supervisor:
    supervisor.start('worker', [sys.executable, '-c', worker, sys.argv[2]], cwd=Path.cwd())
    supervisor.wait_until('worker_ready', lambda: 'ready' in (supervisor.run_dir/'logs/worker.log').read_text(), 5, ['worker'])
raise SystemExit(supervisor.report['exit_code'])
'''
        process = subprocess.Popen([sys.executable, '-c', code, str(self.root), str(marker)],
                                   cwd=self.module.BACKEND_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            import time
            deadline = time.monotonic()+5
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), 'The worker must have entered supervisor cleanup')
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143, (stdout, stderr))
            report = json.loads((self.root/'run/supervisor.json').read_text())
            self.assertEqual(report['exit_code'], 143)
            self.assertTrue(report['cleanup']['complete'])
            self.assertFalse(process_is_running(report['processes']['worker']['pid']))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            report_path = self.root/'run/supervisor.json'
            if report_path.exists():
                for record in json.loads(report_path.read_text()).get('processes', {}).values():
                    try:
                        os.killpg(record['pgid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_intentional_worker_termination_does_not_fail_a_passing_test(self):
        with self.module.Supervisor(self.root/'run', shutdown_timeout=1) as supervisor:
            supervisor.start('worker', [sys.executable, '-c', 'import time; time.sleep(60)'], cwd=self.root)
            self.assertEqual(supervisor.run_browser([sys.executable, '-c', 'pass'], cwd=self.root,
                             env=os.environ.copy(), timeout=5, required=['worker']), 0)
        report = json.loads((self.root/'run/supervisor.json').read_text())
        self.assertEqual(report['exit_code'], 0)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['processes']['worker']['returncode'], -signal.SIGTERM)
        self.assertTrue(report['cleanup']['complete'])

    def test_cleanup_reaps_root_even_when_nonblocking_poll_has_no_exit_code(self):
        from unittest.mock import patch
        with self.module.Supervisor(self.root/'run',shutdown_timeout=1) as supervisor:
            process=supervisor.start('worker',[sys.executable,'-c','import time; time.sleep(60)'],cwd=self.root)
            # /proc can observe the final transition before a nonblocking poll
            # provides a code. The cleanup completion must include wait/reap.
            with patch.object(process,'poll',return_value=None):
                supervisor.cleanup()
                self.assertIsNotNone(supervisor.report['processes']['worker']['returncode'])
                self.assertEqual(supervisor.report['processes']['worker']['returncode'],-signal.SIGTERM)

    def test_worker_early_exit_is_a_failed_stage_and_cleanup_is_recorded(self):
        with self.assertRaises(self.module.SupervisorError):
            with self.module.Supervisor(self.root/'run', shutdown_timeout=1) as supervisor:
                supervisor.start('worker', [sys.executable, '-c', 'import sys; sys.exit(9)'], cwd=self.root)
                supervisor.wait_until('worker_ready', lambda: False, 5, ['worker'])
        report = json.loads((self.root/'run/supervisor.json').read_text())
        self.assertNotEqual(report['exit_code'], 0)
        self.assertEqual(report['stages'][-1]['status'], 'failed')
        self.assertEqual(report['processes']['worker']['returncode'], 9)
        self.assertTrue(report['cleanup']['complete'])


if __name__ == '__main__':
    unittest.main()
