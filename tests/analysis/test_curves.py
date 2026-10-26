import math

import pytest
from replay_protocol import ChannelQuality, ChannelSeries
from analysis_support import NAME, metadata, replay


def test_minmax_keeps_single_sample_peak_and_trough_under_a_two_point_budget():
    from app.analysis.service import ReplayAnalysis
    data = replay([0.] * 40 + [7., -5.] + [0.] * 60)
    response = ReplayAnalysis(data, metadata=metadata()).curve([NAME], max_points=2)
    assert [(p.time_s, p.value) for p in response.series[0].points] == [(40., 7.), (41., -5.)]
    assert len(response.series[0].limit_intervals) == 2
    assert response.series[0].points[0].quality.source_indices == (40,)


def test_null_runs_preserve_breaks_and_too_small_budget_is_explicit():
    from app.analysis.service import ReplayAnalysis
    from app.analysis.models import AnalysisError
    analysis = ReplayAnalysis(replay([0., 3., None, None, -4., 0.]), metadata=metadata())
    response = analysis.curve([NAME], max_points=5)
    assert [p.value for p in response.series[0].points] == [0., 3., None, -4., 0.]
    with pytest.raises(AnalysisError) as caught:
        analysis.curve([NAME], max_points=4)
    assert caught.value.code == 'CURVE_BUDGET_TOO_SMALL'
    assert caught.value.details['required_points'] == 5


def test_declared_long_gap_is_not_connected_and_interval_is_clipped():
    from app.analysis.service import ReplayAnalysis
    analysis = ReplayAnalysis(replay([0., 2., 3., 0.], times=[0., 1., 10., 11.]), metadata=metadata(), max_gap_s=2.)
    result = analysis.curve([NAME], max_points=5)
    assert [p.value for p in result.series[0].points] == [0., 2., None, 3., 0.]
    narrow = analysis.curve([NAME], from_s=10.2, to_s=10.8, max_points=2)
    assert [p.time_s for p in narrow.series[0].points] == [10.2, 10.8]
    assert narrow.series[0].points[0].quality.processing == ('interpolated',)
    interval = narrow.series[0].limit_intervals[0]
    assert (interval.from_s, interval.to_s) == (10.2, 10.8)


@pytest.mark.parametrize('budget', [2, 3, 4, 5, 9, 17])
def test_deterministic_odd_even_budgets_keep_source_order_and_global_extrema(budget):
    from app.analysis.service import ReplayAnalysis
    data = replay([0., 1., -2., 8., 0., 2., -7., 3., 2., 0., 0., 4.])
    analysis = ReplayAnalysis(data, metadata=metadata())
    before = data.model_dump_json()
    one = analysis.curve([NAME], max_points=budget)
    analysis.curve([NAME], from_s=1., to_s=5., max_points=3)
    assert analysis.curve([NAME], max_points=budget) == one
    points = one.series[0].points
    assert len(points) <= budget
    assert all(a.time_s < b.time_s for a, b in zip(points, points[1:]))
    assert min(p.value for p in points) == -7. and max(p.value for p in points) == 8.
    assert data.model_dump_json() == before


def test_boundaries_unknown_channels_range_validation_and_zero_duration():
    from app.analysis.service import ReplayAnalysis
    from app.analysis.models import AnalysisError
    analysis = ReplayAnalysis(replay([0., 2.]), metadata=metadata())
    result = analysis.curve([NAME], from_s=.25, to_s=.75, max_points=2)
    assert [p.value for p in result.series[0].points] == [.5, 1.5]
    for options in ({'from_s': -1.}, {'to_s': 2.}, {'from_s': .9, 'to_s': .1}, {'max_points': True}, {'max_points': 1}):
        with pytest.raises(ValueError):
            analysis.curve([NAME], **options)
    with pytest.raises(AnalysisError) as caught:
        analysis.curve(['unknown'])
    assert caught.value.status_code == 404
    single = ReplayAnalysis(replay([2.]), metadata=metadata()).curve([NAME], max_points=2)
    assert single.from_s == single.to_s == 0.
    assert len(single.series[0].points) == 1


def test_multirate_custom_and_wrapped_channels_keep_quality_and_own_clock():
    from app.analysis.service import ReplayAnalysis
    measured = [ChannelQuality(origin='measured', valid=True, processing=['original'], source_indices=[i]) for i in range(2)]
    extras = {
        'temperature': ChannelSeries(timestamps_s=[.2, .8], values=[10., 20.], quality=measured, unit='C', angle_semantics='linear'),
        'wheel_motor_joint1': ChannelSeries(timestamps_s=[0., 1.], values=[3., -3.], quality=measured, unit='rad', angle_semantics='angular_wrapped', wrap_period=2 * math.pi),
    }
    analysis = ReplayAnalysis(replay([0., 0.], extra=extras), metadata=metadata())
    result = analysis.curve(['temperature', 'wheel_motor_joint1'], max_points=4)
    assert [point.value for point in result.series[0].points] == [None, 10., 20., None]
    assert result.series[1].points[-1].value == pytest.approx(2 * math.pi - 3.)
    assert result.series[1].points[-1].quality.origin == 'derived'
    assert result.series[1].limit_intervals == []


def test_finite_extremes_and_invalid_source_values_are_not_averaged_into_false_peaks():
    from app.analysis.service import ReplayAnalysis
    quality = [ChannelQuality(origin='measured', valid=i != 1, processing=['original'], source_indices=[i]) for i in range(4)]
    analysis = ReplayAnalysis(replay([1e308, 1.7e308, -1e308, 0.], qualities=quality), metadata=metadata())
    points = analysis.curve([NAME], max_points=4).series[0].points
    assert [p.value for p in points] == [1e308, None, -1e308, 0.]
    assert all(p.value is None or math.isfinite(p.value) for p in points)
