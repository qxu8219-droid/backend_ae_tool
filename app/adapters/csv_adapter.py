"""Strict UTF-8 CSV reader with bounded reads and original physical line numbers."""
from __future__ import annotations

import csv
import codecs
import io
from pathlib import Path
from threading import RLock

from app.schemas import ParseIssue
from .base import AdapterError, LimitError, ParseLimits, RawColumn, RawTable
from .mapping import parse_mapping


_CSV_LIMIT_LOCK = RLock()


class _BoundedRaw(io.RawIOBase):
    """Read at most 64 KiB at a time, including files growing after stat()."""

    def __init__(self, source, limit):
        super().__init__()
        self.source, self.limit, self.count = source, limit, 0

    def readable(self):
        return True

    def readinto(self, target):
        data = self.source.read(min(len(target), 65536, self.limit - self.count + 1))
        self.count += len(data)
        if self.count > self.limit:
            raise LimitError("CSV exceeds max_file_bytes")
        target[:len(data)] = data
        return len(data)


class CSVAdapter:
    suffixes = (".csv",)

    def __init__(self, limits: ParseLimits = ParseLimits()):
        self.limits = limits

    def probe(self, path: Path) -> float:
        try:
            with Path(path).open("rb") as stream:
                text = codecs.getincrementaldecoder("utf-8-sig")().decode(stream.read(4096), final=False)
        except UnicodeError:
            return 0.0
        text = text.lstrip()
        if not text or text[0] in "{[" or "\x00" in text:
            return 0.0
        try:
            header = next(csv.reader(io.StringIO(text), strict=True))
        except (csv.Error, StopIteration):
            return 0.0
        return 0.6 if len(header) > 1 and all(header) else 0.0

    def load(self, path: Path, config=None):
        from .normalize import normalize_table

        mapping = parse_mapping(config)
        return normalize_table(self.read(path, mapping), Path(path), mapping, limits=self.limits, parser="csv_v1")

    def read(self, path: Path, config=None) -> RawTable:
        # Validate the same mapping for read/load, even though CSV has one grid.
        parse_mapping(config)
        path = Path(path)
        if path.stat().st_size > self.limits.max_file_bytes:
            raise LimitError("CSV exceeds max_file_bytes")
        # CSV's field limit is process-global. Restore it even on error and keep
        # concurrent adapter readers from changing one another's bound.
        with _CSV_LIMIT_LOCK:
            old_limit = csv.field_size_limit(self.limits.max_field_bytes)
            try:
                with path.open("rb") as source:
                    with io.TextIOWrapper(io.BufferedReader(_BoundedRaw(source, self.limits.max_file_bytes)), encoding="utf-8-sig", newline="") as stream:
                        return self._read_stream(stream)
            except UnicodeError as exc:
                raise AdapterError("INVALID_ENCODING", "CSV must be UTF-8 (a UTF-8 BOM is allowed)") from exc
            finally:
                csv.field_size_limit(old_limit)

    def _read_stream(self, stream) -> RawTable:
        limits = self.limits
        # A physical line cannot exceed a fully quoted row of bounded fields.
        line_limit = min(limits.max_file_bytes, limits.max_channels * (2 * limits.max_field_bytes + 3) + 2)

        def lines():
            while line := stream.readline(line_limit + 1):
                if len(line.encode("utf-8")) > line_limit:
                    raise LimitError("CSV physical line exceeds the configured row bounds")
                yield line

        reader = csv.reader(lines(), strict=True)

        def invalid(message, line=None):
            return AdapterError("INVALID_CSV", message, [ParseIssue(severity="error", code="INVALID_CSV", line=line, message=message)])

        try:
            header = next(reader, None)
            if not header or any(not name.strip() for name in header):
                raise invalid("CSV requires a non-empty header with no blank field names", 1)
            if len(set(header)) != len(header):
                raise invalid("CSV header contains duplicate field names", 1)
            if len(header) > limits.max_channels:
                raise LimitError("CSV exceeds max_channels")
            if any(len(name.encode("utf-8")) > limits.max_field_bytes for name in header):
                raise LimitError("CSV header exceeds max_field_bytes")
            columns = {name: RawColumn([], lines=[]) for name in header}
            sample_count = 0
            while True:
                line = reader.line_num + 1
                row = next(reader, None)
                if row is None:
                    break
                if len(row) != len(header):
                    raise invalid("CSV record width differs from its header", line)
                sample_count += 1
                if sample_count > limits.max_samples:
                    raise LimitError("CSV exceeds max_samples")
                if sample_count * len(header) > limits.max_points:
                    raise LimitError("CSV exceeds max_points")
                for name, value in zip(header, row, strict=True):
                    if len(value.encode("utf-8")) > limits.max_field_bytes:
                        raise LimitError("CSV field exceeds max_field_bytes")
                    columns[name].values.append(value)
                    columns[name].lines.append(line)
            if sample_count == 0:
                raise invalid("CSV contains no data records", reader.line_num or 1)
            return RawTable(columns, "csv")
        except csv.Error as exc:
            if "field larger than field limit" in str(exc):
                raise LimitError("CSV field exceeds max_field_bytes") from exc
            raise invalid(f"Malformed CSV: {exc}", reader.line_num or 1) from exc
