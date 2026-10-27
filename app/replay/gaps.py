"""A recorded sparse-data bound cannot be weakened by a service-wide default."""
import math
from app.adapters.b041.profile import B041Profile
from .base import SamplingError


def effective_gap(mapping_snapshot, requested):
    profile = mapping_snapshot.get('b041')
    if profile is None: return requested
    bound = B041Profile.model_validate(profile).max_interpolation_gap_s
    if requested is None: return bound
    if type(requested) not in (int, float) or not math.isfinite(requested) or requested <= 0:
        raise SamplingError('INVALID_MAX_GAP', 'max_gap_s must be positive and finite')
    return min(bound, requested)
