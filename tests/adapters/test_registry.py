from decimal import Decimal

import pytest


class ToyAdapter:
    def __init__(self, confidence=0.5):
        self.confidence = confidence

    def probe(self, path):
        return self.confidence

    def load(self, path, config):
        from app.adapters.base import RawTable, RawColumn, ParseLimits
        from app.adapters.normalize import normalize_table
        return normalize_table(RawTable({config.timestamp.field: RawColumn([0]), 'torso_joint1':RawColumn([0])}, 'toy'),path,config,limits=ParseLimits(),parser='toy')


def registry():
    from app.adapters.registry import AdapterRegistry

    return AdapterRegistry()


def failure(code, fn):
    from app.adapters.base import AdapterError

    with pytest.raises(AdapterError) as error:
        fn()
    assert error.value.code == code


def test_explicit_parser_beats_suffix_and_mapping_selection(tmp_path):
    r = registry()
    r.register("toy", ToyAdapter(0), suffixes=(".toy",))
    path = tmp_path / "data.csv"
    path.write_text("not csv")
    assert r.select(path, parser="toy")[0] == "toy"
    assert r.load(path, {"adapter": "json_v1", "timestamp": {"field": "clock"}}, parser="toy").mapping_snapshot["timestamp"]["field"] == "clock"


def test_mapping_selection_beats_suffix(tmp_path):
    r = registry()
    r.register("toy", ToyAdapter(0))
    path = tmp_path / "data.csv"
    path.write_text("toy source")
    assert r.load(path, {"adapter": "toy"}).metadata["parser"] == "toy"


def test_known_case_insensitive_suffix_prevents_probe_guess(tmp_path):
    r = registry()
    r.register("toy", ToyAdapter(1))
    path = tmp_path / "data.CSV"
    path.write_text("not even valid csv")
    assert r.select(path)[0] == "csv_v1"


def test_unknown_suffix_selects_highest_positive_confidence(tmp_path):
    r = registry()
    r.register("toy", ToyAdapter(0.99))
    path = tmp_path / "data.unknown"
    path.write_text("timestamp,q\n0,1\n")
    assert r.select(path)[0] == "toy"


def test_ambiguous_probe_and_unsupported_file_are_explicit(tmp_path):
    r = registry()
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x00\xff")
    failure("UNSUPPORTED_LOG_FORMAT", lambda: r.select(path))
    r.register("one", ToyAdapter(0.8))
    r.register("two", ToyAdapter(0.8))
    failure("AMBIGUOUS_LOG_FORMAT", lambda: r.select(path))
    failure("UNKNOWN_PARSER", lambda: r.select(path, parser="missing"))


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1, True, "0.5", Decimal("0.5")])
def test_invalid_plugin_confidence_is_rejected(tmp_path, score):
    r = registry()
    r.register("bad", ToyAdapter(score))
    path = tmp_path / "data.bin"
    path.write_text("")
    failure("INVALID_ADAPTER_PROBE", lambda: r.select(path))


def test_duplicate_names_suffixes_and_bad_contract_do_not_register(tmp_path):
    r = registry()
    failure("ADAPTER_CONFLICT", lambda: r.register("csv_v1", ToyAdapter()))
    failure("ADAPTER_CONFLICT", lambda: r.register("bad", ToyAdapter(), suffixes=(".CSV",)))
    failure("INVALID_ADAPTER", lambda: r.register("bad", object()))
    failure("INVALID_ADAPTER", lambda: r.register("../bad", ToyAdapter()))
    failure("INVALID_ADAPTER", lambda: r.register("bad", ToyAdapter(), suffixes=("csv",)))
    r.register("bad", ToyAdapter(), suffixes=(".fine",))
    assert r.select(tmp_path / "x.fine")[0] == "bad"


class EntryPoint:
    def __init__(self, name, factory):
        self.name = name
        self.factory = factory

    def load(self):
        return self.factory


def test_entrypoints_are_opt_in_and_receive_limits(monkeypatch, tmp_path):
    import importlib.metadata
    from app.adapters.base import ParseLimits
    from app.adapters.registry import AdapterRegistry

    calls = []

    def factory(*, limits):
        calls.append(limits.max_samples)
        adapter = ToyAdapter()
        adapter.suffixes = (".plug",)
        return adapter

    def entry_points(*, group):
        assert group == "model_eval.replay_adapters"
        return [EntryPoint("installed", factory)]

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    r = AdapterRegistry(ParseLimits(max_samples=42))
    assert calls == []
    r.discover_plugins()
    assert calls == [42]
    assert r.select(tmp_path / "data.plug")[0] == "installed"


def test_plugin_discovery_conflict_is_atomic(monkeypatch, tmp_path):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [EntryPoint("okay", lambda **kwargs: ToyAdapter()), EntryPoint("csv_v1", lambda **kwargs: ToyAdapter())])
    r = registry()
    failure("ADAPTER_CONFLICT", r.discover_plugins)
    failure("UNKNOWN_PARSER", lambda: r.select(tmp_path / "x", parser="okay"))
