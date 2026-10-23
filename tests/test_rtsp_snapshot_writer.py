import json
import math
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

from scripts.spike.rtsp_snapshot_writer import SnapshotRegistry, SEI_METADATA_UUID, create_snapshot_writer


PIXELS = [[bit, 0] for bit in range(64)]
# CRC32 over four big-endian ID bytes, independent of the production decoder.
CRC = {1: 0x5643ef8a, 2: 0xcf4abe30, 3: 0xb84d8ea6,
       7: 0xbf204abf, 8: 0x2f9f572e, 0x80000000: 0xcc1d6927,
       0xffffffff: 0xffffffff}


def snapshot(render_seq, **extra):
    return {'session_id': 'session-A', 'epoch': 1, 'render_seq': render_seq,
            'log_time_s': 2.5, 'joints': [0.1] * 28,
            'marker_layout': 'capture32_crc32', 'marker_bits': 64,
            'marker_pixels': [p.copy() for p in PIXELS], **extra}


def raw_marker(capture_id, crc=None):
    bits = capture_id | ((CRC[capture_id] if crc is None else crc) << 32)
    return np.array([[(20, 240, 10) if bits & (1 << bit) else (240, 20, 10)
                      for bit in range(64)]], dtype=np.uint8)


def payload(capture_id, *, raw=None, encoded=b'\x00\x00\x00\x01\x65test',
            reference=(9, 60), fabric=(1, 5)):
    data = {'LdrColor-rp': encoded, 'Attribute-rp': [capture_id],
            'rgb-rp': raw_marker(capture_id) if raw is None else raw,
            'reference_time': reference}
    if fabric is not None:
        data['rpFabricTime-rp'] = {'fabricFrameTimeNumerator': fabric[0],
                                  'fabricFrameTimeDenominator': fabric[1]}
    return data


def catalog(frame):
    return json.loads(frame.metadata)['snapshots']


