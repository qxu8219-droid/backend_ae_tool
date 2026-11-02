"""Independent T08 acceptance cases with manually calculated source expectations.

These tests deliberately use RawTable so parser behavior cannot hide normalization
faults. Source indices refer to positions in the unmodified input, not kept rows.
"""

import math
from pathlib import Path

import pytest

from app.adapters.base import AdapterError, ParseLimits, RawColumn, RawTable
from app.adapters.mapping import parse_mapping
from app.adapters.normalize import normalize_table
from replay_protocol import CANONICAL_JOINTS


ARM = "left_arm_joint1"
OTHER_ARM = "right_arm_joint1"


def normalize(tmp_path: Path, columns, mapping=None):
    raw = tmp_path / "source.csv"
    raw.write_bytes(b"independent normalization acceptance source\n")
    return normalize_table(
        RawTable(columns=columns, layout="acceptance"),
        raw,
        parse_mapping(mapping),
        limits=ParseLimits(),
        parser="csv_v1",
    )


def test_epoch_nanoseconds_keep_relative_precision_and_original_source_indices(tmp_path):
    epoch = 1_761_200_000_000_000_000
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn(
                [epoch, epoch, epoch - 1, epoch + 100, epoch + 50, epoch + 200],
                lines=[11, 12, 13, 14, 15, 16],
            ),
            ARM: RawColumn([10, 11, 12, 13, 14, 15], lines=[11, 12, 13, 14, 15, 16]),
        },
        {"timestamp": {"unit": "ns", "start_offset": 0.5}},
    )
    channel = data.channels[ARM]
    assert data.sample_times_s == pytest.approx([0.5, 0.5000001, 0.5000002], abs=1e-16, rel=0)
    assert channel.values == [10.0, 13.0, 15.0]
    assert [quality.source_indices for quality in channel.quality] == [(0,), (3,), (5,)]
    assert data.metadata["source_rows"][ARM] == [11, 12, 13, 14, 15, 16]
    # Rejected duplicate/backward records remain attributable to physical lines.
    rejected_lines = {
        issue["line"]
        for issue in data.metadata["issues"]
        if issue["severity"] in {"warning", "error"}
    }
    assert {12, 13, 15} <= rejected_lines


def test_missing_leading_and_long_gap_values_never_become_measured_zero(tmp_path):
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([0, 0.1, 0.2, 0.35, 0.36, 0.7, 0.8]),
            ARM: RawColumn([None, 1, None, None, None, None, 2]),
        },
        {"options": {"max_forward_fill_s": 0.25}},
    )
    channel = data.channels[ARM]
    assert channel.values == [None, 1.0, 1.0, 1.0, None, None, 2.0]
    assert [quality.valid for quality in channel.quality] == [False, True, True, True, False, False, True]
    for index in (2, 3):
        assert "forward_filled" in channel.quality[index].processing
        assert "original" not in channel.quality[index].processing
        assert 1 in channel.quality[index].source_indices
    for index in (0, 4, 5):
        assert not channel.quality[index].valid
        assert "forward_filled" not in channel.quality[index].processing


def test_zero_forward_fill_disables_reuse_even_for_submillisecond_gaps(tmp_path):
    data = normalize(
        tmp_path,
        {"timestamp": RawColumn([0, 0.00001]), ARM: RawColumn([2, None])},
        {"options": {"max_forward_fill_s": 0.0}},
    )
    assert data.channels[ARM].values == [2.0, None]
    assert not data.channels[ARM].quality[1].valid


def test_nonfinite_values_are_holes_and_wholly_invalid_channel_is_unavailable(tmp_path):
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([0, 0.1, 0.2]),
            ARM: RawColumn([1, float("nan"), float("inf")]),
            OTHER_ARM: RawColumn([None, float("nan"), float("-inf")]),
        },
    )
    assert data.channels[ARM].values == [1.0, 1.0, 1.0]
    assert data.channels[OTHER_ARM].values == [None, None, None]
    assert all(not item.valid for item in data.channels[OTHER_ARM].quality)
    assert OTHER_ARM in data.metadata["stats"]["joints_missing"]
    assert any(
        issue["channel"] == OTHER_ARM and issue["severity"] == "warning"
        for issue in data.metadata["issues"]
    )


def test_absent_canonical_joint_has_explicit_invalid_default_quality(tmp_path):
    data = normalize(tmp_path, {"timestamp": RawColumn([0, 1]), ARM: RawColumn([2, 3])})
    assert data.canonical_order == list(CANONICAL_JOINTS)
    channel = data.channels[OTHER_ARM]
    assert channel.values == [0.0, 0.0]
    assert all(not quality.valid and quality.origin == "missing" for quality in channel.quality)
    assert all("defaulted" in quality.processing for quality in channel.quality)
    assert OTHER_ARM in data.metadata["stats"]["joints_missing"]


