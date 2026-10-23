"""P0 H.264 stream carrying a catalog of independently validated snapshots.

Call ``arm`` after applying a pose, then hold that pose and snapshot unchanged
through ``rep.orchestrator.step(delta_time=0, wait_for_render=True)``. The
factory disables SRTX and capture-on-play: this uses the local POST_RENDER
Attribute/FabricReader path, not SRTX's completion-time core-annotator reads.

The SDK encoder returns previously completed bytes while submitting the current
raw frame. The current Attribute ID therefore identifies the encoder poll, not
the encoded image. Only snapshots whose raw image marker matches that Attribute
enter the catalog. The receiver must decode the H.264 image's own ID plus CRC32
and choose the exact catalog entry; this module never guesses its origin.

SDK imports are delayed until the factory. Video timestamps are an output clock,
not image identity. A successful server call does not prove browser delivery.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
import json
import uuid
import zlib


SEI_METADATA_UUID = uuid.uuid5(uuid.NAMESPACE_DNS, 'model-evaluation.p0.snapshot.v1').bytes
CAPTURE_ATTRIBUTE = 'p0:capture_id'
MARKER_LAYOUT = 'capture32_crc32'


@dataclass(frozen=True)
class PreparedFrame:
    encoder_poll_capture_id: int | None
    encoded_bytes: bytes
    metadata: bytes
    timestamp_ns: int


def _single_output(data: dict, name: str):
    values = [value for key, value in data.items() if key == name or key.startswith(name + '-')]
    return values[0] if len(values) == 1 else None


def _capture_id(value):
    if hasattr(value, 'tolist'):
        value = value.tolist()
    while isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if type(value) is not int or not -(2**31) <= value < 2**32 or value == 0:
        return None
    # Attribute supports signed USD Int; preserve the full uint32 bit pattern.
    return value if value > 0 else value + 2**32


class MarkerDecodeError(ValueError):
    pass


class MarkerCRCError(MarkerDecodeError):
    pass


def decode_capture_marker(raw, pixels) -> int:
    """Read low 32 ID bits then high 32 CRC bits from independent RGB pixels."""
    if isinstance(raw, dict):
        raw = raw.get('data')
    shape = getattr(raw, 'shape', ())
    if len(shape) != 3 or shape[2] not in (3, 4) or str(getattr(raw, 'dtype', None)) != 'uint8':
        raise MarkerDecodeError('same-payload raw rgb is missing or is not uint8 RGB/RGBA')
    if len(pixels) != 64:
        raise MarkerDecodeError('capture32_crc32 requires 64 marker pixels')
    bits = 0
    for bit, (x, y) in enumerate(pixels):
        if not (0 <= x < shape[1] and 0 <= y < shape[0]):
            raise MarkerDecodeError('marker pixel lies outside the raw image')
        red, green = int(raw[y, x, 0]), int(raw[y, x, 1])
        if abs(green - red) < 30:
            raise MarkerDecodeError('raw marker has insufficient red/green contrast')
        bits |= int(green > red) << bit
    capture_id, checksum = bits & 0xffffffff, bits >> 32
    if checksum != zlib.crc32(capture_id.to_bytes(4, 'big')):
        raise MarkerCRCError('raw marker CRC32 does not match capture ID')
    if capture_id == 0:
        raise MarkerDecodeError('capture ID zero is reserved for unarmed frames')
    return capture_id


def _rational(value):
    try:
        numerator, denominator = value
        # Do not silently truncate a float supplied as a timestamp component.
        if int(numerator) != numerator or int(denominator) != denominator:
            return None
        if numerator < 0 or denominator <= 0:
            return None
        reduced = Fraction(int(numerator), int(denominator))
        return reduced.numerator, reduced.denominator
    except (TypeError, ValueError, OverflowError):
        return None


class SnapshotRegistry:
    """Freeze armed states; include at most 16 raw-validated candidates in SEI.

    IDs are positive uint32 values and never wrap or reset on a business seek.
    Raw validation and encoded output polling are intentionally independent.
    A bad current raw frame cannot add a candidate, but an existing catalog may
    accompany the encoder's previously completed output.
    """

    def __init__(self, capacity: int = 512, catalog_capacity: int = 16):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('snapshot capacity must be a positive integer')
        if type(catalog_capacity) is not int or not 1 <= catalog_capacity <= 16:
            raise ValueError('catalog capacity must be an integer in [1,16]')
        self.capacity = capacity
        self.catalog_capacity = catalog_capacity
        self._snapshots = OrderedDict()
        self._catalog = {}
        self._session_id = None
        self._marker_config = None
        self._last_armed = 0
        self._last_poll_capture_id = None
        self._last_timestamp_ns = -1
        self._stats = dict.fromkeys([
            'armed', 'payloads', 'prepared_frames', 'snapshots_evicted',
            'empty_h264', 'invalid_capture_id', 'unknown_snapshot',
            'missing_render_time', 'nonmonotonic_render_time', 'empty_catalog',
            'raw_marker_errors', 'raw_crc_mismatches', 'raw_id_mismatches',
            'raw_validated_frames', 'catalog_additions', 'catalog_evicted',
        ], 0)
        self._diagnostics = {}

    @property
    def sync_stats(self) -> dict:
        return {**self._stats, 'retained_snapshots': len(self._snapshots),
                'catalog_size': len(self._catalog),
                'last_armed_capture_id': self._last_armed,
                'last_encoder_poll_capture_id': self._last_poll_capture_id,
                **self._diagnostics}

    def arm(self, capture_id: int, snapshot: dict) -> None:
        if type(capture_id) is not int or not self._last_armed < capture_id <= 0xffffffff:
            raise ValueError('capture_id must be a strictly increasing positive uint32 without wrap')
        if not isinstance(snapshot, dict):
            raise ValueError('snapshot must be a JSON object')
        if 'capture_id' in snapshot and snapshot['capture_id'] != capture_id:
            raise ValueError('snapshot capture_id differs from the render attribute')
        candidate = json.loads(json.dumps(snapshot, allow_nan=False))
        session_id = candidate.get('session_id')
        if not isinstance(session_id, str) or not session_id or self._session_id not in (None, session_id):
            raise ValueError('snapshots must have one nonempty session_id per registry')
        pixels = candidate.get('marker_pixels')
        if (candidate.get('marker_layout') != MARKER_LAYOUT or candidate.get('marker_bits') != 64
                or not isinstance(pixels, list) or len(pixels) != 64
                or any(not isinstance(p, list) or len(p) != 2
                       or any(type(v) is not int or v < 0 for v in p) for p in pixels)
                or len({tuple(p) for p in pixels}) != 64):
            raise ValueError('snapshot requires capture32_crc32 and 64 unique integer marker_pixels')
        marker_config = {'marker_layout': MARKER_LAYOUT, 'marker_bits': 64, 'marker_pixels': pixels}
        if self._marker_config is not None and marker_config != self._marker_config:
            raise ValueError('marker layout cannot change within a registry session')
        candidate['capture_id'] = capture_id
        frozen = json.dumps(candidate, allow_nan=False, separators=(',', ':')).encode('utf-8')
        self._session_id = session_id
        self._marker_config = marker_config
        self._snapshots[capture_id] = frozen
        self._last_armed = capture_id
        self._stats['armed'] += 1
        while len(self._snapshots) > self.capacity:
            self._snapshots.popitem(last=False)
            self._stats['snapshots_evicted'] += 1

    def _validate_current_raw(self, data: dict, capture_id: int | None) -> None:
        if capture_id is None:
            self._stats['invalid_capture_id'] += 1
            return
        frozen = self._snapshots.get(capture_id)
        if frozen is None:
            self._stats['unknown_snapshot'] += 1
            return
        try:
            decoded = decode_capture_marker(_single_output(data, 'rgb'), self._marker_config['marker_pixels'])
        except MarkerCRCError as exc:
            self._stats['raw_crc_mismatches'] += 1
            self._diagnostics['last_raw_error'] = str(exc)
            return
        except MarkerDecodeError as exc:
            self._stats['raw_marker_errors'] += 1
            self._diagnostics['last_raw_error'] = str(exc)
            return
        self._diagnostics['last_raw_capture_id'] = decoded
        if decoded != capture_id:
            self._stats['raw_id_mismatches'] += 1
            self._diagnostics['last_raw_error'] = f'raw ID {decoded} differs from Attribute {capture_id}'
            return
        self._stats['raw_validated_frames'] += 1
        self._diagnostics['last_raw_error'] = None
        if capture_id not in self._catalog:
            self._catalog[capture_id] = frozen
            self._stats['catalog_additions'] += 1
            while len(self._catalog) > self.catalog_capacity:
                del self._catalog[min(self._catalog)]
                self._stats['catalog_evicted'] += 1

    def prepare_frame(self, data: dict) -> PreparedFrame | None:
        self._stats['payloads'] += 1
        capture_id = _capture_id(_single_output(data, 'Attribute'))
        self._last_poll_capture_id = capture_id
        # Validate even when the encoder is warming up: a future completed
        # encoded output may depict this raw frame.
        self._validate_current_raw(data, capture_id)
        encoded = _single_output(data, 'LdrColor')
        if hasattr(encoded, 'tobytes'):
            encoded = encoded.tobytes()
        if not isinstance(encoded, (bytes, bytearray, memoryview)) or not encoded:
            self._stats['empty_h264'] += 1
            return None
        if not self._catalog:
            self._stats['empty_catalog'] += 1
            return None

        reference = _rational(data.get('reference_time'))
        fabric = _single_output(data, 'rpFabricTime')
        fabric_time = _rational((fabric.get('fabricFrameTimeNumerator'),
                                 fabric.get('fabricFrameTimeDenominator'))) if isinstance(fabric, dict) else None
        render_time = fabric_time or reference
        if render_time is None:
            self._stats['missing_render_time'] += 1
            return None
        timestamp_ns = render_time[0] * 1_000_000_000 // render_time[1]
        if timestamp_ns <= self._last_timestamp_ns:
            self._stats['nonmonotonic_render_time'] += 1
            return None

        meta = {'sei_schema': 'p0.snapshot_catalog.v1', 'session_id': self._session_id,
                'encoder_poll_capture_id': capture_id,
                'snapshots': [json.loads(self._catalog[key]) for key in sorted(self._catalog)],
                **self._marker_config, 'encoder_poll_reference_time': reference,
                'rp_fabric_time': fabric_time, 'video_timestamp_ns': timestamp_ns}
        metadata = json.dumps(meta, allow_nan=False, separators=(',', ':')).encode('utf-8')
        self._last_timestamp_ns = timestamp_ns
        self._stats['prepared_frames'] += 1
        self._diagnostics.update(last_reference_time=reference, last_rp_fabric_time=fabric_time,
                                 last_video_timestamp_ns=timestamp_ns, last_catalog_bytes=len(metadata))
        return PreparedFrame(capture_id, bytes(encoded), metadata, timestamp_ns)


def create_snapshot_writer(stage, product, camera_path, port=8554, *, width, height, debug_dir=None):
    """Attach one local H.264 writer; invoke after SimulationApp initialization.

    This changes capture-on-play and SRTX settings for this P0 process. Keep the
    returned writer attached throughout play/pause/seek; those are business-log
    controls, not Kit timeline stop/detach operations. Explicitly call ``detach``
    before shutting down the app. Raw RGB is always part of this same writer
    payload. ``debug_dir`` saves the first five prepared encoder polls beside
    their raw PNG and H.264 bytes; those two images need not depict the same ID.
    """
    import carb
    import omni.replicator.core as rep
    from isaacsim.core.utils.extensions import enable_extension
    from pxr import Sdf

    debug_path = None
    if debug_dir is not None:
        from pathlib import Path
        from PIL import Image
        debug_path = Path(debug_dir)
        debug_path.mkdir(parents=True, exist_ok=True)

    settings = carb.settings.get_settings()
    settings.set('/exts/omni.replicator.srtx/enabled', False)
    enable_extension('isaacsim.streaming.rtsp')
    rep.orchestrator.set_capture_on_play(False)
    from isaacsim.streaming.rtsp import RTSPStreamWriter
    from isaacsim.streaming.rtsp.impl.render_var_utils import ensure_render_var_on_product

    camera = stage.GetPrimAtPath(camera_path)
    if not camera or not camera.IsValid():
        raise ValueError(f'camera prim does not exist: {camera_path}')
    capture_attr = camera.CreateAttribute(CAPTURE_ATTRIBUTE, Sdf.ValueTypeNames.Int)
    capture_attr.Set(0)
    product_path = product.path if hasattr(product, 'path') else str(product)
    success, _ = ensure_render_var_on_product(stage, product_path, 'LdrColor', 'h264')
    if not success:
        raise RuntimeError(f'could not create H.264 LdrColor on {product_path}')

    class SnapshotRTSPWriter(RTSPStreamWriter):
        def __init__(self):
            super().__init__(port=port, mountPath='/stream', encoding='h264', width=width, height=height)
            self.registry = SnapshotRegistry()
            self._sync_transport = dict.fromkeys(['stream_calls', 'streamed_frames', 'stream_rejected',
                                                   'stream_errors', 'failed_stream_drops'], 0)
            self._debug_frames = 0
            self._debug_attempts = 0
            self._debug_errors = 0
            self._debug_last_error = None
            # Use the base encoded annotator, replacing its simulation-time
            # annotator with readers bound to the render product's Fabric data.
            self.annotators = [self.annotators[0], rep.annotators.get('Attribute', init_params={
                'prims': [camera_path], 'attribute': CAPTURE_ATTRIBUTE}), rep.annotators.get('rpFabricTime'),
                rep.annotators.get('rgb')]
            self._annotators = list(self.annotators)

        @property
        def sync_stats(self):
            return {**self.registry.sync_stats, **self._sync_transport,
                    'metadata_uuid_hex': SEI_METADATA_UUID.hex(),
                    'stream_failed': self._stream_failed,
                    'debug_frames': self._debug_frames, 'debug_attempts': self._debug_attempts,
                    'debug_errors': self._debug_errors,
                    'debug_last_error': self._debug_last_error}

        def arm(self, capture_id: int, snapshot: dict):
            self.registry.arm(capture_id, snapshot)
            signed_id = capture_id if capture_id < 2**31 else capture_id - 2**32
            if not capture_attr.Set(signed_id):
                raise RuntimeError('could not author capture ID on camera')

        def _save_debug_capture(self, frame, data):
            if debug_path is None or self._debug_attempts >= 5:
                return
            self._debug_attempts += 1
            prefix = debug_path / f'poll-{self._debug_attempts:03d}-capture-{frame.encoder_poll_capture_id}'
            raw = _single_output(data, 'rgb')
            if isinstance(raw, dict):
                raw = raw.get('data')
            encoded = _single_output(data, 'LdrColor')
            info = {
                'metadata': json.loads(frame.metadata),
                'payload_keys': list(data),
                'reference_time': list(data.get('reference_time', ())),
                'raw_type': type(raw).__name__,
                'raw_shape': list(getattr(raw, 'shape', ())),
                'raw_dtype': str(getattr(raw, 'dtype', None)),
                'encoded_shape': list(getattr(encoded, 'shape', ())),
                'encoded_dtype': str(getattr(encoded, 'dtype', None)),
                'encoded_bytes': len(frame.encoded_bytes),
            }
            prefix.with_suffix('.h264').write_bytes(frame.encoded_bytes)
            try:
                shape = getattr(raw, 'shape', ())
                if (len(shape) != 3 or tuple(shape[:2]) != (height, width)
                        or shape[2] not in (3, 4) or str(getattr(raw, 'dtype', None)) != 'uint8'):
                    raise RuntimeError(f'same-payload raw rgb is unavailable or invalid: {info}')
                Image.fromarray(raw.copy()).save(prefix.with_suffix('.png'))
                self._debug_frames += 1
            except Exception as exc:
                info['debug_error'] = str(exc)
                raise
            finally:
                prefix.with_suffix('.json').write_text(json.dumps(info, indent=2), encoding='utf-8')

        def write(self, data: dict):
            if self._stream_failed:
                self._sync_transport['failed_stream_drops'] += 1
                return
            frame = self.registry.prepare_frame(data)
            if frame is None:
                return
            try:
                self._save_debug_capture(frame, data)
            except Exception as exc:
                # Debug image or file errors must not change catalog admission
                # or prevent forwarding previously completed encoded output.
                self._debug_errors += 1
                self._debug_last_error = str(exc)
                carb.log_error(f'P0 same-payload RGB/H.264 diagnostic failed: {exc}')
            try:
                if self._server is None:
                    self._start_server(self._configured_width, self._configured_height)
                self._sync_transport['stream_calls'] += 1
                accepted = self._server.stream_video_pre_encoded_with_metadata(
                    frame.encoded_bytes, len(frame.encoded_bytes), self._width, self._height,
                    start_time_ns=frame.timestamp_ns, ended_time_ns=frame.timestamp_ns,
                    metadata=frame.metadata, metadata_uuid=SEI_METADATA_UUID)
                self._sync_transport['streamed_frames' if accepted else 'stream_rejected'] += 1
            except Exception as exc:
                self._sync_transport['stream_errors'] += 1
                self._stop_server()
                self._stream_failed = True
                carb.log_error(f'P0 snapshot RTSP failed on port {port}: {exc}')

    writer = SnapshotRTSPWriter()
    writer.attach([product])
    return writer
