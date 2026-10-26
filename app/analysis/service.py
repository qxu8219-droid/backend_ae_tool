"""Prepared original-sample analysis, suitable for CPU execution outside the SDK."""
from app.schemas.rest import CurvePoint, CurveResponse, CurveSeries, LimitInterval
from .curves import reduce_points, window_points
from .events import HistoryIndex
from .models import AnalysisError


class ReplayAnalysis:
    def __init__(self, data, **options):
        self.history = HistoryIndex(data, **options)

    def curve(self, channels, *, from_s=0., to_s=None, max_points=2000):
        if not isinstance(channels, (list, tuple)) or not channels or any(not isinstance(name, str) for name in channels):
            raise AnalysisError('INVALID_CHANNELS', 'Expected a nonempty list of channel names')
        names = self.history._names(channels)
        if len(names) > 128:
            raise AnalysisError('INVALID_CHANNELS', 'At most 128 channels can be queried')
        if type(max_points) is not int or not 2 <= max_points <= 10000:
            raise AnalysisError('INVALID_CURVE_BUDGET', 'max_points must be an integer in 2..10000')
        start, end = self.history.time_range(from_s, to_s)
        series = []
        for name in names:
            points = reduce_points(window_points(self.history.channels[name], start, end), max_points)
            intervals = [LimitInterval(channel=name, from_s=max(start, event.from_s), to_s=min(end, event.to_s),
                                       severity=event.severity, code=event.code)
                         for event in self.history.overlapping(start, end, channels=[name])]
            series.append(CurveSeries(channel=name, unit=self.history.units[name],
                points=[CurvePoint(time_s=p.time_s, value=p.value, quality=p.quality) for p in points], limit_intervals=intervals))
        return CurveResponse(log_id=self.history.log_id, from_s=start, to_s=end, max_points=max_points, series=series)
