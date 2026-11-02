"""Shared numerical position assessment; no application or SDK dependencies."""
from dataclasses import dataclass
import math

from .models import ChannelQuality

LIMIT_POLICY_ID = 'joint_limits.v1'
_NON_OBSERVED = frozenset(('derived', 'defaulted', 'forward_filled'))


def finite(value, name):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be a finite number')
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(result):
        raise ValueError(f'{name} must be finite')
    return result


@dataclass(frozen=True, slots=True)
class LimitSpec:
    kind: str
    lower: float | None
    upper: float | None

    def __post_init__(self):
        if self.kind not in ('revolute', 'continuous', 'prismatic'):
            raise ValueError('Unknown joint type')
        if self.kind == 'continuous':
            if self.lower is not None or self.upper is not None:
                raise ValueError('Continuous joints cannot have position bounds')
        else:
            object.__setattr__(self, 'lower', finite(self.lower, 'lower'))
            object.__setattr__(self, 'upper', finite(self.upper, 'upper'))
            if self.lower > self.upper:
                raise ValueError('Position bounds must be ordered')

    @property
    def unit(self):
        return 'm' if self.kind == 'prismatic' else 'rad'


@dataclass(frozen=True, slots=True)
class LimitPolicy:
    warning_margin: float = .05
    rad_tolerance: float = 1e-6
    m_tolerance: float = 1e-8

    def __post_init__(self):
        margin = finite(self.warning_margin, 'warning_margin')
        if not 0 <= margin < .5:
            raise ValueError('Warning margin must be in [0, 0.5)')
        for name in ('rad_tolerance', 'm_tolerance'):
            if finite(getattr(self, name), name) < 0:
                raise ValueError('Tolerance must be nonnegative')


DEFAULT_LIMIT_POLICY = LimitPolicy()


@dataclass(frozen=True, slots=True)
class LimitAssessment:
    state: str
    side: str | None = None


def quality_eligible(quality: ChannelQuality) -> bool:
    return (quality.valid and quality.origin in ('measured', 'commanded')
            and not _NON_OBSERVED.intersection(quality.processing))


def assess_limit(value, quality: ChannelQuality, spec: LimitSpec, policy=DEFAULT_LIMIT_POLICY):
    if value is None or not quality.valid or quality.origin == 'missing':
        return LimitAssessment('unavailable')
    value = finite(value, 'joint value')
    if spec.kind == 'continuous':
        return LimitAssessment('unlimited')
    if not quality_eligible(quality):
        return LimitAssessment('unassessed')
    lo, hi = spec.lower, spec.upper
    tolerance = policy.m_tolerance if spec.unit == 'm' else policy.rad_tolerance
    if value < lo and lo - value > tolerance:
        return LimitAssessment('error', 'lower')
    if value > hi and value - hi > tolerance:
        return LimitAssessment('error', 'upper')
    if lo == hi:
        return LimitAssessment('normal')
    # Weighted endpoints avoid overflowing hi-lo for finite extreme bounds.
    lower_warning = (1. - policy.warning_margin) * lo + policy.warning_margin * hi
    upper_warning = policy.warning_margin * lo + (1. - policy.warning_margin) * hi
    if value <= lower_warning + tolerance:
        return LimitAssessment('warning', 'lower')
    if value >= upper_warning - tolerance:
        return LimitAssessment('warning', 'upper')
    return LimitAssessment('normal')


def limit_flags(values, qualities, specs, policy=DEFAULT_LIMIT_POLICY):
    if len(values) != len(qualities) or not len(specs) <= len(values) <= 28:
        raise ValueError('Limit bitmap inputs have inconsistent lengths')
    exceeded = warning = 0
    for index, spec in enumerate(specs):
        result = assess_limit(values[index], qualities[index], spec, policy)
        if result.state == 'error':
            exceeded |= 1 << index
        elif result.state == 'warning':
            warning |= 1 << index
    return exceeded, warning
