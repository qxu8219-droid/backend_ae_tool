"""Hand-calculated examples for explicit, visual-only wheel roll estimates."""
import math

import pytest

from replay_protocol import CANONICAL_JOINTS, ChannelQuality, ChannelSeries, ReplayData, TimeBasis


WHEELS = tuple(f"wheel_motor_joint{i}" for i in range(1, 4))


def series(times, values, *, unit="m", semantics="linear", qualities=None):
    return ChannelSeries(
        timestamps_s=times,
        values=values,
        quality=qualities or [
            ChannelQuality(origin="measured", valid=value is not None, processing=["original"], source_indices=[i])
            for i, value in enumerate(values)
        ],
        unit=unit,
        angle_semantics=semantics,
        wrap_period=2 * math.pi if semantics == "angular_wrapped" else None,
    )


def trajectory(x, y=None, yaw=None, *, times=None, origin=0.0, extra=None, yaw_semantics="angular_unwrapped"):
    times = times or [float(i) for i in range(len(x))]
    clocks = [origin + t for t in times]
    channels = {
        "chassis.x": series(clocks, x),
        "chassis.y": series(clocks, y if y is not None else [0.0] * len(x)),
        "chassis.yaw": series(clocks, yaw if yaw is not None else [0.0] * len(x), unit="rad", semantics=yaw_semantics),
        **(extra or {}),
    }
    return ReplayData(
        log_id="virtual-example", robot_id="robot", raw_file_ref="source.csv", raw_file_sha256="0" * 64,
        mapping_snapshot_id="mapping", mapping_snapshot={}, time_basis=TimeBasis(source_origin=1000.0, relative_origin_s=origin),
        canonical_order=list(CANONICAL_JOINTS), sample_times_s=clocks, channels=channels,
    )


def configuration(**changes):
    from app.adapters.mapping import WheelRollConfig
    return WheelRollConfig(**{
        "enabled": True, "wheel_radius_m": 0.5,
        "joint_signs": {WHEELS[0]: 1, WHEELS[1]: -1, WHEELS[2]: 1},
        **changes,
    })


def derive(data, config=None, *, max_gap_s=None):
    from app.replay.channel import PreparedChannel
    from app.replay.virtual_channels import prepare_virtual_wheels
    origin = data.sample_times_s[0]
    prepared = {
        name: PreparedChannel(channel.model_copy(update={
            "timestamps_s": [t - origin for t in channel.timestamps_s],
        }), max_gap_s=max_gap_s)
        for name, channel in data.channels.items()
    }
    return prepare_virtual_wheels(data, prepared, config or configuration())


def test_signed_forward_reverse_motion_respects_explicit_radius_signs_and_initial_reference():
    result = derive(trajectory([0.0, 1.0, 0.5, -0.5]), configuration(initial_angle_rad=0.25))
    assert result.channels[WHEELS[0]].values == pytest.approx([0.25, 2.25, 1.25, -0.75])
    assert result.channels[WHEELS[1]].values == pytest.approx([0.25, -1.75, -0.75, 1.25])
    for channel in result.channels.values():
        assert channel.unit == "rad"
        assert channel.angle_semantics == "angular_unwrapped"
        assert all(q.valid and q.origin == "derived" and "derived" in q.processing for q in channel.quality)
        assert all(q.method == "signed_body_forward_distance.v1" for q in channel.quality)


def test_disabled_derivation_returns_no_replacements():
    from app.adapters.mapping import WheelRollConfig
    result = derive(trajectory([0.0, 1.0]), WheelRollConfig())
    assert result.channels == {}
    assert result.metadata["status"] == "disabled"


