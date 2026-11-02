from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from pydantic import Field, StrictBool, model_validator
from replay_protocol import ContractModel, FiniteNumber, Identifier, JsonValue, SafeInt

Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Time = Annotated[str, Field(min_length=1, max_length=96)]
Vector = Annotated[tuple[FiniteNumber | None, ...], Field(min_length=23, max_length=23, strict=False)]


class SourceFile(ContractModel):
    name: Identifier
    sha256: Hash
    role: Identifier
    bytes: SafeInt | None = None


class Source(ContractModel):
    id: Identifier
    format: Literal['full23_parquet', 'runtime_events']
    time_unit: Literal['ns', 's']
    clock: Literal['source_log_time_ns', 'run_monotonic_event_time']
    sha256: Hash
    name: Identifier
    episode_index: SafeInt | None = None
    base_locked: StrictBool | None = None
    files: list[SourceFile] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def source_clock(self):
        expected = ('ns', 'source_log_time_ns') if self.format == 'full23_parquet' else ('s', 'run_monotonic_event_time')
        if (self.time_unit, self.clock) != expected:
            raise ValueError('Clock does not match recording format')
        return self


class GroupQuality(ContractModel):
    valid: StrictBool
    source_time: Time | None
    age_s: FiniteNumber | None
    timing: Literal['source_log_time_ns', 'event_monotonic_only']


Groups = Annotated[tuple[GroupQuality, ...], Field(min_length=6, max_length=6, strict=False)]


class Sample(ContractModel):
    time: Time
    row: Annotated[SafeInt, Field(ge=1)]
    frame_index: SafeInt | None
    event: Identifier
    state: Vector | None
    action: Vector | None
    state_quality: Groups | None
    action_quality: Groups | None
    context: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode='after')
    def coherent_groups(self):
        try:
            if not Decimal(self.time).is_finite(): raise ValueError('Nonfinite clock')
        except InvalidOperation as exc:
            raise ValueError('Invalid recording clock') from exc
        if self.state is None and self.action is None:
            raise ValueError('Sample has no recorded vector')
        for basis in ('state', 'action'):
            if (getattr(self, basis) is None) != (getattr(self, basis + '_quality') is None):
                raise ValueError('Recorded vector requires six group qualities')
        return self


class AnnotationInput(ContractModel):
    alignment: Literal['none', 'source_frames', 'reference_only'] = 'none'
    source_id: Identifier | None = None
    sha256: Hash | None = None
    segments: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=20000)
    reference: dict[str, JsonValue] = Field(default_factory=dict)


class Event(ContractModel):
    time: Time | None
    row: Annotated[SafeInt, Field(ge=1)]
    kind: Identifier
    details: dict[str, JsonValue] = Field(default_factory=dict)


class Recording(ContractModel):
    schema_: Literal['b041.recording.v1'] = Field(alias='schema')
    source: Source
    samples: list[Sample] = Field(min_length=1, max_length=20000)
    annotations: AnnotationInput = Field(default_factory=AnnotationInput)
    events: list[Event] = Field(default_factory=list, max_length=20000)
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict)
