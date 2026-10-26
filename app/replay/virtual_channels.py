"""Explicit visual wheel estimates; no robot geometry or hardware signs are inferred."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from replay_protocol import ChannelQuality, ChannelSeries, JsonValue, ReplayData

from app.adapters.mapping import WheelRollConfig
from .channel import PreparedChannel


_WHEELS = tuple(f"wheel_motor_joint{i}" for i in range(1, 4))
_CHASSIS = ("chassis.x", "chassis.y", "chassis.yaw")
_MODEL = "signed_body_forward_distance.v1"
_RATIO_ROUNDOFF = 8 * math.ulp(1.0)


@dataclass(frozen=True)
class VirtualWheelResult:
    channels: dict[str, ChannelSeries]
    metadata: dict[str, JsonValue]


def _forward_step(previous, current, max_lateral_ratio):
    """Project the center chord onto the midpoint body heading."""
    dx, dy = current[0] - previous[0], current[1] - previous[1]
    yaw_change = current[2] - previous[2]
    if not all(math.isfinite(value) for value in (dx, dy, yaw_change)):
        return None, "numeric_overflow"
    midpoint_yaw = previous[2] * 0.5 + current[2] * 0.5
    cosine, sine = math.cos(midpoint_yaw), math.sin(midpoint_yaw)
    forward = dx * cosine + dy * sine
    lateral = -dx * sine + dy * cosine
    translation = math.hypot(dx, dy)
    if not all(math.isfinite(value) for value in (forward, lateral, translation)):
        return None, "numeric_overflow"
    if translation <= 1e-9 and abs(yaw_change) > 1e-9:
        return None, "unsupported_pure_rotation"
    if translation:
        # A relative tolerance recognizes pure lateral movement despite trig
        # roundoff (for example cos(pi/2)); it is not a hardware dead zone.
        pure_lateral = abs(forward) / translation <= _RATIO_ROUNDOFF
        if pure_lateral or abs(lateral) / translation > max_lateral_ratio + _RATIO_ROUNDOFF:
            return None, "unsupported_lateral_motion"
    return forward, None


def prepare_virtual_wheels(
    data: ReplayData,
    channels: Mapping[str, PreparedChannel],
    config: WheelRollConfig,
) -> VirtualWheelResult:
    """Prepare replacements only for wheels with no valid existing sample.

    Prepared channels already use the relative playback clock. The archived
    union clock is normalized here once; the source data remains untouched.
    """
    config = WheelRollConfig.model_validate(config.model_dump(mode="python"))
    preserved = [name for name in _WHEELS if name in channels and any(q.valid for q in channels[name].quality)]
    needed = [name for name in _WHEELS if name not in preserved]
    metadata: dict[str, JsonValue] = {
        "enabled": config.enabled,
        "model": _MODEL,
        "wheel_radius_m": config.wheel_radius_m,
        "joint_signs": dict(config.joint_signs),
        "initial_angle_rad": config.initial_angle_rad,
        "initial_reference": "Configured visual angle at the start of the log; not an observed absolute wheel angle.",
        "max_lateral_ratio": config.max_lateral_ratio,
        "max_segment_s": config.max_segment_s,
        "limitations": [
            "Visual center-distance approximation ignores wheel-specific steering and offsets, including during arcs.",
            "Wrapped yaw cannot recover unknown full turns between source samples.",
            "Missing or unsupported motion makes all subsequent cumulative roll unavailable; integration never restarts.",
        ],
        "source_indices_semantics": "Indices into data.sample_times_s identify only the current integration interval endpoints.",
        "all_prior_trajectory_contributes": True,
        "preserved_channels": preserved,
        "derived_channels": [],
        "status": "disabled" if not config.enabled else "preserved",
        "reason": None,
        "first_unavailable_index": None,
        "channel_statuses": {name: {"status": "preserved", "reason": None} for name in preserved},
    }
    if not config.enabled or not needed:
        return VirtualWheelResult({}, metadata)

    times = [time - data.sample_times_s[0] for time in data.sample_times_s]
    values: dict[str, list[float | None]] = {name: [] for name in needed}
    qualities: dict[str, list[ChannelQuality]] = {name: [] for name in needed}
    wheel_failures: dict[str, str] = {}
    cumulative_distance = 0.0
    prior_pose = None
    processing = ["derived"]
    trajectory_failure = "chassis_channel_missing" if any(name not in channels for name in _CHASSIS) else None

    for index, time in enumerate(times):
        if trajectory_failure is None:
            samples = [channels[name].sample(time) for name in _CHASSIS]
            for sample in samples:
                for operation in sample.quality.processing:
                    if operation != "original" and operation not in processing:
                        processing.append(operation)
            if any(not sample.quality.valid or sample.value is None for sample in samples):
                trajectory_failure = "chassis_sample_unavailable"
            else:
                pose = tuple(sample.value for sample in samples)
                if not all(math.isfinite(value) for value in pose):
                    trajectory_failure = "numeric_overflow"
                elif index:
                    if any(not channels[name].interval_available(times[index - 1], time) for name in _CHASSIS):
                        trajectory_failure = "chassis_interval_unavailable"
                    elif config.max_segment_s is not None and time - times[index - 1] > config.max_segment_s:
                        trajectory_failure = "segment_too_long"
                    else:
                        step, trajectory_failure = _forward_step(prior_pose, pose, config.max_lateral_ratio)
                        if trajectory_failure is None:
                            cumulative_distance += step
                            if not math.isfinite(cumulative_distance):
                                trajectory_failure = "numeric_overflow"
                prior_pose = pose

        for name in needed:
            value = None
            reason = wheel_failures.get(name) or trajectory_failure
            if reason is None:
                value = config.initial_angle_rad + config.joint_signs[name] * cumulative_distance / config.wheel_radius_m
                if not math.isfinite(value):
                    value, reason = None, "numeric_overflow"
            if reason is not None:
                wheel_failures.setdefault(name, reason)
                if metadata["reason"] is None:
                    metadata["reason"] = reason
                    metadata["first_unavailable_index"] = index
            values[name].append(value)
            qualities[name].append(ChannelQuality(
                origin="derived", valid=value is not None, processing=tuple(processing),
                source_indices=(index - 1, index) if index else (0,), method=_MODEL,
            ))

    result = {
        name: ChannelSeries(timestamps_s=list(times), values=values[name], quality=qualities[name],
                            unit="rad", angle_semantics="angular_unwrapped")
        for name in needed
    }
    for name in needed:
        valid_count = sum(quality.valid for quality in qualities[name])
        status = "derived" if valid_count == len(times) else "partial" if valid_count else "unavailable"
        metadata["channel_statuses"][name] = {"status": status, "reason": wheel_failures.get(name)}
        if valid_count:
            metadata["derived_channels"].append(name)
    has_valid = any(quality.valid for items in qualities.values() for quality in items)
    metadata["status"] = "derived" if not wheel_failures else "partial" if has_valid else "unavailable"
    return VirtualWheelResult(result, metadata)
