import math
import unittest
import tempfile
from pathlib import Path

from scripts.spike.smoke import decode_marker_rgb, sample_pose
from scripts.spike.smoke import external_dependencies
from scripts.spike.smoke import runtime_pose
from scripts.spike.smoke import prepare_output, quaternion_angle_error
from scripts.spike.smoke import loop_statistics
from scripts.spike.smoke import capture_stamp


class SmokeHelpersTests(unittest.TestCase):
    def test_snapshot_transport_failure_cannot_be_reported_as_success(self):
        from scripts.spike.smoke import snapshot_transport_error
        self.assertIsNone(snapshot_transport_error({'prepared_frames':3,'stream_failed':False,'stream_errors':0}))
        self.assertIn('failed',snapshot_transport_error({'prepared_frames':3,'stream_failed':True,'stream_errors':1}))
        self.assertIsNotNone(snapshot_transport_error({'prepared_frames':0,'stream_failed':False,'stream_errors':0}))

    def test_capture_stamp_keeps_full_id_and_crc_without_modulo_alias(self):
        self.assertEqual(capture_stamp(1), (0x5643ef8a << 32) | 1)
        self.assertEqual(capture_stamp(0x12345678), (0x4a090e98 << 32) | 0x12345678)
        self.assertNotEqual(capture_stamp(1), capture_stamp(4097))
        with self.assertRaises(ValueError):capture_stamp(0)
        with self.assertRaises(ValueError):capture_stamp(2**32)

    def test_rtsp_statistics_do_not_claim_encoded_or_verified_frames(self):
        stats = loop_statistics('rtsp', 30, 2.0, [.01, .03], [0, 0])
        self.assertEqual(stats['render_iterations'], 30)
        self.assertEqual(stats['server_loop_fps'], 15)
        self.assertAlmostEqual(stats['mean_render_iteration_ms'], 20)
        for key in ['frames', 'published_fps', 'mean_capture_ms', 'mean_jpeg_bytes']:
            self.assertNotIn(key, stats)
        paired = loop_statistics('paired', 10, 2.0, [.02], [100])
        self.assertEqual(paired['frames'], 10)
        self.assertEqual(paired['published_fps'], 5)

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

    def test_self_contained_62_package_cannot_whitelist_external_mdl(self):
        self.assertEqual(external_dependencies('/project/assets', ['/isaac/OmniPBR.mdl'], None),
                         ['/isaac/OmniPBR.mdl'])

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
