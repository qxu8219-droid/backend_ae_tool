"""Rendered progress produces alert candidates; browser presentation gates release."""
from replay_protocol import RenderedFrame
from app.schemas.ws import AlertData
from .models import AnalysisError


class RenderedEventCursor:
    def __init__(self, index, *, session_id):
        self.index, self.session_id = index, session_id
        self._capture = 0
        self._epoch = self._control = None
        self._time = None

    def advance(self, frame):
        frame = RenderedFrame.model_validate(frame)
        if frame.session_id != self.session_id or frame.log_id != self.index.log_id:
            raise AnalysisError('EVENT_CONTEXT_MISMATCH', 'Rendered frame belongs to another session or log')
        self.index.time_range(frame.log_time_s, frame.log_time_s)
        if frame.capture_id <= self._capture or (self._epoch is not None and
                (frame.playback_epoch < self._epoch or frame.control_seq < self._control)):
            return ()
        new_epoch = self._epoch is None or frame.playback_epoch != self._epoch
        if not new_epoch and frame.log_time_s < self._time:
            raise AnalysisError('EVENT_TIME_REGRESSION', 'Backward replay must start a new playback epoch')
        events = self.index.entered_between(frame.log_time_s if new_epoch else self._time,
                                           frame.log_time_s, include_start=new_epoch)
        # No peak/exit information from later samples is sent as a current alert.
        alerts = tuple(AlertData(alert_id=f'{event.event_id}-e{frame.playback_epoch}', severity=event.severity,
            source='limits', code=event.code, message=f'{event.channel}: {event.code} at observed log time {event.from_s:g}s',
            log_id=event.log_id, playback_epoch=frame.playback_epoch, log_time_s=event.from_s,
            joint_index=event.joint_index, details={'history_event_id': event.event_id,
                                                  'origin': event.peak_quality.origin}) for event in events)
        self._capture, self._epoch, self._control, self._time = frame.capture_id, frame.playback_epoch, frame.control_seq, frame.log_time_s
        return alerts
