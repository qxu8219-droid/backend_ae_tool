import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.spike.runtime_profile import load_profile, check_runtime, physics_launch_args, check_asset_version


class RuntimeProfileTests(unittest.TestCase):
    def test_launcher_rejects_relative_interpreter_before_changing_directory(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/run_p0.sh'
        with tempfile.TemporaryDirectory() as temp:
            stub = Path(temp) / 'python'
            stub.write_text('#!/bin/sh\nexit 99\n')
            stub.chmod(0o755)
            result = subprocess.run(['bash', str(script)], cwd=temp,
                env=dict(os.environ, ISAACSIM_PYTHON='./python'), text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('absolute path', result.stderr)

    def test_rejects_a_valid_asset_exported_for_another_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / 'conversion.json'
            metadata.write_text(json.dumps({'isaac_version':['5.1.0']}))
            with self.assertRaises(ValueError):
                check_asset_version(root, '6.2.0')
            metadata.write_text(json.dumps({'isaac_version':['6.2.0','rc.14']}))
            check_asset_version(root, '6.2.0')
            metadata.write_text(json.dumps({'isaac_version':[]}))
            with self.assertRaises(ValueError):
                check_asset_version(root, '6.2.0')

    def test_profile_resolves_project_asset_and_new_root(self):
        root = Path(__file__).resolve().parents[1]
        profile = load_profile(root)
        self.assertEqual(profile['isaacsim_version'], '6.2.0')
        self.assertEqual(profile['asset_path'], root / 'assets/robots/r1pro_2026/isaacsim_6_x')
        self.assertEqual(profile['referenced_runtime_root'], '/World/Robot/Geometry/base_link')

    def test_runtime_mismatch_and_wrong_engine_are_rejected(self):
        check_runtime('6.2.0', 'physx', '6.2.0')
        with self.assertRaises(ValueError):
            check_runtime('5.1.0', 'physx', '6.2.0')
        with self.assertRaises(ValueError):
            check_runtime('6.2.0', 'newton', '6.2.0')
        self.assertIn('--/exts/isaacsim.physics.newton/auto_switch_on_startup=false', physics_launch_args())
        self.assertIn('--/exts/isaacsim.core.simulation_manager/default_engine=physx', physics_launch_args())

    def test_launcher_uses_selected_pip_python_and_preserves_arguments(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/run_p0.sh'
        with tempfile.TemporaryDirectory(prefix='p0 runtime ') as temp:
            stub = Path(temp) / 'python'
            stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"eula":os.environ.get("OMNI_KIT_ACCEPT_EULA"),"cwd":os.getcwd(),"inherited":{key:os.environ.get(key) for key in ["CARB_APP_PATH","EXP_PATH","ISAAC_PATH","PYTHONPATH","LD_LIBRARY_PATH"]}}))\n')
            stub.chmod(0o755)
            env = dict(os.environ, ISAACSIM_PYTHON=str(stub))
            for key in ['CARB_APP_PATH','EXP_PATH','ISAAC_PATH','PYTHONPATH','LD_LIBRARY_PATH']:
                env[key] = '/stale/isaac51-or-ros'
            env.pop('OMNI_KIT_ACCEPT_EULA', None)
            result = subprocess.run(['bash', str(script), '--mode', 'baseline', '--output-dir', 'a path'],
                                    cwd=temp, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['args'], ['scripts/spike/smoke.py', '--mode', 'baseline', '--output-dir', 'a path'])
            self.assertEqual(data['eula'], 'YES')
            self.assertEqual(data['cwd'], str(script.parents[1]))
            self.assertTrue(all(value is None for value in data['inherited'].values()), data)


if __name__ == '__main__':
    unittest.main()
