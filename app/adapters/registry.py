"""Deterministic adapter selection; installed plugin discovery is explicit."""
from __future__ import annotations

import importlib.metadata
import math
from pathlib import Path
import re

from .base import AdapterError, ParseLimits
from .csv_adapter import CSVAdapter
from .json_adapter import JSONAdapter
from .mapping import parse_mapping


class AdapterRegistry:
    def __init__(self, limits: ParseLimits = ParseLimits()):
        self.limits = limits
        self._adapters = {}
        self._suffixes = {}
        self.register("csv_v1", CSVAdapter(limits), suffixes=(".csv",))
        self.register("json_v1", JSONAdapter(limits), suffixes=(".json",))
        from .b041.adapter import B041Adapter
        self.register("b041_bundle_v1", B041Adapter(limits), suffixes=(".b041",))

    def register(self, name, adapter, suffixes=()):
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name) is None:
            raise AdapterError("INVALID_ADAPTER", "Adapter name must be a stable identifier", status_code=400)
        if not callable(getattr(adapter, "load", None)) or not callable(getattr(adapter, "probe", None)):
            raise AdapterError("INVALID_ADAPTER", "Adapters require callable load and probe methods", status_code=400)
        if isinstance(suffixes, str):
            raise AdapterError("INVALID_ADAPTER", "Adapter suffixes must be a sequence", status_code=400)
        try:
            suffixes = tuple(suffixes)
        except TypeError as exc:
            raise AdapterError("INVALID_ADAPTER", "Adapter suffixes must be a sequence", status_code=400) from exc
        if any(not isinstance(suffix, str) or re.fullmatch(r"\.[A-Za-z0-9]+", suffix) is None for suffix in suffixes):
            raise AdapterError("INVALID_ADAPTER", "Adapter suffixes must have the form .extension", status_code=400)
        suffixes = tuple(suffix.lower() for suffix in suffixes)
        if name in self._adapters or len(set(suffixes)) != len(suffixes) or any(suffix in self._suffixes for suffix in suffixes):
            raise AdapterError("ADAPTER_CONFLICT", "Adapter name or suffix is already registered", status_code=400)
        self._adapters[name] = adapter
        self._suffixes.update({suffix: name for suffix in suffixes})

    def select(self, path: Path, parser=None):
        path = Path(path)
        if parser is not None:
            if not isinstance(parser, str) or parser not in self._adapters:
                raise AdapterError("UNKNOWN_PARSER", "Requested parser is not registered", status_code=400)
            return parser, self._adapters[parser]
        if name := self._suffixes.get(path.suffix.lower()):
            return name, self._adapters[name]
        scores = []
        for name, adapter in self._adapters.items():
            try:
                score = adapter.probe(path)
            except AdapterError:
                raise
            except Exception as exc:
                raise AdapterError("ADAPTER_PROBE_FAILED", f"Adapter probe failed: {name}") from exc
            if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
                raise AdapterError("INVALID_ADAPTER_PROBE", f"Adapter probe must return a finite confidence in [0, 1]: {name}")
            scores.append((score, name))
        best_score = max(score for score, _ in scores)
        if best_score == 0:
            raise AdapterError("UNSUPPORTED_LOG_FORMAT", "No adapter recognizes this log format", status_code=415)
        matches = [name for score, name in scores if score == best_score]
        if len(matches) != 1:
            raise AdapterError("AMBIGUOUS_LOG_FORMAT", "Multiple adapters match; specify an explicit parser", status_code=400)
        return matches[0], self._adapters[matches[0]]

    def load(self, path: Path, config=None, parser=None):
        from replay_protocol import ReplayData
        from .normalize import upload_result

        mapping = parse_mapping(config)
        name, adapter = self.select(path, parser=parser if parser is not None else mapping.adapter)
        try:
            value = adapter.load(Path(path), mapping)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError('ADAPTER_LOAD_FAILED', f'Adapter failed while reading source: {name}') from exc
        try:
            data = ReplayData.model_validate(value)
            upload_result(data)
            return data
        except AdapterError:
            raise
        except (ValueError, TypeError, AttributeError) as exc:
            raise AdapterError('INVALID_ADAPTER_OUTPUT', f'Adapter did not return valid ReplayData: {name}') from exc

    def discover_plugins(self):
        """Load trusted installed entry points, never arbitrary user paths.

        The group is ``model_eval.replay_adapters``. Each entry point name is
        its adapter ID and resolves to a factory accepting ``limits=``. The
        returned adapter may declare ``suffixes``. Registration is atomic on
        conflict, although trusted factory side effects cannot be rolled back.
        """
        pending = AdapterRegistry(self.limits)
        pending._adapters = dict(self._adapters)
        pending._suffixes = dict(self._suffixes)
        for entry in importlib.metadata.entry_points(group="model_eval.replay_adapters"):
            try:
                factory = entry.load()
                adapter = factory(limits=self.limits)
            except Exception as exc:
                raise AdapterError("ADAPTER_PLUGIN_FAILED", f"Installed adapter factory failed: {entry.name}") from exc
            pending.register(entry.name, adapter, suffixes=getattr(adapter, "suffixes", ()))
        self._adapters, self._suffixes = pending._adapters, pending._suffixes
