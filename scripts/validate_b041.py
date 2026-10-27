#!/usr/bin/env python3
"""Independently check and materialize local B041 recordings; CPU only.

Run with the backend Python environment. Imports use the real isolated LogStore
worker. Oracles below do not import the production B041 transform functions.
"""
from __future__ import annotations

import argparse
import asyncio
import cmath
import copy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analysis.service import ReplayAnalysis
from app.replay.interpolator import ReplaySampler
from app.schemas.rest import LogAnnotations
from app.services.log_store import LogStore
from replay_protocol import CANONICAL_JOINTS


ARM_TORSO = {**{f"left_arm_joint{i+1}": i for i in range(7)},
             **{f"right_arm_joint{i+1}": i+7 for i in range(7)},
             **{f"torso_joint{i+1}": i+19 for i in range(4)}}
RAW_NAMES = [*[f"left_arm_joint{i}" for i in range(1, 8)],
             *[f"right_arm_joint{i}" for i in range(1, 8)],
             "left_gripper", "right_gripper", "base_vx", "base_vy", "base_wz",
             *[f"torso_joint{i}" for i in range(1, 5)]]
GROUP = [0]*7 + [1]*7 + [2, 3, 4, 4, 4] + [5]*4


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, label, tolerance=2e-12):
    if expected is None:
        require(actual is None, f"{label}: expected null, got {actual}")
    else:
        require(actual is not None and math.isclose(actual, expected, abs_tol=tolerance, rel_tol=tolerance),
                f"{label}: got {actual}, expected {expected}")


def safe_component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Unsafe source/profile path component")
    return value


def new_directory(path):
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Destination already exists: {path}")
    path.mkdir(parents=True, exist_ok=False)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def oracle_pose(times, velocities, observed_valid, config):
    """Constant body twist via midpoint orientation times sinc, with a validity latch."""
    x, y, heading = config["initial_pose"]
    location = complex(x, y)
    alive, poses, flags = True, [], []
    for i, t in enumerate(times):
        alive = alive and observed_valid[i]
        if i:
            dt = t-times[i-1]
            alive = alive and 0 < dt <= config["max_gap_s"] + 1e-12
            if alive:
                previous = [v*s+b for v, s, b in zip(velocities[i-1], config["velocity_scale"], config["velocity_bias"])]
                velocity, turn = complex(previous[0], previous[1]), previous[2]*dt
                if config["velocity_frame"] == "body":
                    half = turn/2
                    gain = 1. if half == 0 else math.sin(half)/half
                    location += velocity * cmath.exp(complex(0, heading+half)) * dt * gain
                else:
                    location += velocity * dt
                heading += turn
        poses.append([location.real, location.imag, heading])
        flags.append(bool(alive))
    return poses, flags


def observed_valid(recording, record, basis, group, max_age):
    q = record[basis+"_quality"][group]
    if not q["valid"]:
        return False
    if recording["source"]["format"] == "runtime_events":
        return q["timing"] == "event_monotonic_only"
    if q["timing"] != "source_log_time_ns" or q["source_time"] is None or q["age_s"] is None:
        return False
    stamp, time = Decimal(q["source_time"]), Decimal(record["time"])
    elapsed = (time-stamp)/Decimal(1_000_000_000)
    return bool(0 <= stamp <= time and 0 <= q["age_s"] <= max_age and abs(float(elapsed)-q["age_s"]) <= 1e-6)


def expected_quality(recording, records, basis, slot, profile):
    """Reconstruct each channel's sensor lineage from its own scalar and group stamps."""
    seen, latest, result = {}, None, []
    for i, record in enumerate(records):
        value = record[basis][slot]
        usable = value is not None and observed_valid(recording, record, basis, GROUP[slot], profile["max_source_age_s"])
        raw_stamp = record[basis+"_quality"][GROUP[slot]]["source_time"]
        stamp = None if raw_stamp in (None, "-1") else Decimal(raw_stamp)
        if stamp is not None and latest is not None and stamp < latest:
            usable = False
        first = seen.get(stamp, i) if stamp is not None else i
        held = stamp in seen if stamp is not None else False
        if usable and stamp is not None:
            seen.setdefault(stamp, i)
            latest = stamp
        result.append({"valid": usable, "source_indices": (first,), "held": held})
    return result


