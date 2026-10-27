"""Independent validation must detect incorrect transforms and quality upgrades."""
import copy
import importlib.util
import json
import math
from pathlib import Path

import pytest

from app.adapters.registry import AdapterRegistry
from test_adapter import bundle


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_b041.py"


def validator():
    assert SCRIPT.is_file(), "Independent validation script is not implemented"
    spec = importlib.util.spec_from_file_location("validate_b041", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile():
    return json.loads((SCRIPT.parents[1] / "configs/mappings/b041_provisional_v1.json").read_text())


def test_complex_trajectory_oracle_has_literal_curved_endpoints_and_latches():
    module = validator()
    config = {"initial_pose": [0., 0., 0.], "velocity_scale": [1., 1., 1.],
              "velocity_bias": [0., 0., 0.], "max_gap_s": 1.1, "velocity_frame": "body"}
    poses, valid = module.oracle_pose([0., 1., 2., 4., 5.], [[1., 0., math.pi/2]] * 5, [True] * 5, config)
    assert poses[1] == pytest.approx([2/math.pi, 2/math.pi, math.pi/2])
    assert poses[2] == pytest.approx([0., 4/math.pi, math.pi])
    assert poses[3] == poses[2] == poses[4]
    assert valid == [True, True, True, False, False]


def test_comparison_detects_signed_gripper_and_invalid_action_corruption(tmp_path):
    module = validator()
    value = bundle()
    path = tmp_path / "recording.b041"
    path.write_text(json.dumps(value))
    mapping = profile()
    data = AdapterRegistry().load(path, mapping)
    report = module.compare_recording(value, data, mapping)
    assert report["state_or_action_samples"] == 3
    assert report["canonical_mapped"] == 22
    assert report["raw_finite_invalid_points"] == 21
    assert report["source_origin_exact_s"] == "1800000000.000000001"
    changed = copy.deepcopy(data)
    changed.channels["left_gripper_finger_joint2"].values[1] = .025
    with pytest.raises(AssertionError, match="left_gripper_finger_joint2"):
        module.compare_recording(value, changed, mapping)
    changed = copy.deepcopy(data)
    qualities = changed.channels["raw.action.left_arm_joint1"].quality
    qualities[0] = qualities[0].model_copy(update={"valid": True})
    with pytest.raises(AssertionError, match="raw.action.left_arm_joint1"):
        module.compare_recording(value, changed, mapping)


def test_action_basis_preserves_finite_invalid_targets_in_archive_but_not_pose(tmp_path):
    module = validator()
    value = bundle()
    mapping = profile()
    mapping["b041"]["basis"] = "action"
    path = tmp_path / "recording.b041"
    path.write_text(json.dumps(value))
    data = AdapterRegistry().load(path, mapping)
    report = module.compare_recording(value, data, mapping)
    first = report["checkpoints"][0]
    assert first["raw"][0] == 999.
    assert first["channels"]["left_arm_joint1"]["mapped"] == 999.
    assert not first["channels"]["left_arm_joint1"]["quality"]["valid"]
    assert first["channels"]["left_arm_joint1"]["sampled"] == 0.


def test_validation_checks_sampler_and_analysis_gap_and_pose_latch(tmp_path):
    module = validator()
    value = bundle()
    value["annotations"].update(alignment="none", segments=[])
    value["samples"][2]["time"] = str(int(value["samples"][0]["time"]) + 500000000)
    for basis in ("state", "action"):
        for quality in value["samples"][2][basis + "_quality"]:
            if quality["valid"]:
                quality["source_time"] = value["samples"][2]["time"]
    path = tmp_path / "recording.b041"
    path.write_text(json.dumps(value))
    data = AdapterRegistry().load(path, profile())
    report = module.compare_recording(value, data, profile())
    assert report["gap_count"] == 1
    assert report["gap_checks"][0]["sampler_valid"] is False
    assert report["gap_checks"][0]["analysis_break"] is True
    assert report["trajectory"]["valid_samples"] == 2
    assert report["trajectory"]["invalid_latched_samples"] == 1


def test_safe_component_rejects_traversal_and_destination_reuse(tmp_path):
    module = validator()
    for bad in ("../raw", "a/b", "", ".", ".."):
        with pytest.raises(ValueError):
            module.safe_component(bad)
    assert module.safe_component("B041-safe_2026") == "B041-safe_2026"
    with pytest.raises(FileExistsError):
        module.new_directory(tmp_path)
