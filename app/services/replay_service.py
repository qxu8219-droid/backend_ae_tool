"""Configured business service: original logs, timeline actor, metadata and WS."""
import asyncio
import uuid

from pydantic import ValidationError

from app.analysis.metadata import load_joint_metadata
from app.analysis.cursor import RenderedEventCursor
from app.core.session_manager import SessionError
from app.schemas.rest import LogAnnotations, RESTPlaybackResponse, Scene, SceneList, Robot, RobotList, USDVariant
from app.sync.outbox import Outbox
from .prepared import prepare_log
from .annotation_bounds import source_frame_end_s
from .replay_session import ReplaySession
from replay_protocol.limits import finite


class ReplayService:
    def __init__(self, manager, logs, *, max_gap_s=None, allowed_origins=None):
        self.manager, self.logs = manager, logs
        self.metadata = load_joint_metadata()
        self.max_gap_s = max_gap_s
        if max_gap_s is not None and finite(max_gap_s, 'max_gap_s') <= 0:
            raise ValueError('max_gap_s must be positive')
        self.allowed_origins = tuple(allowed_origins or ('http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8768'))
        self.contexts = {}
        self._closing = False
        self._close_task = None

    async def __aenter__(self):
        await self.manager.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    def context(self, sid, *, ready=False):
        if sid not in self.contexts:
            raise SessionError(404, 'SESSION_NOT_FOUND', 'Session does not exist')
        context = self.contexts[sid]
        if context.closed:
            raise SessionError(409, 'SESSION_STOPPING', 'Session is being deleted')
        status = self.manager.get(sid)
        if ready and (status.status == 'error' or context.timeline.error is not None):
            raise SessionError(409, 'SESSION_ERROR', 'Delete and recreate the failed session before reloading')
        if ready and status.status != 'ready':
            raise SessionError(503, 'WORKER_NOT_READY', 'Worker is not ready')
        return context

    def scenes(self):
        return SceneList(scenes=[Scene(id='empty', name='空场景', source='builtin', description='固定地面与第三人称相机')])

    def robots(self):
        return RobotList(robots=[Robot(id=self.metadata.metadata.robot_id, name='Galaxea R1 Pro 2026',
            usd_variants=[USDVariant(isaacsim_version='6.2.0', usd_path='assets/robots/r1pro_2026/isaacsim_6_x/r1pro_2026.usd')])])

    def joints(self, robot_id):
        if robot_id != self.metadata.metadata.robot_id:
            raise SessionError(404, 'ROBOT_NOT_FOUND', 'Robot is not registered')
        return self.metadata.metadata.model_copy(deep=True)

    async def create(self, request):
        if self._closing:
            raise SessionError(503, 'SERVICE_CLOSED', 'Service is closing')
        created = await self.manager.create(request)
        self.contexts[created.session_id] = ReplaySession(self, created.session_id)
        return created

    def playback(self, sid):
        return self.context(sid).timeline.snapshot()

    def status(self, sid):
        if sid not in self.contexts:
            raise SessionError(404, 'SESSION_NOT_FOUND', 'Session does not exist')
        return self.manager.get(sid)

    async def upload(self, sid, chunks, filename, **options):
        context = self.context(sid, ready=True)
        result = await self.logs.import_file(sid, chunks, filename, **options)
        if context.closed:
            raise SessionError(409, 'SESSION_STOPPING', 'Session closed during import')
        return result

    async def prepared_log(self, context, lid):
        async with context.prepare_lock:
            if context.closed:
                raise SessionError(409, 'SESSION_STOPPING', 'Session is being deleted')
            if lid in context.prepared:
                context.prepared.move_to_end(lid)
                return context.prepared[lid]
            data = await self.logs.get(context.sid, lid)
            job = asyncio.create_task(asyncio.to_thread(prepare_log, data, context.sid, self.metadata, self.max_gap_s))
            context.prepare_jobs.add(job)
            try:
                result = await asyncio.shield(job)
                if context.closed:
                    raise SessionError(409, 'SESSION_STOPPING', 'Session closed during preparation')
                context.prepared[lid] = result
                while len(context.prepared) > 2:
                    context.prepared.popitem(last=False)
                return result
            except asyncio.CancelledError:
                await asyncio.gather(job, return_exceptions=True)
                raise
            finally:
                context.prepare_jobs.discard(job)

    async def curve(self, sid, lid, channels, **query):
        context = self.context(sid)
        prepared = await self.prepared_log(context, lid)
        result = await asyncio.to_thread(prepared.analysis.curve, channels, **query)
        if context.closed:
            raise SessionError(409, 'SESSION_STOPPING', 'Session closed during query')
        return result

    async def channels(self, sid, lid):
        self.context(sid)
        return await self.logs.channels(sid, lid)

    async def annotations(self, sid, lid):
        context = self.context(sid)
        data = await self.logs.get(sid, lid)
        if context.closed:
            raise SessionError(409, 'SESSION_STOPPING', 'Session closed during query')
        if 'log_annotations' not in data.metadata:
            return LogAnnotations(log_id=lid, source_id=None, source_sha256=data.raw_file_sha256,
                alignment='none', provisional=False, assumptions=[], reference={}, segments=[], events=[])
        try:
            value = data.metadata['log_annotations']
            if not isinstance(value, dict) or 'log_id' in value:
                raise ValueError('archive annotations must be an inline object without log identity')
            result = LogAnnotations.model_validate({**value, 'log_id': lid})
            times = data.sample_times_s
            duration = times[-1]
            terminal_interval = times[-1] - times[-2] if len(times) > 1 else 0.
            end_bound = source_frame_end_s(data) if result.segments else None
            if end_bound is None:
                end_bound = duration + terminal_interval
            tolerance = 1e-8
            if any(segment.start_s > duration + tolerance or segment.end_s > end_bound + tolerance
                   for segment in result.segments):
                raise ValueError('annotation time lies outside the source domain')
            source = data.metadata.get('b041', {})
            count = source.get('source_frame_count') if isinstance(source, dict) else None
            if result.segments and (type(count) is not int or count <= 0 or
                    any(segment.end_frame > count for segment in result.segments)):
                raise ValueError('annotation frame lies outside the verified source domain')
            if any(event.time_s is not None and event.time_s > duration + tolerance for event in result.events):
                raise ValueError('recorded event time lies outside the replay domain')
            return result
        except (ValueError, ValidationError):
            # Do not echo arbitrary metadata, file paths, or large validation inputs.
            raise SessionError(422, 'INVALID_LOG_ANNOTATIONS', 'Stored annotations violate their contract or source bounds') from None

    async def control(self, sid, command, body=None):
        context = self.context(sid, ready=True)
        if context.pending_controls >= 32:
            raise SessionError(409, 'CONTROL_BUSY', 'Too many playback controls are waiting')
        context.pending_controls += 1
        try:
            async with context.control_lock:
                self.context(sid, ready=True)
                payload = body.model_dump(exclude_unset=True) if hasattr(body, 'model_dump') else dict(body or {})
                request_id = payload.pop('request_id', None) or uuid.uuid4().hex
                if command == 'load':
                    prepared = await self.prepared_log(context, payload['log_id'])
                    self.context(sid, ready=True)
                    snapshot = context.timeline.load(payload['log_id'])
                    context.current = prepared
                    context.cursor = RenderedEventCursor(prepared.analysis.history, session_id=sid)
                else:
                    method = {'play': 'play', 'pause': 'pause', 'stop': 'stop', 'seek': 'seek', 'step': 'step', 'speed': 'set_speed'}.get(command)
                    if method is None:
                        raise SessionError(400, 'INVALID_CONTROL', 'Unknown playback control')
                    snapshot = getattr(context.timeline, method)(**payload)
                context.accepted_control(command, request_id)
                return RESTPlaybackResponse(request_id=request_id, control_seq=snapshot.control_seq,
                    playback_epoch=snapshot.playback_epoch, playback=snapshot)
        finally:
            context.pending_controls -= 1

    def subscribe(self, sid):
        context = self.context(sid)
        context.clients = {client for client in context.clients if not client.closed}
        if len(context.clients) >= 8:
            raise SessionError(409, 'WS_CAPACITY', 'At most eight WebSocket clients are supported')
        client = Outbox(sid)
        client.offer('connection_snapshot', context.connection_snapshot(client.rate))
        context.clients.add(client)
        return client

    def unsubscribe(self, sid, client):
        client.close(1000, 'CLIENT_CLOSED')
        if sid in self.contexts:
            self.contexts[sid].clients.discard(client)

    async def delete(self, sid):
        context = self.contexts.get(sid)
        if context is None:
            await self.manager.delete(sid)
            return
        task = context.delete_task
        if task is None or (task.done() and task.exception() is not None):
            context.delete_task = task = asyncio.create_task(self._delete(context))
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        await asyncio.shield(task)

    async def _delete(self, context):
        await context.stop()
        for client in context.clients:
            client.close(1001, 'SESSION_DELETED')
        errors = []
        for operation in (self.manager.delete(context.sid), self.logs.delete_session(context.sid)):
            try:
                await operation
            except Exception as exc:
                errors.append(exc)
        await asyncio.gather(*tuple(context.prepare_jobs), return_exceptions=True)
        context.prepared.clear(); context.current = context.cursor = None
        if errors:
            raise errors[0]
        self.contexts.pop(context.sid, None)

    async def close(self):
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        errors = []
        for sid in tuple(self.contexts):
            try:
                await self.delete(sid)
            except Exception as exc:
                errors.append(exc)
        try:
            await self.manager.close()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise errors[0]
