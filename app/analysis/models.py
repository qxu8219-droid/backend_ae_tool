"""Internal analysis results; HTTP endpoint binding remains a separate layer."""
from typing import Literal
from replay_protocol import ChannelQuality, ContractModel, FiniteNumber, Identifier, NonnegativeNumber, SafeInt


class AnalysisError(ValueError):
    def __init__(self, code, message, *, status_code=400, details=None):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.details = details or {}


class LimitEvent(ContractModel):
    event_id: Identifier
    log_id: Identifier
    channel: Identifier
    joint_index: SafeInt
    unit: Literal['rad', 'm']
    severity: Literal['warning', 'error']
    side: Literal['lower', 'upper']
    from_s: NonnegativeNumber
    last_observed_s: NonnegativeNumber
    exit_s: NonnegativeNumber | None
    closed_by: Literal['normal', 'transition', 'provenance_change', 'missing', 'gap', 'end']
    peak_time_s: NonnegativeNumber
    peak_value: FiniteNumber
    peak_quality: ChannelQuality
    first_channel_index: SafeInt
    last_channel_index: SafeInt
    lower: FiniteNumber
    upper: FiniteNumber
    raw_file_ref: Identifier
    raw_file_sha256: Identifier
    mapping_snapshot_id: Identifier
    limits_source: Identifier
    limits_source_sha256: Identifier
    policy_id: Identifier

    @property
    def to_s(self):
        return self.last_observed_s if self.exit_s is None else self.exit_s

    @property
    def code(self):
        return 'JOINT_LIMIT_EXCEEDED' if self.severity == 'error' else 'JOINT_LIMIT_NEAR'

    def intersects(self, start, end):
        return self.from_s <= end and (self.to_s >= start if self.exit_s is None else self.exit_s > start)
