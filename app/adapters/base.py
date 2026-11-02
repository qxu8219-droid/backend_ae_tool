"""Bounded, transport-independent import interfaces."""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
from pathlib import Path
from typing import Protocol

from app.schemas import ParseIssue
from replay_protocol import ReplayData


@dataclass(frozen=True)
class ParseLimits:
    max_file_bytes: int = 64 * 1024 * 1024
    max_samples: int = 20000
    max_channels: int = 128
    max_points: int = 600000
    max_field_bytes: int = 65536
    max_issues: int = 1000
    memory_bytes: int = 768 * 1024 * 1024
    timeout_s: float = 30.0

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == 'timeout_s':
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError('timeout_s must be positive and finite')
            elif type(value) is not int or value <= 0:
                raise ValueError(f'{field.name} must be a positive integer')


class AdapterError(Exception):
    def __init__(self, code: str, message: str, issues: list[ParseIssue] | None = None, status_code: int = 422):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.issues = issues or [ParseIssue(severity='error', code=code, message=message)]


class MappingError(AdapterError):
    def __init__(self, message: str, issues: list[ParseIssue] | None = None):
        super().__init__('INVALID_MAPPING', message, issues, 400)


class LimitError(AdapterError):
    def __init__(self, message: str, issues: list[ParseIssue] | None = None):
        super().__init__('IMPORT_LIMIT_EXCEEDED', message, issues, 413)


@dataclass
class RawColumn:
    values: list[object]
    timestamps: list[object] | None = None
    lines: list[int] | None = None


@dataclass
class RawTable:
    columns: dict[str, RawColumn]
    layout: str


class ReplayAdapter(Protocol):
    def probe(self, path: Path) -> float: ...
    def load(self, path: Path, config=None) -> ReplayData: ...