def compare_recording(recording, data, mapping):
    profile = mapping["b041"]
    basis = profile["basis"]
    selected = [r for r in recording["samples"] if r[basis] is not None]
    scale = Decimal("1e-9") if recording["source"]["time_unit"] == "ns" else Decimal(1)
    origin = Decimal(selected[0]["time"])
    times = [float((Decimal(r["time"])-origin)*scale) for r in selected]
    require(data.sample_times_s == times, "Exact relative timestamp subtraction differs")
    require(Decimal(data.metadata["source_origin_exact_s"]) == origin*scale, "Exact absolute timestamp origin differs")
    require(data.metadata["b041"]["samples"] == recording["samples"], "Original sample provenance was changed")
    require(len(data.metadata["stats"]["joints_missing"]) == 28-len(data.metadata["stats"]["joints_mapped"]), "Canonical counts differ")
    require(data.metadata["source_rows"]["timestamp"] == [r["row"] for r in selected], "Original row numbers differ")
    stats, expected_channels = {}, {}
    finite_invalid = 0

    def check_channel(name, records, role, slot, values, derived=False):
        nonlocal finite_invalid
        series = data.channels[name]
        expected = expected_quality(recording, records, role, slot, profile)
        expected_times = [float((Decimal(r["time"])-origin)*scale) for r in records]
        require(series.timestamps_s == expected_times, f"{name}: source times differ")
        require(len(series.values) == len(values), f"{name}: sample count differs")
        require(data.metadata["source_rows"][name] == [r["row"] for r in records], f"{name}: source rows differ")
        for i, (value, q, want) in enumerate(zip(series.values, series.quality, expected)):
            close(value, values[i], f"{name}[{i}]")
            require(q.valid == want["valid"], f"{name}[{i}]: validity changed")
            require(q.source_indices == want["source_indices"], f"{name}[{i}]: sensor source index differs")
            require(q.origin == ("derived" if derived else "measured" if role == "state" else "commanded"), f"{name}[{i}]: origin upgraded")
            if derived:
                require(q.method == f"b041.{profile['status']}.ee2_affine.v1" and "derived" in q.processing,
                        f"{name}[{i}]: missing affine derivation")
            if want["held"]:
                require("forward_filled" in q.processing and "original" not in q.processing,
                        f"{name}[{i}]: repeated sensor stamp mislabelled as fresh")
        valid_values = [v for v in values if v is not None]
        stats[name] = {"samples": len(values), "min": min(valid_values) if valid_values else None,
                       "max": max(valid_values) if valid_values else None,
                       "invalid": sum(not q["valid"] for q in expected),
                       "causal_holds": sum(q["held"] for q in expected)}
        expected_channels[name] = (values, expected)
        if name.startswith("raw."):
            finite_invalid += sum(v is not None and not q["valid"] for v, q in zip(values, expected))

    for name, slot in ARM_TORSO.items():
        check_channel(name, selected, basis, slot, [r[basis][slot] for r in selected])
    for side, slot in (("left", 14), ("right", 15)):
        config = profile[side+"_gripper"]
        for finger in (1, 2):
            values = [None if r[basis][slot] is None else config[f"zero{finger}_m"] +
                      (r[basis][slot]-config["native_closed"])*config[f"travel{finger}_m"]/
                      (config["native_open"]-config["native_closed"]) for r in selected]
            check_channel(f"{side}_gripper_finger_joint{finger}", selected, basis, slot, values, True)
    for role in ("state", "action"):
        within = [r for r in recording["samples"] if r[role] is not None and
                  origin <= Decimal(r["time"]) <= Decimal(selected[-1]["time"])]
        for slot, name in enumerate(RAW_NAMES):
            if within:
                check_channel(f"raw.{role}.{name}", within, role, slot, [r[role][slot] for r in within])
    for name in CANONICAL_JOINTS[:6]:
        require(all(not q.valid and q.origin == "missing" for q in data.channels[name].quality), f"{name}: invented chassis joint")

    trajectory = {"available": False, "valid_samples": 0, "invalid_latched_samples": 0}
    pose_expected, pose_valid = None, None
    should_integrate = (recording["source"]["format"] == "full23_parquet" and profile["chassis"]["mode"] != "unavailable"
                        and recording["source"]["base_locked"] is not True)
    if should_integrate:
        velocities = [r["state"][16:19] if r["state"] is not None else [None]*3 for r in selected]
        observed = [r["state"] is not None and all(v is not None for v in velocity) and
                    observed_valid(recording, r, "state", 4, profile["max_source_age_s"])
                    for r, velocity in zip(selected, velocities)]
        pose_expected, pose_valid = oracle_pose(times, velocities, observed, profile["chassis"])
        for j, axis in enumerate(("x", "y", "yaw")):
            series = data.channels["chassis."+axis]
            for i, pose in enumerate(pose_expected):
                close(series.values[i], pose[j], f"chassis.{axis}[{i}]", 1e-10)
                require(series.quality[i].valid == pose_valid[i], f"chassis.{axis}[{i}]: validity latch differs")
                require(series.quality[i].origin == "derived", "Integrated trajectory marked as measured")
                require(series.quality[i].source_indices == (i,), "Trajectory endpoint provenance differs")
        trajectory.update(available=True, valid_samples=sum(pose_valid), invalid_latched_samples=len(pose_valid)-sum(pose_valid),
                          final_pose=pose_expected[-1], method="independent complex midpoint-sinc body twist")
    else:
        require(not any("chassis."+axis in data.channels for axis in ("x", "y", "yaw")), "Unavailable chassis has numeric world pose")

    sampler = ReplaySampler(data, session_id="b041-validation")
    checkpoints = []
    selected_indices = sorted({0, len(selected)//2, len(selected)-1})
    saved = {}
    for index in selected_indices:
        frame = sampler.sample_at(times[index])
        require(frame.source_sample_index == index, "Seek does not retain normalized original source index")
        require(frame.log_time_s == times[index], "Sample clock differs")
        checks = {}
        for name in list(ARM_TORSO) + [f"{s}_gripper_finger_joint{f}" for s in ("left", "right") for f in (1, 2)]:
            joint = CANONICAL_JOINTS.index(name)
            values, quality = expected_channels[name]
            previous = next((i for i in range(index, -1, -1) if quality[i]["valid"]), None)
            value = values[index] if quality[index]["valid"] else values[previous] if previous is not None else 0.
            close(frame.joints[joint], value, f"sampled {name}[{index}]")
            require(frame.quality[joint].valid == quality[index]["valid"], f"sampled {name}: invalid promoted")
            checks[name] = {"mapped": values[index], "quality": data.channels[name].quality[index].model_dump(mode="json"),
                            "sampled": frame.joints[joint], "sample_quality": frame.quality[joint].model_dump(mode="json")}
        if should_integrate and pose_valid[index]:
            require(frame.chassis is not None, "Valid integrated pose absent")
            for got, want in zip((frame.chassis.x, frame.chassis.y, frame.chassis.yaw), pose_expected[index]):
                close(got, want, "sampled chassis", 1e-10)
        else:
            require(frame.chassis is None, "Invalid/unavailable chassis displayed as valid")
        saved[index] = frame.model_dump(mode="json")
        checkpoints.append({"sample_index": index, "row": selected[index]["row"], "frame_index": selected[index]["frame_index"],
                            "source_time": selected[index]["time"], "time_s": times[index], "raw": selected[index][basis],
                            "raw_quality": selected[index][basis+"_quality"], "channels": checks,
                            "chassis": frame.chassis.model_dump(mode="json") if frame.chassis else None})
    for index in reversed(selected_indices):
        require(sampler.sample_at(times[index]).model_dump(mode="json") == saved[index], "Reversed seek changes pose or source provenance")

    analysis = ReplayAnalysis(data)
    gap_bound = profile["max_interpolation_gap_s"]
    require(analysis.history.max_gap_s == gap_bound, "Analysis does not share profile gap bound")
    gaps = []
    for i in range(1, len(times)):
        if times[i]-times[i-1] <= gap_bound:
            continue
        midpoint = (times[i]+times[i-1])/2
        frame = sampler.sample_at(midpoint)
        points = analysis.curve(["left_arm_joint1"], from_s=times[i-1], to_s=times[i], max_points=100).series[0].points
        broken = any(p.value is None and not p.quality.valid and p.quality.method == "analysis.max_gap.v1" for p in points)
        require(not frame.quality[10].valid and broken, "Sampler or analysis bridges an unsupported data gap")
        gaps.append({"from_index": i-1, "to_index": i, "gap_s": times[i]-times[i-1],
                     "sampler_valid": frame.quality[10].valid, "analysis_break": broken})

    annotations = LogAnnotations.model_validate({"log_id": data.log_id, **data.metadata["log_annotations"]})
    require(annotations.alignment == recording["annotations"]["alignment"], "Annotation alignment changed")
    require(len(annotations.segments) == len(recording["annotations"]["segments"]), "Annotation count changed")
    for segment, original in zip(annotations.segments, recording["annotations"]["segments"]):
        require(segment.start_frame == original["start_frame"] and segment.end_frame == original["end_frame"], "Half-open frame bounds changed")
        close(segment.start_s, float((Decimal(recording["samples"][segment.start_frame]["time"])-origin)*scale), "Annotation start")
        if segment.end_frame < len(recording["samples"]):
            edge = Decimal(recording["samples"][segment.end_frame]["time"])
        elif "end_log_ns" in original:
            edge = Decimal(original["end_log_ns"])
        else:
            edge = 2*Decimal(recording["samples"][-1]["time"])-Decimal(recording["samples"][-2]["time"])
        close(segment.end_s, float((edge-origin)*scale), "Annotation end")
        require(segment.review_status == (original.get("review_status") or "unknown"), "Annotation review status changed")
        require(segment.boundary_status == (original.get("boundary_status") or "unknown"), "Boundary review status changed")
        require(segment.training_approved == original.get("training_approved"), "Training approval inferred")
    require(len(annotations.events) == len(recording["events"]), "Original event trace lost")
    return {"status": "passed", "source_id": recording["source"]["id"], "basis": basis,
            "state_or_action_samples": len(selected), "channels": len(data.channels),
            "canonical_mapped": len(data.metadata["stats"]["joints_mapped"]),
            "duration_s": times[-1], "source_origin_exact_s": data.metadata["source_origin_exact_s"],
            "first_source_time": selected[0]["time"], "last_source_time": selected[-1]["time"],
            "maximum_sample_gap_s": max((b-a for a, b in zip(times, times[1:])), default=0),
            "raw_finite_invalid_points": finite_invalid, "source_ranges": stats,
            "gap_count": len(gaps), "gap_checks": gaps, "trajectory": trajectory,
            "checkpoints": checkpoints, "annotations": annotations.model_dump(mode="json"),
            "checks": ["all canonical and raw values", "all sample validity and source indices", "exact clocks",
                       "independent complex trajectory and validity latch", "reversed seek", "sampler/analysis gaps", "annotation status"]}


async def import_and_check(bundle_path, recording, mapping, destination):
    new_directory(destination)
    shutil.copyfile(bundle_path, destination/"recording.b041")
    write_json(destination/"mapping.json", mapping)
    store = LogStore(destination/"normalized")
    async def chunks():
        with bundle_path.open("rb") as stream:
            while chunk := stream.read(65536):
                yield chunk
    result = await store.import_file("b041-local-validation", chunks(), bundle_path.name, mapping_config=mapping)
    data = await store.get("b041-local-validation", result.log_id)
    require(data.raw_file_sha256 == file_hash(bundle_path), "Bundle upload hash differs")
    report = compare_recording(recording, data, mapping)
    write_json(destination/"comparison.json", report)
    artifacts = {p.relative_to(destination).as_posix(): file_hash(p) for p in destination.rglob("*") if p.is_file()}
    manifest = {"schema": "b041.derived-validation.v1", "status": "passed", "source_id": recording["source"]["id"],
                "profile_id": mapping["b041"]["profile_id"], "basis": mapping["b041"]["basis"],
                "bundle_sha256": file_hash(bundle_path), "original_source_sha256": recording["source"]["sha256"],
                "original_source_files": recording["source"]["files"], "mapping_snapshot_id": data.mapping_snapshot_id,
                "log_id": data.log_id, "normalized_archive": str(Path("normalized")/Path(data.raw_file_ref).parent),
                "files": artifacts, "source_kind": "selected numeric bundle; original source remains separate"}
    write_json(destination/"manifest.json", manifest)
    return {"destination": str(destination), "log_id": data.log_id,
            **{k: report[k] for k in ("status", "source_id", "basis", "state_or_action_samples", "channels", "canonical_mapped",
                                     "duration_s", "raw_finite_invalid_points", "gap_count", "trajectory")}}


async def validate(args):
    raw_root = ROOT/"data/real_logs/raw"
    catalog = ROOT/"data/real_logs/catalog.json"
    sources = sorted(p for p in raw_root.rglob("*") if p.is_file()) + [catalog]
    before = {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sources}
    mapping = json.loads(args.profile.read_text())
    require(mapping["b041"]["basis"] == "state", "Materialization profile must select state")
    profile_id = safe_component(mapping["b041"]["profile_id"])
    bundles = [(p, json.loads(p.read_text())) for p in sorted(args.bundles_root.glob("*/recording.b041"))]
    require(bundles, "No prepared recording bundles")
    targets = []
    ids = set()
    for p, recording in bundles:
        source_id = safe_component(recording["source"]["id"])
        require(source_id not in ids, "Duplicate recording id")
        ids.add(source_id)
        require(recording["source"]["sha256"] in before.values(), "Original source hash not present in protected local sources")
        target = args.derived_root/source_id/profile_id
        require(not target.exists(), f"Derived destination already exists: {target}")
        targets.append(target)
    output = args.output_dir.resolve()
    for path in [output, *[t.resolve() for t in targets]]:
        require(not path.is_relative_to(raw_root.resolve()), "Cannot write under protected originals")
        require(not path.is_relative_to(args.bundles_root.resolve()), "Cannot write into prepared source tree")
    new_directory(output)
    reports, errors = [], []
    try:
        for (path, recording), target in zip(bundles, targets):
            result = await import_and_check(path, recording, mapping, target)
            reports.append(result)
            print(json.dumps(result), flush=True)
        offline = [(p, d) for p, d in bundles if d["source"]["format"] == "full23_parquet"]
        require(offline, "Need one offline recording for the action-basis check")
        path, recording = max(offline, key=lambda item: Decimal(item[1]["samples"][0]["time"]))
        require(any(not q["valid"] for r in recording["samples"] for q in (r["action_quality"] or [])[:2]),
                "Selected action probe lacks invalid arm groups")
        action_mapping = copy.deepcopy(mapping)
        action_mapping["b041"]["basis"] = "action"
        result = await import_and_check(path, recording, action_mapping, output/"action-basis"/safe_component(recording["source"]["id"]))
        reports.append(result)
        print(json.dumps(result), flush=True)
    except Exception as exc:
        errors.append({"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        after = {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sources}
        unchanged = before == after
        write_json(output/"evidence.json", {"schema": "b041.source-validation.v1", "status": "passed" if not errors and unchanged else "failed",
                   "oracle": "independent full23 index/affine/complex SE2 arithmetic; actual isolated LogStore import",
                   "reports": reports, "errors": errors, "raw_and_catalog_file_count": len(before),
                   "source_hashes_before": before, "source_hashes_after": after, "originals_unchanged": unchanged})
        require(unchanged, "Protected original source files changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles-root", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--derived-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(validate(args))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
