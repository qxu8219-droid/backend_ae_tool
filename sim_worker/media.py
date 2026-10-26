"""Formal capture32+CRC snapshot catalog, migrated from the validated P0 writer.

Encoded output can lag the current encoder poll. Only raw-verified candidates
enter SEI; receivers choose the candidate by independently decoded image ID.
"""
from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
import time,uuid,zlib,json
from .resources import start_checked,attach_checked,release_all,verify_rtsp_binding
from replay_protocol import AppliedFrame,RenderedFrame,FrameSnapshot,SnapshotCatalog

SEI_METADATA_UUID=uuid.uuid5(uuid.NAMESPACE_DNS,'model-evaluation.snapshot_catalog.v1').bytes
CAPTURE_ATTRIBUTE='replay:capture_id'

@dataclass(frozen=True)
class PreparedFrame:
    encoder_poll_capture_id:int|None
    encoded_bytes:bytes
    metadata:bytes
    timestamp_ns:int

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
    def __init__(self,pixels,limits,*,capacity=16,clock=time.time,on_verified=None):
        if not 1<=capacity<=16 or len(pixels)!=64:raise ValueError('Invalid catalog geometry/capacity')
        self.pixels=tuple(tuple(p) for p in pixels);self.limits=limits;self.capacity=capacity;self.clock=clock;self.on_verified=on_verified
        self.armed=OrderedDict();self.verified=OrderedDict();self.last_id=0;self.last_timestamp=-1;self.raw_errors=0;self.prepared=0;self.session_id=None;self.stale_time=0;self.diagnostic_errors=0;self.last_diagnostic_error=None
    def arm(self,id,applied):
        applied=AppliedFrame.model_validate(applied)
        if type(id)is not int or not self.last_id<id<=0xffffffff:raise ValueError('Capture IDs cannot reset or wrap')
        if self.session_id not in (None,applied.session_id):raise ValueError('Catalog session cannot change')
        self.session_id=applied.session_id;self.armed[id]=applied;self.last_id=id
        while len(self.armed)>64:self.armed.popitem(last=False)
    def rendered(self,id):return self.verified.get(id,(None,None))[0]
    def stats(self):return {'catalog_size':len(self.verified),'armed':len(self.armed),'last_capture_id':self.last_id,'raw_errors':self.raw_errors,'prepared_frames':self.prepared,'nonmonotonic_video_time':self.stale_time,'diagnostic_errors':self.diagnostic_errors,'last_diagnostic_error':self.last_diagnostic_error}
    def prepare_frame(self,data):
        id=_capture_id(_single_output(data,'Attribute'))
        applied=self.armed.get(id)
        if applied is not None and id not in self.verified:
            try:
                if decode_capture_marker(_single_output(data,'rgb'),self.pixels)!=id:raise MarkerDecodeError('Raw identity differs from capture attribute')
                rendered=RenderedFrame(**applied.model_dump(mode='python'),capture_id=id,render_seq=id,captured_wall_s=self.clock(),raw_identity_verified=True)
                flags=warns=0
                for index,limit in enumerate(self.limits):
                    if not applied.quality[index].valid or limit['type']=='continuous':continue
                    lo,hi=limit['lower'],limit['upper'];value=applied.joints[index]
                    if value<lo or value>hi:flags|=1<<index
                    elif hi>lo and (value-lo)/(hi-lo)<=.05 or hi>lo and (value-lo)/(hi-lo)>=.95:warns|=1<<index
                payload={key:getattr(rendered,key) for key in FrameSnapshot.model_fields if hasattr(rendered,key)}
                snapshot=FrameSnapshot(**payload,limit_flags=flags,limit_warn_flags=warns)
                self.verified[id]=(rendered,snapshot)
                while len(self.verified)>self.capacity:self.verified.popitem(last=False)
                if self.on_verified:
                    try:self.on_verified(rendered,snapshot,_single_output(data,'rgb'))
                    except Exception as exc:
                        self.diagnostic_errors+=1;self.last_diagnostic_error=str(exc)[:512]
            except MarkerDecodeError:self.raw_errors+=1
        encoded=_single_output(data,'LdrColor')
        if hasattr(encoded,'tobytes'):encoded=encoded.tobytes()
        if not isinstance(encoded,(bytes,bytearray,memoryview)) or not encoded or not self.verified:return None
        fabric=_single_output(data,'rpFabricTime')
        render_time=_rational((fabric.get('fabricFrameTimeNumerator'),fabric.get('fabricFrameTimeDenominator'))) if isinstance(fabric,dict) else None
        render_time=render_time or _rational(data.get('reference_time'))
        if render_time is None:return None
        timestamp=render_time[0]*1000000000//render_time[1]
        if timestamp<=self.last_timestamp:self.stale_time+=1;return None
        while True:
            catalog=SnapshotCatalog(session_id=self.session_id,marker_pixels=self.pixels,snapshots=[v[1] for v in self.verified.values()],encoder_poll_capture_id=id)
            metadata=catalog.model_dump_json().encode()
            if len(metadata)<=65536:break
            if len(self.verified)==1:raise RuntimeError('Snapshot exceeds metadata budget')
            self.verified.popitem(last=False)
        self.last_timestamp=timestamp;self.prepared+=1
        return PreparedFrame(id,bytes(encoded),metadata,timestamp)

def create_snapshot_writer(stage, product, camera_path, port=8554, *, width, height, registry, debug_dir=None,allow_native_wildcard=False):
    """Attach one local H.264 writer; invoke after SimulationApp initialization.

    This changes capture-on-play and SRTX settings for this worker process. Keep the
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
            self.registry = registry
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

        def _start_server(self,width,height):
            from omni.kit.livestream.core import Server,ServerConfig,VideoCodec
            self._width=width;self._height=height
            config=ServerConfig(width=width,height=height,stream_port=self._port,
                rtsp_mount_point=self._mount_path,public_ip='127.0.0.1',video_pre_encode=VideoCodec.H264)
            self._server=start_checked(Server('rtsp'),config)
            self.binding_addresses=verify_rtsp_binding(self._port,allow_native_wildcard=allow_native_wildcard)

        def _stop_server(self):
            server=self._server;self._server=None
            self._width=0;self._height=0;self._base_timestamp=None
            if server is not None:
                errors=release_all([('rtsp-stop',server.stop),('rtsp-close',server.close)])
                if errors:raise RuntimeError('; '.join(errors))

        @property
        def sync_stats(self):
            return {**self.registry.stats(), **self._sync_transport,
                    'metadata_uuid_hex': SEI_METADATA_UUID.hex(),
                    'stream_failed': self._stream_failed,
                    'debug_frames': self._debug_frames, 'debug_attempts': self._debug_attempts,
                    'debug_errors': self._debug_errors,
                    'debug_last_error': self._debug_last_error}

        def arm(self, capture_id: int, applied):
            self.registry.arm(capture_id, applied)
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
                carb.log_error(f'Worker same-payload RGB/H.264 diagnostic failed: {exc}')
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
                carb.log_error(f'Worker snapshot RTSP failed on port {port}: {exc}')

    writer = SnapshotRTSPWriter()
    return attach_checked(writer,product,width,height)
