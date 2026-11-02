import copy
import json
import math

import pytest

from app.adapters.base import AdapterError
from app.adapters.registry import AdapterRegistry
from app.replay.interpolator import ReplaySampler


def bundle():
    origin = 1800000000000000001
    samples = []
    for index in range(3):
        state = [.1 + index / 10] * 23
        state[14:19] = [index * 50, 80, 1., 0., 0.]
        action = list(state); action[0] = 999.
        quality = [{'valid': True, 'source_time': str(origin + index * 100000000), 'age_s': 0., 'timing': 'source_log_time_ns'} for _ in range(6)]
        action_quality = copy.deepcopy(quality); action_quality[0].update(valid=False, source_time='-1', age_s=-1.)
        samples.append({'time': str(origin + index * 100000000), 'row': index + 1, 'frame_index': index, 'event': 'frame',
            'state': state, 'action': action, 'state_quality': quality, 'action_quality': action_quality, 'context': {}})
    return {'schema': 'b041.recording.v1', 'source': {'id': 'B041-test', 'format': 'full23_parquet', 'time_unit': 'ns',
        'clock': 'source_log_time_ns', 'sha256': 'a' * 64, 'name': 'source.parquet', 'episode_index': 0,
        'base_locked': None, 'files': []}, 'samples': samples,
        'annotations': {'alignment': 'source_frames', 'source_id': 'B041-test', 'sha256': 'b' * 64, 'reference': {}, 'segments': [
            {'id': 'phase', 'start_frame': 0, 'end_frame': 3, 'start_log_ns': str(origin), 'end_log_ns': str(origin + 300000000),
             'label': 'pick', 'review_status': 'human_reviewed', 'boundary_status': 'pending_review', 'training_approved': False, 'details': {}}]},
        'events': [], 'diagnostics': {}}


def load(tmp_path, value=None, config=None):
    path = tmp_path / 'recording.b041'; path.write_text(json.dumps(value or bundle()))
    return AdapterRegistry().load(path, config)


def test_default_provisional_mapping_keeps_raw_values_and_precise_ns_clock(tmp_path):
    data = load(tmp_path)
    assert data.sample_times_s == [0., .1, .2]
    assert data.metadata['source_origin_exact_s'] == '1800000000.000000001'
    assert data.channels['left_gripper_finger_joint1'].values == [0., .025, .05]
    assert data.channels['left_gripper_finger_joint2'].values == [0., -.025, -.05]
    assert data.channels['raw.state.left_gripper'].values == [0., 50., 100.]
    assert all(q.origin == 'derived' and q.method.startswith('b041.provisional.') for q in data.channels['left_gripper_finger_joint1'].quality)
    assert data.channels['left_arm_joint1'].quality[0].origin == 'measured'
    assert data.channels['raw.action.left_arm_joint1'].values[0] == 999.
    assert not data.channels['raw.action.left_arm_joint1'].quality[0].valid
    assert not any(q.valid for q in data.channels['wheel_motor_joint1'].quality)
    assert data.metadata['log_annotations']['segments'][0]['end_s'] == .3
    assert not data.metadata['log_annotations']['segments'][0]['training_approved']
    step = data.metadata['conversion_steps']['left_gripper_finger_joint1']
    assert step['origin'] == 'derived' and step['field'] == 'state[14]' and step['source_unit'] == 'native_opening'
    assert data.metadata['conversion_steps']['chassis.x']['origin'] == 'derived'


def test_basis_does_not_replace_invalid_actions_with_feedback(tmp_path):
    data = load(tmp_path, config={'b041': {'basis': 'action'}})
    assert data.channels['left_arm_joint1'].values[0] == 999.
    assert not data.channels['left_arm_joint1'].quality[0].valid
    target = ReplaySampler(data, session_id='s').sample_at(0.)
    assert target.joints[10] == 0 and not target.quality[10].valid