@pytest.mark.parametrize("origin_kind", ["measured", "commanded", "derived"])
def test_any_valid_existing_wheel_channel_is_preserved_including_its_holes(origin_kind):
    qualities = [
        ChannelQuality(origin=origin_kind, valid=valid, processing=["derived"] if origin_kind == "derived" else ["original"],
                       method="existing-method" if origin_kind == "derived" else None)
        for valid in (False, True, False)
    ]
    wheel = series([0.0, 1.0, 2.0], [None, 11.0, None], unit="rad", semantics="angular_unwrapped", qualities=qualities)
    data = trajectory([0.0, 1.0, 2.0], extra={WHEELS[0]: wheel})
    result = derive(data)
    assert WHEELS[0] not in result.channels
    assert data.channels[WHEELS[0]].values == [None, 11.0, None]
    assert result.metadata["preserved_channels"] == [WHEELS[0]]
    assert result.channels[WHEELS[1]].values == [0.0, -2.0, -4.0]


def test_all_invalid_existing_wheel_values_are_replaced():
    wheel = series([0.0, 1.0], [99.0, 99.0], unit="rad", semantics="angular_unwrapped", qualities=[
        ChannelQuality(origin="missing", valid=False, processing=["defaulted"]) for _ in range(2)
    ])
    result = derive(trajectory([0.0, 1.0], extra={WHEELS[0]: wheel}))
    assert result.channels[WHEELS[0]].values == [0.0, 2.0]


@pytest.mark.parametrize(("x", "y", "yaw", "reason"), [
    ([0.0, 0.0, 1.0], [0.0, 1.0, 1.0], [0.0, 0.0, 0.0], "unsupported_lateral_motion"),
    ([0.0, 0.0, 1.0], [0.0, 0.0, 0.0], [0.0, 1.0, 1.0], "unsupported_pure_rotation"),
    ([0.0, 1.0, 2.0], [0.0, 0.1, 0.1], [0.0, 0.0, 0.0], "unsupported_lateral_motion"),
])
def test_unsupported_motion_invalidates_entire_future_prefix_without_restart(x, y, yaw, reason):
    result = derive(trajectory(x, y, yaw))
    for channel in result.channels.values():
        assert channel.values == [0.0, None, None]
        assert [q.valid for q in channel.quality] == [True, False, False]
    assert result.metadata["reason"] == reason


def test_pure_lateral_motion_remains_unsupported_even_with_ratio_one():
    result = derive(trajectory([0.0, 0.0], [0.0, 1.0]), configuration(max_lateral_ratio=1.0))
    assert result.channels[WHEELS[0]].values == [0.0, None]


def test_stationary_pose_is_valid_at_configured_initial_reference():
    result = derive(trajectory([4.0, 4.0], [2.0, 2.0], [1.0, 1.0]), configuration(initial_angle_rad=-3.0))
    assert result.channels[WHEELS[0]].values == [-3.0, -3.0]
    assert all(q.valid for q in result.channels[WHEELS[0]].quality)


def test_arc_is_center_distance_visual_approximation_with_limitations_metadata():
    # Quarter-circle chord (1, 1), tangent midpoint 45 degrees: distance sqrt(2).
    result = derive(trajectory([0.0, 1.0], [0.0, 1.0], [0.0, math.pi / 2]), configuration(wheel_radius_m=1.0))
    assert result.channels[WHEELS[0]].values == pytest.approx([0.0, 1.4142135623730951])
    assert result.metadata["model"] == "signed_body_forward_distance.v1"
    assert any("steering" in limitation and "offset" in limitation for limitation in result.metadata["limitations"])


def test_wrapped_yaw_branch_uses_lifted_midpoint_for_signed_direction():
    result = derive(trajectory([0.0, -1.0], yaw=[math.radians(179), math.radians(-179)], yaw_semantics="angular_wrapped"))
    assert result.channels[WHEELS[0]].values == pytest.approx([0.0, 2.0])


def test_zero_lateral_budget_accepts_aligned_motion_despite_trigonometric_roundoff():
    result = derive(trajectory([0.0, 0.0], [0.0, 1.0], [math.pi / 2, math.pi / 2]),
                    configuration(max_lateral_ratio=0.0))
    assert result.channels[WHEELS[0]].values == pytest.approx([0.0, 2.0])


def test_nonzero_log_origin_is_subtracted_once_on_union_clock():
    result = derive(trajectory([0.0, 1.0, 3.0], times=[0.0, 0.5, 2.0], origin=12.5))
    assert result.channels[WHEELS[0]].timestamps_s == [0.0, 0.5, 2.0]
    assert result.channels[WHEELS[0]].values == [0.0, 2.0, 6.0]


