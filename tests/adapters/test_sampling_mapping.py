"""T09 declarations preserve source data and require explicit virtual-wheel inputs."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import pytest

from app.adapters.base import MappingError, ParseLimits, RawColumn, RawTable
from app.adapters.mapping import parse_mapping
from app.adapters.normalize import normalize_table, upload_result


WHEEL = 'wheel_motor_joint1'
SIGNS = {'wheel_motor_joint1': 1, 'wheel_motor_joint2': -1, 'wheel_motor_joint3': 1}


def normalize(tmp_path, columns, mapping):
    source = tmp_path / 'source.csv'
    source.write_bytes(b'T09 angular declarations fixture\n')
    return normalize_table(RawTable(columns, 'test'), source, parse_mapping(mapping),
                           limits=ParseLimits(), parser='csv_v1')


def test_legacy_mapping_gets_safe_defaults_without_mutating_archived_snapshot():
    snapshot = {'schema_version': 1, 'options': {'resample': 'none', 'unwrap_continuous': False}}
    original = deepcopy(snapshot)
    config = parse_mapping(snapshot)
    assert config.virtual_channels.wheel_roll_from_chassis.enabled is False
    assert config.virtual_channels.wheel_roll_from_chassis.wheel_radius_m is None
    assert config.virtual_channels.wheel_roll_from_chassis.joint_signs == {}
    assert config.chassis.yaw_semantics == 'angular_wrapped'
    assert snapshot == original


def test_virtual_wheel_mapping_round_trips_explicit_physical_parameters():
    config = parse_mapping({'virtual_channels': {'wheel_roll_from_chassis': {
        'enabled': True, 'wheel_radius_m': 0.125, 'joint_signs': SIGNS,
        'initial_angle_rad': -2.5, 'max_lateral_ratio': 0.2, 'max_segment_s': 0.5,
    }}})
    wheel = config.virtual_channels.wheel_roll_from_chassis
    assert wheel.enabled is True
    assert wheel.wheel_radius_m == 0.125
    assert wheel.joint_signs == SIGNS
    assert wheel.initial_angle_rad == -2.5
    assert wheel.max_lateral_ratio == 0.2
    assert wheel.max_segment_s == 0.5
    assert parse_mapping(config.model_dump(mode='json')) == config


@pytest.mark.parametrize('parameters', [
    {'enabled': True},
    {'enabled': True, 'joint_signs': SIGNS},
    {'enabled': True, 'wheel_radius_m': 0.125},
    {'enabled': True, 'wheel_radius_m': 0.125, 'joint_signs': {WHEEL: 1}},
    {'wheel_radius_m': 0}, {'wheel_radius_m': -1}, {'wheel_radius_m': True},
    {'wheel_radius_m': float('inf')}, {'wheel_radius_m': float('nan')},
    {'initial_angle_rad': True}, {'initial_angle_rad': float('inf')},
    {'max_segment_s': 0}, {'max_segment_s': -1}, {'max_segment_s': True},
    {'max_segment_s': float('nan')},
    {'max_lateral_ratio': -0.01}, {'max_lateral_ratio': 1.01},
    {'max_lateral_ratio': True}, {'max_lateral_ratio': float('inf')},
    {'enabled': 1}, {'enabled': 0}, {'enabled': 'true'},
    {'joint_signs': {WHEEL: 0}}, {'joint_signs': {WHEEL: 2}},
    {'joint_signs': {WHEEL: True}}, {'joint_signs': {WHEEL: 1.0}},
    {'joint_signs': {WHEEL: '1'}}, {'joint_signs': {'torso_joint1': 1}},
    {'unknown': 1},
])
def test_invalid_virtual_wheel_parameters_fail_with_mapping_error(parameters):
    with pytest.raises(MappingError) as caught:
        parse_mapping({'virtual_channels': {'wheel_roll_from_chassis': parameters}})
    assert caught.value.code == 'INVALID_MAPPING'


def test_incomplete_enabled_virtual_wheels_explain_missing_radius_and_signs():
    with pytest.raises(MappingError) as caught:
        parse_mapping({'virtual_channels': {'wheel_roll_from_chassis': {'enabled': True}}})
    assert 'wheel_radius_m' in caught.value.message
    assert 'joint_signs' in caught.value.message


@pytest.mark.parametrize(('name', 'semantics'), [
    (WHEEL, 'angular_wrapped'), (WHEEL, 'angular_unwrapped'),
    ('steer_motor_joint1', 'bounded'), ('left_gripper_finger_joint2', 'linear'),
])
def test_explicit_joint_semantics_match_joint_type(name, semantics):
    config = parse_mapping({'joints': {'per_joint_overrides': {name: {'angle_semantics': semantics}}}})
    assert config.joints.per_joint_overrides[name].angle_semantics == semantics


@pytest.mark.parametrize(('name', 'semantics'), [
    (WHEEL, 'bounded'), (WHEEL, 'linear'),
    ('steer_motor_joint1', 'angular_wrapped'), ('torso_joint1', 'angular_unwrapped'),
    ('left_arm_joint1', 'linear'), ('left_gripper_finger_joint2', 'bounded'),
    ('right_gripper_finger_joint1', 'angular_unwrapped'),
])
def test_incompatible_joint_semantics_are_rejected(name, semantics):
    with pytest.raises(MappingError):
        parse_mapping({'joints': {'per_joint_overrides': {name: {'angle_semantics': semantics}}}})


@pytest.mark.parametrize('scale', [1.0, -1.0, 2.0])
def test_wrapped_degree_values_keep_declared_si_transform_and_source_provenance(tmp_path, scale):
    mapping = {'joints': {'per_joint_overrides': {WHEEL: {
        'unit': 'deg', 'scale': scale, 'offset': 0.25, 'angle_semantics': 'angular_wrapped',
    }}}}
    data = normalize(tmp_path, {'timestamp': RawColumn([10, 11]), WHEEL: RawColumn([171, -171])}, mapping)
    channel = data.channels[WHEEL]
    assert channel.values == pytest.approx([2.9845130209103035 * scale + 0.25,
                                            -2.9845130209103035 * scale + 0.25])
    assert channel.unit == 'rad'
    assert channel.angle_semantics == 'angular_wrapped'
    assert channel.wrap_period == 2 * math.pi
    descriptor = next(item for item in upload_result(data).channels if item.name == WHEEL)
    assert descriptor.angle_semantics == 'angular_wrapped'
    assert descriptor.wrap_period == 2 * math.pi
    assert [quality.source_indices for quality in channel.quality] == [(0,), (1,)]
    assert all(quality.origin == 'measured' and quality.processing == ('original', 'unit_converted')
               for quality in channel.quality)
    assert data.raw_file_sha256 == hashlib.sha256(b'T09 angular declarations fixture\n').hexdigest()
    assert data.metadata['conversion_steps'][WHEEL]['order'] == 'source * unit_factor * scale + offset_output_unit'
    assert data.mapping_snapshot['joints']['per_joint_overrides'][WHEEL]['angle_semantics'] == 'angular_wrapped'


def test_missing_wrapped_joint_stays_invalid_with_coherent_channel_and_descriptor(tmp_path):
    data = normalize(tmp_path, {'timestamp': RawColumn([0, 1]), 'torso_joint1': RawColumn([0, 1])},
                     {'joints': {'per_joint_overrides': {WHEEL: {'angle_semantics': 'angular_wrapped'}}}})
    channel = data.channels[WHEEL]
    assert channel.angle_semantics == 'angular_wrapped'
    assert channel.wrap_period == 2 * math.pi
    assert all(not quality.valid and quality.origin == 'missing' for quality in channel.quality)
    descriptor = next(item for item in upload_result(data).channels if item.name == WHEEL)
    assert descriptor.wrap_period == 2 * math.pi


@pytest.mark.parametrize('declaration', [None, 'angular_unwrapped'])
def test_unwrapped_joint_retains_multi_turn_source_angles(tmp_path, declaration):
    mapping = {} if declaration is None else {
        'joints': {'per_joint_overrides': {WHEEL: {'angle_semantics': declaration}}}}
    data = normalize(tmp_path, {'timestamp': RawColumn([0, 1]), WHEEL: RawColumn([0, 8 * math.pi])}, mapping)
    assert data.channels[WHEEL].values == [0.0, 8 * math.pi]
    assert data.channels[WHEEL].angle_semantics == 'angular_unwrapped'
    assert data.channels[WHEEL].wrap_period is None
    assert data.sample_times_s == [0.0, 1.0]


@pytest.mark.parametrize('semantics', ['angular_wrapped', 'angular_unwrapped'])
def test_chassis_yaw_declaration_survives_si_conversion_and_world_transform(tmp_path, semantics):
    data = normalize(tmp_path, {
        'timestamp': RawColumn([0, 1]), 'torso_joint1': RawColumn([0, 1]),
        'x': RawColumn([0, 1]), 'y': RawColumn([0, 0]), 'yaw': RawColumn([171, -171]),
    }, {'chassis': {'available': True, 'yaw_unit': 'deg', 'yaw_semantics': semantics,
                    'transform': {'rotation_rad': 0.5}}})
    channel = data.channels['chassis.yaw']
    assert channel.values == pytest.approx([3.4845130209103035, -2.4845130209103035])
    assert channel.angle_semantics == semantics
    expected_period = 2 * math.pi if semantics == 'angular_wrapped' else None
    assert channel.wrap_period == expected_period
    descriptor = next(item for item in upload_result(data).channels if item.name == 'chassis.yaw')
    assert descriptor.angle_semantics == semantics
    assert descriptor.wrap_period == expected_period


@pytest.mark.parametrize('mapping', [
    {'chassis': {'yaw_semantics': 'bounded'}}, {'chassis': {'yaw_semantics': 'linear'}},
    {'options': {'unwrap_continuous': 0}}, {'options': {'unwrap_continuous': True}},
    {'options': {'resample': 'uniform'}}, {'virtual_channels': {'other_estimate': {}}},
])
def test_sampling_declarations_do_not_enable_implicit_or_unknown_processing(mapping):
    with pytest.raises(MappingError):
        parse_mapping(mapping)


def test_reference_mapping_declares_disabled_virtual_wheels():
    path = Path(__file__).resolve().parents[2] / 'configs/mappings/r1pro_default.json'
    config = parse_mapping(json.loads(path.read_text(encoding='utf-8')))
    assert config.virtual_channels.wheel_roll_from_chassis.enabled is False
    assert config.virtual_channels.wheel_roll_from_chassis.wheel_radius_m is None
    assert config.virtual_channels.wheel_roll_from_chassis.joint_signs == {}
