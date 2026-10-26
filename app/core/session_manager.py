"""One async session owner; SDK, ZMQ and gateway lifetimes remain separate."""
import asyncio
from collections import OrderedDict
import fcntl
import json
from pathlib import Path
import time
import uuid

from app.schemas.rest import CreateSessionRequest, SessionCreated
from replay_protocol import RPCRequest, TargetMessage
from sim_worker.config import WorkerConfig
from .config import Settings, child_environment, worker_launch
from .gateway import GatewayError, MediaGateway, ROOT
from .ports import PortPool
from .processes import ManagedProcess
from .session import Session
from .worker_client import AsyncWorkerClient, WorkerClosed


class SessionError(RuntimeError):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


class SessionManager:
    def __init__(self, settings: Settings, *, worker_launcher=worker_launch):
        self.settings = settings
        self.root = Path(settings.state_dir).resolve()
        self.launcher = worker_launcher
        self.pool = PortPool(settings.port_first, settings.port_last)
        self.gateway = MediaGateway(settings.gateway, self.root / 'gateway')
        self.sessions = OrderedDict()
        self.active_sid = None
        self.started = False
        self.closing = False
        self._lock_file = None
        self._close_task = None

    async def start(self):
        if self.started:
            return
        if self.closing:
            raise SessionError(503, 'SERVICE_CLOSED', 'Session manager is closed')
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / '.manager.lock').open('a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise SessionError(409, 'SERVICE_ALREADY_RUNNING', 'Another session owner holds this state directory')
        self._lock_file = lock
        try:
            await self.gateway.start()
            self.started = True
        except BaseException:
            try:
                await self.gateway.close()
            finally:
                lock.close(); self._lock_file = None
            raise

    async def create(self, request):
        if not self.started or self.closing:
            raise SessionError(503, 'SERVICE_CLOSED', 'Start the session manager before creating a session')
        request = CreateSessionRequest.model_validate(request)
        if request.scene_id != 'empty' or request.robot_id != 'galaxea_r1pro_2026':
            raise SessionError(404, 'UNKNOWN_ASSET', 'Unknown configured scene or robot')
        if self.active_sid is not None:
            raise SessionError(409, 'SESSION_CAPACITY', 'Delete the existing session before creating another')
        sid = 's-' + uuid.uuid4().hex
        lease = self.pool.lease(['tcp'] * 4)
        output = self.root / sid
        try:
            output.mkdir(exist_ok=False)
            config = WorkerConfig(sid, *lease.ports)
            record = Session(sid, request, config, output, lease, self.gateway.whep_origin)
        except BaseException:
            lease.close()
            raise
        self.sessions[sid] = record
        self.active_sid = sid
        while len(self.sessions) > self.settings.history_limit:
            old_sid = next(iter(self.sessions))
            if old_sid == self.active_sid:
                break
            self.sessions.pop(old_sid)
        record.emit('worker_phase_changed')
        record.task = asyncio.create_task(self._run(record), name='session-' + sid)
        return SessionCreated(session_id=sid, ws_url=f'/ws/sessions/{sid}')

    def _record(self, sid):
        if sid not in self.sessions:
            raise SessionError(404, 'SESSION_NOT_FOUND', 'Session does not exist')
        return self.sessions[sid]

    def get(self, sid):
        return self._record(sid).snapshot()

    def fail_session(self, sid, code, message):
        record = self._record(sid)
        record.fail(code, message)
        return record.snapshot()

    def event_loss_count(self, sid):
        record = self._record(sid)
        return record.dropped_events + (record.client.dropped_events if record.client is not None else 0)

    def drain_events(self, sid):
        record = self._record(sid)
        result = list(record.events); record.events.clear()
        return result

    def drain_worker_events(self, sid):
        record = self._record(sid)
        result = list(record.worker_events); record.worker_events.clear()
        return result

    async def rpc(self, sid, request):
        record = self._record(sid)
        if record.status != 'ready' or record.client is None:
            raise SessionError(503, 'WORKER_NOT_READY', 'Worker is not ready')
        try:
            return await record.client.request(request, timeout_s=self.settings.rpc_timeout_s)
        except TimeoutError as exc:
            record.fail('WORKER_RPC_TIMEOUT', 'Worker RPC exceeded its deadline')
            raise SessionError(503, 'WORKER_RPC_TIMEOUT', 'Worker RPC exceeded its deadline') from exc

    async def publish(self, sid, target):
        record = self._record(sid)
        if record.status != 'ready' or record.client is None:
            raise SessionError(503, 'WORKER_NOT_READY', 'Worker is not ready')
        await record.client.publish(TargetMessage.model_validate(target))

    def _line(self, record, line):
        if line.startswith('WORKER_STARTUP_ERROR '):
            record.startup_error = line[len('WORKER_STARTUP_ERROR '):][:512]
        if record.status != 'starting':
            return
        if line.startswith('WORKER_PHASE '):
            message = json.loads(line[len('WORKER_PHASE '):])
            phases = ('booting', 'loading_scene', 'loading_robot', 'rtsp_publishing')
            phase = message.get('phase')
            if message.get('session_id') == record.sid and phase in phases and phases.index(phase) >= phases.index(record.phase):
                record.set_phase(phase)
        elif line.startswith('WORKER_READY '):
            message = json.loads(line[len('WORKER_READY '):])
            if message.get('session_id') == record.sid:
                record.runtime_ready.set()

    async def _initialize(self, record):
        if not (await self.gateway.health()).reachable:
            await self.gateway.recover()
        await self.gateway.add_path(record.sid, record.config.rtsp_port)
        await record.runtime_ready.wait()
        await record.client.handshake(timeout_s=self.settings.startup_timeout_s)
        for name, fields in [('load_scene', {'scene_id': record.request.scene_id}), ('load_robot', {'robot_id': record.request.robot_id})]:
            request = RPCRequest(session_id=record.sid, request_id=uuid.uuid4().hex, control_seq=0, playback_epoch=0,
                                 command={'cmd': name, **fields})
            response = await record.client.request(request, timeout_s=self.settings.load_timeout_s)
            if not response.ok:
                raise RuntimeError(response.error.message)

    async def _heartbeat(self, record):
        while not record.stop.is_set():
            began = time.monotonic()
            request = RPCRequest(session_id=record.sid, request_id='heartbeat-' + uuid.uuid4().hex,
                                 control_seq=0, playback_epoch=0, command={'cmd': 'heartbeat'})
            try:
                response = await record.client.request(request, timeout_s=self.settings.heartbeat_interval_s * .8)
                if response.ok:
                    record.last_heartbeat = time.monotonic()
            except (TimeoutError, WorkerClosed):
                pass
            await asyncio.sleep(max(0., self.settings.heartbeat_interval_s - (time.monotonic() - began)))

    async def _media_monitor(self, record):
        def observe(health):
            available = health.reachable and health.configured
            record.publishing = health.publishing and available
            if available != record.gateway_reachable:
                record.gateway_reachable = available
                record.emit('gateway_recovered' if available else 'gateway_unreachable', health.error)
            return available

        while not record.stop.is_set():
            health = await self.gateway.health(record.sid)
            if not observe(health):
                try:
                    if health.reachable:
                        await self.gateway.reconcile_path(record.sid)
                    else:
                        await self.gateway.recover()
                    observe(await self.gateway.health(record.sid))
                except (GatewayError, OSError):
                    await asyncio.sleep(1.)
            await asyncio.sleep(self.settings.gateway_poll_s)

    async def _run(self, record):
        tasks = []
        try:
            if record.stop.is_set():
                return
            record.lease.release(1)
            record.client = AsyncWorkerClient(record.config)
            launch = self.launcher(self.settings, record.config, record.output_dir / 'worker')
            record.lease.close()
            record.process = await ManagedProcess.spawn(launch.command, record.output_dir / 'worker.log',
                env=child_environment(launch.environment), cwd=ROOT, on_line=lambda line: self._line(record, line))
            initialize = asyncio.create_task(self._initialize(record)); tasks.append(initialize)
            while not record.stop.is_set():
                now = time.monotonic()
                if record.process.returncode is not None:
                    record.fail('WORKER_EXITED', record.startup_error or f'Worker exited with code {record.process.returncode}')
                    break
                if record.client.failure:
                    record.fail('WORKER_TRANSPORT_FAILED', record.client.failure)
                    break
                if record.status == 'starting':
                    if initialize.done():
                        initialize.result()
                        record.status = 'ready'; record.last_heartbeat = now
                        record.set_phase('ready')
                        tasks += [asyncio.create_task(self._heartbeat(record)), asyncio.create_task(self._media_monitor(record))]
                    elif now - record.started >= self.settings.startup_timeout_s:
                        record.fail('WORKER_STARTUP_TIMEOUT', 'Worker initialization exceeded its startup deadline')
                    elif record.phase != 'booting' and now - record.phase_started >= self.settings.load_timeout_s:
                        record.fail('WORKER_LOAD_TIMEOUT', 'Worker loading phase exceeded its deadline')
                elif record.status == 'ready' and now - record.last_heartbeat >= self.settings.heartbeat_timeout_s:
                    record.fail('WORKER_HEARTBEAT_TIMEOUT', 'Worker stopped responding to heartbeats')
                for event in record.client.drain_events():
                    if len(record.worker_events) == record.worker_events.maxlen:
                        record.dropped_events += 1
                    record.worker_events.append(event)
                    if event.event.type == 'error':
                        record.fail(event.event.error.code, event.event.error.message)
                    elif event.event.type == 'rendered':
                        record.capture_times.append(event.event.frame.captured_wall_s)
                await asyncio.sleep(.02)
        except asyncio.CancelledError:
            record.stop.set()
            raise
        except Exception as exc:
            record.fail('WORKER_START_FAILED', str(exc) or type(exc).__name__)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._cleanup(record)

    async def _cleanup(self, record):
        async with record.cleanup_lock:
            errors = []
            if record.client is not None:
                if record.runtime_ready.is_set() and record.process and record.process.returncode is None:
                    try:
                        await record.client.request(RPCRequest(session_id=record.sid, request_id='shutdown-' + uuid.uuid4().hex,
                            control_seq=0, playback_epoch=0, command={'cmd': 'shutdown'}), timeout_s=.2)
                    except Exception:
                        pass
                try:
                    await record.client.close()
                except Exception as exc:
                    errors.append('ZMQ cleanup: ' + str(exc))
            if record.process is not None:
                try:
                    await record.process.terminate(grace_s=self.settings.shutdown_grace_s)
                except Exception as exc:
                    errors.append('Worker cleanup: ' + str(exc))
            try:
                await self.gateway.remove_path(record.sid)
            except Exception as exc:
                errors.append('Media path cleanup: ' + str(exc))
            record.lease.close()
            record.worker_events.clear(); record.capture_times.clear(); record.publishing = False
            record.cleanup_errors = errors
            if errors:
                record.status = 'error'; record.phase = 'error'
                record.emit('cleanup_failed', '; '.join(errors))
            elif record.status != 'error':
                record.status = 'stopped'; record.set_phase('stopped')
            try:
                (record.output_dir / 'lifecycle-final.json').write_text(json.dumps({
                    'status': record.snapshot().model_dump(mode='json'), 'cleanup_errors': errors,
                    'ports': record.config.__dict__, 'worker_exit_code': record.process.returncode if record.process else None,
                }, indent=2))
            except OSError as exc:
                record.diagnostic_errors.append(str(exc))

    async def delete(self, sid):
        record = self.sessions.get(sid)
        if record is None:
            return
        task = record.deletion_task
        if task is None or (task.done() and record.cleanup_errors):
            task = asyncio.create_task(self._delete(record), name='delete-' + sid)
            record.deletion_task = task
            # The session owns finalization even when every caller is canceled.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        await asyncio.shield(task)

    async def _delete(self, record):
        sid = record.sid
        if record.status == 'stopped' and not record.cleanup_errors:
            if self.active_sid == sid:
                self.active_sid = None
            return
        record.status = 'stopping'; record.set_phase('stopping'); record.publishing = False
        record.stop.set()
        if record.task is not None:
            await asyncio.shield(record.task)
        if record.cleanup_errors:
            await self._cleanup(record)
        if record.cleanup_errors:
            raise SessionError(503, 'SESSION_CLEANUP_FAILED', '; '.join(record.cleanup_errors))
        record.status = 'stopped'; record.set_phase('stopped')
        if self.active_sid == sid:
            self.active_sid = None

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        self.closing = True
        errors = []
        try:
            if self.active_sid:
                try:
                    await self.delete(self.active_sid)
                except Exception as exc:
                    errors.append(str(exc))
            if self.started or self._lock_file is not None:
                try:
                    await self.gateway.close()
                except Exception as exc:
                    errors.append(str(exc))
        finally:
            if self._lock_file is not None:
                self._lock_file.close(); self._lock_file = None
            self.started = False
        if errors:
            raise SessionError(503, 'SERVICE_CLEANUP_FAILED', '; '.join(errors))

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()
