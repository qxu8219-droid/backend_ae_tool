"""Behavior tests for the disposable P0 paired-frame HTTP viewer."""

import copy
import base64
import http.client
import importlib.util
import json
from pathlib import Path
import struct
import shutil
import subprocess
import tempfile
import threading
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/spike/paired_preview.py"
if MODULE_PATH.exists():
    SPEC = importlib.util.spec_from_file_location("paired_preview", MODULE_PATH)
    PREVIEW = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(PREVIEW)
else:
    PREVIEW = None


def metadata(seq=1):
    return {
        "epoch": 2,
        "render_seq": seq,
        "capture_seq": seq,
        "log_time_s": 3.5,
        "joints": [index / 100 for index in range(28)],
        "joint_names": [f"关节_{index:02d}" for index in range(28)],
        "chassis": {"x": 1.0, "y": -2.0, "yaw": 0.75},
        "captured_wall_s": 1234567.25,
        "state": "paused",
        "speed": 1.0,
    }


JPEG = b"\xff\xd8test-jpeg-payload\xff\xd9"


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PREVIEW, "paired_preview.py is not implemented yet")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)

    def test_wire_format_uses_big_endian_utf8_byte_length_and_exact_jpeg(self):
        frame = PREVIEW.encode_frame(metadata(), JPEG)
        length = struct.unpack(">I", frame[:4])[0]
        raw_metadata = frame[4:4 + length]
        self.assertEqual(json.loads(raw_metadata.decode("utf-8")), metadata())
        self.assertEqual(frame[4 + length:], JPEG)
        self.assertGreater(length, len(raw_metadata.decode("utf-8")))

    def test_decoder_recovers_a_hand_built_packet(self):
        raw_metadata = json.dumps(metadata(), ensure_ascii=False).encode("utf-8")
        packet = len(raw_metadata).to_bytes(4, "big") + raw_metadata + JPEG
        actual_metadata, actual_jpeg = PREVIEW.decode_frame(packet)
        self.assertEqual(actual_metadata, metadata())
        self.assertEqual(actual_jpeg, JPEG)

    def test_decoder_rejects_truncation_non_object_and_invalid_json(self):
        for packet in (b"", b"\x00\x00\x00", b"\x00\x00\x00\x14{}", b"\x00\x00\x00\x02[]x", b"\x00\x00\x00\x02{!x"):
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                PREVIEW.decode_frame(packet)

    def test_invalid_metadata_cannot_replace_a_good_frame(self):
        PREVIEW.publish_frame(self.output, metadata(), JPEG)
        before = (self.output / "latest.frame").read_bytes()
        for key, value in (("joints", [0.0] * 27), ("joint_names", ["a"] * 29),
                           ("epoch", True), ("render_seq", -1), ("log_time_s", float("nan")),
                           ("chassis", {"x": 0, "y": float("inf"), "yaw": 0})):
            bad = copy.deepcopy(metadata())
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                PREVIEW.publish_frame(self.output, bad, JPEG)
            self.assertEqual((self.output / "latest.frame").read_bytes(), before)

    def test_readers_observe_only_complete_old_or_new_frames(self):
        PREVIEW.publish_frame(self.output, metadata(0), bytes([0]) * 131072)
        done, started = threading.Event(), threading.Event()
        observations, errors = [], []

        def read_frames():
            while not done.is_set():
                try:
                    raw = (self.output / "latest.frame").read_bytes()
                    length = int.from_bytes(raw[:4], "big")
                    meta = json.loads(raw[4:4 + length])
                    image = raw[4 + length:]
                    if image != bytes([meta["render_seq"]]) * 131072:
                        errors.append("metadata and JPEG were partially published")
                    observations.append(meta["render_seq"])
                except Exception as error:
                    errors.append(repr(error))
                started.set()

        reader = threading.Thread(target=read_frames)
        reader.start()
        self.assertTrue(started.wait(2))
        try:
            for seq in range(1, 41):
                PREVIEW.publish_frame(self.output, metadata(seq), bytes([seq]) * 131072)
        finally:
            done.set()
            reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(observations)
        self.assertEqual(errors, [])
        self.assertEqual([path.name for path in self.output.iterdir()], ["latest.frame"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PREVIEW, "paired_preview.py is not implemented yet")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.server = PREVIEW.make_server(self.output, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.worker.join(2)
        self.server.server_close()

    def request(self, method, path, payload=None, raw=None, content_type="application/json"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        body = raw if raw is not None else (json.dumps(payload) if payload is not None else None)
        try:
            connection.request(method, path, body=body, headers={"Content-Type": content_type})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_server_binds_loopback_and_reports_waiting_without_a_frame(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, _, body = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["frame_available"])
        self.assertEqual(self.request("GET", "/frame")[0], 503)

    def test_frame_http_response_is_exact_atomic_packet_with_no_cache(self):
        PREVIEW.publish_frame(self.output, metadata(), JPEG)
        status, headers, body = self.request("GET", "/frame")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(body, (self.output / "latest.frame").read_bytes())
        self.assertTrue(json.loads(self.request("GET", "/status")[2])["frame_available"])

    def test_page_is_served_and_unknown_routes_are_not_filesystem_paths(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertTrue(body.startswith(b"<!doctype html>"))
        self.assertEqual(self.request("GET", "/../control.json")[0], 404)

    def test_controls_clamp_documented_ranges_and_publish_increasing_request_ids(self):
        last_id = -1
        cases = [({"action": "play"}, {"action": "play"}),
                 ({"action": "pause"}, {"action": "pause"}),
                 ({"action": "stop"}, {"action": "stop"}),
                 ({"action": "seek", "time_s": -3}, {"action": "seek", "time_s": 0.0}),
                 ({"action": "seek", "time_s": 15}, {"action": "seek", "time_s": 12.0}),
                 ({"action": "step", "direction": -1}, {"action": "step", "direction": -1}),
                 ({"action": "step", "direction": 1}, {"action": "step", "direction": 1}),
                 ({"action": "speed", "speed": 0.01}, {"action": "speed", "speed": 0.1}),
                 ({"action": "speed", "speed": 20}, {"action": "speed", "speed": 10.0})]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                status, _, body = self.request("POST", "/control", payload)
                self.assertEqual(status, 200, body)
                published = json.loads((self.output / "control.json").read_text())
                self.assertEqual(json.loads(body)["control"], published)
                request_id = published.pop("request_id")
                self.assertGreater(request_id, last_id)
                last_id = request_id
                self.assertEqual(published, expected)

    def test_control_rejects_invalid_values_without_modifying_mailbox(self):
        self.request("POST", "/control", {"action": "pause"})
        before = (self.output / "control.json").read_bytes()
        invalid = [None, [], {}, {"action": "delete"}, {"action": "play", "extra": 1},
                   {"action": "play", "time_s": 2}, {"action": "seek"},
                   {"action": "seek", "time_s": True}, {"action": "seek", "time_s": "2"},
                   {"action": "seek", "time_s": float("nan")},
                   {"action": "speed", "speed": float("inf")}, {"action": "speed"},
                   {"action": "step", "direction": True}, {"action": "step", "direction": 0},
                   {"action": "step", "direction": "forward"}, {"action": 1}]
        for payload in invalid:
            with self.subTest(payload=payload):
                status, _, _ = self.request("POST", "/control", raw=json.dumps(payload))
                self.assertEqual(status, 400)
                self.assertEqual((self.output / "control.json").read_bytes(), before)
        for raw in ('{"action":"pause","action":"play"}', "{broken"):
            self.assertEqual(self.request("POST", "/control", raw=raw)[0], 400)
        self.assertEqual(self.request("POST", "/control", {"action": "play"}, content_type="text/plain")[0], 415)
        self.assertEqual(self.request("POST", "/control", raw=" " * 4097)[0], 413)

    def test_control_ids_remain_monotonic_when_existing_mailbox_is_newer(self):
        (self.output / "control.json").write_text(json.dumps({"request_id": 2**53-3, "action": "pause"}))
        status, _, body = self.request("POST", "/control", {"action": "play"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["control"]["request_id"], 2**53-2)

    def test_control_id_stays_exact_after_long_uptime_and_rejects_exhaustion(self):
        from unittest.mock import patch
        with patch('time.monotonic_ns', return_value=2**53+1):
            status, _, body = self.request('POST', '/control', {'action':'pause'})
        self.assertEqual(status, 200)
        self.assertLessEqual(json.loads(body)['control']['request_id'], 2**53-1)
        path=self.output/'control.json'
        path.write_text(json.dumps({'request_id':2**53-1,'action':'pause'}))
        before=path.read_bytes()
        self.assertEqual(self.request('POST','/control',{'action':'play'})[0],400)
        self.assertEqual(path.read_bytes(),before)


@unittest.skipUnless(shutil.which("node"), "Node is optional; needed for page orchestration tests")
class PageOrchestrationTests(unittest.TestCase):
    """Run the real page script with browser-only boundaries replaced.

    This checks scheduling/marker rejection, not an actual browser JPEG decoder
    or physical display timing. The bitmap fixture represents decoded pixels.
    """

    def run_page(self, frames, query="", control_while_delayed=False):
        packets = []
        for meta, marker in frames:
            raw = json.dumps(meta).encode()
            packet = len(raw).to_bytes(4, "big") + raw + marker.to_bytes(2, "little")
            packets.append(base64.b64encode(packet).decode())
        harness = r"""
const fs = require('node:fs'), vm = require('node:vm');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(config.html, 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];
let visibleBlits=0, fetches=0, delayed=[];
function makeCanvas(visible=false) {
  let bitmap;
  const target = {
    drawImage(image) { bitmap=image; if(visible) visibleBlits++; },
    getImageData(x,y) {
      const bit=(bitmap.marker >>> (x-1)) & 1;
      return {data:new Uint8ClampedArray(bit ? [10,240,10,255] : [240,10,10,255])};
    }
  };
  const context=new Proxy(target,{get:(object,key)=>key in object?object[key]:(()=>{})});
  return {width:1480,height:890,getContext:()=>context};
}
const visible=makeCanvas(true);
const elements={paired:visible,transport:{textContent:''},seek:{value:0,addEventListener(){}}};
const button={dataset:{action:'pause'},addEventListener(event,callback){this.click=callback;}};
const sandbox={
  console,TextDecoder,Blob,URLSearchParams,structuredClone,Date,Uint8Array,DataView,
  window:{addEventListener(){}},location:{search:config.query},
  document:{getElementById:id=>elements[id],createElement:()=>makeCanvas(),querySelectorAll:()=>[button]},
  setTimeout:(callback,delay)=>delay===700?delayed.push(callback):setImmediate(callback),
  requestAnimationFrame:callback=>setImmediate(callback),
  createImageBitmap:async blob=>{
    const raw=Buffer.from(await blob.arrayBuffer());
    return {width:32,height:32,marker:raw.readUInt16LE(),close(){}};
  },
  fetch:async url=>{
    if(url==='/control') return {ok:true,json:async()=>({ok:true})};
    const encoded=config.packets[fetches++];
    if(!encoded) return new Promise(()=>{});
    const raw=Buffer.from(encoded,'base64');
    return {ok:true,status:200,arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)};
  }
};
vm.runInNewContext(code,sandbox);
(async()=>{
  for(let n=0;n<40;n++) await new Promise(setImmediate);
  const beforeDelay={visibleBlits,presented:sandbox.window.p0Stats.presented_frames,fetches};
  if(config.controlWhileDelayed) {
    button.click();
    for(let n=0;n<5;n++) await new Promise(setImmediate);
  }
  delayed.forEach(callback=>callback()); delayed=[];
  for(let n=0;n<40;n++) await new Promise(setImmediate);
  console.log(JSON.stringify({stats:sandbox.window.p0Stats,visibleBlits,beforeDelay}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            input=json.dumps({"html": str(MODULE_PATH.with_suffix(".html")), "packets": packets,
                              "query": query, "controlWhileDelayed": control_while_delayed}),
            text=True, capture_output=True, timeout=10, check=True,
        )
        return json.loads(result.stdout)

    def marked_metadata(self, seq=17):
        meta = metadata(seq)
        meta["marker_pixels"] = [[index + 1, 1] for index in range(12)]
        meta["marker_modulus"] = 4096
        return meta

    def test_marker_mismatch_is_rejected_without_changing_visible_canvas(self):
        wrong_capture = self.marked_metadata()
        wrong_capture["capture_seq"] = 18
        out_of_bounds = self.marked_metadata()
        out_of_bounds["marker_pixels"][0] = [32, 1]
        for meta, marker in ((self.marked_metadata(), 18), (wrong_capture, 17), (out_of_bounds, 17)):
            with self.subTest(meta=meta, marker=marker):
                result = self.run_page([(meta, marker)])
                self.assertEqual(result["stats"].get("marker_mismatches"), 1)
                self.assertEqual(result["stats"]["presented_frames"], 0)
                self.assertEqual(result["visibleBlits"], 0)

    def test_marker_rollover_and_high_bit_decode_from_pixels(self):
        for sequence, marker in ((4096, 0), (2048, 2048), (4113, 17)):
            meta = self.marked_metadata(sequence)
            meta["capture_seq"] = marker
            with self.subTest(sequence=sequence):
                result = self.run_page([(meta, marker)])
                self.assertEqual(result["stats"].get("marker_verified_frames"), 1)
                self.assertEqual(result["stats"]["presented_frames"], 1)
                self.assertEqual(result["stats"].get("last_marker_value"), marker)

    def test_marker_success_and_stale_epoch_preserve_latest_pair(self):
        good = self.marked_metadata()
        stale = self.marked_metadata(18)
        stale["epoch"] = 1
        result = self.run_page([(good, 17), (stale, 18)])
        self.assertEqual(result["stats"].get("marker_verified_frames"), 1)
        self.assertEqual(result["stats"]["presented_frames"], 1)
        self.assertEqual(result["stats"]["stale_dropped"], 1)
        self.assertEqual(result["stats"]["last_metadata"]["render_seq"], 17)
        self.assertEqual(result["visibleBlits"], 1)

    def test_one_side_delay_holds_the_entire_pair_and_only_one_frame_inflight(self):
        for query in ("?image_delay_ms=700", "?metadata_delay_ms=700"):
            with self.subTest(query=query):
                result = self.run_page([(self.marked_metadata(), 17)], query)
                self.assertEqual(result["beforeDelay"], {"visibleBlits": 0, "presented": 0, "fetches": 1})
                self.assertEqual(result["stats"]["presented_frames"], 1)
                self.assertEqual(result["visibleBlits"], 1)
                self.assertEqual(result["stats"]["max_inflight_frames"], 1)

    def test_control_invalidates_a_pending_decode_before_it_can_present(self):
        result = self.run_page([(self.marked_metadata(), 17)], "?image_delay_ms=700", control_while_delayed=True)
        self.assertEqual(result["stats"]["presented_frames"], 0)
        self.assertEqual(result["stats"]["stale_dropped"], 1)
        self.assertEqual(result["visibleBlits"], 0)


if __name__ == "__main__":
    unittest.main()
