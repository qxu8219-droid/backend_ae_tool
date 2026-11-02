#!/usr/bin/env python3
"""Prepare an offline, traceable B041 numeric recording in a new directory.

PyArrow is required only for the parquet command. Runtime preparation uses the
standard library. No archived code is imported, and source files are never written.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import sys


SCHEMA = "b041.recording.v1"
GROUPS = ((0, 7), (7, 14), (14, 15), (15, 16), (16, 19), (19, 23))
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ROWS = 200_000
ACTION_EVENTS = {"target", "rim_pose_target"}
STATE_EVENTS = {"target", "convergence_wait_feedback", "rim_pose_reached"}


def fail(code: str, detail: str) -> None:
    raise ValueError(f"{code}: {detail}")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bounded_json(path: Path):
    if path.stat().st_size > MAX_JSON_BYTES:
        fail("INPUT_TOO_LARGE", str(path))
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def portable(value, key=""):
    """Preserve integer nanoseconds in all nested evidence, including annotations."""
    if isinstance(value, dict):
        return {k: portable(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable(v, key) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value) if key.endswith("_ns") or abs(value) > 2**53 - 1 else value
    if isinstance(value, (float, Decimal)):
        return float(value) if math.isfinite(value) else None
    fail("INVALID_VALUE", f"unsupported value in {key}")


def finite_number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        fail("INVALID_VECTOR", "values must be numbers or null")
    return float(value) if math.isfinite(value) else None


def vector(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 23:
        fail("INVALID_VECTOR", "expected full23 array")
    return [finite_number(v) for v in value]


def exact_int(value, code, label):
    if type(value) is not int:
        fail(code, f"{label} must be an integer")
    return value


def qualities(values, row=None, prefix=None):
    if values is None:
        return None
    result = []
    if row is not None:
        keys = [prefix + suffix for suffix in ("_valid", "_source_log_ns", "_age")]
        if any(not isinstance(row.get(k), list) or len(row[k]) != 6 for k in keys):
            fail("INVALID_QUALITY", f"expected six groups for {prefix}")
    for i, (start, end) in enumerate(GROUPS):
        valid = all(v is not None for v in values[start:end])
        source_time, age = None, None
        if row is not None:
            original = row[keys[0]][i]
            if original not in (0, 1, None) and not (
                isinstance(original, (float, Decimal)) and not math.isfinite(original)
            ):
                fail("INVALID_QUALITY", "valid flags must be 0, 1 or null")
            valid = valid and original == 1
            stamp = row[keys[1]][i]
            if stamp is not None:
                source_time = str(exact_int(stamp, "INVALID_QUALITY", keys[1]))
            age = finite_number(row[keys[2]][i])
        result.append({"valid": valid, "source_time": source_time, "age_s": age,
                       "timing": "source_log_time_ns" if row is not None else "event_monotonic_only"})
    return result


def sample(time, row, state, action, *, event, frame=None, context=None,
           state_quality=None, action_quality=None):
    return {"time": str(time), "row": row, "frame_index": frame, "event": event,
            "state": state, "action": action, "state_quality": state_quality,
            "action_quality": action_quality, "context": portable(context or {})}


def check_samples(samples):
    if not samples or not any(q["valid"] for s in samples
                             for key in ("state_quality", "action_quality")
                             for q in (s[key] or [])):
        fail("NO_USABLE_DATA", "no recorded valid feedback or published target sequence")
    times = [Decimal(s["time"]) for s in samples]
    if any(not t.is_finite() for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        fail("INVALID_TIME_ORDER", "sample clocks must be finite and strictly increasing")


def empty_annotations():
    return {"alignment": "none", "source_id": None, "sha256": None,
            "segments": [], "reference": {}}


def runtime(run: Path):
    events_path, summary_path = run / "events.jsonl", run / "SUMMARY.json"
    summary = bounded_json(summary_path)
    if not isinstance(summary, dict):
        fail("INVALID_SUMMARY", "SUMMARY.json must be an object")
    if events_path.stat().st_size > MAX_JSON_BYTES:
        fail("INPUT_TOO_LARGE", str(events_path))
    files = [(events_path, "numeric_source"), (summary_path, "run_summary")]
    events, samples = [], []
    with events_path.open(encoding="utf-8") as stream:
        for row, line in enumerate(stream, 1):
            if row > MAX_ROWS:
                fail("INPUT_TOO_LARGE", "too many events")
            obj = json.loads(line, parse_float=Decimal)
            if not isinstance(obj, dict) or not isinstance(obj.get("event"), str):
                fail("INVALID_EVENT", f"row {row}: missing event name")
            kind, timestamp = obj["event"], obj.get("monotonic")
            time = str(timestamp) if isinstance(timestamp, (int, float, Decimal)) and not isinstance(timestamp, bool) and math.isfinite(timestamp) else None
            events.append({"time": time, "row": row, "kind": kind, "details": portable(obj)})
            state = vector(obj.get("feedback23")) if kind in STATE_EVENTS else None
            action = vector(obj.get("target23")) if kind in ACTION_EVENTS else None
            if state is None and action is None:
                continue
            if time is None:
                fail("INVALID_TIME", f"row {row}: missing finite event monotonic")
            context = {k: v for k, v in obj.items() if k not in ("feedback23", "target23", "monotonic", "event")}
            samples.append(sample(time, row, state, action, event=kind, context=context,
                                  state_quality=qualities(state), action_quality=qualities(action)))
    check_samples(samples)
    annotations = empty_annotations()
    selection = run / "selection.json"
    if selection.is_file():
        reference = bounded_json(selection)
        annotations.update(alignment="reference_only", source_id=reference.get("case", {}).get("source_id"),
                           sha256=digest(selection), reference=portable(reference))
        files.append((selection, "reference_selection"))
    # Local profile copies, if present, are evidence only. Never follow recorded remote paths.
    for path in sorted(run.glob("*profile*.json")):
        files.append((path, "profile_reference"))
    settings = summary.get("effective_settings", {})
    base_setting = settings.get("base", summary.get("base")) if isinstance(settings, dict) else summary.get("base")
    locked = True if base_setting == "locked" else False if base_setting in ("free", "enabled", "unlocked") else None
    source = {"id": run.name, "format": "runtime_events", "time_unit": "s",
              "clock": "run_monotonic_event_time", "sha256": digest(events_path),
              "name": events_path.name, "episode_index": None, "base_locked": locked}
    return {"schema": SCHEMA, "source": source, "samples": samples,
            "annotations": annotations, "events": events,
            "diagnostics": {"summary": portable(summary), "base_lock_source": "SUMMARY.json" if locked is not None else None,
                            "state_event_kinds": sorted(STATE_EVENTS), "action_event_kinds": sorted(ACTION_EVENTS),
                            "predicted_horizons_are_not_commands": True}}, files


def align_annotations(path, source_id, rows):
    doc = bounded_json(path)
    sources = [s for s in doc.get("sources", []) if s.get("source_id") == source_id]
    if len(sources) != 1:
        fail("ANNOTATION_SOURCE_MISMATCH", "expected one exact source_id")
    source = sources[0]
    if source.get("source_frame_count") != len(rows):
        fail("ANNOTATION_COUNT_MISMATCH", "sidecar frame count differs from Parquet")
    episodes = {r["episode_index"] for r in rows}
    if episodes != {source.get("source_episode_index")}:
        fail("ANNOTATION_EPISODE_MISMATCH", "sidecar episode differs from Parquet")
    segments = []
    previous_end = 0
    ids = set()
    for index, original in enumerate(source.get("segments", [])):
        start = exact_int(original.get("start_frame"), "ANNOTATION_BOUNDARY", "start_frame")
        end = exact_int(original.get("end_frame"), "ANNOTATION_BOUNDARY", "end_frame")
        if not 0 <= start < end <= len(rows) or start < previous_end:
            fail("ANNOTATION_BOUNDARY", "invalid or overlapping half-open source-frame interval")
        previous_end = end
        for field, frame in (("start_log_ns", start), ("end_log_ns", end)):
            stamp = original.get(field)
            if stamp is None:
                continue
            exact_int(stamp, "ANNOTATION_CLOCK_MISMATCH", field)
            if frame < len(rows):
                expected = rows[frame]["source_log_time_ns"]
            else:
                fps = source.get("source_fps")
                if not isinstance(fps, (int, float, Decimal)) or fps <= 0:
                    fail("ANNOTATION_CLOCK_MISMATCH", "terminal boundary needs declared source_fps")
                # The terminal edge is an interval boundary, never sample N.
                elapsed = (Decimal(len(rows)) * 1_000_000_000 / Decimal(str(fps))).to_integral_value(rounding=ROUND_HALF_UP)
                expected = rows[0]["source_log_time_ns"] + int(elapsed)
                if stamp != expected:
                    # Some reviewed sources construct the exclusive terminal
                    # edge as last sample + one rounded period. At fractional
                    # nanosecond rates this can differ from rounding the total.
                    # Only accept that exact construction on a verified grid;
                    # observed/internal boundaries retain strict equality.
                    period = Decimal(1_000_000_000) / Decimal(str(fps))
                    origin = rows[0]["source_log_time_ns"]
                    on_grid = all(
                        row["source_log_time_ns"] == origin + int(
                            (Decimal(i) * period).to_integral_value(rounding=ROUND_HALF_UP))
                        for i, row in enumerate(rows)
                    )
                    alternate = rows[-1]["source_log_time_ns"] + int(
                        period.to_integral_value(rounding=ROUND_HALF_UP))
                    if on_grid and stamp == alternate:
                        expected = alternate
            if stamp != expected:
                fail("ANNOTATION_CLOCK_MISMATCH", f"{field} does not match source frame {frame}")
        segment_id = original.get("id", original.get("segment_id", f"{source_id}:segment:{index}"))
        if not isinstance(segment_id, str) or segment_id in ids:
            fail("ANNOTATION_BOUNDARY", "segment ids must be unique strings")
        ids.add(segment_id)
        boundary = original.get("boundary_status", original.get("boundary_uncertainty", {}).get("status"))
        segments.append({**portable(original), "id": segment_id,
                         "label": original.get("label", original.get("subtask", original.get("skill", segment_id))),
                         "review_status": original.get("review_status"), "boundary_status": boundary,
                         "details": portable(original)})
    return {"alignment": "source_frames", "source_id": source_id, "sha256": digest(path),
            "segments": segments, "reference": portable({k: v for k, v in source.items() if k != "segments"})}


def parquet(path: Path, source_id: str, annotations: Path):
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ValueError("PYARROW_REQUIRED: run the local preparer in an existing PyArrow environment") from exc
    file = pq.ParquetFile(path)
    if file.metadata.num_rows > MAX_ROWS:
        fail("INPUT_TOO_LARGE", "too many Parquet rows")
    required = {"observation.state", "action", "source_log_time_ns", "frame_index", "episode_index"}
    for prefix in ("observation.state", "action"):
        required.update(prefix + suffix for suffix in ("_valid", "_source_log_ns", "_age"))
    if not required <= set(file.schema_arrow.names):
        fail("INVALID_PARQUET_SCHEMA", "missing full23 numeric or quality columns")
    # Do not read binary image structs or unknown columns into the numeric package.
    optional = {"timestamp", "index", "task_index", "observation.image_source_log_ns",
                "observation.image_header_ns", "observation.image_age", "observation.image_valid"}
    columns = [name for name in file.schema_arrow.names if name in required | optional]
    rows = file.read(columns=columns).to_pylist()
    if not rows:
        fail("NO_USABLE_DATA", "empty Parquet episode")
    samples = []
    for index, row in enumerate(rows):
        if exact_int(row["frame_index"], "FRAME_ALIGNMENT", "frame_index") != index:
            fail("FRAME_ALIGNMENT", "full source episode requires consecutive frames starting at zero")
        exact_int(row["episode_index"], "ANNOTATION_EPISODE_MISMATCH", "episode_index")
        time = exact_int(row["source_log_time_ns"], "INVALID_TIME", "source_log_time_ns")
        state, action = vector(row["observation.state"]), vector(row["action"])
        context = {k: v for k, v in row.items() if k in optional}
        samples.append(sample(time, index + 1, state, action, event="frame", frame=index, context=context,
                              state_quality=qualities(state, row, "observation.state"),
                              action_quality=qualities(action, row, "action")))
    check_samples(samples)
    aligned = align_annotations(annotations, source_id, rows)
    source = {"id": source_id, "format": "full23_parquet", "time_unit": "ns",
              "clock": "source_log_time_ns", "sha256": digest(path), "name": path.name,
              "episode_index": rows[0]["episode_index"], "base_locked": None}
    return {"schema": SCHEMA, "source": source, "samples": samples,
            "annotations": aligned, "events": [],
            "diagnostics": {"selected_columns": columns, "source_frame_count": len(rows),
                            "terminal_boundary_is_not_a_sample": True}}, [(path, "numeric_source"), (annotations, "annotation_sidecar")]


def ensure_output(output, inputs):
    if output.exists() or output.is_symlink():
        fail("OUTPUT_EXISTS", "output directory must be new")
    protected = [Path(__file__).resolve().parents[1] / "data" / "real_logs" / "raw"]
    for path in inputs:
        protected.extend(p for p in (path, *path.parents) if p.name == "raw")
        if path.is_dir():
            protected.append(path)
    if any(output.is_relative_to(p.resolve()) for p in protected):
        fail("RAW_OUTPUT_FORBIDDEN", "output cannot be inside original source directories")


def write_bundle(bundle, files, output):
    common = Path(os.path.commonpath([str(p.parent) for p, _ in files]))
    records = [{"name": p.relative_to(common).as_posix(), "sha256": digest(p),
                "role": role, "bytes": p.stat().st_size} for p, role in files]
    bundle["source"]["files"] = records
    samples = bundle["samples"]
    qc = {"sample_count": len(samples), "state_samples": sum(s["state"] is not None for s in samples),
          "action_samples": sum(s["action"] is not None for s in samples),
          "event_count": len(bundle["events"]), "segment_count": len(bundle["annotations"]["segments"]),
          "first_time": samples[0]["time"], "last_time": samples[-1]["time"]}
    bundle["diagnostics"]["qc"] = qc
    encoded = (json.dumps(bundle, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
    unchanged = all(digest(p) == rec["sha256"] for (p, _), rec in zip(files, records))
    if not unchanged:
        fail("SOURCE_CHANGED", "source bytes changed while preparing")
    manifest = {"schema": "b041.preparation.v1", "bundle": "recording.b041",
                "bundle_sha256": hashlib.sha256(encoded).hexdigest(), "source": bundle["source"],
                "source_hashes_unchanged": unchanged, "qc": qc}
    output.mkdir(parents=True, exist_ok=False)
    (output / "recording.b041").write_bytes(encoded)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "qc.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("parquet")
    p.add_argument("file", type=Path)
    p.add_argument("--source-id", required=True)
    p.add_argument("--annotations", type=Path, required=True)
    r = commands.add_parser("runtime")
    r.add_argument("run_dir", type=Path)
    for command in (p, r):
        command.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = args.output_dir.resolve()
        inputs = [args.file.resolve(), args.annotations.resolve()] if args.command == "parquet" else [args.run_dir.resolve()]
        ensure_output(output, inputs)
        bundle, files = parquet(*inputs[:1], args.source_id, inputs[1]) if args.command == "parquet" else runtime(inputs[0])
        manifest = write_bundle(bundle, files, output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"output": str(output), **manifest["qc"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
