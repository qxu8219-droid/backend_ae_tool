"""Immutable source-sample event index; independent of replay/render cadence."""
from bisect import bisect_left, bisect_right
from dataclasses import asdict
import hashlib
import json
from types import MappingProxyType

from replay_protocol import CANONICAL_JOINTS, ReplayData
from replay_protocol.limits import DEFAULT_LIMIT_POLICY, LIMIT_POLICY_ID, assess_limit, finite, quality_eligible
from app.replay.channel import PreparedChannel
from app.replay.interpolator import _validate_units
from .metadata import MetadataArtifact, load_joint_metadata
from .models import AnalysisError, LimitEvent


class HistoryIndex:
    def __init__(self, data, *, metadata=None, policy=DEFAULT_LIMIT_POLICY, max_gap_s=None, max_events=200000):
        if type(max_events) is not int or max_events < 1:
            raise AnalysisError('INVALID_ANALYSIS_LIMIT', 'max_events must be a positive integer')
        if max_gap_s is not None:
            max_gap_s = finite(max_gap_s, 'max_gap_s')
            if max_gap_s <= 0:
                raise AnalysisError('INVALID_MAX_GAP', 'max_gap_s must be positive')
        source = ReplayData.model_validate(data.model_dump(mode='python'))
        from app.replay.gaps import effective_gap
        max_gap_s = effective_gap(source.mapping_snapshot, max_gap_s)
        artifact = metadata or load_joint_metadata()
        meta = type(artifact.metadata).model_validate(artifact.metadata.model_dump(mode='python'))
        artifact = MetadataArtifact(meta, artifact.source_sha256)
        if source.robot_id != meta.robot_id:
            raise AnalysisError('ROBOT_METADATA_MISMATCH', 'Log and limit metadata identify different robots')
        self.log_id = source.log_id
        self.duration_s = source.sample_times_s[-1] - source.time_basis.relative_origin_s
        self.policy, self.max_gap_s = policy, max_gap_s
        self.specs = artifact.specs()
        self.metadata_artifact = artifact
        channels, units, events = {}, {}, []
        identity = {'file': source.raw_file_sha256, 'mapping': source.mapping_snapshot_id,
                    'limits': artifact.source_sha256, 'policy': LIMIT_POLICY_ID, 'parameters': asdict(policy)}
        for name, series in source.channels.items():
            _validate_units(name, series)
            times = tuple(t - source.time_basis.relative_origin_s for t in series.timestamps_s)
            shifted = series.model_copy(update={'timestamps_s': list(times)})
            channels[name] = PreparedChannel(shifted, max_gap_s=max_gap_s)
            units[name] = series.unit
            if name not in CANONICAL_JOINTS:
                continue
            joint_index = CANONICAL_JOINTS.index(name)
            spec = self.specs[joint_index]
            if spec.kind == 'continuous':
                continue
            active = None

            def finish(reason, exit_s=None):
                nonlocal active
                if active is None:
                    return
                first, last, peak, severity, side, _origin, _method = active
                event_key = {**identity, 'channel': name, 'first': first, 'last': last,
                             'severity': severity, 'side': side, 'origin': _origin, 'method': _method}
                event_id = 'limit-' + hashlib.sha256(json.dumps(event_key, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                events.append(LimitEvent(event_id=event_id, log_id=source.log_id, channel=name, joint_index=joint_index,
                    unit=spec.unit, severity=severity, side=side, from_s=times[first], last_observed_s=times[last],
                    exit_s=exit_s, closed_by=reason, peak_time_s=times[peak], peak_value=series.values[peak],
                    peak_quality=series.quality[peak], first_channel_index=first, last_channel_index=last,
                    lower=spec.lower, upper=spec.upper, raw_file_ref=source.raw_file_ref,
                    raw_file_sha256=source.raw_file_sha256, mapping_snapshot_id=source.mapping_snapshot_id,
                    limits_source=meta.limits_source, limits_source_sha256=artifact.source_sha256, policy_id=LIMIT_POLICY_ID))
                if len(events) > max_events:
                    raise AnalysisError('ANALYSIS_LIMIT_EXCEEDED', 'Original-sample event index exceeds configured capacity', status_code=413)
                active = None

            for index, (time, value, quality) in enumerate(zip(times, series.values, series.quality)):
                if index and max_gap_s is not None and time - times[index - 1] > max_gap_s:
                    finish('gap')
                result = assess_limit(value, quality, spec, policy)
                key = (result.state, result.side, quality.origin, quality.method)
                if active is not None and tuple(active[3:]) != key:
                    observed = value is not None and quality_eligible(quality)
                    if not observed:
                        finish('missing')
                    elif result.state == 'normal':
                        finish('normal', time)
                    elif tuple(active[3:5]) != key[:2]:
                        finish('transition', time)
                    else:
                        finish('provenance_change', time)
                if result.state in ('warning', 'error'):
                    if active is None:
                        active = [index, index, index, *key]
                    else:
                        active[1] = index
                        peak_value = series.values[active[2]]
                        if (result.side == 'lower' and value < peak_value) or (result.side == 'upper' and value > peak_value):
                            active[2] = index
            finish('end')
        self.channels = MappingProxyType(channels)
        self.units = MappingProxyType(units)
        self.events = tuple(sorted(events, key=lambda event: (event.from_s, event.joint_index, event.event_id)))
        self._starts = tuple(event.from_s for event in self.events)
        self._by_channel = {name: tuple(event for event in self.events if event.channel == name) for name in channels}
        self._ends = {name: tuple(event.to_s for event in items) for name, items in self._by_channel.items()}

    def time_range(self, start, end=None):
        start = finite(start, 'from_s')
        end = self.duration_s if end is None else finite(end, 'to_s')
        if not 0 <= start <= end <= self.duration_s:
            raise AnalysisError('INVALID_ANALYSIS_RANGE', 'Expected 0 <= from_s <= to_s <= log duration')
        return start, end

    def _names(self, channels):
        if channels is None:
            return tuple(self.channels)
        if isinstance(channels, str) or not channels:
            raise AnalysisError('INVALID_CHANNELS', 'Expected a nonempty channel list')
        names = tuple(dict.fromkeys(channels))
        if any(name not in self.channels for name in names):
            raise AnalysisError('UNKNOWN_CHANNEL', 'A requested channel is not registered in this log', status_code=404)
        return names

    @staticmethod
    def _bounded(events, max_results):
        if type(max_results) is not int or not 1 <= max_results <= 200000:
            raise AnalysisError('INVALID_EVENT_BUDGET', 'Event query budget must be 1..200000')
        if len(events) > max_results:
            raise AnalysisError('EVENT_QUERY_TOO_LARGE', 'Narrow the event query or raise its explicit result budget', status_code=413)
        return tuple(sorted(events, key=lambda event: (event.from_s, event.joint_index, event.event_id)))

    def overlapping(self, from_s, to_s=None, *, channels=None, max_results=10000):
        start, end = self.time_range(from_s, to_s)
        result = []
        for name in self._names(channels):
            items = self._by_channel[name]
            position = bisect_left(self._ends[name], start)
            for event in items[position:]:
                if event.from_s > end:
                    break
                if event.intersects(start, end):
                    result.append(event)
        return self._bounded(result, max_results)

    def entered_between(self, from_s, to_s, *, include_start=False, max_results=10000):
        start, end = self.time_range(from_s, to_s)
        first = (bisect_left if include_start else bisect_right)(self._starts, start)
        last = bisect_right(self._starts, end)
        return self._bounded(self.events[first:last], max_results)
