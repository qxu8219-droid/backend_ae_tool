import hashlib
import json
from pathlib import Path

import pytest
from replay_protocol import ChannelQuality

ROOT = Path(__file__).resolve().parents[2]


def quality(origin='measured', *, valid=True, processing=('original',)):
    return ChannelQuality(origin=origin, valid=valid, processing=processing,
                          method='fixture.estimate' if origin == 'derived' else None)


@pytest.mark.parametrize('value,state,side', [
    (-2.000002, 'error', 'lower'), (-2.0000005, 'warning', 'lower'),
    (-2., 'warning', 'lower'), (-1.6, 'warning', 'lower'), (-1.59, 'normal', None),
    (0., 'normal', None), (5.59, 'normal', None), (5.6, 'warning', 'upper'),
    (6., 'warning', 'upper'), (6.0000005, 'warning', 'upper'), (6.000002, 'error', 'upper'),
])
def test_asymmetric_limits_use_total_travel_and_numeric_tolerance(value, state, side):
    from replay_protocol.limits import LimitSpec, assess_limit
    result = assess_limit(value, quality(), LimitSpec('revolute', -2., 6.))
    assert (result.state, result.side) == (state, side)


@pytest.mark.parametrize('lo,hi,value,want', [
    (0., .05, .001, 'warning'), (0., .05, .025, 'normal'),
    (-.05, 0., -.001, 'warning'), (0., .05, .050000005, 'warning'),
    (0., .05, .05000002, 'error'), (0., 0., 0., 'normal'), (0., 0., .000001, 'error'),
])
def test_one_sided_prismatic_and_fixed_ranges(lo, hi, value, want):
    from replay_protocol.limits import LimitSpec, assess_limit
    assert assess_limit(value, quality(), LimitSpec('prismatic', lo, hi)).state == want


@pytest.mark.parametrize('origin,valid,processing,want', [
    ('measured', True, ('original',), 'error'),
    ('commanded', True, ('unit_converted',), 'error'),
    ('measured', True, ('interpolated',), 'error'),
    ('measured', False, ('forward_filled',), 'unavailable'),
    ('measured', True, ('forward_filled',), 'unassessed'),
    ('measured', True, ('derived',), 'unassessed'),
    ('derived', True, ('derived',), 'unassessed'),
    ('missing', False, ('defaulted',), 'unavailable'),
])
def test_limit_flags_do_not_claim_missing_or_estimated_positions_are_measured(origin, valid, processing, want):
    from replay_protocol.limits import LimitSpec, assess_limit, limit_flags
    q = quality(origin, valid=valid, processing=processing)
    spec = LimitSpec('revolute', -1., 1.)
    assert assess_limit(2., q, spec).state == want
    assert limit_flags([2.], [q], [spec]) == ((1, 0) if want == 'error' else (0, 0))


def test_continuous_and_extreme_values_do_not_overflow_normalized_range():
    from replay_protocol.limits import LimitSpec, assess_limit
    assert assess_limit(1e308, quality(), LimitSpec('continuous', None, None)).state == 'unlimited'
    bounded = LimitSpec('revolute', -1.7e308, 1.7e308)
    assert assess_limit(0., quality(), bounded).state == 'normal'
    assert assess_limit(1.6e308, quality(), bounded).state == 'warning'
    assert assess_limit(1.79e308, quality(), bounded).state == 'error'


def test_common_metadata_reproduces_cached_values_and_retains_actual_source_hash():
    from app.analysis.metadata import load_joint_metadata, metadata_from_common
    common = ROOT / 'assets/robots/r1pro_2026/isaacsim_6_x/reproduction/configs/r1pro_2026_common.json'
    actual = metadata_from_common(common)
    cached = load_joint_metadata()
    assert actual.metadata == cached.metadata
    assert actual.source_sha256 == cached.source_sha256 == hashlib.sha256(common.read_bytes()).hexdigest()
    assert actual.metadata.joints[1].lower is None and actual.metadata.joints[1].upper is None
    assert actual.metadata.joints[17].unit == 'm'
    assert actual.metadata.joints[6].limits_provenance == 'vendor URDF'


def test_metadata_rejects_missing_position_bound_or_provenance(tmp_path):
    from app.analysis.metadata import metadata_from_common
    common = ROOT / 'assets/robots/r1pro_2026/isaacsim_6_x/reproduction/configs/r1pro_2026_common.json'
    original = json.loads(common.read_text())
    for field in ('lower', 'limits_provenance'):
        source = json.loads(json.dumps(original))
        joint = source['joints']['torso_joint1']
        if field == 'lower':
            del joint['limit'][field]
        else:
            del joint[field]
        path = tmp_path / (field + '.json'); path.write_text(json.dumps(source))
        with pytest.raises(ValueError):
            metadata_from_common(path)