@pytest.mark.parametrize("bad", ["not-a-number", True, {"value": 1}, [1]])
def test_mapped_joint_does_not_silently_coerce_text_boolean_or_container(tmp_path, bad):
    with pytest.raises(AdapterError) as caught:
        normalize(tmp_path, {"timestamp": RawColumn([0, 1]), ARM: RawColumn([1, bad], lines=[8, 9])})
    assert caught.value.code == "FIELD_NOT_NUMERIC"
    assert any(issue.line == 9 and issue.channel == ARM for issue in caught.value.issues)


def test_wholly_invalid_joint_set_is_fatal_instead_of_all_zero_replay(tmp_path):
    with pytest.raises(AdapterError) as caught:
        normalize(tmp_path, {"timestamp": RawColumn([0, 1]), ARM: RawColumn([None, float("nan")])})
    assert caught.value.code == "NO_JOINT_CHANNELS"


def test_valid_custom_scalar_cannot_rescue_a_log_without_valid_joint_data(tmp_path):
    with pytest.raises(AdapterError) as caught:
        normalize(
            tmp_path,
            {
                "timestamp": RawColumn([0, 1]),
                ARM: RawColumn([None, None]),
                "battery_v": RawColumn([48, 47]),
            },
            {"custom_channels": {"battery": {"field": "battery_v", "unit": "V"}}},
        )
    assert caught.value.code == "NO_JOINT_CHANNELS"


def test_independent_clock_keeps_irregular_samples_and_drops_outside_reference_bounds(tmp_path):
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([1000, 1002], lines=[1, 2]),
            ARM: RawColumn(
                [90, 0, 5, 17, 20, 30],
                timestamps=[999, 1000, 1000.5, 1001.7, 1002, 1003],
                lines=[10, 11, 12, 13, 14, 15],
            ),
        },
    )
    channel = data.channels[ARM]
    assert channel.timestamps_s == pytest.approx([0, 0.5, 1.7, 2], abs=1e-12)
    assert channel.values == [0.0, 5.0, 17.0, 20.0]
    assert [quality.source_indices for quality in channel.quality] == [(1,), (2,), (3,), (4,)]
    assert data.sample_times_s == pytest.approx([0, 0.5, 1.7, 2], abs=1e-12)
    assert {10, 15} <= {
        issue["line"] for issue in data.metadata["issues"]
        if issue["channel"] == ARM and issue["severity"] in {"warning", "error"}
    }


def test_finite_out_of_limit_joint_remains_raw_value_for_later_warning(tmp_path):
    data = normalize(tmp_path, {"timestamp": RawColumn([0, 1]), ARM: RawColumn([-100, 100])})
    assert data.channels[ARM].values == [-100.0, 100.0]
    assert all(quality.valid for quality in data.channels[ARM].quality)


def test_angle_and_prismatic_transforms_apply_unit_then_sign_scale_and_si_offset(tmp_path):
    left_1 = "left_gripper_finger_joint1"
    left_2 = "left_gripper_finger_joint2"
    right_1 = "right_gripper_finger_joint1"
    right_2 = "right_gripper_finger_joint2"
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([0, 1]),
            "shoulder_degrees": RawColumn([90, -180]),
            "left_opening_mm": RawColumn([10, 20]),
            "left_opening_other_mm": RawColumn([-10, -20]),
            "right_opening_mm": RawColumn([30, 40]),
            "right_opening_other_mm": RawColumn([-30, -40]),
        },
        {
            "joints": {
                "unit": "deg",
                "origin": "commanded",
                "map": {
                    ARM: "shoulder_degrees",
                    left_1: "left_opening_mm",
                    left_2: "left_opening_other_mm",
                    right_1: "right_opening_mm",
                    right_2: "right_opening_other_mm",
                },
                "per_joint_overrides": {
                    ARM: {"scale": -2.0, "offset": 0.5},
                    left_1: {"unit": "mm", "scale": -2.0, "offset": 0.1},
                    left_2: {"unit": "mm"},
                    right_1: {"unit": "mm"},
                    right_2: {"unit": "mm"},
                },
            },
        },
    )
    assert data.channels[ARM].values == pytest.approx([0.5 - math.pi, 0.5 + 2 * math.pi])
    assert data.channels[left_1].values == pytest.approx([0.08, 0.06])
    assert data.channels[left_2].values == pytest.approx([-0.01, -0.02])
    assert data.channels[right_1].values == pytest.approx([0.03, 0.04])
    assert data.channels[right_2].values == pytest.approx([-0.03, -0.04])
    for name in (ARM, left_1, left_2, right_1, right_2):
        assert all(item.origin == "commanded" for item in data.channels[name].quality)
        assert all("unit_converted" in item.processing for item in data.channels[name].quality)
    for name in (left_1, left_2, right_1, right_2):
        assert data.channels[name].unit == "m"
        assert data.channels[name].angle_semantics == "linear"


