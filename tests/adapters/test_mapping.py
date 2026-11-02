import pytest
from app.adapters.base import MappingError, ParseLimits
from app.adapters.mapping import parse_mapping
from replay_protocol import CANONICAL_JOINTS


def test_reference_mapping_is_explicit_and_prismatic_units_do_not_inherit_angles():
    config = parse_mapping(None)
    assert config.timestamp.field == 'timestamp'
    assert config.joints.map == {name: name for name in CANONICAL_JOINTS}
    assert config.joints.per_joint_overrides['right_gripper_finger_joint2'].unit == 'm'
    assert config.chassis.available is False


@pytest.mark.parametrize('mapping', [
    {'timestamp': {'unit': 'minutes'}},
    {'timestamp': {'start_offset': -1.0}},
    {'joints': {'map': {'unknown': 'x'}}},
    {'joints': {'per_joint_overrides': {'torso_joint1': {'unit': 'm'}}}},
    {'joints': {'per_joint_overrides': {'left_gripper_finger_joint1': {'unit': 'deg'}}}},
    {'joints': {'per_joint_overrides': {'torso_joint1': {'scale': True}}}},
    {'joints': {'per_joint_overrides': {'torso_joint1': {'scale': float('inf')}}}},
    {'chassis': {'available': True, 'frame': 'odom'}},
    {'options': {'resample': 'uniform'}},
    {'options': {'unwrap_continuous': True}},
    {'virtual_channels': {'wheel_roll_from_chassis': {'enabled': True}}},
    {'unknown': 1},
])
def test_invalid_or_unimplemented_mapping_is_explicit_not_silently_ignored(mapping):
    with pytest.raises(MappingError) as error:
        parse_mapping(mapping)
    assert error.value.status_code == 400


def test_custom_names_cannot_replace_robot_channels_or_duplicate_sources_accidentally():
    with pytest.raises(MappingError):
        parse_mapping({'custom_channels': {'torso_joint1': {'field': 'battery', 'unit': 'V'}}})


def test_limits_are_positive_and_have_real_finite_deadlines():
    for options in [{'max_samples': 0}, {'memory_bytes': -1}, {'timeout_s': float('nan')}, {'max_points': True}]:
        with pytest.raises(ValueError): ParseLimits(**options)
