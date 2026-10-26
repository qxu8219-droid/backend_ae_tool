"""Sampling expectations calculated independently of the implementation."""
import math

import pytest

from app.replay.base import SamplingError
from app.replay.channel import PreparedChannel
from replay_protocol import ChannelQuality, ChannelSeries


def channel(values, *, times=None, quality=None, semantics="linear", period=None):
    return ChannelSeries(
        timestamps_s=times or [float(index) for index in range(len(values))],
        values=values,
        quality=quality or [
            ChannelQuality(origin="measured" if value is not None else "missing",
                           valid=value is not None, processing=("original",),
                           source_indices=(index,))
            for index, value in enumerate(values)
        ],
        unit="rad" if semantics != "linear" else "m",
        angle_semantics=semantics,
        wrap_period=period,
    )


def test_irregular_linear_samples_preserve_exact_quality_and_interpolate_provenance():
    source = channel([2.0, 6.0, 14.0], times=[4.0, 6.0, 10.0])
    prepared = PreparedChannel(source)
    assert prepared.sample(6.0).value == 6.0
    assert prepared.sample(6.0).quality == source.quality[1]
    midpoint = prepared.sample(8.0)
    assert midpoint.value == 10.0
    assert midpoint.quality.valid
    assert midpoint.quality.origin == "measured"
    assert midpoint.quality.processing == ("interpolated",)
    assert midpoint.quality.source_indices == (1, 2)
    assert midpoint.quality.method is None


@pytest.mark.parametrize("semantics", ["bounded", "angular_unwrapped"])
def test_bounded_and_unwrapped_angles_keep_large_motion_without_clamping(semantics):
    prepared = PreparedChannel(channel([0.0, 12.0 * math.pi], semantics=semantics))
    assert prepared.sample(0.5).value == 6.0 * math.pi
    assert prepared.sample(1.0).value == 12.0 * math.pi


def test_no_extrapolation_and_no_interpolation_across_invalid_values():
    source = channel([2.0, 99.0, 6.0], quality=[
        ChannelQuality(origin="measured", valid=True, processing=("original",), source_indices=(0,)),
        ChannelQuality(origin="measured", valid=False, processing=("forward_filled",), source_indices=(0,)),
        ChannelQuality(origin="measured", valid=True, processing=("original",), source_indices=(2,)),
    ])
    prepared = PreparedChannel(source)
    for time in (-1.0, 0.5, 1.0, 1.5, 3.0):
        sample = prepared.sample(time)
        assert sample.value is None
        assert not sample.quality.valid
    assert prepared.sample(1.0).quality == source.quality[1]
    assert prepared.sample(-1.0).quality.origin == "missing"
    assert prepared.sample(3.0).quality.origin == "missing"


def test_maximum_gap_is_inclusive_and_does_not_hide_valid_endpoints():
    prepared = PreparedChannel(channel([1.0, 3.0, 8.0], times=[0.0, 2.0, 5.0]), max_gap_s=2.0)
    assert prepared.sample(1.0).value == 2.0
    assert prepared.sample(3.0).value is None
    assert not prepared.sample(3.0).quality.valid
    assert prepared.sample(5.0).value == 8.0


def test_last_valid_uses_source_history_even_after_coverage_without_future_lookahead():
    prepared = PreparedChannel(channel([None, 5.0, None, 9.0]))
    assert prepared.last_valid(-1.0) is None
    assert prepared.last_valid(0.0) is None
    assert prepared.last_valid(0.5) is None
    assert prepared.last_valid(1.0).value == 5.0
    assert prepared.last_valid(2.9).value == 5.0
    assert prepared.last_valid(3.0).value == 9.0
    assert prepared.last_valid(100.0).value == 9.0


def test_preparation_snapshots_source_lists_and_exposes_immutable_sequences():
    source = channel([2.0, 4.0])
    prepared = PreparedChannel(source)
    source.timestamps_s[1] = 100.0
    source.values[0] = 900.0
    source.quality[0] = ChannelQuality(origin="missing", valid=False)
    assert prepared.times == (0.0, 1.0)
    assert prepared.values == (2.0, 4.0)
    assert prepared.sample(0.5).value == 3.0
    assert prepared.sample(0.0).quality.valid
    with pytest.raises((AttributeError, TypeError)):
        prepared.values = (1.0, 2.0)


def test_mixed_origin_and_method_merge_all_processing_and_deduplicate_indices():
    source = channel([2.0, 8.0], quality=[
        ChannelQuality(origin="commanded", valid=True, processing=("unit_converted", "forward_filled"),
                       source_indices=(3, 4)),
        ChannelQuality(origin="derived", valid=True, processing=("derived",),
                       source_indices=(4, 8), method="custom.v1"),
    ])
    sample = PreparedChannel(source).sample(0.5)
    assert sample.value == 5.0
    assert sample.quality.origin == "derived"
    assert sample.quality.method == "interpolation.composite.v1"
    assert sample.quality.processing == ("unit_converted", "forward_filled", "derived", "interpolated")
    assert sample.quality.source_indices == (3, 4, 8)


