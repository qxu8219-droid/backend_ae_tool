"""Immutable, binary-search sampling of a single source channel."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math

from pydantic import ValidationError
from replay_protocol import ChannelQuality, ChannelSeries

from .base import SampleValue, SamplingError


_MISSING = ChannelQuality(origin="missing", valid=False)
_WRAPPED_METHOD = "wrapped_shortest_path.v1"


def _finite_number(value: object, *, code: str, name: str) -> float:
    if type(value) not in (float, int):
        raise SamplingError(code, f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise SamplingError(code, f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise SamplingError(code, f"{name} must be a finite number")
    return number


def _derived_quality(quality: ChannelQuality, *, valid: bool) -> ChannelQuality:
    processing = tuple(dict.fromkeys(
        (*(item for item in quality.processing if item != "original"), "derived")
    ))
    return ChannelQuality(origin="derived", valid=valid, processing=processing,
                          source_indices=quality.source_indices, method=_WRAPPED_METHOD)


def _merged_quality(left: ChannelQuality, right: ChannelQuality, *, valid: bool) -> ChannelQuality:
    """Combine endpoint provenance without mislabelling a composite source."""
    processing = tuple(dict.fromkeys(
        item for quality in (left, right) for item in quality.processing if item != "original"
    ))
    if valid and "interpolated" not in processing:
        processing += ("interpolated",)
    composite = (left.origin, left.method) != (right.origin, right.method)
    return ChannelQuality(
        origin="derived" if composite else left.origin,
        valid=valid,
        processing=processing,
        source_indices=tuple(dict.fromkeys((*left.source_indices, *right.source_indices))),
        method="interpolation.composite.v1" if composite else left.method,
    )


def _lift_wrapped(values: list[float | None], quality: list[ChannelQuality], period: float) -> None:
    """Lift once per valid run; ties follow the direction in the source log."""
    previous_raw = None
    previous_lift = None
    overflowed = False
    half_period = period / 2.0
    for index, (raw, provenance) in enumerate(zip(values, quality)):
        if raw is None or not provenance.valid:
            previous_raw = previous_lift = None
            overflowed = False
            continue
        if previous_raw is None:
            previous_raw = previous_lift = raw
            continue
        if overflowed:
            values[index] = None
            quality[index] = _derived_quality(provenance, valid=False)
            continue

        raw_delta = raw - previous_raw
        if math.isfinite(raw_delta):
            delta = math.remainder(raw_delta, period)
        else:
            # Reduce before subtraction when two finite source values overflow
            # their difference. Reduced values are each within half a period.
            delta = math.remainder(
                math.remainder(raw, period) - math.remainder(previous_raw, period), period,
            )
        if abs(delta) == half_period:
            delta = math.copysign(half_period, 1.0 if raw > previous_raw else -1.0)
        # Preserve an unchanged branch exactly, including the original quality.
        lifted = raw if previous_lift == previous_raw and delta == raw_delta else previous_lift + delta
        previous_raw = raw
        if not math.isfinite(lifted):
            values[index] = None
            quality[index] = _derived_quality(provenance, valid=False)
            overflowed = True
        else:
            previous_lift = lifted
            values[index] = lifted
            if lifted != raw:
                quality[index] = _derived_quality(provenance, valid=True)


@dataclass(frozen=True, slots=True, init=False)
class PreparedChannel:
    """Validate and snapshot once; queries never depend on earlier queries.

    ``times`` retain the input clock. Wrapped ``values`` contain the compiled
    continuous lift, while all other angle semantics preserve their raw values.
    """

    times: tuple[float, ...]
    values: tuple[float | None, ...]
    quality: tuple[ChannelQuality, ...]
    max_gap_s: float | None
    _valid_prefix: tuple[int, ...]
    _invalid_interval_prefix: tuple[int, ...]

    def __init__(self, series: ChannelSeries, *, max_gap_s: float | None = None):
        if max_gap_s is not None:
            max_gap_s = _finite_number(max_gap_s, code="INVALID_MAX_GAP", name="max_gap_s")
            if max_gap_s <= 0:
                raise SamplingError("INVALID_MAX_GAP", "max_gap_s must be positive")
        try:
            snapshot = ChannelSeries.model_validate(series)
        except (ValidationError, TypeError, ValueError, OverflowError) as exc:
            raise SamplingError("INVALID_CHANNEL", f"invalid source channel: {exc}") from exc

        values, quality = list(snapshot.values), list(snapshot.quality)
        if snapshot.angle_semantics == "angular_wrapped":
            _lift_wrapped(values, quality, snapshot.wrap_period)
        last_valid = -1
        valid_prefix = []
        for index, (value, provenance) in enumerate(zip(values, quality)):
            if value is not None and provenance.valid:
                last_valid = index
            valid_prefix.append(last_valid)
        object.__setattr__(self, "times", tuple(snapshot.timestamps_s))
        object.__setattr__(self, "values", tuple(values))
        object.__setattr__(self, "quality", tuple(quality))
        object.__setattr__(self, "max_gap_s", max_gap_s)
        object.__setattr__(self, "_valid_prefix", tuple(valid_prefix))
        invalid_intervals = [0]
        for index in range(len(snapshot.timestamps_s) - 1):
            available = (quality[index].valid and quality[index + 1].valid
                         and values[index] is not None and values[index + 1] is not None
                         and (max_gap_s is None or snapshot.timestamps_s[index + 1] - snapshot.timestamps_s[index] <= max_gap_s))
            invalid_intervals.append(invalid_intervals[-1] + int(not available))
        object.__setattr__(self, "_invalid_interval_prefix", tuple(invalid_intervals))

    def interval_available(self, start: float, end: float) -> bool:
        """Check every crossed source edge, including gaps with valid endpoints."""
        start = _finite_number(start, code="INVALID_SAMPLE_TIME", name="interval start")
        end = _finite_number(end, code="INVALID_SAMPLE_TIME", name="interval end")
        if start > end:
            raise SamplingError("INVALID_INTERVAL", "Interval start must not exceed its end")
        if start < self.times[0] or end > self.times[-1]:
            return False
        if start == end:
            return self.sample(start).quality.valid
        first_edge = bisect_right(self.times, start) - 1
        last_edge = bisect_left(self.times, end) - 1
        return self._invalid_interval_prefix[last_edge + 1] == self._invalid_interval_prefix[first_edge]

    def sample(self, time: float) -> SampleValue:
        """Return an exact or interpolated value, never extrapolating coverage."""
        time = _finite_number(time, code="INVALID_SAMPLE_TIME", name="sample time")
        left = bisect_right(self.times, time) - 1
        if left < 0 or time > self.times[-1]:
            return SampleValue(None, _MISSING)
        if self.times[left] == time:
            quality = self.quality[left]
            return SampleValue(self.values[left] if quality.valid else None, quality)

        right = left + 1
        left_value, right_value = self.values[left], self.values[right]
        span = self.times[right] - self.times[left]
        valid = (self.quality[left].valid and self.quality[right].valid
                 and left_value is not None and right_value is not None
                 and (self.max_gap_s is None or span <= self.max_gap_s))
        value = None
        if valid:
            weight = (time - self.times[left]) / span
            # Weighted terms avoid overflowing b-a for opposite extreme values.
            # Identical subnormal endpoints must not underflow in both products.
            value = (left_value if left_value == right_value else
                     (1.0 - weight) * left_value + weight * right_value)
            if not math.isfinite(value):
                value, valid = None, False
        return SampleValue(value, _merged_quality(self.quality[left], self.quality[right], valid=valid))

    def last_valid(self, time: float) -> SampleValue | None:
        """Find the latest valid compiled source sample at or before ``time``."""
        time = _finite_number(time, code="INVALID_SAMPLE_TIME", name="sample time")
        index = bisect_right(self.times, time) - 1
        if index < 0:
            return None
        index = self._valid_prefix[index]
        if index < 0:
            return None
        return SampleValue(self.values[index], self.quality[index])
