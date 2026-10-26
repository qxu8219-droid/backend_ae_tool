"""Async ZMQ owner: independent response/event pumps keep heartbeats responsive."""
import asyncio
from collections import deque
import math
import uuid

import zmq
import zmq.asyncio
from replay_protocol import RPCRequest, RPCResponse, TargetMessage, WorkerEvent
from sim_worker.transport import MAX_MESSAGE_BYTES, _socket


class WorkerClosed(RuntimeError):
    pass


def valid_timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Timeout must be positive and finite')
    return value


class AsyncWorkerClient:
    def __init__(self, config):
        self.config = config
        self.context = zmq.asyncio.Context()
        self.sockets = []
        self.pending = {}
        self.events = deque(maxlen=128)
        self.dropped_events = 0
        self.invalid_responses = 0
        self.failure = None
        self.closed = False
        self.subscribed = asyncio.Event()
        try:
            self.targets = _socket(self.context, zmq.XPUB); self.sockets.append(self.targets)
            self.targets.setsockopt(zmq.XPUB_VERBOSE, 1)
            self.targets.bind(config.endpoint('target'))
            self.rpc = _socket(self.context, zmq.DEALER); self.sockets.append(self.rpc)
            self.rpc.connect(config.endpoint('rpc'))
            self.telemetry = _socket(self.context, zmq.SUB); self.sockets.append(self.telemetry)
            self.telemetry.setsockopt(zmq.SUBSCRIBE, config.topic('events'))
            self.telemetry.connect(config.endpoint('event'))
        except BaseException:
            for sock in self.sockets:
                sock.close(0)
            self.context.term()
            raise
        self.pumps = [asyncio.create_task(self._pump(kind)) for kind in ('rpc', 'events', 'subscriptions')]

    @property
    def pending_count(self):
        return len(self.pending)

    def _ensure_open(self):
        if self.closed or self.failure:
            raise WorkerClosed(self.failure or 'Worker transport is closed')

    async def _pump(self, kind):
        try:
            while not self.closed:
                if kind == 'subscriptions':
                    message = await self.targets.recv()
                    if len(message) > 1 and message[1:] == self.config.topic('target'):
                        if message[0]:
                            self.subscribed.set()
                        else:
                            self.subscribed.clear()
                elif kind == 'rpc':
                    raw = await self.rpc.recv()
                    try:
                        if len(raw) > MAX_MESSAGE_BYTES:
                            raise ValueError('RPC response exceeds limit')
                        response = RPCResponse.model_validate_json(raw)
                        pending = self.pending.get(response.request_id)
                        if pending is None:
                            continue
                        request, stage, future = pending
                        if (response.session_id, response.control_seq, response.playback_epoch) != (
                            request.session_id, request.control_seq, request.playback_epoch
                        ):
                            raise ValueError('RPC response context mismatch')
                        if not future.done() and (not response.ok or response.stage == stage):
                            future.set_result(response)
                    except ValueError:
                        self.invalid_responses += 1
                else:
                    parts = await self.telemetry.recv_multipart()
                    try:
                        if len(parts) != 2 or parts[0] != self.config.topic('events') or len(parts[1]) > MAX_MESSAGE_BYTES:
                            raise ValueError('Invalid event envelope')
                        event = WorkerEvent.model_validate_json(parts[1])
                        if event.session_id != self.config.session_id:
                            raise ValueError('Wrong event session')
                        if len(self.events) == self.events.maxlen:
                            self.dropped_events += 1
                        self.events.append(event)
                    except ValueError:
                        self.invalid_responses += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = str(exc) or type(exc).__name__
            self._fail_pending(self.failure)
            self.subscribed.set()

    def _fail_pending(self, reason):
        for _, _, future in self.pending.values():
            if not future.done():
                future.set_exception(WorkerClosed(reason))

    async def request(self, request, *, timeout_s=10., expected_stage=None):
        self._ensure_open()
        valid_timeout(timeout_s)
        request = RPCRequest.model_validate(request)
        if request.session_id != self.config.session_id:
            raise ValueError('Wrong request session')
        stage = expected_stage or ('rendered' if request.command.cmd == 'apply_keyframe' else 'accepted')
        if stage not in ('accepted', 'applied', 'rendered'):
            raise ValueError('Unknown confirmation stage')
        if request.request_id in self.pending:
            raise ValueError('Request is already pending')
        if len(self.pending) >= 64:
            raise RuntimeError('Worker pending request capacity reached')
        future = asyncio.get_running_loop().create_future()
        self.pending[request.request_id] = (request, stage, future)
        try:
            raw = request.model_dump_json().encode()
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError('RPC request exceeds limit')
            await self.rpc.send(raw, flags=zmq.NOBLOCK)
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self.pending.pop(request.request_id, None)
            if not future.done():
                future.cancel()

    async def handshake(self, *, timeout_s=150.):
        valid_timeout(timeout_s)
        async with asyncio.timeout(timeout_s):
            while True:
                self._ensure_open()
                await self.subscribed.wait()
                self._ensure_open()
                request = RPCRequest(session_id=self.config.session_id, request_id='subscribe-' + uuid.uuid4().hex,
                                     control_seq=0, playback_epoch=0,
                                     command={'cmd': 'subscribe_ready', 'channels': ['target', 'telemetry']})
                try:
                    result = await self.request(request, timeout_s=1.)
                    if result.ok:
                        return result
                except TimeoutError:
                    pass
                await asyncio.sleep(.02)

    async def publish(self, target):
        self._ensure_open()
        target = TargetMessage.model_validate(target)
        if target.frame.session_id != self.config.session_id:
            raise ValueError('Wrong target session')
        if not self.subscribed.is_set():
            raise WorkerClosed('Worker target subscription is not ready')
        raw = target.model_dump_json().encode()
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError('Target exceeds message limit')
        await self.targets.send_multipart([self.config.topic('target'), raw], flags=zmq.NOBLOCK)

    def drain_events(self):
        result = list(self.events)
        self.events.clear()
        return result

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self._fail_pending('Worker transport closed')
        self.subscribed.set()
        for task in self.pumps:
            task.cancel()
        await asyncio.gather(*self.pumps, return_exceptions=True)
        for sock in self.sockets:
            sock.close(0)
        self.context.term()
        self.events.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