def test_matching_derived_methods_remain_identifiable_when_interpolated():
    source = channel([2.0, 8.0], quality=[
        ChannelQuality(origin="derived", valid=True, processing=("derived",),
                       source_indices=(index,), method="same.v1")
        for index in range(2)
    ])
    sample = PreparedChannel(source).sample(0.5)
    assert sample.quality.origin == "derived"
    assert sample.quality.method == "same.v1"
    assert sample.quality.processing == ("derived", "interpolated")


def test_weighted_linear_interpolation_avoids_difference_overflow():
    prepared = PreparedChannel(channel([-1e308, 1e308]))
    assert prepared.sample(0.5).value == 0.0
    assert prepared.sample(0.75).value == 5e307
    assert prepared.sample(0.75).quality.valid


def test_constant_subnormal_channel_does_not_underflow_during_interpolation():
    smallest_float = float.fromhex("0x0.0000000000001p-1022")
    prepared = PreparedChannel(channel([smallest_float, smallest_float]))
    assert prepared.sample(0.5).value == smallest_float


def test_wrapped_overflow_invalidates_remaining_run_then_resets_after_missing_sample():
    prepared = PreparedChannel(channel(
        [1.79e308, 1e308, 1.01e308, None, 1.0],
        semantics="angular_wrapped", period=1e308,
    ))
    for time in (0.5, 1.0, 1.5, 2.0, 3.0):
        sample = prepared.sample(time)
        assert sample.value is None
        assert not sample.quality.valid
    assert prepared.last_valid(2.0).value == 1.79e308
    assert prepared.sample(4.0).value == 1.0


def test_wrapped_reduction_handles_overflow_in_raw_endpoint_difference():
    prepared = PreparedChannel(channel([-1e308, 1e308], semantics="angular_wrapped", period=4.0))
    assert prepared.sample(0.5).value == -1e308
    assert prepared.sample(1.0).value == -1e308
    assert prepared.sample(1.0).quality.valid
    assert prepared.sample(1.0).quality.method == "wrapped_shortest_path.v1"


@pytest.mark.parametrize("time", [True, False, float("nan"), float("inf"), -float("inf"), "0"])
def test_query_time_requires_a_finite_number(time):
    prepared = PreparedChannel(channel([1.0]))
    with pytest.raises(SamplingError):
        prepared.sample(time)
    with pytest.raises(SamplingError):
        prepared.last_valid(time)


@pytest.mark.parametrize("gap", [True, 0.0, -1.0, float("nan"), float("inf"), "1"])
def test_maximum_gap_requires_a_positive_finite_number(gap):
    with pytest.raises(SamplingError):
        PreparedChannel(channel([1.0]), max_gap_s=gap)


def test_mutated_invalid_source_is_revalidated_before_snapshot():
    source = channel([1.0, 2.0])
    source.values[0] = float("nan")
    with pytest.raises(SamplingError):
        PreparedChannel(source)


def test_wrapped_angles_lift_across_branch_and_keep_lifted_history():
    source = channel([3.0, -3.0, -2.5], semantics="angular_wrapped", period=2.0 * math.pi)
    prepared = PreparedChannel(source)
    assert prepared.sample(0.0).quality == source.quality[0]
    assert prepared.sample(0.5).value == pytest.approx(math.pi)
    lifted = prepared.sample(1.0)
    assert lifted.value == pytest.approx(2.0 * math.pi - 3.0)
    assert lifted.quality.origin == "derived"
    assert lifted.quality.method == "wrapped_shortest_path.v1"
    assert lifted.quality.processing == ("derived",)
    assert lifted.quality.source_indices == (1,)
    assert prepared.sample(1.5).value == pytest.approx(2.0 * math.pi - 2.75)
    assert prepared.last_valid(1.5).value == lifted.value
    assert source.values == [3.0, -3.0, -2.5]


def test_wrapped_lifting_resets_at_invalid_sample_without_bridge():
    prepared = PreparedChannel(channel(
        [3.0, -3.0, None, -3.0, 3.0], semantics="angular_wrapped", period=2.0 * math.pi,
    ))
    assert prepared.sample(1.0).value == pytest.approx(2.0 * math.pi - 3.0)
    assert prepared.sample(2.5).value is None
    assert prepared.sample(3.0).value == -3.0
    assert prepared.sample(4.0).value == pytest.approx(3.0 - 2.0 * math.pi)


@pytest.mark.parametrize("values,want", [([0.0, 2.0], 1.0), ([2.0, 0.0], 1.0),
                                        ([0.0, 6.0], 1.0), ([0.0, -6.0], -1.0)])
def test_wrapped_half_period_tie_follows_raw_direction(values, want):
    prepared = PreparedChannel(channel(values, semantics="angular_wrapped", period=4.0))
    assert prepared.sample(0.5).value == want


def test_wrapped_sampling_is_independent_of_query_order():
    prepared = PreparedChannel(channel([3.0, -3.0, -2.5], semantics="angular_wrapped", period=2.0 * math.pi))
    expected = {0.0: 3.0, 0.5: math.pi, 1.0: 2.0 * math.pi - 3.0,
                1.5: 2.0 * math.pi - 2.75, 2.0: 2.0 * math.pi - 2.5}
    for time in (2.0, 0.0, 1.5, 0.5, 1.0, 2.0, 1.5, 0.0):
        assert prepared.sample(time).value == pytest.approx(expected[time])
