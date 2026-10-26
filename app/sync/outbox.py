import asyncio
from collections import deque
from copy import deepcopy
import time
import uuid


class ConnectionClosed(Exception):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(reason)


class Outbox:
    def __init__(self, session_id, *, capacity=64):
        if type(capacity) is not int or not 2 <= capacity <= 1024:
            raise ValueError('WS queue capacity must be 2..1024')
        self.session_id = session_id
        self.connection_id = 'connection-' + uuid.uuid4().hex
        self.capacity = capacity
        self.rate = 20
        self.next_health = 0.
        self._queue = deque()
        self._available = asyncio.Event()
        self._closed = None
        self._seq = 0

    @property
    def pending_count(self):
        return len(self._queue)

    @property
    def closed(self):
        return self._closed is not None

    def close(self, code=1001, reason='SESSION_CLOSED'):
        if self._closed is None:
            self._closed = ConnectionClosed(code, reason)
            self._queue.clear()
            self._available.set()

    def offer(self, kind, data):
        if self.closed:
            return
        if kind in ('health_telemetry', 'playback_state'):
            self._queue = deque(item for item in self._queue if item[0] != kind)
        if len(self._queue) >= self.capacity:
            self.close(1013, 'SLOW_CONSUMER')
            return
        self._queue.append((kind, deepcopy(data)))
        self._available.set()

    def reset(self, snapshot):
        if self.closed:
            return
        playback = snapshot['playback']
        retained = [item for item in self._queue if item[0] in ('pong', 'telemetry_rate_changed') or
                    (item[0] == 'alert' and item[1].get('log_id') == playback['log_id'] and
                     item[1].get('playback_epoch') == playback['playback_epoch'])]
        self._queue.clear()
        self.offer('connection_snapshot', snapshot)
        for kind, data in retained:
            self.offer(kind, data)

    async def next(self):
        while True:
            if self._closed is not None:
                raise self._closed
            if self._queue:
                if self._seq >= 2**53 - 1:
                    self.close(1013, 'SEQUENCE_EXHAUSTED')
                    continue
                kind, data = self._queue.popleft()
                self._seq += 1
                return {'schema_version': 1, 'session_id': self.session_id, 'connection_id': self.connection_id,
                        'seq': self._seq, 'wall_time': time.time(), 'type': kind, 'data': data}
            self._available.clear()
            await self._available.wait()
