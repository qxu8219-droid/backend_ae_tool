"""Own subprocess groups, stream diagnostics, and finish cleanup despite failures."""
import asyncio
import os
from pathlib import Path
import signal
import sys
import time


def process_info(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'state': fields[0], 'group': int(fields[2]), 'start': int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


class ManagedProcess:
    def __init__(self, process, log, on_line):
        self.process = process
        self.pid = process.pid
        self._birth = process_info(self.pid)
        self._log = log
        self._on_line = on_line
        self.diagnostic_errors = []
        self._pump = asyncio.create_task(self._read_output())
        self._cleanup = None

    @classmethod
    async def spawn(cls, command, log_path, *, env=None, cwd=None, on_line=None):
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open('xb')
        wrapper = Path(__file__).with_name('process_child.py')
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, str(wrapper), str(os.getpid()), *map(str, command),
            cwd=cwd, env=env, start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        ))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            try:
                process = await spawn
                owned = cls(process, log, on_line)
                await owned.terminate()
            finally:
                log.close()
            raise
        except BaseException:
            log.close()
            raise
        return cls(process, log, on_line)

    @property
    def returncode(self):
        return self.process.returncode

    def _diagnostic(self, error):
        if len(self.diagnostic_errors) < 8:
            self.diagnostic_errors.append(str(error)[:256])

    async def _read_output(self):
        pending = b''
        while data := await self.process.stdout.read(16384):
            try:
                self._log.write(data)
                self._log.flush()
            except OSError as exc:
                self._diagnostic(exc)
            pending += data
            lines = pending.split(b'\n')
            pending = lines.pop()
            for line in lines:
                if self._on_line is not None and len(line) <= 65536:
                    try:
                        self._on_line(line.decode('utf-8', errors='replace'))
                    except Exception as exc:
                        self._diagnostic(exc)
            if len(pending) > 65536:
                pending = b''

    def _members(self):
        leader = process_info(self.pid)
        if leader and self._birth and leader['start'] != self._birth['start']:
            return []  # PID was reused; never signal the new owner's group.
        members = []
        for path in Path('/proc').glob('[0-9]*'):
            info = process_info(int(path.name))
            if info and info['group'] == self.pid and info['state'] != 'Z':
                members.append(int(path.name))
        return members

    def signal_group(self, signum):
        if self._members():
            try:
                os.killpg(self.pid, signum)
            except ProcessLookupError:
                pass

    async def terminate(self, *, grace_s=10.):
        if not 0 <= grace_s <= 10:
            raise ValueError('Shutdown grace must be 0..10 seconds')
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._terminate(grace_s))
        await asyncio.shield(self._cleanup)

    async def _terminate(self, grace_s):
        try:
            self.signal_group(signal.SIGTERM)
            deadline = time.monotonic() + grace_s
            while self._members() and time.monotonic() < deadline:
                await asyncio.sleep(.02)
            self.signal_group(signal.SIGKILL)
            await asyncio.wait_for(self.process.wait(), timeout=2.)
            await asyncio.wait_for(self._pump, timeout=2.)
            if self._members():
                raise RuntimeError('Owned process group survived SIGKILL')
        finally:
            self._log.close()