class SnapshotRegistryTests(unittest.TestCase):
    def test_encoder_poll_catalog_does_not_claim_current_or_previous_image_identity(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        registry.prepare_frame(payload(1))
        registry.arm(2, snapshot(2))
        frame = registry.prepare_frame(payload(2, encoded=b'encoded-origin-is-unknown', fabric=(2, 5)))
        meta = json.loads(frame.metadata)
        self.assertEqual(meta['sei_schema'], 'p0.snapshot_catalog.v1')
        self.assertEqual(meta['session_id'], 'session-A')
        self.assertEqual(meta['encoder_poll_capture_id'], 2)
        self.assertEqual([item['capture_id'] for item in meta['snapshots']], [1, 2])
        self.assertNotIn('capture_id', meta)
        self.assertNotIn('joints', meta)
        self.assertEqual(frame.encoded_bytes, b'encoded-origin-is-unknown')
        self.assertEqual(len(SEI_METADATA_UUID), 16)

    def test_catalog_is_bounded_and_independent_of_snapshot_and_returned_json_mutation(self):
        registry = SnapshotRegistry(catalog_capacity=2)
        original = snapshot(1)
        registry.arm(1, original)
        original['joints'][0] = 999
        original['marker_pixels'][0][0] = 999
        first = registry.prepare_frame(payload(1))
        self.assertEqual(catalog(first)[0]['joints'][0], 0.1)
        self.assertEqual(catalog(first)[0]['marker_pixels'][0], [0, 0])
        external = catalog(first)
        external[0]['joints'][0] = 888
        repeated = registry.prepare_frame(payload(1, fabric=(3, 10)))
        self.assertEqual(catalog(repeated)[0]['joints'][0], 0.1)
        for capture_id in (2, 3):
            registry.arm(capture_id, snapshot(capture_id))
            frame = registry.prepare_frame(payload(capture_id, fabric=(capture_id, 5)))
        self.assertEqual([item['capture_id'] for item in catalog(frame)], [2, 3])
        self.assertEqual(registry.sync_stats['catalog_evicted'], 1)
        self.assertEqual(registry.sync_stats['catalog_size'], 2)
        with self.assertRaises(ValueError):
            SnapshotRegistry(catalog_capacity=17)

    def test_wrong_crc_or_marker_id_never_validates_snapshot(self):
        for raw, expected_counter in [(raw_marker(2, crc=0), 'raw_crc_mismatches'),
                                      (raw_marker(1), 'raw_id_mismatches')]:
            with self.subTest(expected_counter=expected_counter):
                registry = SnapshotRegistry()
                registry.arm(1, snapshot(1))
                registry.prepare_frame(payload(1))
                registry.arm(2, snapshot(2))
                frame = registry.prepare_frame(payload(2, raw=raw, fabric=(2, 5)))
                self.assertEqual([item['capture_id'] for item in catalog(frame)], [1])
                self.assertEqual(registry.sync_stats[expected_counter], 1)
                self.assertEqual(json.loads(frame.metadata)['encoder_poll_capture_id'], 2)

    def test_missing_low_contrast_or_invalid_raw_cannot_create_first_catalog_entry(self):
        for raw in (np.zeros((1, 64, 3), dtype=np.uint8),
                    np.zeros((1, 10, 3), dtype=np.uint8), b'not-an-image'):
            registry = SnapshotRegistry()
            registry.arm(1, snapshot(1))
            self.assertIsNone(registry.prepare_frame(payload(1, raw=raw)))
            self.assertEqual(registry.sync_stats['catalog_size'], 0)
            self.assertEqual(registry.sync_stats['raw_marker_errors'], 1)
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        missing = payload(1)
        del missing['rgb-rp']
        self.assertIsNone(registry.prepare_frame(missing))
        self.assertEqual(registry.sync_stats['raw_marker_errors'], 1)

    def test_encoder_warmup_still_validates_raw_snapshot_for_later_output(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        self.assertIsNone(registry.prepare_frame(payload(1, encoded=b'')))
        self.assertEqual(registry.sync_stats['catalog_size'], 1)
        registry.arm(2, snapshot(2))
        frame = registry.prepare_frame(payload(2, fabric=(2, 5)))
        self.assertEqual([item['capture_id'] for item in catalog(frame)], [1, 2])

    def test_unknown_or_evicted_snapshot_can_only_forward_existing_catalog(self):
        registry = SnapshotRegistry(capacity=1)
        registry.arm(1, snapshot(1))
        registry.prepare_frame(payload(1))
        registry.arm(2, snapshot(2))
        for index, capture_id in enumerate((1, 7), 2):
            frame = registry.prepare_frame(payload(capture_id, fabric=(index, 5)))
            self.assertEqual([item['capture_id'] for item in catalog(frame)], [1])
        self.assertEqual(registry.sync_stats['unknown_snapshot'], 2)
        self.assertEqual(registry.sync_stats['snapshots_evicted'], 1)

    def test_missing_attribute_does_not_guess_raw_identity_or_latest_snapshot(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        registry.prepare_frame(payload(1))
        registry.arm(2, snapshot(2))
        no_attribute = payload(2, fabric=(2, 5))
        del no_attribute['Attribute-rp']
        frame = registry.prepare_frame(no_attribute)
        meta = json.loads(frame.metadata)
        self.assertIsNone(meta['encoder_poll_capture_id'])
        self.assertEqual([item['capture_id'] for item in catalog(frame)], [1])
        self.assertEqual(registry.sync_stats['invalid_capture_id'], 1)

    def test_repeated_poll_capture_is_allowed_with_new_output_clock_and_not_duplicated_in_catalog(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        registry.prepare_frame(payload(1))
        second = registry.prepare_frame(payload(1, fabric=(2, 5)))
        self.assertIsNotNone(second)
        self.assertEqual(len(catalog(second)), 1)

    def test_seek_log_time_is_candidate_data_not_video_timestamp(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1, epoch=1, log_time_s=10.0))
        first = registry.prepare_frame(payload(1, reference=(9, 60), fabric=(1, 5)))
        registry.arm(2, snapshot(2, epoch=2, log_time_s=0.0))
        second = registry.prepare_frame(payload(2, reference=(10, 60), fabric=(7, 30)))
        self.assertEqual(first.timestamp_ns, 200_000_000)
        self.assertEqual(second.timestamp_ns, 233_333_333)
        meta = json.loads(first.metadata)
        self.assertEqual(meta['encoder_poll_reference_time'], [3, 20])
        self.assertEqual(meta['rp_fabric_time'], [1, 5])
        self.assertEqual([item['log_time_s'] for item in catalog(second)], [10.0, 0.0])

    def test_missing_duplicate_and_backward_output_clock_are_rejected(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        self.assertIsNone(registry.prepare_frame(payload(1, reference=(1, 0), fabric=None)))
        self.assertIsNotNone(registry.prepare_frame(payload(1, reference=(1, 2), fabric=None)))
        self.assertIsNone(registry.prepare_frame(payload(1, reference=(1, 2), fabric=None)))
        self.assertIsNone(registry.prepare_frame(payload(1, reference=(1, 3), fabric=None)))
        self.assertIsNotNone(registry.prepare_frame(payload(1, reference=(2, 3), fabric=None)))
        self.assertEqual(registry.sync_stats['missing_render_time'], 1)
        self.assertEqual(registry.sync_stats['nonmonotonic_render_time'], 2)

    def test_capture_id_uses_full_uint32_without_wrap_and_signed_fabric_roundtrip(self):
        registry = SnapshotRegistry()
        registry.arm(0x80000000, snapshot(0x80000000))
        data = payload(0x80000000)
        data['Attribute-rp'] = [-0x80000000]
        self.assertEqual(catalog(registry.prepare_frame(data))[0]['capture_id'], 0x80000000)
        registry.arm(0xffffffff, snapshot(0xffffffff))
        data = payload(0xffffffff, fabric=(2, 5))
        data['Attribute-rp'] = [-1]
        self.assertEqual(catalog(registry.prepare_frame(data))[-1]['capture_id'], 0xffffffff)
        for invalid in (0, 1, 0xffffffff, 0x100000000, True, -1):
            with self.assertRaises(ValueError):
                registry.arm(invalid, snapshot(1))

    def test_arm_rejects_mutating_session_layout_nonfinite_data_and_id_mismatch(self):
        registry = SnapshotRegistry()
        registry.arm(1, snapshot(1))
        for invalid in (snapshot(2, log_time_s=math.nan), snapshot(2, capture_id=3),
                        snapshot(2, session_id='other-session'), snapshot(2, marker_bits=12),
                        snapshot(2, marker_pixels=PIXELS[:-1])):
            with self.assertRaises(ValueError):
                registry.arm(2, invalid)
        registry.arm(2, snapshot(2))
        self.assertIsNotNone(registry.prepare_frame(payload(2)))

    def test_debug_failure_cannot_prevent_forwarding_existing_validated_catalog(self):
        # The SDK and USD are native/GPU dependencies; replace only that boundary.
        class NativeWriter:
            def __init__(self, **kwargs):
                self.annotators = ['encoded-LdrColor']
                self._server = None
                self._stream_failed = False
                self._configured_width, self._configured_height = kwargs['width'], kwargs['height']
                self.sent = []

            def attach(self, products):
                pass

            def _start_server(self, width, height):
                self._width, self._height = width, height
                def stream(encoded, size, width, height, **kwargs):
                    self.sent.append(json.loads(kwargs['metadata']))
                    return True
                self._server = types.SimpleNamespace(stream_video_pre_encoded_with_metadata=stream)

        def module(name, **attrs):
            result = types.ModuleType(name)
            result.__dict__.update(attrs)
            return result

        rep = module('omni.replicator.core',
                     annotators=types.SimpleNamespace(get=lambda *a, **kw: object()),
                     orchestrator=types.SimpleNamespace(set_capture_on_play=lambda value: None))
        modules = {
            'carb': module('carb', settings=types.SimpleNamespace(get_settings=lambda:
                           types.SimpleNamespace(set=lambda *a: None)), log_error=lambda text: None),
            'omni': module('omni'), 'omni.replicator': module('omni.replicator', core=rep),
            'omni.replicator.core': rep,
            'isaacsim.core.utils.extensions': module('extensions', enable_extension=lambda name: None),
            'isaacsim.streaming.rtsp': module('rtsp', RTSPStreamWriter=NativeWriter),
            'isaacsim.streaming.rtsp.impl.render_var_utils': module('render_var_utils',
                      ensure_render_var_on_product=lambda *a: (True, '/rp/LdrColor')),
            'pxr': module('pxr', Sdf=types.SimpleNamespace(ValueTypeNames=types.SimpleNamespace(Int='Int'))),
        }
        modules['omni'].replicator = modules['omni.replicator']
        camera = types.SimpleNamespace(IsValid=lambda: True, CreateAttribute=lambda *a:
                                       types.SimpleNamespace(Set=lambda value: True))
        stage = types.SimpleNamespace(GetPrimAtPath=lambda path: camera)
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', modules):
            writer = create_snapshot_writer(stage, '/rp', '/camera', width=64, height=1, debug_dir=directory)
            writer.arm(1, snapshot(1))
            writer.write(payload(1))
            writer.arm(2, snapshot(2))
            writer.write(payload(2, raw=b'invalid-raw', fabric=(2, 5)))
            self.assertEqual(len(writer.sent), 2)
            self.assertEqual([item['capture_id'] for item in writer.sent[-1]['snapshots']], [1])
            self.assertEqual(writer.sync_stats['debug_errors'], 1)
            writer.arm(3, snapshot(3))
            with patch('pathlib.Path.write_bytes', side_effect=OSError('disk unavailable')):
                writer.write(payload(3, fabric=(3, 5)))
        self.assertEqual(len(writer.sent), 3)
        self.assertEqual(writer.sync_stats['debug_errors'], 2)


if __name__ == '__main__':
    unittest.main()
