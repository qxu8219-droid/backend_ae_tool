"""Portable producer tests; optional Parquet cases also run with stdlib unittest."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prepare_b041.py"
HAVE_ARROW = importlib.util.find_spec("pyarrow") is not None
NS = 1792116367247308928
_spec = importlib.util.spec_from_file_location("b041_prepare_under_test", SCRIPT)
prepare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = self.root / "raw" / "synthetic_run"
        self.run.mkdir(parents=True)
        self.output = self.root / "prepared"

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args),
                               "--output-dir", str(self.output)],
                              capture_output=True, text=True)

    def runtime(self, events, summary=None, selection=None):
        (self.run / "SUMMARY.json").write_text(json.dumps(summary or {"mode": "run"}))
        (self.run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        if selection is not None:
            (self.run / "selection.json").write_text(json.dumps(selection))

    def load(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.output / "recording.b041").read_text())

    def test_runtime_keeps_actual_samples_and_all_events_without_expanding_horizons(self):
        # Mistaking a planned/horizon/wait target for a publication changes these counts.
        state, target = [1.] * 23, [2.] * 23
        events = [
            {"event": "raw_response", "monotonic": 9.1, "capture_monotonic": 8.9,
             "request_state23": state, "response": {"action23": [target] * 32}},
            {"event": "rim_pose_plan", "monotonic": 9.2, "target23": target},
            {"event": "rim_pose_target", "monotonic": 9.3, "target23": target},
            {"event": "target", "monotonic": 9.4, "target23": target,
             "feedback23": state, "chunk": 0, "step": 2, "reference_step": 3},
            {"event": "convergence_wait_feedback", "monotonic": 9.5,
             "target23": target, "feedback23": state},
            {"event": "hold_test", "monotonic": 9.6, "start23": state,
             "worst_abs_drift23": target},
        ]
        self.runtime(events, {"effective_settings": {"base": "locked"}},
                     {"case": {"source_id": "old_source", "pose_frame": 345,
                               "source_log_time_ns": NS}})
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.iterdir()}
        bundle = self.load(self.call("runtime", self.run))
        self.assertEqual(bundle["schema"], "b041.recording.v1")
        self.assertEqual([s["row"] for s in bundle["samples"]], [3, 4, 5])
        self.assertEqual([s["time"] for s in bundle["samples"]], ["9.3", "9.4", "9.5"])
        self.assertEqual(sum(s["state"] is not None for s in bundle["samples"]), 2)
        self.assertEqual(sum(s["action"] is not None for s in bundle["samples"]), 2)
        self.assertEqual(bundle["samples"][1]["context"]["reference_step"], 3)
        quality = bundle["samples"][1]["state_quality"]
        self.assertEqual(len(quality), 6)
        self.assertTrue(all(q == {"valid": True, "source_time": None, "age_s": None,
                                 "timing": "event_monotonic_only"} for q in quality))
        self.assertEqual(len(bundle["events"]), 6)
        self.assertEqual(len(bundle["events"][0]["details"]["response"]["action23"]), 32)
        self.assertEqual(bundle["source"]["base_locked"], True)
        self.assertEqual(bundle["annotations"]["alignment"], "reference_only")
        self.assertEqual(bundle["annotations"]["segments"], [])
        self.assertEqual(bundle["annotations"]["reference"]["case"]["source_log_time_ns"], str(NS))
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.run.iterdir()})
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["bundle_sha256"], hashlib.sha256((self.output / "recording.b041").read_bytes()).hexdigest())
        self.assertTrue(all(not Path(f["name"]).is_absolute() for f in bundle["source"]["files"]))
        self.assertTrue(manifest["source_hashes_unchanged"])

    def test_runtime_nonfinite_and_unknown_lock_are_not_fabricated(self):
        vector = [1.] * 23
        vector[14] = float("nan")
        self.runtime([{"event": "target", "monotonic": 1., "feedback23": vector,
                       "target23": [0.] * 23}])
        bundle = self.load(self.call("runtime", self.run))
        self.assertIsNone(bundle["source"]["base_locked"])
        self.assertIsNone(bundle["samples"][0]["state"][14])
        self.assertFalse(bundle["samples"][0]["state_quality"][2]["valid"])
        self.assertNotIn("NaN", (self.output / "recording.b041").read_text())

    def test_home_hold_and_all_missing_data_are_rejected_without_output(self):
        for event in [{"event": "graph", "monotonic": 1.},
                      {"event": "hold_test", "monotonic": 1., "start23": [1.] * 23},
                      {"event": "target", "monotonic": 1., "feedback23": [None] * 23}]:
            with self.subTest(event=event["event"]):
                self.runtime([event])
                result = self.call("runtime", self.run)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("NO_USABLE_DATA", result.stderr)
                self.assertFalse(self.output.exists())

    def test_refuses_existing_destination_and_writes_under_raw(self):
        self.runtime([{"event": "target", "monotonic": 1., "feedback23": [1.] * 23}])
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("unchanged")
        result = self.call("runtime", self.run)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OUTPUT_EXISTS", result.stderr)
        self.assertEqual(marker.read_text(), "unchanged")
        self.output = self.run.parent / "derived"
        result = self.call("runtime", self.run)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RAW_OUTPUT_FORBIDDEN", result.stderr)
        self.assertFalse(self.output.exists())

    def test_runtime_invalid_vector_and_reversed_sample_clock_reject(self):
        for events, code in [
            ([{"event": "target", "monotonic": 1., "feedback23": [1.] * 22}], "INVALID_VECTOR"),
            ([{"event": "target", "monotonic": t, "feedback23": [1.] * 23}
              for t in [2., 1.]], "INVALID_TIME_ORDER"),
        ]:
            with self.subTest(code=code):
                self.runtime(events)
                result = self.call("runtime", self.run)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(code, result.stderr)

    def parquet_fixture(self, change=None):
        import pyarrow as pa
        import pyarrow.parquet as pq
        rows = []
        for frame in range(3):
            rows.append({"source_log_time_ns": NS + frame * 66666667,
                         "frame_index": frame, "episode_index": 8,
                         "observation.state": [float(frame)] * 23, "action": [2.] * 23,
                         "observation.state_valid": [1.] * 6, "action_valid": [1.] * 6,
                         "observation.state_source_log_ns": [NS - 10] * 6,
                         "action_source_log_ns": [NS - 20] * 6,
                         "observation.state_age": [.01] * 6, "action_age": [.02] * 6,
                         "observation.image_header_ns": [NS - 30] * 3})
        rows[0]["action_valid"][0] = 0.
        rows[0]["action_source_log_ns"][0] = -1
        rows[0]["action_age"][0] = -1.
        rows[1]["observation.state"][14] = float("nan")
        source = {"source_id": "synthetic_B041", "source_episode_index": 8,
                  "source_frame_count": 3, "source_fps": 15,
                  "training_approved": False, "segments": [
                      {"id": "first", "start_frame": 0, "end_frame": 2,
                       "start_log_ns": NS, "end_log_ns": NS + 133333334,
                       "skill": "pick_lift", "review_status": "human_reviewed",
                       "boundary_uncertainty": {"status": "manual_edit_pending_review"},
                       "training_approved": False},
                      {"id": "last", "start_frame": 2, "end_frame": 3,
                       "skill": "place", "review_status": "draft"}]}
        if change:
            change(rows, source)
        path = self.run / "episode.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        sidecar = self.run / "annotations.json"
        sidecar.write_text(json.dumps({"sources": [source]}))
        return path, sidecar

    def test_terminal_clock_accepts_both_exact_rounding_conventions_without_rewriting(self):
        rows = [{"episode_index": 8, "source_log_time_ns": NS},
                {"episode_index": 8, "source_log_time_ns": NS + 66666667}]
        for terminal in (NS + 133333333, NS + 133333334):
            with self.subTest(terminal=terminal):
                source = {"source_id": "synthetic_B041", "source_episode_index": 8,
                          "source_frame_count": 2, "source_fps": 15,
                          "segments": [{"id": "all", "start_frame": 0, "end_frame": 2,
                                        "start_log_ns": NS, "end_log_ns": terminal}]}
                path = self.run / "annotations.json"
                path.write_text(json.dumps({"sources": [source]}))
                original = path.read_bytes()
                result = prepare.align_annotations(path, "synthetic_B041", rows)
                self.assertEqual(result["segments"][0]["end_log_ns"], str(terminal))
                self.assertEqual(path.read_bytes(), original)

    def test_terminal_clock_fallback_does_not_relax_internal_or_irregular_clocks(self):
        cases = [
            ([NS, NS + 66666667, NS + 133333333], 2, NS + 133333334),
            ([NS, NS + 66666667], 2, NS + 133333332),
            ([NS, NS + 66666667], 2, NS + 133333335),
            ([NS, NS + 66666665], 2, NS + 133333332),
        ]
        for clocks, end, terminal in cases:
            with self.subTest(clocks=clocks, terminal=terminal):
                rows = [{"episode_index": 8, "source_log_time_ns": t} for t in clocks]
                source = {"source_id": "synthetic_B041", "source_episode_index": 8,
                          "source_frame_count": len(rows), "source_fps": 15,
                          "segments": [{"id": "all", "start_frame": 0, "end_frame": end,
                                        "start_log_ns": NS, "end_log_ns": terminal}]}
                path = self.run / "annotations.json"
                path.write_text(json.dumps({"sources": [source]}))
                with self.assertRaisesRegex(ValueError, "ANNOTATION_CLOCK_MISMATCH"):
                    prepare.align_annotations(path, "synthetic_B041", rows)

    @unittest.skipUnless(HAVE_ARROW, "Parquet tests run separately in an existing PyArrow environment")
    def test_parquet_retains_exact_clocks_quality_and_unapproved_half_open_segments(self):
        path, sidecar = self.parquet_fixture()
        bundle = self.load(self.call("parquet", path, "--source-id", "synthetic_B041",
                                     "--annotations", sidecar))
        self.assertEqual(bundle["source"]["episode_index"], 8)
        self.assertEqual(bundle["samples"][0]["time"], "1792116367247308928")
        self.assertEqual(bundle["samples"][2]["time"], "1792116367380642262")
        self.assertEqual(bundle["samples"][0]["action_quality"][0],
                         {"valid": False, "source_time": "-1", "age_s": -1.,
                          "timing": "source_log_time_ns"})
        self.assertIsNone(bundle["samples"][1]["state"][14])
        self.assertFalse(bundle["samples"][1]["state_quality"][2]["valid"])
        self.assertEqual(bundle["samples"][0]["context"]["observation.image_header_ns"],
                         ["1792116367247308898"] * 3)
        segments = bundle["annotations"]["segments"]
        self.assertEqual(bundle["annotations"]["alignment"], "source_frames")
        self.assertEqual(segments[1]["end_frame"], 3)
        self.assertEqual(segments[0]["boundary_status"], "manual_edit_pending_review")
        self.assertEqual(segments[0]["review_status"], "human_reviewed")
        self.assertFalse(segments[0]["details"]["training_approved"])
        self.assertEqual(segments[0]["details"]["start_log_ns"], str(NS))

    @unittest.skipUnless(HAVE_ARROW, "Parquet tests run separately in an existing PyArrow environment")
    def test_parquet_refuses_wrong_identity_count_episode_frame_and_clock_alignment(self):
        cases = [
            (lambda rows, s: s.update(source_id="other"), "ANNOTATION_SOURCE_MISMATCH"),
            (lambda rows, s: s.update(source_frame_count=4), "ANNOTATION_COUNT_MISMATCH"),
            (lambda rows, s: s.update(source_episode_index=9), "ANNOTATION_EPISODE_MISMATCH"),
            (lambda rows, s: rows[1].update(frame_index=2), "FRAME_ALIGNMENT"),
            (lambda rows, s: s["segments"][0].update(end_frame=4), "ANNOTATION_BOUNDARY"),
            (lambda rows, s: s["segments"][0].update(start_log_ns=NS+1), "ANNOTATION_CLOCK_MISMATCH"),
        ]
        for change, code in cases:
            with self.subTest(code=code):
                path, sidecar = self.parquet_fixture(change)
                result = self.call("parquet", path, "--source-id", "synthetic_B041", "--annotations", sidecar)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(code, result.stderr)
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