def test_missing_pose_interval_prevents_later_cumulative_restart():
    result = derive(trajectory([0.0, 1.0, None, 3.0, 4.0]))
    assert result.channels[WHEELS[0]].values == [0.0, 2.0, None, None, None]
    assert result.metadata["reason"] == "chassis_sample_unavailable"


def test_missing_initial_pose_never_invents_a_valid_zero():
    result = derive(trajectory([None, 1.0, 2.0]))
    assert result.channels[WHEELS[0]].values == [None, None, None]
    assert all(not q.valid for q in result.channels[WHEELS[0]].quality)


def test_absent_chassis_axis_has_no_valid_wheel_estimates():
    data = trajectory([0.0, 1.0])
    del data.channels["chassis.y"]
    result = derive(data)
    assert result.channels[WHEELS[0]].values == [None, None]
    assert result.metadata["reason"] == "chassis_channel_missing"


def test_maximum_segment_duration_invalidates_future_even_after_shorter_intervals():
    result = derive(trajectory([0.0, 1.0, 2.0], times=[0.0, 2.0, 2.5]), configuration(max_segment_s=1.0))
    assert result.channels[WHEELS[0]].values == [0.0, None, None]
    assert result.metadata["reason"] == "segment_too_long"


def test_provenance_carries_prior_processing_but_bounds_indices_to_current_union_interval():
    data = trajectory([0.0, 1.0, 2.0, 3.0])
    data.channels["chassis.x"].quality[1] = ChannelQuality(origin="measured", valid=True, processing=["forward_filled", "unit_converted"], source_indices=[70])
    result = derive(data)
    quality = result.channels[WHEELS[0]].quality
    assert quality[0].source_indices == (0,)
    assert quality[-1].source_indices == (2, 3)
    assert set(quality[-1].processing) == {"forward_filled", "unit_converted", "derived"}
    assert result.metadata["all_prior_trajectory_contributes"] is True
    assert "sample_times_s" in result.metadata["source_indices_semantics"]


def test_union_interpolation_propagates_interpolated_processing():
    data = trajectory([0.0, 1.0, 2.0])
    data.channels["chassis.x"] = series([0.0, 2.0], [0.0, 2.0])
    result = derive(data)
    assert result.channels[WHEELS[0]].values == [0.0, 2.0, 4.0]
    assert "interpolated" in result.channels[WHEELS[0]].quality[-1].processing


def test_channel_gap_limit_rejects_union_interpolation_and_all_later_values():
    data = trajectory([0.0, 1.0, 2.0])
    data.channels["chassis.x"] = series([0.0, 2.0], [0.0, 2.0])
    result = derive(data, max_gap_s=1.0)
    assert result.channels[WHEELS[0]].values == [0.0, None, None]


def test_wheel_arithmetic_overflow_is_invalid_and_does_not_recover():
    result = derive(trajectory([0.0, 1.0, 2.0, 0.0]), configuration(wheel_radius_m=1e-308))
    values = result.channels[WHEELS[0]].values
    assert values[0] == 0.0
    assert values[1] == pytest.approx(1e308)
    assert values[2:] == [None, None]
    assert result.metadata["reason"] == "numeric_overflow"


def test_coordinate_subtraction_overflow_is_unavailable_without_exception():
    result = derive(trajectory([-1e308, 1e308]))
    assert result.channels[WHEELS[0]].values == [0.0, None]
    assert result.metadata["reason"] == "numeric_overflow"


def test_derivation_leaves_source_and_configuration_detached_and_unmodified():
    data = trajectory([0.0, 1.0])
    config = configuration()
    data_before = data.model_dump(mode="json")
    config_before = config.model_dump(mode="json")
    result = derive(data, config)
    result.metadata["joint_signs"][WHEELS[0]] = -1
    result.channels[WHEELS[0]].values[0] = 99.0
    assert data.model_dump(mode="json") == data_before
    assert config.model_dump(mode="json") == config_before