def test_global_angle_unit_never_turns_default_prismatic_meters_into_radians(tmp_path):
    gripper = "left_gripper_finger_joint1"
    data = normalize(
        tmp_path,
        {"timestamp": RawColumn([0, 1]), ARM: RawColumn([90, 180]), gripper: RawColumn([0.01, -0.02])},
        {"joints": {"unit": "deg"}},
    )
    assert data.channels[ARM].values == pytest.approx([math.pi / 2, math.pi])
    assert data.channels[gripper].values == [0.01, -0.02]
    assert data.channels[gripper].unit == "m"


def chassis_mapping():
    return {
        "chassis": {
            "available": True,
            "frame": "odom",
            "map": {"x": "pos_x_mm", "y": "pos_y_mm", "yaw": "heading_deg"},
            "position_unit": "mm",
            "yaw_unit": "deg",
            "transform": {
                "rotation_rad": math.pi / 2,
                "translation_x_m": 2.0,
                "translation_y_m": -1.0,
            },
        },
    }


def test_explicit_chassis_transform_rotates_then_translates_and_offsets_yaw(tmp_path):
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([0, 1]),
            ARM: RawColumn([1, 2]),
            "pos_x_mm": RawColumn([1000, 0]),
            "pos_y_mm": RawColumn([0, 1000]),
            "heading_deg": RawColumn([90, -90]),
        },
        chassis_mapping(),
    )
    assert data.channels["chassis.x"].values == pytest.approx([2, 1])
    assert data.channels["chassis.y"].values == pytest.approx([0, -1], abs=1e-12)
    assert data.channels["chassis.yaw"].values == pytest.approx([math.pi, 0])
    assert data.metadata["stats"]["chassis"]["available"] is True
    assert data.metadata["stats"]["chassis"]["frame"] == "world"
    # An effective mapping snapshot records the source frame and transform.
    assert data.mapping_snapshot["chassis"]["frame"] == "odom"
    assert data.mapping_snapshot["chassis"]["transform"]["rotation_rad"] == math.pi / 2


def test_missing_chassis_axis_cannot_be_reported_as_available_pose(tmp_path):
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([0, 1]),
            ARM: RawColumn([1, 2]),
            "pos_x_mm": RawColumn([1000, 0]),
            "heading_deg": RawColumn([90, -90]),
        },
        chassis_mapping(),
    )
    assert data.metadata["stats"]["chassis"]["available"] is False
    assert any(issue["severity"] == "warning" for issue in data.metadata["issues"])
    if "chassis.y" in data.channels:
        assert all(not quality.valid for quality in data.channels["chassis.y"].quality)


def test_one_invalid_chassis_axis_invalidates_whole_pose_at_that_sample(tmp_path):
    data = normalize(
        tmp_path,
        {
            "timestamp": RawColumn([0, 0.4, 0.8]),
            ARM: RawColumn([1, 2, 3]),
            "pos_x_mm": RawColumn([1000, 2000, 3000]),
            "pos_y_mm": RawColumn([0, None, 1000]),
            "heading_deg": RawColumn([90, 0, -90]),
        },
        chassis_mapping(),
    )
    for name in ("chassis.x", "chassis.y", "chassis.yaw"):
        assert data.channels[name].values[1] is None
        assert data.channels[name].quality[1].valid is False
        assert data.channels[name].quality[0].valid is True
        assert data.channels[name].quality[2].valid is True


def test_incompatible_independent_chassis_grids_are_rejected_without_interpolation(tmp_path):
    with pytest.raises(AdapterError):
        normalize(
            tmp_path,
            {
                "timestamp": RawColumn([0, 1]),
                ARM: RawColumn([1, 2]),
                "pos_x_mm": RawColumn([1000, 0], timestamps=[0, 1]),
                "pos_y_mm": RawColumn([0, 1000], timestamps=[0.1, 0.9]),
                "heading_deg": RawColumn([90, -90], timestamps=[0, 1]),
            },
            chassis_mapping(),
        )
