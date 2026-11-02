"""Hand-derived B041 fixtures; no production adapter/sampler builds expectations."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest


def fixture(tmp_path):
    profile = {'adapter': 'b041_bundle_v1', 'b041': {
        'basis': 'state', 'status': 'provisional', 'max_source_age_s': .2,
        'max_interpolation_gap_s': .2,
        'chassis': {'mode': 'auto', 'velocity_frame': 'body', 'initial_pose': [0, 0, 0],
                    'velocity_scale': [1, 1, 1], 'velocity_bias': [0, 0, 0], 'max_gap_s': .2},
        'left_gripper': {'native_closed': 0, 'native_open': 100, 'zero1_m': 0,
                         'zero2_m': 0, 'travel1_m': .05, 'travel2_m': -.05},
        'right_gripper': {'native_closed': 0, 'native_open': 100, 'zero1_m': 0,
                          'zero2_m': 0, 'travel1_m': .05, 'travel2_m': -.05},
    }}
    bundle = {'schema': 'b041.recording.v1', 'source': {'id': 'fixture',
        'format': 'full23_parquet', 'time_unit': 'ns', 'clock': 'source_log_time_ns', 'base_locked': None}, 'samples': []}
    for index, stamp in enumerate([1_700_000_000_000_000_001, 1_700_000_000_100_000_001]):
        bundle['samples'].append({'time': str(stamp), 'state':
            [i / 10 + index * .2 for i in range(14)] + [25 + 50*index, 50 + 50*index, 1, 0, 0] +
            [1 + index*.2, 2 + index*.2, 3 + index*.2, 4 + index*.2],
            'state_quality': [{'valid': True, 'timing': 'source_log_time_ns',
                               'source_time': str(stamp-1_000_000), 'age_s': .001} for _ in range(6)]})
    # Values at t=.05, halfway between the two raw rows, calculated explicitly.
    source = [0]*6 + [1.1, 2.1, 3.1, 4.1] + [.1, .2, .3, .4, .5, .6, .7] + [.025, -.025] + [.8, .9, 1., 1.1, 1.2, 1.3, 1.4] + [.0375, -.0375]
    qualities = []
    for i in range(28):
        missing, derived = i < 6, i in (17, 18, 26, 27)
        qualities.append({'origin': 'missing' if missing else 'derived' if derived else 'measured',
            'valid': not missing, 'processing': ['defaulted'] if missing else ['derived', 'interpolated'] if derived else ['interpolated'],
            'source_indices': [] if missing else [0, 1],
            'method': 'missing_joint_zero.v1' if missing else 'b041.provisional.ee2_affine.v1' if derived else None})
    frame = {'session_id': 's-one', 'log_id': 'log-one', 'capture_id': 1, 'render_seq': 1,
        'raw_identity_verified': True, 'log_time_s': .05, 'duration_s': .1, 'progress': .5,
        'source_sample_index': None, 'source_joints': source, 'joints': list(source), 'quality': qualities,
        'playback_epoch': 1, 'control_seq': 1, 'target_seq': 1, 'state': 'playing', 'speed': 1.,
        'captured_wall_s': 100., 'applied_wall_s': 99.99,
        'chassis': {'x': .05, 'y': 0., 'yaw': 0., 'frame': 'world', 'quality': [
            {'origin': 'derived', 'valid': True, 'processing': ['derived', 'interpolated'],
             'source_indices': [0, 1], 'method': 'b041.provisional.se2_body_velocity.v1'} for _ in range(3)]}}
    row = {'frame': frame, 'snapshot': {key: copy.deepcopy(value) for key, value in frame.items()
                                      if key not in ('raw_identity_verified', 'applied_wall_s')}}
    paths = [tmp_path / name for name in ('source.b041', 'profile.json', 'rendered.jsonl')]
    paths[0].write_text(json.dumps(bundle)); paths[1].write_text(json.dumps(profile)); paths[2].write_text(json.dumps(row)+'\n')
    return paths, bundle, profile, row


def validate(paths, **kwargs):
    from scripts.validate_replay_trace import validate_replay_traces
    return validate_replay_traces(paths[0], paths[1], [paths[2]], **kwargs)


def test_checks_all_source_values_readback_and_interpolated_provenance(tmp_path):
    paths, _, _, _ = fixture(tmp_path)
    result = validate(paths)
    assert result['passed'] is True
    assert result['frames_checked'] == result['frames_passed'] == 1
    assert result['trace_count'] == 1
    assert result['counts']['interpolated_frames'] == 1
    assert result['independent_pixel_verification'] is False


@pytest.mark.parametrize('corruption', ['source', 'readback', 'quality', 'base_quality', 'id', 'snapshot', 'progress', 'session'])
def test_rejects_each_material_trace_corruption(tmp_path, corruption):
    paths, _, _, row = fixture(tmp_path)
    second = copy.deepcopy(row); second['frame']['capture_id'] = second['frame']['render_seq'] = 2
    second['frame']['captured_wall_s'] = 100.1
    frame = second['frame']
    if corruption == 'source': frame['source_joints'][13] += .001
    if corruption == 'readback': frame['joints'][26] += .001
    if corruption == 'quality': frame['quality'][17]['source_indices'] = [0]
    if corruption == 'base_quality': frame['chassis']['quality'][1]['origin'] = 'measured'
    if corruption == 'id': frame['capture_id'] = frame['render_seq'] = 1
    if corruption == 'progress': frame['progress'] = .7
    if corruption == 'session': frame['session_id'] = 's-other'
    second['snapshot'] = {key: copy.deepcopy(value) for key, value in frame.items()
                          if key not in ('raw_identity_verified', 'applied_wall_s')}
    if corruption == 'snapshot': second['snapshot']['source_joints'][10] += .01
    paths[2].write_text(json.dumps(row)+'\n'+json.dumps(second)+'\n')
    result = validate(paths)
    assert result['passed'] is False
    assert result['frames_checked'] == 2
    assert result['frames_failed'] == 1
    assert result['failures'][0]['line'] == 2


@pytest.mark.parametrize('unsupported', ['invalid_group', 'repeated_stamp', 'regressing_stamp', 'gap', 'runtime', 'world', 'locked', 'missing_state'])
def test_rejects_inputs_outside_the_deliberately_narrow_oracle(tmp_path, unsupported):
    paths, bundle, profile, _ = fixture(tmp_path)
    if unsupported == 'invalid_group': bundle['samples'][1]['state_quality'][5]['valid'] = False
    if unsupported in ('repeated_stamp', 'regressing_stamp'):
        old = int(bundle['samples'][0]['state_quality'][1]['source_time'])
        bundle['samples'][1]['state_quality'][1]['source_time'] = str(old - (1 if unsupported == 'regressing_stamp' else 0))
    if unsupported == 'gap': profile['b041']['chassis']['max_gap_s'] = .05
    if unsupported == 'runtime': bundle['source']['format'] = 'runtime_events'
    if unsupported == 'world': profile['b041']['chassis']['velocity_frame'] = 'world'
    if unsupported == 'locked': bundle['source']['base_locked'] = True
    if unsupported == 'missing_state': bundle['samples'][1]['state'] = None
    paths[0].write_text(json.dumps(bundle)); paths[1].write_text(json.dumps(profile))
    with pytest.raises(ValueError, match='Unsupported'):
        validate(paths)


def test_midpoint_body_twist_matches_a_quarter_circle(tmp_path):
    from scripts.validate_replay_trace import B041TraceOracle
    _, bundle, profile, _ = fixture(tmp_path)
    for sample in bundle['samples']: sample['state'][18] = 5 * math.pi
    oracle = B041TraceOracle(bundle, profile)
    pose = oracle.expected(.05)['chassis']
    assert pose == pytest.approx([.1/math.pi, .1/math.pi, math.pi/4], abs=1e-12)


def test_empty_trace_and_bounded_failure_list_are_explicit(tmp_path):
    paths, _, _, row = fixture(tmp_path)
    paths[2].write_text('')
    result = validate(paths)
    assert result['passed'] is False
    assert result['frames_checked'] == 0
    row['frame']['raw_identity_verified'] = False
    paths[2].write_text((json.dumps(row)+'\n')*8)
    result = validate(paths, failure_limit=2)
    assert result['frames_failed'] == 8
    assert len(result['failures']) == 2


def test_cli_discovers_each_worker_and_writes_a_bounded_report(tmp_path):
    paths, _, _, row = fixture(tmp_path)
    second = tmp_path / 'second-worker' / 'rendered.jsonl'
    second.parent.mkdir()
    row['frame']['session_id'] = row['snapshot']['session_id'] = 's-two'
    second.write_text(json.dumps(row)+'\n')
    output = tmp_path / 'report.json'
    script = Path(__file__).resolve().parents[2] / 'scripts/validate_replay_trace.py'
    result = subprocess.run([sys.executable, str(script), '--bundle', str(paths[0]), '--profile', str(paths[1]),
                             '--trace-root', str(tmp_path), '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report['trace_count'] == report['frames_checked'] == 2
    assert report['passed'] is True


def test_cli_returns_failure_report_for_unsupported_input(tmp_path):
    paths, bundle, _, _ = fixture(tmp_path)
    bundle['source']['base_locked'] = True
    paths[0].write_text(json.dumps(bundle))
    output = tmp_path / 'report.json'
    script = Path(__file__).resolve().parents[2] / 'scripts/validate_replay_trace.py'
    result = subprocess.run([sys.executable, str(script), '--bundle', str(paths[0]), '--profile', str(paths[1]),
                             '--trace', str(paths[2]), '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 1
    report = json.loads(output.read_text())
    assert report['passed'] is False and 'Unsupported' in report['input_rejected']
