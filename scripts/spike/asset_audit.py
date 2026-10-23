"""Audit the local R1Pro 2026 asset using only the Python standard library.

This checks bytes, names and textual USD asset paths. Binary USD composition
must additionally be checked by the Isaac Sim 5.1 runtime.
"""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


CANONICAL_JOINTS: tuple[str, ...] = (
    "steer_motor_joint1", "wheel_motor_joint1",
    "steer_motor_joint2", "wheel_motor_joint2",
    "steer_motor_joint3", "wheel_motor_joint3",
    "torso_joint1", "torso_joint2", "torso_joint3", "torso_joint4",
    "left_arm_joint1", "left_arm_joint2", "left_arm_joint3", "left_arm_joint4",
    "left_arm_joint5", "left_arm_joint6", "left_arm_joint7",
    "left_gripper_finger_joint1", "left_gripper_finger_joint2",
    "right_arm_joint1", "right_arm_joint2", "right_arm_joint3", "right_arm_joint4",
    "right_arm_joint5", "right_arm_joint6", "right_arm_joint7",
    "right_gripper_finger_joint1", "right_gripper_finger_joint2",
)
CONTINUOUS_JOINTS = ("wheel_motor_joint1", "wheel_motor_joint2", "wheel_motor_joint3")
PRISMATIC_JOINTS = (
    "left_gripper_finger_joint1", "left_gripper_finger_joint2",
    "right_gripper_finger_joint1", "right_gripper_finger_joint2",
)
EXPECTED_TYPES = {
    name: "continuous" if name in CONTINUOUS_JOINTS else
    "prismatic" if name in PRISMATIC_JOINTS else "revolute"
    for name in CANONICAL_JOINTS
}
EXPECTED_COUNTS = {"revolute": 21, "continuous": 3, "prismatic": 4}
COMMON_CONFIG = "reproduction/configs/r1pro_2026_common.json"
ENTRYPOINT = "r1pro_2026.usd"
MANIFEST = "CHECKSUMS.sha256"


