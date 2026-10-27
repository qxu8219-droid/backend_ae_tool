#!/usr/bin/env python3
"""Independently check every decoded screen-recording frame, including gaps.

Run with the system interpreter, which supplies optional OpenCV/NumPy locally::

    /usr/bin/python3 scripts/validate_recording.py VIDEO.webm OBSERVATIONS.jsonl \
        --output recording-report.json --frames recording-frames.jsonl

The VIDEO argument also accepts a chromium-cdp-png/v1 screen-recording manifest.
Those lossless viewport frames retain native timestamps and are never resampled.
References are independently rasterized expected panel text, never screenshots
of the panel under test. Capture identity is read from the *recorded image*.
This is bounded glyph-image comparison, not strict equality of original pixels.
No application renderer, stamp decoder, store, or runtime is imported here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import struct
import zlib


def dependencies():
    try:
        import cv2
        import numpy as np
    except ImportError as error:
        raise RuntimeError('Recording analysis requires optional OpenCV and NumPy; use /usr/bin/python3 on this host') from error
    return cv2, np


class RecordingReader:
    """Decode native PNG screencast frames with timestamps, or a legacy video."""
    def __init__(self, path):
        self.path = Path(path)
        self.errors, self.index, self.decoded = [], 0, 0
        self.capture, self.entries = None, None
        self.format, self.fps = 'encoded-video', None
        cv2, _ = dependencies()
        if self.path.suffix.lower() == '.json':
            manifest = json.loads(self.path.read_text())
            if manifest.get('schema') != 'chromium-cdp-png/v1':
                raise ValueError('Unsupported screen-recording manifest schema')
            self.format = manifest['schema']
            self.entries = manifest['frames']
            self.declared = manifest['received_frames']
            self.width, self.height = (manifest['viewport'][key] for key in ('width', 'height'))
            if (manifest.get('complete') is not True or manifest.get('errors') or
                    self.declared != len(self.entries) or manifest.get('persisted_frames') != len(self.entries)):
                self.errors.append('Screencast manifest is incomplete or received/persisted frame totals differ')
            self.first_timestamp = None
            self.previous_timestamp = None
        else:
            self.capture = cv2.VideoCapture(str(self.path))
            if not self.capture.isOpened():
                raise ValueError(f'Cannot decode recording: {self.path}')
            self.fps = float(self.capture.get(cv2.CAP_PROP_FPS))
            self.declared = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
            self.width, self.height = (int(self.capture.get(key)) for key in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))

    def read(self):
        cv2, np = dependencies()
        if self.entries is None:
            ok, frame = self.capture.read()
            if not ok: return False, None, None
            time = self.index/self.fps if self.fps else None
        else:
            if self.index >= len(self.entries): return False, None, None
            entry, time = self.entries[self.index], None
            frame = None
            try:
                stamp = entry['timestamp_ms']
                if type(stamp) not in (int, float) or not math.isfinite(stamp):
                    raise ValueError('Missing/nonfinite browser screencast timestamp')
                if self.previous_timestamp is not None and stamp < self.previous_timestamp:
                    raise ValueError('Browser screencast timestamps regressed')
                if self.first_timestamp is None: self.first_timestamp = stamp
                self.previous_timestamp = stamp
                time = (stamp-self.first_timestamp)/1000
                relative = Path(entry['file'])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Unsafe recorded PNG path')
                data = (self.path.parent / relative).read_bytes()
                if hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise ValueError('Recorded PNG SHA-256 differs from screencast manifest')
                if len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n':
                    raise ValueError('Recorded PNG header missing/truncated')
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.shape[:2] != (self.height, self.width) or (entry['height'], entry['width']) != frame.shape[:2]:
                    raise ValueError('PNG missing/truncated or recorded viewport dimensions differ')
            except (OSError, ValueError, TypeError, KeyError, cv2.error) as error:
                if len(self.errors) < 100: self.errors.append(f'frame {self.index}: {error}')
                frame = None
        self.index += 1
        if frame is not None: self.decoded += 1
        return True, frame, time

    def close(self):
        if self.capture is not None: self.capture.release()


def decode_identity(frame, observation, min_contrast=30):
    """Decode documented red/green bits, then verify independent stdlib CRC32."""
    _, np = dependencies()
    canvas, layout = observation['canvas'], observation['layout']
    image = layout['imageRect']
    scale_x = canvas['width'] / canvas['intrinsicWidth']
    scale_y = canvas['height'] / canvas['intrinsicHeight']
    points = layout['markerPixels']
    if len(points) != 64:
        raise ValueError('capture32_crc32 must contain exactly 64 marker coordinates')
    words = [0, 0]
    minimum = 255.
    for index, (x, y) in enumerate(points):
        px = int(canvas['x'] + scale_x * (image['x'] + x * image['width'] / layout['sourceWidth']))
        py = int(canvas['y'] + scale_y * (image['y'] + y * image['height'] / layout['sourceHeight']))
        if not (0 <= px < frame.shape[1] and 0 <= py < frame.shape[0]):
            return {'status': 'stamp_outside_viewport'}
        # A small median resists isolated codec ringing without crossing bit cells.
        patch = frame[max(0, py-1):py+2, max(0, px-1):px+2].astype(np.float32)
        contrast = float(np.median(patch[:, :, 1] - patch[:, :, 2]))
        minimum = min(minimum, abs(contrast))
        if abs(contrast) < min_contrast:
            return {'status': 'unreadable_stamp', 'min_contrast': round(minimum, 2)}
        if contrast > 0:
            words[index // 32] |= 1 << (index % 32)
    capture, crc = words
    expected = zlib.crc32(struct.pack('>I', capture))
    return {'status': 'readable' if capture > 0 and crc == expected else 'crc_mismatch',
            'capture_id': capture, 'crc': crc, 'expected_crc': expected,
            'min_contrast': round(minimum, 2)}


def compare_region(actual, expected, block_threshold=38.):
    """Small block error catches a single stale digit; blank area cannot dilute it."""
    _, np = dependencies()
    error = np.abs(actual.astype(np.float32) - expected.astype(np.float32)).max(axis=2)
    height, width = error.shape
    # Average only existing pixels in each block; padding must not dilute edges.
    padded_height, padded_width = (height + 3) // 4 * 4, (width + 3) // 4 * 4
    padded = np.pad(error, ((0, padded_height-height), (0, padded_width-width)))
    weights = np.pad(np.ones_like(error), ((0, padded_height-height), (0, padded_width-width)))
    shape = (padded_height // 4, 4, padded_width // 4, 4)
    block_means = padded.reshape(shape).sum(axis=(1, 3)) / weights.reshape(shape).sum(axis=(1, 3))
    maximum = float(block_means.max())
    return {'matched': maximum <= block_threshold, 'max_block_mae': round(maximum, 3),
            'mean_absolute_error': round(float(error.mean()), 3)}


def validate_recording(video: Path, observations: Path, *, frames_output: Path | None = None,
                       block_threshold=38., window: Path | None = None):
    cv2, np = dependencies()
    video, observations = Path(video), Path(observations)
    rows = [json.loads(line) for line in observations.read_text().splitlines() if line.strip()]
    rows = [row for row in rows if row.get('kind') == 'capture']
    if not rows:
        raise ValueError('No independent per-capture references in recording observations')
    by_capture = defaultdict(list)
    geometry = {}
    for row in rows:
        by_capture[int(row['snapshot']['capture_id'])].append(row)
        key = json.dumps([row['canvas'], row['layout']], sort_keys=True)
        row['_geometry'] = key
        geometry[key] = row

    @lru_cache(maxsize=128)
    def reference_regions(path, regions_json):
        reference = cv2.imread(str(observations.parent / path))
        if reference is None:
            raise ValueError(f'Cannot read independent reference {path}')
        result = []
        for region in json.loads(regions_json):
            x, y, width, height = (int(region[key]) for key in ('x', 'y', 'width', 'height'))
            crop = reference[y:y+height, x:x+width].copy()
            if crop.shape[:2] != (height, width):
                raise ValueError(f'Reference region outside image: {region}')
            result.append((region, crop))
        if not result:
            raise ValueError('A capture reference must include visible panel regions')
        return result

    def compare_panel(frame, row):
        canvas = row['canvas']
        sx = canvas['width'] / canvas['intrinsicWidth']
        sy = canvas['height'] / canvas['intrinsicHeight']
        measurements = {}
        for region, expected in reference_regions(row['reference'], json.dumps(row['regions'], sort_keys=True)):
            x = canvas['x'] + region['x'] * sx
            y = canvas['y'] + region['y'] * sy
            width, height = round(region['width'] * sx), round(region['height'] * sy)
            if width < 1 or height < 1 or x < 0 or y < 0 or x+width > frame.shape[1] or y+height > frame.shape[0]:
                return {'status': 'panel_outside_viewport'}
            actual = cv2.getRectSubPix(frame, (width, height), (x+(width-1)/2, y+(height-1)/2))
            wanted = cv2.resize(expected, (width, height), interpolation=cv2.INTER_LINEAR)
            measurements[region['name']] = compare_region(actual, wanted, block_threshold)
        return {'status': 'matched' if all(item['matched'] for item in measurements.values()) else 'panel_mismatch',
                'regions': measurements}

    reader = RecordingReader(video)
    fps, declared = reader.fps, reader.declared
    video_width, video_height = reader.width, reader.height
    counts, failures = Counter(), []
    identities = set()
    frame_index = 0
    frame_records = []
    output = frames_output.open('w') if frames_output is not None else None
    try:
        while True:
            ok, frame, timestamp = reader.read()
            if not ok:
                break
            decoded = [(key, decode_identity(frame, row)) for key, row in geometry.items()] if frame is not None else []
            readable = [(key, identity) for key, identity in decoded if identity['status'] == 'readable']
            record = {'frame_index': frame_index, 'time_s': round(timestamp, 6) if timestamp is not None else None}
            if frame is None:
                record['status'] = 'recording_read_error'
            elif not readable:
                statuses = {identity['status'] for _, identity in decoded}
                record['status'] = ('crc_mismatch' if 'crc_mismatch' in statuses else
                                    'unreadable_stamp' if 'unreadable_stamp' in statuses else 'stamp_outside_viewport')
            else:
                candidates = []
                for key, identity in readable:
                    for row in by_capture.get(identity['capture_id'], []):
                        if row['_geometry'] == key:
                            candidates.append((identity, row, compare_panel(frame, row)))
                passing = [candidate for candidate in candidates if candidate[2]['status'] == 'matched']
                if passing:
                    identity, row, comparison = passing[0]
                    record.update(comparison)
                    record.update(capture_id=identity['capture_id'], session_id=row['snapshot']['session_id'],
                                  matching_reference_count=len(passing))
                    identities.add((row['snapshot']['session_id'], identity['capture_id']))
                elif candidates:
                    # Report the closest candidate, useful across session ID resets.
                    identity, row, comparison = min(candidates, key=lambda candidate: sum(
                        measure['max_block_mae'] for measure in candidate[2].get('regions', {}).values())
                        if candidate[2]['status'] == 'panel_mismatch' else float('inf'))
                    record.update(comparison)
                    record.update(capture_id=identity['capture_id'], session_id=row['snapshot']['session_id'])
                else:
                    record.update(status='missing_reference', capture_id=readable[0][1]['capture_id'])
            counts[record['status']] += 1
            frame_records.append({key: record[key] for key in
                                  ('frame_index', 'time_s', 'status', 'capture_id', 'session_id') if key in record})
            if record['status'] != 'matched' and len(failures) < 100:
                failures.append(record)
            if output:
                output.write(json.dumps(record, separators=(',', ':')) + '\n')
            frame_index += 1
    finally:
        reader.close()
        if output:
            output.close()
    categories = ['matched', 'panel_mismatch', 'unreadable_stamp', 'crc_mismatch',
                  'missing_reference', 'stamp_outside_viewport', 'panel_outside_viewport', 'recording_read_error']
    integrity_passed = declared > 0 and declared == frame_index and not reader.errors
    report = {
        'schema': 'recording-validation/v1', 'video': str(video), 'observations': str(observations),
        'scope': 'Every recorded-screen frame: independent image capture32/CRC and expected visible panel glyph regions; bounded glyph comparison, not pixel equality',
        'recording_format': reader.format, 'recorded_frames': frame_index,
        'recording_integrity_passed': integrity_passed, 'recording_integrity_errors': reader.errors,
        'decoded_frames': reader.decoded, 'container_frame_count': declared, 'fps': fps,
        'container_frame_count_verified': declared > 0 and declared == frame_index,
        'video_size': [video_width, video_height], 'reference_count': len(rows),
        'unique_matched_identities': len(identities), 'counts': {name: counts[name] for name in categories},
        'matched_frame_fraction': counts['matched'] / frame_index if frame_index else 0.,
        'strict_full_frame_passed': frame_index > 0 and counts['matched'] == frame_index and integrity_passed,
        'all_compared_panels_matched': counts['matched'] > 0 and counts['panel_mismatch'] == 0,
        'comparison': {'block_size': 4, 'max_block_mae_threshold': block_threshold,
                       'min_stamp_red_green_contrast': 30},
        'limitations': ['Image stamp encodes capture ID but not session ID; visually identical references after a session reset are not distinguishable.',
                       'Blank/loading/offscreen/unreadable frames remain explicit coverage gaps; they are never counted as successful pairs.',
                       'The decoder frame total is checked against container metadata; absent or differing totals prevent a strict full-container pass.',
                       'Panel check covers the declared text regions, not every robot pixel or every joint.'],
        'failures': failures, 'failure_details_capped_at': 100,
    }
    if window is not None:
        specification = json.loads(Path(window).read_text())
        begin, finish = specification['start'], specification['end']
        if begin['session_id'] != finish['session_id']:
            raise ValueError('An active recording window must remain within one session')
        def boundary_matches(frame, boundary):
            return (frame.get('session_id') == boundary['session_id'] and
                    frame.get('capture_id') in boundary['capture_ids'])
        starts = [frame for frame in frame_records if boundary_matches(frame, begin)]
        ends = [frame for frame in frame_records if boundary_matches(frame, finish)]
        if not starts or not ends or starts[0]['frame_index'] > ends[-1]['frame_index']:
            report['active_window'] = {'passed': False, 'reason': 'missing_predeclared_anchor',
                                       'specification': specification}
        else:
            first, last = starts[0], ends[-1]
            inside = frame_records[first['frame_index']:last['frame_index']+1]
            active_counts = Counter(frame['status'] for frame in inside)
            outside_counts = counts - active_counts
            duration = last['time_s'] - first['time_s'] if last['time_s'] is not None and first['time_s'] is not None else 0.
            minimum = float(specification.get('minimum_duration_s', 8.))
            report['active_window'] = {
                'passed': active_counts['matched'] == len(inside) and duration >= minimum and integrity_passed,
                'specification': specification, 'first_frame': first, 'last_frame': last,
                'duration_s': round(duration, 6), 'minimum_duration_s': minimum,
                'decoded_frames': len(inside),
                'counts': {name: active_counts[name] for name in categories},
                'outside_window_frames': frame_index - len(inside),
                'outside_window_counts': {name: outside_counts[name] for name in categories},
                'outside_window_scope': 'All frames are still classified; no paired-panel acceptance claim before or after the predeclared active interval.',
            }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('observations', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frames', type=Path)
    parser.add_argument('--window', type=Path, help='Predeclared browser capture-ID anchor candidates and minimum duration')
    args = parser.parse_args()
    report = validate_recording(args.video, args.observations, frames_output=args.frames, window=args.window)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('decoded_frames', 'counts', 'strict_full_frame_passed', 'active_window') if key in report}))
    accepted = report['active_window']['passed'] if args.window else report['strict_full_frame_passed']
    return 0 if accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
