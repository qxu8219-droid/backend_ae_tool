"""Real ports/processes: these tests must not substitute the resource owner."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_port_lease_excludes_occupied_ports_and_releases_on_failure():
    from app.core.ports import PortPool
    port = unused_port()
    pool = PortPool(port, port)
    lease = pool.lease(['tcp'])
    with pytest.raises(OSError):
        pool.lease(['tcp'])
    lease.close()
    with pool.lease(['tcp']) as second:
        assert second.ports == [port]
    with pytest.raises(OSError):
        pool.lease(['tcp', 'tcp'])
    with pool.lease(['tcp']):
        pass


def test_port_transfer_releases_only_the_selected_bind_owner():
    from app.core.ports import PortPool
    pool = PortPool(6100, 6199)
    with pool.lease(['tcp', 'udp']) as lease:
        lease.release(0)
        with socket.socket() as tcp:
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp.bind(('127.0.0.1', lease.ports[0]))
        with socket.socket(type=socket.SOCK_DGRAM) as udp:
            with pytest.raises(OSError):
                udp.bind(('127.0.0.1', lease.ports[1]))


def test_terminate_escalates_for_child_that_ignores_sigterm(tmp_path):
    from app.core.processes import ManagedProcess
    async def run():
        child = await ManagedProcess.spawn(
            [sys.executable, '-u', '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("READY"); time.sleep(60)'],
            tmp_path / 'child.log',
        )
        for _ in range(100):
            if 'READY' in (tmp_path / 'child.log').read_text():
                break
            await asyncio.sleep(.01)
        start = time.monotonic()
        await child.terminate(grace_s=.1)
        assert time.monotonic() - start < 2
        assert not alive(child.pid) and child.returncode == -signal.SIGKILL
        await child.terminate(grace_s=.1)
    asyncio.run(run())


def test_cleanup_kills_group_descendant_even_after_leader_exits(tmp_path):
    from app.core.processes import ManagedProcess
    async def run():
        pid_file = tmp_path / 'grandchild.pid'
        code = ('import subprocess,sys; from pathlib import Path; '
                'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                f'Path({str(pid_file)!r}).write_text(str(p.pid))')
        process = await ManagedProcess.spawn([sys.executable, '-c', code], tmp_path / 'group.log')
        try:
            for _ in range(100):
                if pid_file.exists() and process.returncode is not None:
                    break
                await asyncio.sleep(.01)
            descendant = int(pid_file.read_text())
            assert alive(descendant)
            await process.terminate(grace_s=.1)
            assert not alive(descendant)
        finally:
            await process.terminate(grace_s=.1)
    asyncio.run(run())


def test_owned_child_dies_when_its_supervisor_is_killed(tmp_path):
    from app.core import process_child  # ensure the production launcher exists
    pid_file = tmp_path / 'owned.pid'
    code = f'''import asyncio,sys
from pathlib import Path
from app.core.processes import ManagedProcess
async def run():
    p=await ManagedProcess.spawn([sys.executable,'-c','import time;time.sleep(60)'],Path({str(tmp_path / 'owned.log')!r}))
    Path({str(pid_file)!r}).write_text(str(p.pid))
    await asyncio.sleep(60)
asyncio.run(run())'''
    parent = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT)
    child_pid = None
    try:
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(.02)
        child_pid = int(pid_file.read_text())
        time.sleep(.1)
        parent.kill(); parent.wait(timeout=3)
        for _ in range(100):
            if not alive(child_pid):
                break
            time.sleep(.01)
        assert not alive(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill(); parent.wait(timeout=3)
        if child_pid and alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)


def test_native_guard_restricts_only_its_configured_wildcard_bind(tmp_path):
    build = subprocess.run(['bash', 'scripts/build_rtsp_loopback.sh'], cwd=ROOT, capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    port = unused_port()
    code = '''import json,socket,os
port=int(os.environ['REPLAY_RTSP_LOOPBACK_PORT'])
with socket.socket() as guarded,socket.socket() as untouched:
    guarded.bind(('0.0.0.0',port));untouched.bind(('0.0.0.0',0))
    print(json.dumps([guarded.getsockname()[0],untouched.getsockname()[0]]))
'''
    env = dict(os.environ, LD_PRELOAD=str(ROOT / 'outputs/tools/rtsp_loopback.so'), REPLAY_RTSP_LOOPBACK_PORT=str(port))
    result = subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ['127.0.0.1', '0.0.0.0']
