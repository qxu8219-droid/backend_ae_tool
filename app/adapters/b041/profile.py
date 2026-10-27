from typing import Annotated, Literal
from pydantic import Field, model_validator
from replay_protocol import ContractModel, FiniteNumber

Positive = Annotated[FiniteNumber, Field(gt=0)]
Triple = Annotated[tuple[FiniteNumber, FiniteNumber, FiniteNumber], Field(strict=False)]


class GripperConfig(ContractModel):
    native_closed: FiniteNumber = 0.
    native_open: FiniteNumber = 100.
    zero1_m: FiniteNumber = 0.
    zero2_m: FiniteNumber = 0.
    travel1_m: FiniteNumber = .05
    travel2_m: FiniteNumber = -.05

    @model_validator(mode='after')
    def ordered_native_endpoints(self):
        if self.native_open <= self.native_closed:
            raise ValueError('native_open must exceed native_closed')
        return self


class ChassisConfig(ContractModel):
    mode: Literal['auto', 'integrate_body', 'unavailable'] = 'auto'
    velocity_frame: Literal['body', 'world'] = 'body'
    initial_pose: Triple = (0., 0., 0.)
    velocity_scale: Triple = (1., 1., 1.)
    velocity_bias: Triple = (0., 0., 0.)
    max_gap_s: Positive = .2


class B041Profile(ContractModel):
    profile_id: Annotated[str, Field(min_length=1, max_length=80)] = 'b041_ee2_body_velocity_v1'
    status: Literal['provisional', 'confirmed'] = 'provisional'
    basis: Literal['state', 'action'] = 'state'
    left_gripper: GripperConfig = Field(default_factory=GripperConfig)
    right_gripper: GripperConfig = Field(default_factory=GripperConfig)
    chassis: ChassisConfig = Field(default_factory=ChassisConfig)
    max_source_age_s: Positive = .2
    max_interpolation_gap_s: Positive = .25
