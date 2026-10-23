import math
import unittest
import tempfile
from pathlib import Path

from scripts.spike.smoke import decode_marker_rgb, sample_pose
from scripts.spike.smoke import external_dependencies
from scripts.spike.smoke import runtime_pose
from scripts.spike.smoke import prepare_output, quaternion_angle_error


class SmokeHelpersTests(unittest.TestCase):
    def test_nonempty_run_directory_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'run'
            prepare_output(p)
            (p / 'latest.frame').write_bytes(b'old-frame')
            with self.assertRaises(ValueError):
                prepare_output(p)
            self.assertEqual((p / 'latest.frame').read_bytes(), b'old-frame')

    def test_yaw_mismatch_is_not_accepted_as_matching_pose(self):
        q = [math.cos(.35), 0, 0, math.sin(.35)]
        self.assertAlmostEqual(quaternion_angle_error([1,0,0,0], q), .7)
        self.assertAlmostEqual(quaternion_angle_error(q, [-x for x in q]), 0)
        with self.assertRaises(ValueError):
            quaternion_angle_error([0,0,0,0], q)

    def test_runtime_wraps_only_continuous_wheels_without_changing_source(self):
        raw = [3 * math.pi, -3 * math.pi, 3.2]
        result = runtime_pose(raw, ['wheel_motor_joint1', 'wheel_motor_joint2', 'torso_joint4'])
        self.assertAlmostEqual(result[0], -math.pi)
        self.assertAlmostEqual(result[1], -math.pi)
        self.assertEqual(result[2], 3.2)
        self.assertEqual(raw[0], 3 * math.pi)

    def test_dependency_audit_allows_only_exact_runtime_builtin(self):
        paths = ['/project/assets/robot.usd', '/isaac/kit/mdl/OmniPBR.mdl',
                 '/other/OmniPBR.mdl', '/source/texture.png']
        self.assertEqual(external_dependencies('/project/assets', paths,
                         '/isaac/kit/mdl/OmniPBR.mdl'),
                         ['/other/OmniPBR.mdl', '/source/texture.png'])

    def test_pose_maps_by_name_and_grippers_have_opposite_signs(self):
        names = ['right_gripper_finger_joint2', 'left_arm_joint2',
                 'left_gripper_finger_joint1', 'right_arm_joint2']
        pose = sample_pose(0.0, names)
        self.assertAlmostEqual(pose[0], -0.025)
        self.assertAlmostEqual(pose[1], 0.35)
        self.assertAlmostEqual(pose[2], 0.025)
        self.assertAlmostEqual(pose[3], -0.35)

    def test_pose_is_repeatable_for_backward_seek(self):
        names = ['wheel_motor_joint1', 'torso_joint4']
        first = sample_pose(2.0, names)
        sample_pose(10.0, names)
        self.assertEqual(first, sample_pose(2.0, names))
        self.assertAlmostEqual(first[0], 2.0)

    def test_pose_rejects_unknown_joint_and_nonfinite_time(self):
        with self.assertRaises(ValueError):
            sample_pose(0.0, ['unknown_joint'])
        with self.assertRaises(ValueError):
            sample_pose(math.nan, ['torso_joint1'])

    def test_marker_decodes_pixels_not_metadata(self):
        self.assertEqual(decode_marker_rgb([(240, 20, 10), (20, 240, 10), (20, 240, 10)]), 6)
        self.assertEqual(decode_marker_rgb([(20, 240, 10), (240, 20, 10)]), 1)
        with self.assertRaises(ValueError):
            decode_marker_rgb([(100, 101, 100)])


if __name__ == '__main__':
    unittest.main()
