"""Versioned inline mapping. Units and source meanings are declarations, not inference."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BeforeValidator, Field, StrictBool, StrictInt, ValidationError, model_validator
from replay_protocol import CANONICAL_JOINTS, ContractModel, FiniteNumber, Identifier, SchemaVersion
from .base import MappingError
from .b041.profile import B041Profile

PRISMATIC = frozenset(name for name in CANONICAL_JOINTS if 'gripper' in name)
WHEEL_JOINTS = frozenset(f'wheel_motor_joint{i}' for i in range(1, 4))
ORIGIN = Literal['measured', 'commanded']


class TimestampMapping(ContractModel):
    field: Identifier = 'timestamp'
    unit: Literal['s', 'ms', 'us', 'ns'] = 's'
    start_offset: FiniteNumber = Field(default=0.0, ge=0)


class JointOverride(ContractModel):
    unit: Literal['rad', 'deg', 'm', 'mm', 'cm'] | None = None
    scale: FiniteNumber = 1.0
    offset: FiniteNumber = 0.0
    origin: ORIGIN | None = None
    angle_semantics: Literal['bounded', 'angular_wrapped', 'angular_unwrapped', 'linear'] | None = None


class JointMapping(ContractModel):
    unit: Literal['rad', 'deg'] = 'rad'
    origin: ORIGIN = 'measured'
    map: dict[Identifier, str] = Field(default_factory=lambda: {name: name for name in CANONICAL_JOINTS})
    per_joint_overrides: dict[Identifier, JointOverride] = Field(default_factory=lambda: {name: JointOverride(unit='m') for name in PRISMATIC})

    @model_validator(mode='after')
    def compatible_fields(self):
        if (set(self.map) | set(self.per_joint_overrides)) - set(CANONICAL_JOINTS):
            raise ValueError('joint mappings must name canonical joints')
        if any(len(value) > 256 for value in self.map.values()):
            raise ValueError('source field names must be at most 256 characters')
        for name, override in self.per_joint_overrides.items():
            allowed = {'m', 'mm', 'cm', None} if name in PRISMATIC else {'rad', 'deg', None}
            if override.unit not in allowed:
                raise ValueError(f'unit is incompatible with joint type: {name}')
            semantics = ({'linear'} if name in PRISMATIC else
                         {'angular_wrapped', 'angular_unwrapped'} if name in WHEEL_JOINTS else {'bounded'})
            if override.angle_semantics is not None and override.angle_semantics not in semantics:
                raise ValueError(f'angle_semantics is incompatible with joint type: {name}')
        return self


class PlanarTransform(ContractModel):
    rotation_rad: FiniteNumber = 0.0
    translation_x_m: FiniteNumber = 0.0
    translation_y_m: FiniteNumber = 0.0


class ChassisMapping(ContractModel):
    available: bool = False
    frame: Identifier = 'world'
    map: dict[Literal['x', 'y', 'yaw'], Identifier] = Field(default_factory=lambda: {'x': 'x', 'y': 'y', 'yaw': 'yaw'})
    position_unit: Literal['m', 'mm', 'cm'] = 'm'
    yaw_unit: Literal['rad', 'deg'] = 'rad'
    yaw_semantics: Literal['angular_wrapped', 'angular_unwrapped'] = 'angular_wrapped'
    origin: ORIGIN = 'measured'
    transform: PlanarTransform = Field(default_factory=PlanarTransform)

    @model_validator(mode='after')
    def explicit_world_transform(self):
        if self.available and set(self.map) != {'x', 'y', 'yaw'}:
            raise ValueError('chassis needs all x/y/yaw field mappings')
        if self.available and self.frame != 'world' and 'transform' not in self.model_fields_set:
            raise ValueError('non-world chassis coordinates require an explicit transform to world')
        return self


class CustomChannel(ContractModel):
    field: Identifier
    unit: Identifier
    scale: FiniteNumber = 1.0
    offset: FiniteNumber = 0.0
    origin: ORIGIN = 'measured'


class WheelRollConfig(ContractModel):
    enabled: StrictBool = False
    wheel_radius_m: Annotated[FiniteNumber, Field(gt=0)] | None = None
    joint_signs: dict[Literal['wheel_motor_joint1', 'wheel_motor_joint2', 'wheel_motor_joint3'], StrictInt] = Field(default_factory=dict)
    initial_angle_rad: FiniteNumber = 0.0
    max_lateral_ratio: FiniteNumber = Field(default=0.05, ge=0, le=1)
    max_segment_s: Annotated[FiniteNumber, Field(gt=0)] | None = None

    @model_validator(mode='after')
    def explicit_physical_parameters(self):
        if any(sign not in (-1, 1) for sign in self.joint_signs.values()):
            raise ValueError('joint_signs values must be the integers -1 or +1')
        if self.enabled and (self.wheel_radius_m is None or set(self.joint_signs) != WHEEL_JOINTS):
            raise ValueError('enabled wheel_roll_from_chassis requires wheel_radius_m and all three explicit joint_signs')
        return self


class VirtualChannelsConfig(ContractModel):
    wheel_roll_from_chassis: WheelRollConfig = Field(default_factory=WheelRollConfig)


def _no_unwrap(value):
    if value is not False:
        raise ValueError('unwrap_continuous must be false; declare per-channel angle_semantics instead')
    return value


class ImportOptions(ContractModel):
    max_forward_fill_s: FiniteNumber = Field(default=0.25, ge=0)
    resample: Literal['none'] = 'none'
    unwrap_continuous: Annotated[Literal[False], BeforeValidator(_no_unwrap)] = False


class MappingConfig(ContractModel):
    schema_version: SchemaVersion = 1
    adapter: Identifier | None = None
    timestamp: TimestampMapping = Field(default_factory=TimestampMapping)
    joints: JointMapping = Field(default_factory=JointMapping)
    chassis: ChassisMapping = Field(default_factory=ChassisMapping)
    custom_channels: dict[Identifier, CustomChannel] = Field(default_factory=dict)
    virtual_channels: VirtualChannelsConfig = Field(default_factory=VirtualChannelsConfig)
    options: ImportOptions = Field(default_factory=ImportOptions)
    b041: B041Profile | None = None

    @model_validator(mode='after')
    def unique_channel_names(self):
        reserved = set(CANONICAL_JOINTS) | {'chassis.x', 'chassis.y', 'chassis.yaw'}
        if set(self.custom_channels) & reserved:
            raise ValueError('custom channels cannot replace robot or chassis channels')
        return self


def parse_mapping(value: MappingConfig | dict | None = None) -> MappingConfig:
    try:
        # Revalidate a detached snapshot: callers cannot later mutate nested config dictionaries.
        if isinstance(value, MappingConfig):
            value = value.model_dump(mode='python')
        return MappingConfig.model_validate({} if value is None else value)
    except (ValidationError, TypeError, ValueError) as exc:
        raise MappingError(str(exc)) from exc
