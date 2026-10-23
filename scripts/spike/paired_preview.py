#!/usr/bin/env python3
"""P0 experiment: localhost HTTP/JPEG transport, not the production NVENC path.

Producer: publish_frame(Path(output_dir), metadata, jpeg_bytes).
Viewer: python3 scripts/spike/paired_preview.py --output-dir outputs/p0 --port 8765
The producer polls control.json and deduplicates its monotonic request_id.
The mailbox deliberately retains only the latest command; HTTP acceptance is
not a simulation acknowledgement. All rendered state comes back in a frame.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from urllib.parse import urlsplit


MAX_METADATA_BYTES = 1024 * 1024
MAX_CONTROL_BYTES = 4096
LOG_DURATION_S = 12.0


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _validate_metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    for key in ("epoch", "render_seq", "capture_seq"):
        value = metadata.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    for key in ("log_time_s", "captured_wall_s", "speed"):
        _finite_number(metadata.get(key), key)
    joints, names = metadata.get("joints"), metadata.get("joint_names")
    if not isinstance(joints, list) or len(joints) != 28:
        raise ValueError("joints must contain exactly 28 values")
    for value in joints:
        _finite_number(value, "joint")
    if not isinstance(names, list) or len(names) != 28 or not all(isinstance(name, str) and name for name in names):
        raise ValueError("joint_names must contain exactly 28 nonempty strings")
    chassis = metadata.get("chassis")
    if not isinstance(chassis, dict):
        raise ValueError("chassis must be an object")
    for key in ("x", "y", "yaw"):
        _finite_number(chassis.get(key), f"chassis.{key}")
    if not isinstance(metadata.get("state"), str) or not metadata["state"]:
        raise ValueError("state must be a nonempty string")


def _reject_constant(value):
    raise ValueError(f"nonfinite JSON value: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_json(raw):
    return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def encode_frame(metadata: dict, jpeg: bytes) -> bytes:
    """Encode uint32 big-endian JSON byte count + UTF-8 JSON + exact JPEG."""
    _validate_metadata(metadata)
    if not isinstance(jpeg, bytes) or not jpeg:
        raise ValueError("jpeg must be nonempty bytes")
    raw_metadata = json.dumps(metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw_metadata) > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds 1 MiB")
    return struct.pack(">I", len(raw_metadata)) + raw_metadata + jpeg


def decode_frame(packet: bytes):
    """Decode and validate a complete packet; useful for producer-side checks."""
    if len(packet) < 5:
        raise ValueError("truncated frame header")
    length = struct.unpack(">I", packet[:4])[0]
    if length == 0 or length > MAX_METADATA_BYTES or 4 + length >= len(packet):
        raise ValueError("invalid JSON length or missing JPEG")
    try:
        metadata = _load_json(packet[4:4 + length].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid frame JSON") from error
    _validate_metadata(metadata)
    return metadata, packet[4 + length:]


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def publish_frame(output_dir: Path, metadata: dict, jpeg: bytes):
    """Atomically replace latest.frame; readers always get a complete pair."""
    payload = encode_frame(metadata, jpeg)
    _atomic_write(Path(output_dir) / "latest.frame", payload)


def validate_control(payload):
    """Reject unknown fields/types; clamp seek to [0,12] and speed to [0.1,10]."""
    if not isinstance(payload, dict):
        raise ValueError("control must be an object")
    action = payload.get("action")
    fields = {"play": set(), "pause": set(), "stop": set(),
              "seek": {"time_s"}, "step": {"direction"}, "speed": {"speed"}}
    if not isinstance(action, str) or action not in fields:
        raise ValueError("action must be play, pause, stop, seek, step, or speed")
    if set(payload) != {"action"} | fields[action]:
        raise ValueError(f"{action} requires exactly these fields: {sorted({'action'} | fields[action])}")
    control = {"action": action}
    if action == "seek":
        control["time_s"] = min(LOG_DURATION_S, max(0.0, _finite_number(payload["time_s"], "time_s")))
    elif action == "speed":
        control["speed"] = min(10.0, max(0.1, _finite_number(payload["speed"], "speed")))
    elif action == "step":
        if type(payload["direction"]) is not int or payload["direction"] not in (-1, 1):
            raise ValueError("direction must be integer -1 or 1")
        control["direction"] = payload["direction"]
    return control


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, output_dir, port):
        self.output_dir = Path(output_dir)
        self.control_lock = threading.Lock()
        self.last_request_id = 0
        super().__init__(("127.0.0.1", port), PreviewHandler)

    def publish_control(self, payload):
        control = validate_control(payload)
        path = self.output_dir / "control.json"
        with self.control_lock:
            # Reading the previous mailbox preserves monotonicity across restarts.
            try:
                previous = _load_json(path.read_bytes()).get("request_id", 0)
                if type(previous) is int:
                    self.last_request_id = max(self.last_request_id, previous)
            except (OSError, ValueError, AttributeError):
                pass
            self.last_request_id = max(self.last_request_id + 1, time.monotonic_ns())
            control["request_id"] = self.last_request_id
            _atomic_write(path, json.dumps(control, allow_nan=False).encode("utf-8"))
        return control


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        pass  # Frame polling should not flood the simulation terminal.

    def _respond(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._respond(status, json.dumps(payload, allow_nan=False).encode("utf-8"))

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self._respond(200, Path(__file__).with_suffix(".html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/frame":
            try:
                frame = (self.server.output_dir / "latest.frame").read_bytes()
            except FileNotFoundError:
                self._json(503, {"error": "Waiting for the producer's first complete frame"})
                return
            self._respond(200, frame, "application/octet-stream")
        elif path == "/status":
            self._json(200, {"ok": True, "mode": "P0 experimental HTTP/JPEG, not NVENC",
                             "frame_available": (self.server.output_dir / "latest.frame").is_file(),
                             "duration_s": LOG_DURATION_S, "speed_range": [0.1, 10.0]})
        else:
            self._json(404, {"error": "Unknown endpoint"})

    def do_POST(self):
        if urlsplit(self.path).path != "/control":
            self._json(404, {"error": "Unknown endpoint"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("A JSON request body is required")
            if length > MAX_CONTROL_BYTES:
                self._json(413, {"error": "Control body exceeds 4096 bytes"})
                return
            payload = _load_json(self.rfile.read(length).decode("utf-8"))
            control = self.server.publish_control(payload)
        except (ValueError, UnicodeError) as error:
            self._json(400, {"error": str(error)})
            return
        self._json(200, {"ok": True, "control": control, "acknowledged_by_simulation": False})


def make_server(output_dir: Path, port: int = 8765):
    """Create the loopback-only server. Caller owns serve_forever/shutdown."""
    return PreviewServer(output_dir, port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/p0"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.output_dir, args.port)
    print(f"P0 experimental HTTP/JPEG viewer: http://127.0.0.1:{server.server_port}/", flush=True)
    print(f"Producer directory: {args.output_dir.resolve()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
