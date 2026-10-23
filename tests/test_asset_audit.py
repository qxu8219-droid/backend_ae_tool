"""Filesystem-level checks; no Isaac Sim installation is required."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.spike.asset_audit import audit_asset


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/spike/asset_audit.py"
COMMON = "reproduction/configs/r1pro_2026_common.json"


class AssetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "asset"
        self.root.mkdir()
        names = [
            "steer_motor_joint1", "wheel_motor_joint1",
            "steer_motor_joint2", "wheel_motor_joint2",
            "steer_motor_joint3", "wheel_motor_joint3",
            "torso_joint1", "torso_joint2", "torso_joint3", "torso_joint4",
            "left_arm_joint1", "left_arm_joint2", "left_arm_joint3",
            "left_arm_joint4", "left_arm_joint5", "left_arm_joint6", "left_arm_joint7",
            "left_gripper_finger_joint1", "left_gripper_finger_joint2",
            "right_arm_joint1", "right_arm_joint2", "right_arm_joint3",
            "right_arm_joint4", "right_arm_joint5", "right_arm_joint6", "right_arm_joint7",
            "right_gripper_finger_joint1", "right_gripper_finger_joint2",
        ]
        self.joints = {
            name: {"type": "continuous" if name.startswith("wheel") else
                   "prismatic" if "finger" in name else "revolute"}
            for name in reversed(names)
        }
        self.write("r1pro_2026.usd", '#usda 1.0\n(subLayers = [@layers/body.usda@])\n')
        self.write("layers/body.usda", '#usda 1.0\ndef Xform "robot" {}\n')
        self.write_config()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_config(self):
        self.write(COMMON, json.dumps({"joints": self.joints, "links": {"large": "unused"}}))
        self.manifest()

    def manifest(self):
        lines = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.name != "CHECKSUMS.sha256":
                lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
                             f"{path.relative_to(self.root).as_posix()}\n")
        self.write("CHECKSUMS.sha256", "".join(lines))

    def assert_rejected(self, fragment):
        result = audit_asset(self.root)
        self.assertFalse(result["passed"], result)
        self.assertIn(fragment, "\n".join(result["errors"]))

    def test_valid_copy_maps_shuffled_json_by_joint_name_and_returns_summary(self):
        result = audit_asset(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["checksums"]["verified"], 3)
        self.assertEqual(result["joints"]["count"], 28)
        self.assertEqual(result["joints"]["type_counts"],
                         {"revolute": 21, "continuous": 3, "prismatic": 4})
        self.assertEqual(result["joints"]["canonical_names"][:4],
                         ["steer_motor_joint1", "wheel_motor_joint1",
                          "steer_motor_joint2", "wheel_motor_joint2"])
        self.assertEqual(result["joints"]["types_by_name"]["wheel_motor_joint1"], "continuous")
        self.assertEqual(result["joints"]["types_by_name"]["left_gripper_finger_joint2"], "prismatic")
        self.assertNotIn("links", result)
        self.assertEqual(result["dependencies"]["references_checked"], 1)
        self.assertFalse(result["dependencies"]["usd_closure_verified"])

    def test_checksum_mismatch_is_rejected(self):
        self.write("layers/body.usda", "#usda 1.0\n# altered\n")
        self.assert_rejected("checksum mismatch: layers/body.usda")

    def test_missing_manifest_file_is_rejected(self):
        (self.root / "layers/body.usda").unlink()
        self.assert_rejected("missing file: layers/body.usda")

    def test_manifest_rejects_paths_outside_asset_root(self):
        for name in ("../outside", "/tmp/outside", "C:\\outside", "\\\\host\\share"):
            with self.subTest(name=name):
                self.write("CHECKSUMS.sha256", "0" * 64 + "  " + name + "\n")
                self.assert_rejected("unsafe manifest path")

    def test_manifest_rejects_malformed_duplicate_and_empty_entries(self):
        line = (self.root / "CHECKSUMS.sha256").read_text().splitlines()[0]
        for content, expected in (("not a digest\n", "invalid checksum line"),
                                  (line + "\n" + line + "\n", "duplicate checksum path"),
                                  ("", "empty checksum manifest")):
            with self.subTest(expected=expected):
                self.write("CHECKSUMS.sha256", content)
                self.assert_rejected(expected)

    def test_unlisted_file_is_rejected(self):
        self.write("unexpected.txt", "not covered by manifest")
        self.assert_rejected("unlisted file: unexpected.txt")

    def test_symlink_is_rejected_even_when_target_is_inside_asset(self):
        (self.root / "linked.usda").symlink_to("layers/body.usda")
        self.manifest()
        self.assert_rejected("symlink: linked.usda")

    def test_symlinked_directory_is_rejected_without_following_it(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("external", encoding="utf-8")
        (self.root / "escape").symlink_to(outside, target_is_directory=True)
        self.assert_rejected("symlink: escape")

    def test_symlink_loop_is_rejected_without_crashing(self):
        entrypoint = self.root / "r1pro_2026.usd"
        entrypoint.unlink()
        entrypoint.symlink_to("r1pro_2026.usd")
        self.assert_rejected("symlink: r1pro_2026.usd")

    def test_symlinked_asset_root_is_rejected_without_resolving_a_loop(self):
        root = Path(self.temp.name) / "loop"
        root.symlink_to("loop")
        result = audit_asset(root)
        self.assertFalse(result["passed"])
        self.assertIn("symlink", "\n".join(result["errors"]))

    def test_text_reference_to_symlink_loop_is_rejected_without_crashing(self):
        (self.root / "loop.usda").symlink_to("loop.usda")
        self.write("layers/body.usda", '#usda 1.0\n(subLayers = [@../loop.usda@])\n')
        self.manifest()
        self.assert_rejected("symlink: loop.usda")

    def test_missing_entrypoint_is_rejected_even_if_manifest_matches(self):
        (self.root / "r1pro_2026.usd").unlink()
        self.manifest()
        self.assert_rejected("missing entrypoint")

    def test_wrong_joint_names_with_correct_count_are_rejected(self):
        self.joints["unknown_joint"] = self.joints.pop("torso_joint4")
        self.write_config()
        self.assert_rejected("joint names differ")

    def test_wrong_joint_count_is_rejected(self):
        self.joints.pop("torso_joint4")
        self.write_config()
        self.assert_rejected("expected 28 joints")

    def test_swapped_types_are_rejected_even_with_correct_type_totals(self):
        self.joints["steer_motor_joint1"]["type"] = "continuous"
        self.joints["wheel_motor_joint1"]["type"] = "revolute"
        self.write_config()
        self.assert_rejected("joint type mismatch: wheel_motor_joint1")

    def test_wrong_type_totals_are_rejected(self):
        self.joints["wheel_motor_joint1"]["type"] = "revolute"
        self.write_config()
        self.assert_rejected("joint type counts differ")

    def test_duplicate_json_joint_names_are_rejected(self):
        text = (self.root / COMMON).read_text()
        self.write(COMMON, text.replace('"joints": {',
                                       '"joints": {"torso_joint1": {"type": "revolute"},', 1))
        self.manifest()
        self.assert_rejected("duplicate JSON key: torso_joint1")

    def test_malformed_config_is_rejected(self):
        for content in ("not json", "[]", '{"joints": []}', '{"joints": {"x": null}}'):
            with self.subTest(content=content):
                self.write(COMMON, content)
                self.manifest()
                self.assert_rejected("common config")

    def test_external_text_asset_references_are_rejected(self):
        for reference in ("/tmp/outside.usda", "../../outside.usda", "C:/outside.usda",
                          "file:///tmp/outside.usda", "omniverse://host/asset.usd"):
            with self.subTest(reference=reference):
                self.write("layers/body.usda", '#usda 1.0\n(subLayers = [@' + reference + '@])\n')
                self.manifest()
                self.assert_rejected("external asset reference")

    def test_missing_text_asset_reference_is_rejected(self):
        self.write("layers/body.usda", '#usda 1.0\n(subLayers = [@missing.usda@])\n')
        self.manifest()
        self.assert_rejected("missing asset reference")

    def test_binary_usd_is_reported_as_unparsed(self):
        (self.root / "layers/body.usda").write_bytes(b"PXR-USDC\x00\x08\x00")
        self.manifest()
        result = audit_asset(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["dependencies"]["binary_layers_not_parsed"], ["layers/body.usda"])
        self.assertFalse(result["dependencies"]["usd_closure_verified"])

    def test_cli_writes_report_and_returns_failure_status_for_invalid_asset(self):
        output = Path(self.temp.name) / "report/audit.json"
        command = [sys.executable, str(SCRIPT), "--asset-dir", str(self.root), "--output", str(output)]
        success = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertTrue(json.loads(output.read_text())["passed"])
        self.write("layers/body.usda", "changed")
        failure = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(failure.returncode, 1, failure.stderr)
        self.assertFalse(json.loads(output.read_text())["passed"])


if __name__ == "__main__":
    unittest.main()
