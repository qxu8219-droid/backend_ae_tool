"""JSON record/column reference layouts, plus explicitly timestamped channels.

Accepted layouts are a list of records, {records: [...]}, a bare object of
equal-length arrays, {columns: {...}}, or {channels: {...}}. In the last layout
each value is an array or {values: [...], timestamps?: [...]}; the configured
reference timestamp field must be an untimestamped array. Sparse record keys
become None. Duplicate object keys and ambiguous/ragged structures are errors.
"""
from __future__ import annotations

import codecs
from decimal import Decimal, DecimalException
import json
from pathlib import Path

from .base import AdapterError, LimitError, ParseLimits, RawColumn, RawTable
from .mapping import parse_mapping


def _invalid(message):
    return AdapterError("INVALID_JSON", message)


class JSONAdapter:
    suffixes = (".json",)

    def __init__(self, limits: ParseLimits = ParseLimits()):
        self.limits = limits

    def probe(self, path: Path) -> float:
        try:
            with Path(path).open("rb") as stream:
                text = codecs.getincrementaldecoder("utf-8-sig")().decode(stream.read(4096), final=False)
        except UnicodeError:
            return 0.0
        text = text.lstrip()
        return 0.9 if text.startswith(("{", "[")) else 0.0

    def load(self, path: Path, config=None):
        from .normalize import normalize_table

        mapping = parse_mapping(config)
        return normalize_table(self.read(path, mapping), Path(path), mapping, limits=self.limits, parser="json_v1")

    def read(self, path: Path, config=None) -> RawTable:
        mapping = parse_mapping(config)
        path = Path(path)
        if path.stat().st_size > self.limits.max_file_bytes:
            raise LimitError("JSON exceeds max_file_bytes")
        with path.open("rb") as stream:
            data = stream.read(self.limits.max_file_bytes + 1)
        if len(data) > self.limits.max_file_bytes:
            raise LimitError("JSON exceeds max_file_bytes")

        def number(token):
            if len(token) > self.limits.max_field_bytes:
                raise LimitError("JSON number exceeds max_field_bytes")
            try:
                return Decimal(token)
            except DecimalException as exc:
                raise _invalid("JSON numeric token is outside the supported decimal range") from exc

        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise _invalid(f"JSON object contains duplicate key: {key[:80]}")
                result[key] = value
            return result

        try:
            document = json.loads(data.decode("utf-8-sig"), object_pairs_hook=object_pairs, parse_float=number, parse_int=number, parse_constant=number)
        except UnicodeError as exc:
            raise AdapterError("INVALID_ENCODING", "JSON must be UTF-8 (a UTF-8 BOM is allowed)") from exc
        except (ValueError, RecursionError) as exc:
            raise _invalid("Malformed JSON document") from exc
        self._check_fields(document)
        if isinstance(document, list):
            return self._records(document)
        if not isinstance(document, dict) or not document:
            raise _invalid("JSON requires non-empty records or column arrays")
        if set(document) == {"records"}:
            return self._records(document["records"])
        if set(document) == {"columns"}:
            return self._columns(document["columns"])
        if set(document) == {"channels"}:
            return self._channels(document["channels"], mapping.timestamp.field)
        return self._columns(document)

    def _check_fields(self, document):
        pending = [document]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                try:
                    size = len(value.encode("utf-8"))
                except UnicodeError as exc:
                    raise _invalid("JSON contains an unpaired Unicode surrogate") from exc
                if size > self.limits.max_field_bytes:
                    raise LimitError("JSON string exceeds max_field_bytes")
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    def _keys(self, columns):
        if not isinstance(columns, dict) or not columns or any(not key.strip() for key in columns):
            raise _invalid("JSON requires non-empty named columns")
        if len(columns) > self.limits.max_channels:
            raise LimitError("JSON exceeds max_channels")

    def _count(self, count, points):
        if count > self.limits.max_samples:
            raise LimitError("JSON exceeds max_samples")
        if points > self.limits.max_points:
            raise LimitError("JSON exceeds max_points")

    def _records(self, records):
        if not isinstance(records, list) or not records or any(not isinstance(record, dict) for record in records):
            raise _invalid("JSON records must be a non-empty array of objects")
        self._count(len(records), 0)
        keys = dict.fromkeys(key for record in records for key in record)
        self._keys(keys)
        self._count(len(records), len(records) * len(keys))
        return RawTable({key: RawColumn([record.get(key) for record in records], lines=list(range(1, len(records) + 1))) for key in keys}, "json_records")

    def _columns(self, columns):
        self._keys(columns)
        if any(not isinstance(values, list) for values in columns.values()):
            raise _invalid("JSON columns must all be arrays")
        sizes = {len(values) for values in columns.values()}
        if len(sizes) != 1 or 0 in sizes:
            raise _invalid("JSON column arrays must have the same non-zero length")
        count = next(iter(sizes))
        self._count(count, count * len(columns))
        return RawTable({key: RawColumn(values, lines=list(range(1, count + 1))) for key, values in columns.items()}, "json_columns")

    def _channels(self, channels, reference_field):
        self._keys(channels)
        result = {}
        points = 0
        for name, channel in channels.items():
            timestamps = None
            if isinstance(channel, list):
                values = channel
            elif isinstance(channel, dict) and "values" in channel and not set(channel) - {"values", "timestamps"}:
                values = channel["values"]
                timestamps = channel.get("timestamps")
                if "timestamps" in channel and not isinstance(timestamps, list):
                    raise _invalid("Channel timestamps must be an array when supplied")
            else:
                raise _invalid("Channels must be arrays or values/timestamps objects")
            if not isinstance(values, list) or not values:
                raise _invalid("Channel values must be non-empty arrays")
            if timestamps is not None and len(timestamps) != len(values):
                raise _invalid("Channel timestamps and values must have equal lengths")
            points += len(values) + (len(timestamps) if timestamps is not None else 0)
            self._count(len(values), points)
            result[name] = RawColumn(values, timestamps=timestamps, lines=list(range(1, len(values) + 1)))
        reference = result.get(reference_field)
        if reference is None or reference.timestamps is not None:
            raise _invalid("Channels require the configured untimestamped reference timestamp array")
        if any(column.timestamps is None and len(column.values) != len(reference.values) for column in result.values()):
            raise _invalid("Untimestamped channels must match the reference timestamp array length")
        return RawTable(result, "json_channels")
