"""Single-loop actor connects Timeline delivery to actual worker evidence."""
import asyncio
from collections import OrderedDict
import time

from app.analysis.cursor import RenderedEventCursor
from app.replay.timeline import Timeline
from app.schemas.ws import ConnectionSnapshotData, SyncStatus
from app.sync.frames import FrameCache
from replay_protocol import FrameIdentity


class ReplaySession:
    def __init__(self, service, sid):
        self.service, self.sid = service, sid
        self.prepared = OrderedDict()
        self.current = self.cursor = None
        self.timeline = Timeline(sid, lambda lid: self.prepared[lid].sampler,
                                 ack_timeout_s=service.manager.settings.rpc_timeout_s)
        self.frames = FrameCache(service.metadata.specs())
        self.frames.context(sid, None, 0, 0)
        self.clients = set()
        self.control_lock = asyncio.Lock()
        self.prepare_lock = asyncio.Lock()
        self.pending_controls = 0
        self.prepare_jobs = set()
        self.rpc_tasks = set()
        self.closed = False
        self.delete_task = None
        self.sync_state = 'awaiting_pair'
        self.last_frame_at = time.monotonic()
        self.last_worker_seq = self.event_loss = 0
        self._state_signature = None
        self._video_signature = None
        self.media_fault = False
        self.task = asyncio.create_task(self.run(), name='replay-' + sid)

    def connection_snapshot(self, rate=20):
        status = self.service.manager.get(self.sid)
        frame = self.frames.latest
        identity = FrameIdentity.model_validate({k: getattr(frame, k) for k in FrameIdentity.model_fields}) if frame else None
        return ConnectionSnapshotData(joint_meta=self.service.metadata.metadata, playback=self.timeline.snapshot(),
            worker=status.worker, video=status.video, telemetry_hz=rate,
            sync=SyncStatus(state=self.sync_state, playback_epoch=self.timeline.snapshot().playback_epoch,
                            last_rendered_identity=identity)).model_dump(mode='json')

    def broadcast(self, kind, data):
        if hasattr(data, 'model_dump'):
            data = data.model_dump(mode='json')
        for client in tuple(self.clients):
            if client.closed:
                self.clients.discard(client)
            else:
                client.offer(kind, data)

    def resync(self, state='resync_required', reason='CONTEXT_CHANGED'):
        if self.media_fault:
            state = 'stalled'
        self.sync_state = state
        self.frames.clear()
        for client in self.clients:
            client.reset(self.connection_snapshot(client.rate))
        self.broadcast('system', {'event': 'sync_resync_required', 'message': reason})

    def publish_state(self, reason, request_id=None):
        snapshot = self.timeline.snapshot()
        self.broadcast('playback_state', {'playback': snapshot.model_dump(mode='json'), 'reason': reason, 'request_id': request_id})

    def accepted_control(self, reason, request_id):
        snapshot = self.timeline.snapshot()
        for task in tuple(self.rpc_tasks):
            task.cancel()
        self.frames.context(self.sid, snapshot.log_id, snapshot.playback_epoch, snapshot.control_seq)
        self.last_frame_at = time.monotonic()
        self.resync('awaiting_pair' if reason == 'load' else 'resync_required', reason)
        self.publish_state(reason, request_id)

    def rendered(self, frame):
        snapshot = self.frames.add(frame)
        if snapshot is None:
            return
        self.last_frame_at = time.monotonic()
        if not self.media_fault and self.sync_state != 'awaiting_pair':
            self.sync_state = 'awaiting_pair'
            for client in self.clients:
                client.reset(self.connection_snapshot(client.rate))
        self.broadcast('frame_snapshot', snapshot)
        if self.cursor is not None:
            for alert in self.cursor.advance(frame):
                self.broadcast('alert', alert)

    async def send_rpc(self, request):
        try:
            response = await self.service.manager.rpc(self.sid, request)
            accepted = self.timeline.receive_rpc(response)
            if accepted and response.frame is not None and response.stage == 'rendered':
                self.rendered(response.frame)
            if accepted:
                self.publish_state('worker_' + response.stage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state = self.timeline.snapshot()
            if (request.control_seq, request.playback_epoch) == (state.control_seq, state.playback_epoch) and not self.closed:
                self.fail(getattr(exc, 'code', 'WORKER_TRANSPORT_FAILED'), str(exc))

    def fail(self, code, message):
        if self.timeline.error is None:
            self.timeline.worker_failed(code, message)
            self.service.manager.fail_session(self.sid, code, message)
            self.resync('stalled', code)
            self.publish_state('session_error')

    async def run(self):
        try:
            while not self.closed:
                status = self.service.manager.get(self.sid)
                for event in self.service.manager.drain_events(self.sid):
                    kind = {'gateway_unreachable': 'mediamtx_unreachable', 'gateway_recovered': 'mediamtx_recovered',
                            'cleanup_failed': 'session_error'}.get(event.kind, event.kind)
                    self.broadcast('system', {'event': kind, 'worker': event.status.worker.model_dump(mode='json'),
                                              'error': event.status.worker.error.model_dump(mode='json') if event.status.worker.error else None,
                                              'message': event.message})
                    if kind in ('mediamtx_unreachable', 'mediamtx_recovered'):
                        self.media_fault = kind == 'mediamtx_unreachable'
                        self.resync('stalled' if kind == 'mediamtx_unreachable' else 'resync_required', kind)
                video_signature = (status.video.publishing, status.video.whep_url, status.video.stream_path)
                if video_signature != self._video_signature:
                    self._video_signature = video_signature
                    for client in self.clients:
                        client.reset(self.connection_snapshot(client.rate))
                if status.status == 'error':
                    error = status.worker.error
                    self.fail(error.code if error else 'WORKER_FAILED', error.message if error else 'Worker failed')
                loss = self.service.manager.event_loss_count(self.sid)
                if loss != self.event_loss:
                    self.event_loss = loss
                    self.resync(reason='WORKER_EVENT_LOSS')
                for event in self.service.manager.drain_worker_events(self.sid):
                    if event.seq > self.last_worker_seq + 1:
                        self.resync(reason='WORKER_EVENT_GAP')
                    self.last_worker_seq = max(self.last_worker_seq, event.seq)
                    if self.timeline.receive_event(event):
                        if event.event.type == 'rendered':
                            self.rendered(event.event.frame)
                        if event.event.type in ('applied', 'rendered'):
                            self.publish_state('worker_' + event.event.type)
                if status.status == 'ready' and self.timeline.error is None:
                    self.timeline.tick()
                    request = self.timeline.take_rpc()
                    if request is not None:
                        task = asyncio.create_task(self.send_rpc(request))
                        self.rpc_tasks.add(task); task.add_done_callback(self.rpc_tasks.discard)
                    target = self.timeline.take_target()
                    if target is not None:
                        await self.service.manager.publish(self.sid, target)
                if self.timeline.error is not None and status.status == 'ready':
                    self.service.manager.fail_session(self.sid, self.timeline.error.code, self.timeline.error.message)
                snapshot = self.timeline.snapshot()
                signature = (snapshot.log_id, snapshot.playback_epoch, snapshot.control_seq, snapshot.state, snapshot.speed)
                if signature != self._state_signature:
                    self._state_signature = signature
                    self.publish_state('timeline')
                now = time.monotonic()
                if snapshot.log_id and now - self.last_frame_at > 2. and self.sync_state != 'stalled':
                    self.resync('stalled', 'FRAME_SNAPSHOT_STALLED')
                for client in tuple(self.clients):
                    if client.closed:
                        self.clients.discard(client)
                    elif now >= client.next_health:
                        client.next_health = now + 1 / client.rate
                        client.offer('health_telemetry', {'worker_alive': status.worker.pid is not None and status.status in ('starting', 'ready'),
                                                        'render_fps': status.render_fps})
                await asyncio.sleep(1 / 120)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fail('REPLAY_ACTOR_FAILED', str(exc) or type(exc).__name__)
        finally:
            for task in self.rpc_tasks:
                task.cancel()
            await asyncio.gather(*tuple(self.rpc_tasks), return_exceptions=True)

    async def stop(self):
        self.closed = True
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.frames.clear()