def test_side_specific_calibration_changes_mapping_identity_without_clamping(tmp_path):
    raw = bundle(); raw['samples'][2]['state'][14] = 101
    first = load(tmp_path, raw)
    assert first.channels['left_gripper_finger_joint1'].values[-1] == pytest.approx(.0505)
    assert any(issue['code'] == 'B041_GRIPPER_RANGE' for issue in first.metadata['issues'])
    second = load(tmp_path, raw, {'b041': {'left_gripper': {'zero1_m': .001, 'travel1_m': .04}}})
    assert second.channels['left_gripper_finger_joint1'].values[-1] == pytest.approx(.0414)
    assert second.channels['right_gripper_finger_joint1'] == first.channels['right_gripper_finger_joint1']
    assert second.mapping_snapshot_id != first.mapping_snapshot_id


def test_saved_mapping_roundtrips_but_ignored_generic_overrides_are_rejected(tmp_path):
    first = load(tmp_path)
    second = load(tmp_path, config=first.mapping_snapshot)
    assert second.mapping_snapshot_id == first.mapping_snapshot_id
    with pytest.raises(AdapterError) as error:
        load(tmp_path, config={'joints': {'per_joint_overrides': {'left_arm_joint1': {'scale': 2.}}}})
    assert error.value.status_code == 400


def test_body_velocity_is_provisional_pose_and_invalidity_latches_after_gap(tmp_path):
    data = load(tmp_path)
    assert data.channels['chassis.x'].values == pytest.approx([0., .1, .2])
    pose = ReplaySampler(data, session_id='s').sample_at(.1).chassis
    assert pose and all(q.origin == 'derived' for q in pose.quality)
    raw = bundle(); raw['samples'][1]['state_quality'][4]['valid'] = False
    invalid = load(tmp_path, raw)
    assert [q.valid for q in invalid.channels['chassis.x'].quality] == [True, False, False]
    assert ReplaySampler(invalid, session_id='s').sample_at(.2).chassis is None


def test_no_valid_integrated_pose_does_not_advertise_chassis_availability(tmp_path):
    raw = bundle(); raw['samples'][0]['state_quality'][4]['valid'] = False
    data = load(tmp_path, raw)
    assert data.metadata['stats']['chassis']['available'] is False
    assert data.metadata['stats']['chassis']['frame'] is None
    assert not any(quality.valid for quality in data.channels['chassis.x'].quality)


@pytest.mark.parametrize('basis', ['state', 'action'])
def test_regressed_velocity_source_clock_invalidates_pose_without_reanchoring(tmp_path, basis):
    raw = bundle(); raw['annotations'].update(alignment='none', segments=[])
    recovered = copy.deepcopy(raw['samples'][-1])
    recovered.update(time=str(int(raw['samples'][0]['time']) + 300000000), row=4, frame_index=3)
    for role in ('state', 'action'):
        for quality in recovered[role + '_quality']:
            if quality['valid']: quality.update(source_time=recovered['time'], age_s=0.)
    raw['samples'].append(recovered)
    raw['samples'][2]['state_quality'][4].update(
        source_time=str(int(raw['samples'][0]['time']) + 50000000), age_s=.15)
    data = load(tmp_path, raw, {'b041': {'basis': basis}})
    assert [q.valid for q in data.channels['raw.state.base_vx'].quality] == [True, True, False, True]
    assert [q.valid for q in data.channels['chassis.x'].quality] == [True, True, False, False]
    assert data.channels['chassis.x'].values == pytest.approx([0., .1, .1, .1])
    sampler = ReplaySampler(data, session_id='s')
    assert sampler.sample_at(.1).chassis is not None
    assert sampler.sample_at(.2).chassis is None
    assert sampler.sample_at(.3).chassis is None


def test_runtime_locked_base_and_sparse_feedback_remain_unavailable(tmp_path):
    raw = bundle(); raw['source'].update(format='runtime_events', time_unit='s', clock='run_monotonic_event_time', base_locked=True)
    raw['annotations'].update(alignment='reference_only', segments=[])
    for index, sample in enumerate(raw['samples']):
        sample.update(time=str(100 + index * 3), frame_index=None, event='target')
        for quality in sample['state_quality'] + sample['action_quality']:
            quality.update(source_time=None, age_s=None, timing='event_monotonic_only')
    data = load(tmp_path, raw)
    assert 'chassis.x' not in data.channels
    assert ReplaySampler(data, session_id='s').sample_at(1.).quality[10].valid is False
    assert data.metadata['log_annotations']['alignment'] == 'reference_only'
    assert data.channels['raw.state.base_vx'].values == [1., 1., 1.]


