"""Versioned inline mapping. Units and source meanings are declarations, not inference."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BeforeValidator, Field, ValidationError, model_validator
from replay_protocol import CANONICAL_JOINTS, ContractModel, FiniteNumber, Identifier, SchemaVersion
from .base import MappingError

PRISMATIC = frozenset(name for name in CANONICAL_JOINTS if 'gripper' in name)
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


def _no_unwrap(value):
    if value is not False:
        raise ValueError("unwrap_continuous must be false; unwrapping belongs to T09")
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
    options: ImportOptions = Field(default_factory=ImportOptions)

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
