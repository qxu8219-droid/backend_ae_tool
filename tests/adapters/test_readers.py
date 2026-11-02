"""Reader boundaries; expected values and source positions are hand-written."""

import json
from decimal import Decimal

import pytest


def csv_adapter(**limits):
    from app.adapters.base import ParseLimits
    from app.adapters.csv_adapter import CSVAdapter

    return CSVAdapter(ParseLimits(**limits))


def json_adapter(**limits):
    from app.adapters.base import ParseLimits
    from app.adapters.json_adapter import JSONAdapter

    return JSONAdapter(ParseLimits(**limits))


def write(tmp_path, text, name="input.log"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def assert_error(code, operation):
    from app.adapters.base import AdapterError

    with pytest.raises(AdapterError) as caught:
        operation()
    assert caught.value.code == code
    return caught.value


def test_csv_bom_multiline_and_physical_source_start(tmp_path):
    path = write(tmp_path, '\ufefftimestamp,q,note\r\n0,1,"two\r\nlines"\r\n1,,ok\r\n')
    table = csv_adapter().read(path)
    assert table.layout == "csv"
    assert table.columns["timestamp"].values == ["0", "1"]
    assert table.columns["q"].values == ["1", ""]
    assert table.columns["note"].values == ["two\r\nlines", "ok"]
    assert table.columns["q"].lines == [2, 4]


@pytest.mark.parametrize(
    "text",
    ["timestamp,q,q\n0,1,2", "timestamp,,q\n0,1,2", "timestamp,q\n0", "timestamp,q\n0,1,2", 'timestamp,q\n0,"open', "timestamp,q\n", ""],
)
def test_csv_rejects_ambiguous_or_malformed_records(tmp_path, text):
    assert_error("INVALID_CSV", lambda: csv_adapter().read(write(tmp_path, text)))


def test_csv_invalid_utf8_has_structured_error(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"timestamp,q\n0,\xff")
    assert_error("INVALID_ENCODING", lambda: csv_adapter().read(path))


@pytest.mark.parametrize(
    "text,limits",
    [
        ("t,q\n0,1\n1,2\n", {"max_samples": 1}),
        ("t,q,z\n0,1,2\n", {"max_channels": 2}),
        ("t,q\n0,1\n1,2\n", {"max_points": 3}),
        ("t,q\n0,éé\n", {"max_field_bytes": 3}),
        ("t,q\n0,1\n", {"max_file_bytes": 7}),
    ],
)
def test_csv_enforces_each_size_bound(tmp_path, text, limits):
    error = assert_error("IMPORT_LIMIT_EXCEEDED", lambda: csv_adapter(**limits).read(write(tmp_path, text)))
    assert error.status_code == 413


def test_csv_allows_exact_bounds_and_quoted_comma(tmp_path):
    text = 't,q\n0,"a,b"\n'
    table = csv_adapter(max_samples=1, max_channels=2, max_points=2, max_field_bytes=3, max_file_bytes=len(text.encode())).read(write(tmp_path, text))
    assert table.columns["q"].values == ["a,b"]


def test_csv_reads_long_multichunk_record_without_truncation(tmp_path):
    text = "t,q\n0," + "x" * 140000 + "\n"
    table = csv_adapter(max_field_bytes=150000).read(write(tmp_path, text))
    assert len(table.columns["q"].values[0]) == 140000


@pytest.mark.parametrize("wrapper", [False, True])
def test_json_records_preserve_sparse_fields_decimal_and_record_numbers(tmp_path, wrapper):
    raw = '[{"timestamp":1700000000.000000001,"q":2},{"timestamp":1700000000.000000002,"extra":3}]'
    if wrapper:
        raw = '{"records":' + raw + "}"
    table = json_adapter().read(write(tmp_path, "\ufeff" + raw))
    assert table.layout == "json_records"
    assert table.columns["timestamp"].values == [Decimal("1700000000.000000001"), Decimal("1700000000.000000002")]
    assert table.columns["q"].values == [2, None]
    assert table.columns["extra"].values == [None, 3]
    assert table.columns["q"].lines == [1, 2]


@pytest.mark.parametrize("wrapper", [False, True])
def test_json_column_arrays_are_a_single_shared_grid(tmp_path, wrapper):
    raw = {"timestamp": [0, 1], "q": [2, None]}
    if wrapper:
        raw = {"columns": raw}
    table = json_adapter().read(write(tmp_path, json.dumps(raw)))
    assert table.layout == "json_columns"
    assert table.columns["q"].values == [2, None]
    assert table.columns["q"].lines == [1, 2]
    assert table.columns["q"].timestamps is None


def test_json_channels_require_reference_and_preserve_independent_clocks(tmp_path):
    raw = {"channels": {"clock": [0, 1], "q": {"timestamps": [0, 0.5, 1], "values": [2, 3, 4]}, "z": {"values": [5, 6]}}}
    table = json_adapter().read(write(tmp_path, json.dumps(raw)), {"timestamp": {"field": "clock"}})
    assert table.layout == "json_channels"
    assert table.columns["q"].values == [2, 3, 4]
    assert table.columns["q"].timestamps == [0, Decimal("0.5"), 1]
    assert table.columns["q"].lines == [1, 2, 3]
    assert table.columns["clock"].timestamps is None


@pytest.mark.parametrize(
    "raw",
    [
        "{}", "[]", "null", "42", '[{"t":0},1]', '{"t":[0],"q":[1,2]}',
        '{"records":[]}', '{"columns":{"t":0}}',
        '{"timestamp":[0],"timestamp":[1]}', '[{"timestamp":0,"q":1,"q":2}]',
        '{"channels":{"q":{"timestamps":[0],"values":[1]}}}',
        '{"channels":{"timestamp":[0],"q":{"timestamps":[0,1],"values":[1]}}}',
        '{"channels":{"timestamp":[0],"q":{"values":[1,2]}}}',
        '{"channels":{"timestamp":{"timestamps":[0],"values":[0]},"q":[1]}}',
        '{"channels":{"timestamp":[0],"q":{"values":[1],"typo":1}}}',
        '{"timestamp":[0],"q":[1],}',
    ],
)
def test_json_rejects_ambiguous_or_malformed_layouts(tmp_path, raw):
    assert_error("INVALID_JSON", lambda: json_adapter().read(write(tmp_path, raw)))


def test_json_nonfinite_values_reach_quality_normalization(tmp_path):
    table = json_adapter().read(write(tmp_path, '{"timestamp":[0,1,2],"q":[NaN,Infinity,-Infinity]}'))
    assert table.columns["q"].values[0].is_nan()
    assert table.columns["q"].values[1] == Decimal("Infinity")
    assert table.columns["q"].values[2] == Decimal("-Infinity")


@pytest.mark.parametrize(
    "raw,limits",
    [
        ('{"t":[0,1],"q":[1,2]}', {"max_samples": 1}),
        ('{"t":[0],"q":[1]}', {"max_channels": 1}),
        ('[{"t":0},{"q":1}]', {"max_channels": 1}),
        ('{"t":[0,1],"q":[1,2]}', {"max_points": 3}),
        ('{"t":[0],"q":["éé"]}', {"max_field_bytes": 3}),
        ('{"t":[0],"q":[12345]}', {"max_field_bytes": 4}),
        ('{"t":[0],"q":[1]}', {"max_file_bytes": 10}),
        ('{"channels":{"timestamp":[0],"q":{"timestamps":[0,0,0],"values":[1,2,3]}}}', {"max_samples": 2}),
    ],
)
def test_json_enforces_each_size_bound(tmp_path, raw, limits):
    assert_error("IMPORT_LIMIT_EXCEEDED", lambda: json_adapter(**limits).read(write(tmp_path, raw)))


def test_probes_are_bounded_content_checks_independent_of_suffix(tmp_path):
    csv_path = write(tmp_path, "timestamp,q\n0,1\n", "csv.bin")
    json_path = write(tmp_path, '[{"timestamp":0,"q":1}]', "json.bin")
    assert csv_adapter().probe(csv_path) > json_adapter().probe(csv_path)
    assert json_adapter().probe(json_path) > csv_adapter().probe(json_path)


@pytest.mark.parametrize("prefix,adapter", [("t,q\n0,", csv_adapter), ('[{"t":0,"q":"', json_adapter)])
def test_probe_utf8_chunk_boundary_does_not_hide_valid_format(tmp_path, prefix, adapter):
    # Byte 4095 starts a two-byte character, straddling the bounded probe.
    text = prefix + "a" * (4095 - len(prefix)) + "é"
    assert adapter().probe(write(tmp_path, text)) > 0


def test_json_impossible_decimal_exponent_has_structured_error(tmp_path):
    assert_error("INVALID_JSON", lambda: json_adapter().read(write(tmp_path, '{"t":[0],"q":[1e999999999999999999999999]}')))


def test_json_unpaired_unicode_and_invalid_utf8_are_structured(tmp_path):
    assert_error("INVALID_JSON", lambda: json_adapter().read(write(tmp_path, '{"t":[0],"q":["\\ud800"]}')))
    path = tmp_path / "bad.json"
    path.write_bytes(b'{"t":[0],"q":["\xff"]}')
    assert_error("INVALID_ENCODING", lambda: json_adapter().read(path))


@pytest.mark.parametrize("adapter,text", [(csv_adapter, "t,q\n0,1\n"), (json_adapter, '{"t":[0],"q":[1]}')])
def test_reader_rechecks_byte_bound_if_file_grows_after_stat(tmp_path, monkeypatch, adapter, text):
    from pathlib import Path

    path = write(tmp_path, text)
    original_stat = Path.stat
    grew = False

    def grow_after_stat(self, *args, **kwargs):
        nonlocal grew
        result = original_stat(self, *args, **kwargs)
        if self == path and not grew:
            grew = True
            with self.open("ab") as stream:
                stream.write(b" " * 100)
        return result

    monkeypatch.setattr(Path, "stat", grow_after_stat)
    assert_error("IMPORT_LIMIT_EXCEEDED", lambda: adapter(max_file_bytes=len(text)).read(path))
