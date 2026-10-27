"""Prepared, seek-order-independent kinematic targets from normalized source data."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
import math
from types import MappingProxyType

from pydantic import ValidationError
from replay_protocol import CANONICAL_JOINTS, ChannelQuality, ChannelSeries, ChassisPose, ReplayData, TargetFrame
from app.adapters.base import MappingError
from app.adapters.mapping import parse_mapping
from .base import SamplingError
from .channel import PreparedChannel


def _time(value) -> float:
    if type(value) not in (int, float):
        raise SamplingError('INVALID_SAMPLE_TIME', 'Playback time must be a finite number, not a boolean or string')
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise SamplingError('INVALID_SAMPLE_TIME', 'Playback time is not representable') from exc
    if not math.isfinite(result):
        raise SamplingError('INVALID_SAMPLE_TIME', 'Playback time must be finite')
    return result


def _validate_units(name: str, channel: ChannelSeries):
    if name in CANONICAL_JOINTS:
        if 'gripper' in name:
            unit, semantics = 'm', ('linear',)
        elif name.startswith('wheel_motor_joint'):
            unit, semantics = 'rad', ('angular_wrapped','angular_unwrapped')
        else:
            unit, semantics = 'rad', ('bounded',)
    elif name in ('chassis.x','chassis.y'):
        unit, semantics = 'm', ('linear',)
    elif name == 'chassis.yaw':
        unit, semantics = 'rad', ('angular_wrapped','angular_unwrapped')
    else:
        return
    if channel.unit != unit or channel.angle_semantics not in semantics:
        raise SamplingError('INVALID_CHANNEL_UNIT', f'{name} must use {unit} and compatible angular semantics')
    if channel.angle_semantics == 'angular_wrapped' and channel.wrap_period != 2*math.pi:
        raise SamplingError('INVALID_WRAP_PERIOD', f'{name} radians require an explicitly declared 2π period')


class ReplaySampler:
    """Snapshot once at load; queries are O(channels × log(samples)) and have no clock state.

    Source clocks include relative_origin_s. Public playback positions start at
    zero. Chassis values have already been transformed to world by the importer.
    A missing chassis is unavailable, never an invented valid world pose.
    """
    def __init__(self, data: ReplayData, *, session_id: str, max_gap_s: float | None = None):
        if type(session_id) is not str or not 1 <= len(session_id) <= 256:
            raise SamplingError('INVALID_SESSION_ID', 'A session identifier is required for runtime targets')
        try:
            # A frozen wire model still contains mutable lists. Isolate all arrays
            # and metadata from mutations by the caller after load.
            source = ReplayData.model_validate(data.model_dump(mode='python'))
            mapping = parse_mapping(source.mapping_snapshot)
        except (ValidationError, MappingError, AttributeError, ValueError) as exc:
            raise SamplingError('INVALID_REPLAY_DATA', 'Replay source or mapping is invalid') from exc
        self._session_id = session_id
        self._log_id = source.log_id
        self._source_origin = source.time_basis.relative_origin_s
        self._times = tuple(t-self._source_origin for t in source.sample_times_s)
        self._duration = self._times[-1]
        channels = {}
        for name, channel in source.channels.items():
            _validate_units(name, channel)
            shifted = channel.model_copy(update={'timestamps_s':[t-self._source_origin for t in channel.timestamps_s]})
            channels[name] = PreparedChannel(shifted, max_gap_s=max_gap_s)
        from .virtual_channels import prepare_virtual_wheels
        virtual = prepare_virtual_wheels(source, MappingProxyType(channels), mapping.virtual_channels.wheel_roll_from_chassis)
        for name, channel in virtual.channels.items():
            _validate_units(name, channel)
            channels[name] = PreparedChannel(channel, max_gap_s=max_gap_s)
        self._channels = MappingProxyType(channels)
        self._metadata = {
            'source_relative_origin_s':self._source_origin,
            'sampling':'per-channel linear / explicitly wrapped shortest-path interpolation',
            'wrapped_lift_provenance':'Lift uses the contiguous valid source-run prefix; source_indices identify current raw endpoints, not an expanded copy of the prefix.',
            'missing_joint_policy':'last valid source at or before query, invalid quality; leading fallback zero',
            'chassis_policy':'already world xy/yaw; unavailable is None; worker must preserve initial z/roll/pitch',
            'virtual_wheels':virtual.metadata,
        }

    @property
    def duration_s(self) -> float:
        return self._duration

    @property
    def sample_times_s(self) -> tuple[float, ...]:
        return self._times

    @property
    def metadata(self) -> dict:
        return deepcopy(self._metadata)

    def frame_time(self, index: int) -> float:
        """Position for the normalized original-sample index, never a render ID."""
        if type(index) is not int:
            raise SamplingError('INVALID_FRAME_INDEX', 'Original sample index must be an integer')
        return self._times[min(max(index,0),len(self._times)-1)]

    def step_time(self, time_s: float, direction: str) -> float:
        time = _time(time_s)
        if direction == 'forward':
            index = bisect_right(self._times,time)
        elif direction == 'backward':
            index = bisect_left(self._times,time)-1
        else:
            raise SamplingError('INVALID_STEP_DIRECTION', 'Step direction must be forward or backward')
        return self.frame_time(index)

    def sample_at(self, time_s: float, *, playback_epoch: int = 0, control_seq: int = 0, target_seq: int = 0) -> TargetFrame:
        time = min(max(_time(time_s),0.),self._duration)
        index = bisect_left(self._times,time)
        source_index = index if index < len(self._times) and self._times[index] == time else None
        values, qualities = [], []
        for name in CANONICAL_JOINTS:
            channel = self._channels.get(name)
            sample = channel.sample(time) if channel is not None else None
            if sample is not None and sample.quality.valid and sample.value is not None:
                values.append(sample.value)
                qualities.append(sample.quality)
                continue
            previous = channel.last_valid(time) if channel is not None else None
            if previous is None:
                values.append(0.)
                qualities.append(ChannelQuality(origin='missing',valid=False,processing=['defaulted'],method='missing_joint_zero.v1'))
            else:
                values.append(previous.value)
                operations = list(dict.fromkeys([p for p in previous.quality.processing if p != 'original']+['forward_filled']))
                qualities.append(ChannelQuality(origin=previous.quality.origin,valid=False,processing=operations,
                    source_indices=previous.quality.source_indices,method='invalid_hold_previous.v1'))
        chassis = None
        if all(name in self._channels for name in ('chassis.x','chassis.y','chassis.yaw')):
            pose = [self._channels[name].sample(time) for name in ('chassis.x','chassis.y','chassis.yaw')]
            if all(value.quality.valid and value.value is not None for value in pose):
                chassis = ChassisPose(x=pose[0].value,y=pose[1].value,yaw=pose[2].value,
                                      quality=tuple(value.quality for value in pose))
        try:
            return TargetFrame(session_id=self._session_id,log_id=self._log_id,playback_epoch=playback_epoch,
                control_seq=control_seq,target_seq=target_seq,log_time_s=time,duration_s=self._duration,
                progress=time/self._duration if self._duration else 0.,source_sample_index=source_index,
                joints=values,quality=qualities,chassis=chassis)
        except ValidationError as exc:
            raise SamplingError('INVALID_TARGET', 'Target identity or sampled values violate the shared protocol') from exc


def sample_at(data: ReplayData, time_s: float, *, session_id: str = 'offline-sampling',
              playback_epoch: int = 0, control_seq: int = 0, target_seq: int = 0,
              max_gap_s: float | None = None) -> TargetFrame:
    """One-off offline convenience; playback should reuse one ReplaySampler per log."""
    return ReplaySampler(data,session_id=session_id,max_gap_s=max_gap_s).sample_at(time_s,
        playback_epoch=playback_epoch,control_seq=control_seq,target_seq=target_seq)