def test_repeated_source_timestamp_is_causal_hold_not_new_observation(tmp_path):
    raw = bundle(); raw['samples'][1]['state_quality'][0].update(source_time=raw['samples'][0]['time'], age_s=.1)
    data = load(tmp_path, raw)
    quality = data.channels['left_arm_joint1'].quality[1]
    assert quality.valid and 'forward_filled' in quality.processing and 'original' not in quality.processing


def test_expired_or_future_group_times_do_not_become_valid_measurements(tmp_path):
    raw = bundle()
    raw['samples'][0]['state_quality'][0]['source_time'] = raw['samples'][1]['time']
    raw['samples'][1]['state_quality'][1].update(source_time=str(int(raw['samples'][1]['time']) - 300000000), age_s=.3)
    data = load(tmp_path, raw)
    assert not data.channels['left_arm_joint1'].quality[0].valid
    assert not data.channels['right_arm_joint1'].quality[1].valid


def test_source_gap_bound_is_shared_with_curves_and_cannot_be_weakened(tmp_path):
    from app.analysis.service import ReplayAnalysis
    raw = bundle(); raw['annotations'].update(alignment='none', segments=[])
    for index, sample in enumerate(raw['samples']):
        sample['time'] = str(1800000000000000001 + index * 1000000000)
        for quality in sample['state_quality']:
            quality['source_time'] = sample['time']
    data = load(tmp_path, raw)
    assert not ReplaySampler(data, session_id='s', max_gap_s=10.).sample_at(.5).quality[10].valid
    analysis = ReplayAnalysis(data, max_gap_s=10.)
    assert analysis.history.max_gap_s == .25
    assert any(point.value is None for point in analysis.curve(['left_arm_joint1']).series[0].points)


def test_duplicate_json_keys_and_configured_limits_reject_before_conversion(tmp_path):
    from app.adapters.base import ParseLimits
    path = tmp_path / 'recording.b041'
    body = json.dumps(bundle()); path.write_text('{"schema":"other",' + body[1:])
    with pytest.raises(AdapterError): AdapterRegistry().load(path)
    path.write_text(body)
    for limits in (ParseLimits(max_file_bytes=32), ParseLimits(max_samples=2), ParseLimits(max_points=10)):
        with pytest.raises(AdapterError) as error: AdapterRegistry(limits).load(path)
        assert error.value.status_code == 413


def test_annotation_payload_cannot_import_successfully_then_fail_its_api_contract(tmp_path):
    raw = bundle()
    event = {'time': raw['samples'][0]['time'], 'row': 1, 'kind': 'event', 'details': {}}
    raw['events'] = [event, event]
    with pytest.raises(AdapterError): load(tmp_path, raw)


@pytest.mark.parametrize('mutate', [
    lambda d: d['samples'][0]['state'].pop(),
    lambda d: d['samples'][0]['state_quality'].pop(),
    lambda d: d['source'].update(time_unit='bad'),
    lambda d: d['annotations']['segments'][0].update(end_frame=4),
    lambda d: d['annotations'].update(source_id='another-recording'),
])
def test_malformed_or_cross_recording_bundle_is_rejected(tmp_path, mutate):
    raw = bundle(); mutate(raw)
    with pytest.raises(AdapterError): load(tmp_path, raw)


def test_se2_body_integration_preserves_curved_motion_and_stops_after_gap():
    from app.adapters.b041.profile import ChassisConfig
    from app.adapters.b041.transforms import integrate_pose
    positions, valid = integrate_pose([0., 1., 2., 5.], [[1., 0., math.pi/2]] * 4, [True]*4, ChassisConfig(max_gap_s=1.1))
    assert positions[1] == pytest.approx([2/math.pi, 2/math.pi, math.pi/2])
    assert positions[2] == pytest.approx([0., 4/math.pi, math.pi])
    assert valid == [True, True, True, False]
