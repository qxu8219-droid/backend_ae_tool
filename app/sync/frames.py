from collections import OrderedDict
import time

from replay_protocol import FrameSnapshot, RenderedFrame
from replay_protocol.limits import limit_flags


class FrameCache:
    def __init__(self, specs, *, capacity=16, ttl_s=2., clock=time.monotonic):
        if type(capacity) is not int or not 1 <= capacity <= 16 or not 0 < ttl_s <= 10:
            raise ValueError('Frame cache must be bounded to 16 entries and at most 10s')
        self.specs, self.capacity, self.ttl_s, self.clock = tuple(specs), capacity, ttl_s, clock
        self._context = None
        self._floor = 0
        self._frames = OrderedDict()

    def context(self, sid, lid, epoch, control):
        value = (sid, lid, epoch, control)
        if value != self._context:
            if self._context is None or sid != self._context[0]:
                self._floor = 0
            self._frames.clear()
            self._context = value

    def clear(self):
        self._frames.clear()

    def _expire(self):
        now = self.clock()
        while self._frames and now - next(iter(self._frames.values()))[0] >= self.ttl_s:
            self._frames.popitem(last=False)

    @property
    def capture_ids(self):
        self._expire()
        return tuple(self._frames)

    @property
    def latest(self):
        self._expire()
        return next(reversed(self._frames.values()))[1] if self._frames else None

    def add(self, frame):
        frame = RenderedFrame.model_validate(frame)
        if (frame.session_id, frame.log_id, frame.playback_epoch, frame.control_seq) != self._context or frame.capture_id <= self._floor:
            return None
        flags, warns = limit_flags(frame.joints, frame.quality, self.specs)
        payload = {name: getattr(frame, name) for name in FrameSnapshot.model_fields if hasattr(frame, name)}
        snapshot = FrameSnapshot(**payload, limit_flags=flags, limit_warn_flags=warns)
        self._floor = frame.capture_id
        self._frames[frame.capture_id] = (self.clock(), snapshot)
        while len(self._frames) > self.capacity:
            self._frames.popitem(last=False)
        self._expire()
        return snapshot
