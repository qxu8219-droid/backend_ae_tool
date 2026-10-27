#!/usr/bin/env python3
"""Stream every rendered runtime trace against a narrow independent B041 oracle.

No application adapter, sampler, protocol class, or renderer is imported.
Supported inputs are deliberately limited to provisional full23/ns state data
with all six sensor groups fresh, unique, ordered, and free of source gaps.
The writer's raw_identity_verified flag is checked as runtime testimony only;
this tool cannot independently establish image identity or browser pixels.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter
import cmath
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, label):
    require(type(value) in (int, float) and math.isfinite(value), f'{label}: expected finite number')
    return float(value)


def near(actual, expected, label, tolerance):
    value = number(actual, label)
    require(abs(value - expected) <= tolerance, f'{label}: {value} != {expected} (absolute tolerance {tolerance})')


class B041TraceOracle:
    """Independent numeric slot mapping and complex midpoint/sinc body twist."""
    def __init__(self, bundle, mapping):
        try:
            self._prepare(bundle, mapping)
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as error:
            raise ValueError(f'Unsupported B041 trace-oracle input: {error}') from error

    def _prepare(self, bundle, mapping):
        source, config = bundle['source'], mapping['b041']
        require(bundle['schema'] == 'b041.recording.v1', 'expected b041.recording.v1')
        require(mapping['adapter'] == 'b041_bundle_v1', 'expected b041_bundle_v1 adapter')
        require(source['format'] == 'full23_parquet' and source['time_unit'] == 'ns'
                and source['clock'] == 'source_log_time_ns', 'requires full23_parquet/ns/source_log_time_ns')
        require(source.get('base_locked') in (None, False), 'locked-base recording is outside scope')
        require(config['basis'] == 'state' and config['status'] == 'provisional', 'requires provisional state basis')
        chassis = config['chassis']
        require(chassis['mode'] == 'auto' and chassis['velocity_frame'] == 'body', 'requires auto body velocity profile')
        self.config, self.source_id = config, source['id']
        for key in ('initial_pose', 'velocity_scale', 'velocity_bias'):
            require(len(chassis[key]) == 3, f'chassis.{key} needs three axes')
            for value in chassis[key]: number(value, key)
        for value in (config['max_source_age_s'], config['max_interpolation_gap_s'], chassis['max_gap_s']):
            require(number(value, 'maximum age/gap') > 0, 'maximum age/gap must be positive')
        for side in ('left', 'right'):
            gripper = config[side + '_gripper']
            for key in ('native_closed', 'native_open', 'zero1_m', 'zero2_m', 'travel1_m', 'travel2_m'):
                number(gripper[key], side + '.' + key)
            require(gripper['native_open'] != gripper['native_closed'], 'gripper affine denominator is zero')
        rows = bundle['samples']
        require(len(rows) >= 2, 'requires at least two full state rows')
        self.times, self.states, self.poses = [], [], []
        previous_stamps = [None] * 6
        origin = Decimal(rows[0]['time'])
        require(origin.is_finite() and origin == origin.to_integral_value(), 'source time must be integer nanoseconds')
        for index, row in enumerate(rows):
            timestamp = Decimal(row['time'])
            require(timestamp.is_finite() and timestamp == timestamp.to_integral_value(), 'source time must be integer nanoseconds')
            time = float((timestamp - origin) * Decimal('1e-9'))
            if index:
                dt = time - self.times[-1]
                require(0 < dt <= min(config['max_interpolation_gap_s'], chassis['max_gap_s']) + 1e-12,
                        f'row {index}: nonmonotonic time or unsupported source gap')
            state = row['state']
            require(isinstance(state, list) and len(state) == 23, f'row {index}: requires all 23 state scalars')
            self.states.append([number(value, f'row {index} state') for value in state])
            self.times.append(time)
            qualities = row['state_quality']
            require(len(qualities) == 6, f'row {index}: requires six state quality groups')
            for group, quality in enumerate(qualities):
                require(quality['valid'] is True and quality['timing'] == 'source_log_time_ns',
                        f'row {index} group {group}: invalid/unsupported timing')
                stamp = Decimal(quality['source_time'])
                require(stamp.is_finite() and stamp == stamp.to_integral_value() and 0 <= stamp <= timestamp,
                        f'row {index} group {group}: invalid sensor timestamp')
                age = number(quality['age_s'], 'source age')
                require(0 <= age <= config['max_source_age_s'] and
                        abs(age - float((timestamp-stamp)*Decimal('1e-9'))) <= 1e-6,
                        f'row {index} group {group}: stale/inconsistent source age')
                require(previous_stamps[group] is None or stamp > previous_stamps[group],
                        f'row {index} group {group}: repeated/regressing sensor stamp')
                previous_stamps[group] = stamp
        location = complex(chassis['initial_pose'][0], chassis['initial_pose'][1])
        heading = chassis['initial_pose'][2]
        self.poses.append([location.real, location.imag, heading])
        for index in range(1, len(self.times)):
            dt = self.times[index] - self.times[index-1]
            vx, vy, omega = [value*scale+bias for value, scale, bias in
                             zip(self.states[index-1][16:19], chassis['velocity_scale'], chassis['velocity_bias'])]
            half_turn = omega*dt/2
            sinc = 1. if half_turn == 0 else math.sin(half_turn)/half_turn
            location += complex(vx, vy) * cmath.exp(complex(0, heading+half_turn)) * dt * sinc
            heading += 2*half_turn
            self.poses.append([location.real, location.imag, heading])
        self.duration = self.times[-1]

    def expected(self, time):
        time = number(time, 'log_time_s')
        require(0 <= time <= self.duration, 'log_time_s outside source recording')
        right = bisect_left(self.times, time)
        exact = right < len(self.times) and self.times[right] == time
        left = right if exact else right-1
        alpha = 0. if exact else (time-self.times[left])/(self.times[right]-self.times[left])
        indices = [left] if exact else [left, right]
        raw = [self.states[left][slot]*(1-alpha)+self.states[right][slot]*alpha for slot in range(23)]
        values = [0.] * 28
        for index in range(4): values[6+index] = raw[19+index]
        for index in range(7):
            values[10+index] = raw[index]
            values[19+index] = raw[7+index]
        for side, native, canonical in (('left', 14, 17), ('right', 15, 26)):
            setting = self.config[side+'_gripper']
            # Affine mapping commutes with linear interpolation.
            fraction = (raw[native]-setting['native_closed'])/(setting['native_open']-setting['native_closed'])
            for finger in (1, 2):
                values[canonical+finger-1] = setting[f'zero{finger}_m'] + fraction*setting[f'travel{finger}_m']
        chassis = [self.poses[left][axis]*(1-alpha)+self.poses[right][axis]*alpha for axis in range(3)]
        return {'values': values, 'chassis': chassis, 'indices': indices, 'interpolated': not exact,
                'source_sample_index': right if exact else None}


def quality(actual, *, missing=False, derived=False, method=None, indices=(), interpolated=False, label):
    origin = 'missing' if missing else 'derived' if derived else 'measured'
    operations = ['defaulted'] if missing else (['derived'] if derived else []) + (['interpolated'] if interpolated else [])
    if not operations: operations = ['original']
    require(actual['origin'] == origin and actual['valid'] is (not missing), f'{label}: origin/validity differs')
    require(actual['source_indices'] == list(indices), f'{label}: source_indices differ')
    require(actual['processing'] == operations, f'{label}: processing differs: {actual["processing"]} != {operations}')
    require(actual['method'] == method, f'{label}: method differs')


def check_frame(row, oracle, previous, expected_identity):
    frame, snapshot = row['frame'], row['snapshot']
    identity = (frame['session_id'], frame['log_id'])
    require(all(isinstance(value, str) and value for value in identity), 'empty session/log identity')
    require(expected_identity is None or identity == expected_identity, 'session/log changed within worker trace')
    capture, sequence = frame['capture_id'], frame['render_seq']
    require(type(capture) is int and 0 < capture <= 0xffffffff, 'capture_id must be positive uint32')
    require(type(sequence) is int and sequence == capture, 'render_seq differs from capture_id')
    if previous:
        require(capture > previous['capture_id'] and sequence > previous['render_seq'], 'capture/render identity is not strictly increasing')
        require(number(frame['captured_wall_s'], 'captured_wall_s') >= previous['captured_wall_s'], 'captured wall time regressed')
    require(frame['raw_identity_verified'] is True, 'runtime writer did not attest raw identity')
    number(frame['captured_wall_s'], 'captured_wall_s')
    require(number(frame['applied_wall_s'], 'applied_wall_s') <= frame['captured_wall_s'], 'application occurs after capture')
    expected = oracle.expected(frame['log_time_s'])
    near(frame['duration_s'], oracle.duration, 'duration_s', 1e-8)
    near(frame['progress'], frame['log_time_s']/oracle.duration, 'progress', 1e-8)
    require(frame['source_sample_index'] == expected['source_sample_index'], 'source_sample_index differs')
    for key in ('source_joints', 'joints', 'quality'):
        require(len(frame[key]) == 28, f'{key}: expected 28 joints')
    for index, value in enumerate(expected['values']):
        near(frame['source_joints'][index], value, f'source_joints[{index}]', 1e-8)
        near(frame['joints'][index], value, f'joints[{index}] readback', 1e-5)
        missing, derived = index < 6, index in (17, 18, 26, 27)
        quality(frame['quality'][index], missing=missing, derived=derived,
                method='missing_joint_zero.v1' if missing else 'b041.provisional.ee2_affine.v1' if derived else None,
                indices=[] if missing else expected['indices'], interpolated=expected['interpolated'] and not missing,
                label=f'quality[{index}]')
    pose = frame['chassis']
    require(isinstance(pose, dict) and pose['frame'] == 'world' and len(pose['quality']) == 3, 'missing/invalid chassis world pose')
    for index, axis in enumerate(('x', 'y', 'yaw')):
        want = expected['chassis'][index]
        if axis == 'yaw':
            delta = math.remainder(number(pose[axis], axis)-want, 2*math.pi)
            near(delta, 0., 'chassis.yaw modulo 2pi', 1e-5)
        else:
            near(pose[axis], want, 'chassis.'+axis, 1e-5)
        quality(pose['quality'][index], derived=True, method='b041.provisional.se2_body_velocity.v1',
                indices=expected['indices'], interpolated=expected['interpolated'], label='chassis.quality.'+axis)
    for key in ('session_id', 'log_id', 'capture_id', 'render_seq', 'log_time_s', 'duration_s', 'progress',
                'source_sample_index', 'playback_epoch', 'control_seq', 'target_seq', 'state', 'speed',
                'joints', 'source_joints', 'quality', 'chassis', 'captured_wall_s'):
        require(snapshot[key] == frame[key], f'snapshot.{key} differs from rendered frame')
    return expected, identity


def validate_replay_traces(bundle_path, profile_path, trace_paths, *, failure_limit=25):
    require(type(failure_limit) is int and failure_limit >= 0, 'failure_limit must be nonnegative integer')
    bundle_path, profile_path = Path(bundle_path), Path(profile_path)
    bundle_bytes, profile_bytes = bundle_path.read_bytes(), profile_path.read_bytes()
    oracle = B041TraceOracle(json.loads(bundle_bytes), json.loads(profile_bytes))
    paths = list(dict.fromkeys(Path(path).resolve() for path in trace_paths))
    require(paths, 'No worker traces supplied')
    report = {'schema': 'replay-trace-validation/v1', 'passed': False,
        'scope': 'Every rendered JSONL frame independently checked against fresh, unique, gap-free provisional B041 state/full23/ns/body inputs; runtime trace testimony, not image proof',
        'independent_pixel_verification': False,
        'raw_identity_verified_scope': 'Production writer attestation only; no raw or browser pixel is decoded by this checker',
        'source_id': oracle.source_id, 'source_samples': len(oracle.times), 'duration_s': oracle.duration,
        'inputs': {'bundle': str(bundle_path), 'bundle_sha256': hashlib.sha256(bundle_bytes).hexdigest(),
                   'profile': str(profile_path), 'profile_sha256': hashlib.sha256(profile_bytes).hexdigest()},
        'trace_count': len(paths), 'frames_checked': 0, 'frames_passed': 0, 'frames_failed': 0,
        'counts': {}, 'failures': [], 'failure_limit': failure_limit, 'traces': [],
        'tolerances': {'source_joints_absolute': 1e-8, 'readback_absolute': 1e-5, 'chassis_absolute': 1e-5},
        'limitations': ['Unsupported source validity, repeated sensor stamps, gaps, action basis, world-velocity profiles, and locked base inputs are rejected.',
                       'SE(2) provenance identifies current endpoints; numeric integration independently uses the complete preceding valid source prefix.',
                       'Control intentions, transport arrival, browser presentation, limit flags, and pixels require separate evidence.']}
    counts = Counter()
    def failure(path, line, message):
        if len(report['failures']) < failure_limit:
            report['failures'].append({'trace': str(path), 'line': line, 'message': str(message)[:700]})
    for path in paths:
        item = {'path': str(path), 'frames_checked': 0, 'frames_passed': 0, 'frames_failed': 0, 'first': None, 'last': None, 'errors': []}
        report['traces'].append(item)
        previous, identity, digest = None, None, hashlib.sha256()
        try:
            before = path.stat()
            with path.open('rb') as stream:
                for line, raw in enumerate(stream, 1):
                    digest.update(raw)
                    if not raw.strip():
                        counts['blank_lines'] += 1
                        continue
                    item['frames_checked'] += 1
                    try:
                        row = json.loads(raw)
                        expected, found_identity = check_frame(row, oracle, previous, identity)
                        frame = row['frame']
                        previous = {key: frame[key] for key in ('capture_id', 'render_seq', 'captured_wall_s')}
                        identity = found_identity
                        boundary = {key: frame[key] for key in ('capture_id', 'render_seq', 'log_time_s', 'captured_wall_s', 'session_id', 'log_id')}
                        if item['first'] is None: item['first'] = boundary
                        item['last'] = boundary
                        item['frames_passed'] += 1
                        counts['interpolated_frames' if expected['interpolated'] else 'exact_source_frames'] += 1
                        counts['runtime_raw_identity_attestations'] += 1
                    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as error:
                        item['frames_failed'] += 1
                        failure(path, line, error)
            after = path.stat()
            item['sha256'] = digest.hexdigest()
            item['bytes'] = after.st_size
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                item['errors'].append('Trace changed during analysis; rerun after writer stops')
            if item['frames_checked'] == 0:
                item['errors'].append('Empty worker trace cannot establish runtime coverage')
        except OSError as error:
            item['errors'].append(str(error))
        for error in item['errors']: failure(path, 0, error)
        for key in ('frames_checked', 'frames_passed', 'frames_failed'): report[key] += item[key]
        item['passed'] = item['frames_checked'] > 0 and item['frames_failed'] == 0 and not item['errors']
    report['counts'] = dict(counts)
    report['passed'] = all(item['passed'] for item in report['traces'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--trace', action='append', type=Path, default=[], help='Worker rendered.jsonl; repeatable')
    parser.add_argument('--trace-root', action='append', type=Path, default=[], help='Recursively discover rendered.jsonl under this root; repeatable')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = args.trace + [path for root in args.trace_root for path in sorted(root.rglob('rendered.jsonl'))]
    try:
        report = validate_replay_traces(args.bundle, args.profile, paths)
    except (ValueError, OSError, TypeError, KeyError) as error:
        report = {'schema': 'replay-trace-validation/v1', 'passed': False,
                  'independent_pixel_verification': False, 'input_rejected': str(error)}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps({key: report[key] for key in ('passed', 'trace_count', 'frames_checked', 'frames_passed', 'frames_failed', 'input_rejected') if key in report}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