def _local_file(root: Path, path: Path) -> Path:
    """Reject symlinks before opening a path, including symlinked parents."""
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink: {relative.as_posix()}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"path escapes asset directory: {path}")
    if not path.is_file():
        raise ValueError(f"missing file: {relative.as_posix()}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _audit_checksums(root, files, errors):
    summary = {"manifest": MANIFEST, "entries": 0, "verified": 0}
    entries = set()
    try:
        lines = _local_file(root, root / MANIFEST).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        errors.append(f"checksum manifest: {exc}")
        return summary
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match:
            errors.append(f"invalid checksum line: {number}")
            continue
        digest, name = match.groups()
        relative = PurePosixPath(name)
        if (relative.is_absolute() or PureWindowsPath(name).drive or "\\" in name
                or ".." in relative.parts or name == MANIFEST):
            errors.append(f"unsafe manifest path: {name}")
            continue
        name = relative.as_posix()
        if name in entries:
            errors.append(f"duplicate checksum path: {name}")
            continue
        entries.add(name)
        try:
            path = _local_file(root, root / relative)
            if _sha256(path) != digest.lower():
                errors.append(f"checksum mismatch: {name}")
            else:
                summary["verified"] += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    summary["entries"] = len(entries)
    if not entries:
        errors.append("empty checksum manifest")
    for name in sorted(set(files) - entries - {MANIFEST}):
        errors.append(f"unlisted file: {name}")
    return summary


def _audit_joints(root, errors):
    try:
        config = json.loads(_local_file(root, root / COMMON_CONFIG).read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_object)
        if not isinstance(config, dict) or not isinstance(config.get("joints"), dict):
            raise ValueError("joints must be an object")
        joints = config["joints"]
        if any(not isinstance(value, dict) or not isinstance(value.get("type"), str)
               for value in joints.values()):
            raise ValueError("each joint must contain a string type")
    except (OSError, ValueError) as exc:
        errors.append(f"common config: {exc}")
        return {}
    if len(joints) != 28:
        errors.append(f"expected 28 joints, found {len(joints)}")
    missing = sorted(set(CANONICAL_JOINTS) - joints.keys())
    extra = sorted(joints.keys() - set(CANONICAL_JOINTS))
    if missing or extra:
        errors.append(f"joint names differ: missing={missing}, extra={extra}")
    counts = dict(Counter(joint["type"] for joint in joints.values()))
    if counts != EXPECTED_COUNTS:
        errors.append(f"joint type counts differ: {counts}")
    # The lookup is deliberately by name, never by JSON dictionary position.
    types = {name: joints[name]["type"] for name in CANONICAL_JOINTS if name in joints}
    for name, joint_type in types.items():
        if joint_type != EXPECTED_TYPES[name]:
            errors.append(f"joint type mismatch: {name}: {joint_type}")
    return {"count": len(joints), "type_counts": counts,
            "canonical_names": list(CANONICAL_JOINTS), "types_by_name": types}


def _audit_text_dependencies(root, files, errors):
    summary = {"method": "text_asset_paths_only", "text_layers_checked": 0,
               "binary_layers_not_parsed": [], "references_checked": 0,
               "usd_closure_verified": False}
    for name in sorted(files):
        path = root / name
        if path.suffix.lower() not in {".usd", ".usda", ".usdc"}:
            continue
        try:
            with _local_file(root, path).open("rb") as stream:
                header = stream.read(8)
                if header == b"PXR-USDC":
                    summary["binary_layers_not_parsed"].append(name)
                    continue
                contents = (header + stream.read()).decode("utf-8")
            summary["text_layers_checked"] += 1
            for reference in re.findall(r"@([^@\r\n]+)@", contents):
                summary["references_checked"] += 1
                target = path.parent / reference
                if (Path(reference).is_absolute() or PureWindowsPath(reference).drive
                        or "\\" in reference or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", reference)
                        or not Path(os.path.abspath(target)).is_relative_to(root)):
                    errors.append(f"external asset reference: {name}: {reference}")
                else:
                    try:
                        _local_file(root, target)
                    except (OSError, ValueError) as exc:
                        errors.append(f"missing asset reference: {name}: {reference}: {exc}")
        except (OSError, ValueError) as exc:
            errors.append(f"USD text check: {name}: {exc}")
    return summary


def audit_asset(asset_dir) -> dict:
    """Return a compact audit report; ``passed`` covers static checks only."""
    supplied = Path(asset_dir).absolute()
    errors = []
    result = {"asset_dir": str(supplied), "entrypoint": ENTRYPOINT, "passed": False,
              "errors": errors, "checksums": {}, "joints": {}, "dependencies": {}}
    if supplied.is_symlink() or not supplied.is_dir():
        errors.append("asset directory must be an existing directory, not a symlink")
        return result
    root = supplied.resolve()
    result["asset_dir"] = str(root)
    files = []
    for directory, directories, filenames in os.walk(root, followlinks=False):
        for name in list(directories) + filenames:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                errors.append(f"symlink: {relative}")
                if name in directories:
                    directories.remove(name)
            elif name in filenames:
                files.append(relative)
    result["file_count"] = len(files)
    result["checksums"] = _audit_checksums(root, files, errors)
    try:
        _local_file(root, root / ENTRYPOINT)
    except (OSError, ValueError) as exc:
        errors.append(f"missing entrypoint: {exc}")
    result["joints"] = _audit_joints(root, errors)
    result["dependencies"] = _audit_text_dependencies(root, files, errors)
    result["passed"] = not errors
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path,
                        default=Path(__file__).resolve().parents[2] / "assets/robots/r1pro_2026/isaacsim_5_1")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    args = parser.parse_args()
    result = audit_asset(args.asset_dir)
    report = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
