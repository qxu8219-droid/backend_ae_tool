"""Normalize B041 recordings without upgrading hypotheses into measurements."""
from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from pydantic import ValidationError

from app.adapters.base import AdapterError, LimitError, ParseLimits, RawColumn, RawTable
from app.adapters.mapping import MappingConfig, parse_mapping
from app.adapters.normalize import normalize_table
from replay_protocol import CANONICAL_JOINTS, ChannelQuality, ReplayData
from app.schemas.rest import LogAnnotations
from .annotations import annotation_payload
from .profile import B041Profile
from .reader import read_recording
from .transforms import gripper, integrate_pose

RAW_NAMES = tuple([f'left_arm_joint{i}' for i in range(1, 8)] + [f'right_arm_joint{i}' for i in range(1, 8)] +
                  ['left_gripper', 'right_gripper', 'base_vx', 'base_vy', 'base_wz'] + [f'torso_joint{i}' for i in range(1, 5)])
GROUPS = (0,) * 7 + (1,) * 7 + (2, 3, 4, 4, 4) + (5,) * 4
JOINT_INDEX = {**{f'left_arm_joint{i + 1}': i for i in range(7)}, **{f'right_arm_joint{i + 1}': i + 7 for i in range(7)},
               **{f'torso_joint{i + 1}': i + 19 for i in range(4)}}


def source_valid(recording, record, group, basis, profile):
    quality = getattr(record, basis + '_quality')[group]
    if not quality.valid: return False
    if recording.source.format == 'runtime_events': return quality.timing == 'event_monotonic_only'
    if quality.timing != 'source_log_time_ns' or quality.source_time is None or quality.age_s is None:
        return False
    try:
        timestamp = Decimal(quality.source_time); time = Decimal(record.time)
        return (timestamp.is_finite() and 0 <= timestamp <= time and
                0 <= quality.age_s <= profile.max_source_age_s and
                abs(float((time - timestamp) / Decimal(1000000000)) - quality.age_s) <= 1e-6)
    except (InvalidOperation, ValueError, OverflowError): return False


class B041Adapter:
    def __init__(self, limits=ParseLimits()): self.limits = limits
    def probe(self, path): return 1. if Path(path).suffix.lower() == '.b041' else 0.

    def load(self, path, config=None):
        requested = parse_mapping(config); profile = requested.b041 or B041Profile()
        recording = read_recording(path, self.limits)
        records = [record for record in recording.samples if getattr(record, profile.basis) is not None]
        if not records: raise AdapterError('NO_B041_BASIS', 'Recording has no samples for the selected state/action basis')
        factor = Decimal('0.000000001') if recording.source.time_unit == 'ns' else Decimal(1)
        origin = Decimal(records[0].time)
        with localcontext() as context:
            context.prec = 50
            times = [float((Decimal(record.time) - origin) * factor) for record in records]
        columns = {'timestamp': RawColumn([record.time for record in records], lines=[record.row for record in records])}
        contexts = {}; raw_custom = {}; notes = []
        def note(code, message, details=None, severity='warning'):
            notes.append({'severity': severity, 'code': code, 'message': message, 'line': None, 'channel': None, 'details': details or {}})
        def add(name, values, selected, basis, group, method=None):
            columns[name] = RawColumn(values, timestamps=[record.time for record in selected], lines=[record.row for record in selected])
            contexts[name] = (selected, basis, group, method)

        for name, index in JOINT_INDEX.items():
            add(name, [getattr(record, profile.basis)[index] for record in records], records, profile.basis, GROUPS[index])
        for side, index in (('left', 14), ('right', 15)):
            calibration = getattr(profile, side + '_gripper')
            values = [gripper(getattr(record, profile.basis)[index], calibration) for record in records]
            outside = [record.row for record in records if (value := getattr(record, profile.basis)[index]) is not None and not calibration.native_closed <= value <= calibration.native_open]
            if outside: note('B041_GRIPPER_RANGE', 'Native gripper outside declared range; values preserved without clipping', {'side': side, 'count': len(outside), 'first_rows': outside[:20]})
            for finger in (1, 2):
                add(f'{side}_gripper_finger_joint{finger}', [value[finger - 1] for value in values], records, profile.basis, GROUPS[index], f'b041.{profile.status}.ee2_affine.v1')

        low, high = Decimal(records[0].time), Decimal(records[-1].time)
        for basis in ('state', 'action'):
            available = [record for record in recording.samples if getattr(record, basis) is not None]
            selected = [record for record in available if low <= Decimal(record.time) <= high]
            if len(selected) != len(available): note('B041_OTHER_BASIS_OUTSIDE_DOMAIN', 'Other-basis samples outside selected replay span remain in the source bundle', {'basis': basis, 'count': len(available) - len(selected)})
            if not selected: continue
            for index, raw_name in enumerate(RAW_NAMES):
                name = f'raw.{basis}.{raw_name}'
                unit = 'native_opening' if index in (14, 15) else 'm/s' if index in (16, 17) else 'rad/s' if index == 18 else 'rad'
                raw_custom[name] = {'field': name, 'unit': unit, 'origin': 'measured' if basis == 'state' else 'commanded'}
                add(name, [getattr(record, basis)[index] for record in selected], selected, basis, GROUPS[index])

        positions = pose_valid = None
        can_integrate = recording.source.format == 'full23_parquet' and profile.chassis.mode != 'unavailable' and recording.source.base_locked is not True
        if can_integrate:
            velocities = [list(record.state[16:19]) if record.state is not None else [None] * 3 for record in records]
            valid = [record.state is not None and all(value is not None for value in velocity) and source_valid(recording, record, 4, 'state', profile) for record, velocity in zip(records, velocities)]
            positions, pose_valid = integrate_pose(times, velocities, valid, profile.chassis)
            for index, axis in enumerate(('x', 'y', 'yaw')):
                columns['pose_' + axis] = RawColumn([pose[index] for pose in positions], timestamps=[record.time for record in records], lines=[record.row for record in records])
            if not all(pose_valid): note('B041_CHASSIS_INTEGRATION_STOPPED', 'Invalid, stale or gapped input stops cumulative pose validity without reanchoring', {'invalid_samples': pose_valid.count(False)})
        else:
            note('B041_CHASSIS_UNAVAILABLE', 'No reliable world pose: keep scene placement; raw velocities are not positions', {'format': recording.source.format, 'base_locked': recording.source.base_locked})
        assumptions = [
            '夹爪暂按EE2总行程、对称分配及共同零位换算；两指不是独立实测。' if profile.status == 'provisional' else '夹爪按所选标定profile换算；两指仍为标量推导。',
            '六个底盘关节没有可确认的角度通道，保持缺失；不推测原始3/6槽位。',
        ]
        if can_integrate:
            assumptions.append(f'底盘按{profile.chassis.velocity_frame}速度从指定本地原点积分，仅为相对推算轨迹，不是实测world/odom位姿。')
        else: assumptions.append('底盘固定场景放置不代表实测静止；仅保留原始速度供检查。')
        if recording.source.format == 'runtime_events':
            assumptions.append('在线反馈为稀疏事件快照，时间是日志事件时刻而非精确传感器采集时间；不扩展模型预测。')
        note('B041_MAPPING_ASSUMPTIONS', 'Explicit B041 mapping assumptions apply', {'profile_id': profile.profile_id, 'status': profile.status, 'assumptions': assumptions})
        if profile.basis == 'action': note('B041_COMMAND_NOT_FEEDBACK', 'Recorded command basis is not hardware execution or feedback; source control layers may differ')
        effective = requested.model_dump(mode='python')
        effective.update(adapter='b041_bundle_v1', b041=profile.model_dump(), timestamp={'field': 'timestamp', 'unit': recording.source.time_unit, 'start_offset': 0.},
            joints={'origin': 'measured' if profile.basis == 'state' else 'commanded'},
            chassis={'available': can_integrate, 'map': {'x': 'pose_x', 'y': 'pose_y', 'yaw': 'pose_yaw'}, 'yaw_semantics': 'angular_unwrapped'},
            custom_channels=raw_custom, options={'max_forward_fill_s': 0., 'resample': 'none', 'unwrap_continuous': False})
        mapping = MappingConfig.model_validate(effective)
        data = normalize_table(RawTable(columns, 'b041.recording.v1'), Path(path), mapping, limits=self.limits, parser='b041_bundle_v1')
        payload = data.model_dump(mode='json'); channels = payload['channels']
        counters = Counter()
        for name, (selected, basis, group, method) in contexts.items():
            if name not in channels: continue
            channel = channels[name]; first_source = {}; previous_source = None
            for position, old in enumerate(channel['quality']):
                index = old['source_indices'][0]; record = selected[index]
                source_quality = getattr(record, basis + '_quality')[group]
                valid = bool(old['valid'] and source_valid(recording, record, group, basis, profile))
                processing = list(old['processing']); source_indices = [index]
                quality_method = method
                if source_quality.source_time is not None and source_quality.source_time != '-1':
                    try: source_time = Decimal(source_quality.source_time)
                    except InvalidOperation: source_time = None
                    if source_time is not None and source_time.is_finite():
                        if previous_source is not None and source_time < previous_source: valid = False
                        if source_time in first_source:
                            processing = ['forward_filled']; source_indices = [first_source[source_time]]
                            quality_method = method or 'b041.causal_hold.v1'
                        elif valid: first_source[source_time] = index
                        if valid: previous_source = source_time
                if method: processing = list(dict.fromkeys([value for value in processing if value != 'original'] + ['derived']))
                if not valid: counters[f'{basis}.group{group}.invalid_points'] += 1
                channel['quality'][position] = ChannelQuality(origin='derived' if method else 'measured' if basis == 'state' else 'commanded',
                    valid=valid, processing=processing, source_indices=source_indices, method=quality_method).model_dump(mode='json')
            if name in JOINT_INDEX:
                channel['source_field'] = f'{basis}[{JOINT_INDEX[name]}]'
            elif 'gripper_finger' in name:
                index = 14 if name.startswith('left_') else 15
                channel['source_field'] = f'{basis}[{index}]'
        if can_integrate:
            for axis in ('x', 'y', 'yaw'):
                for position, old in enumerate(channels['chassis.' + axis]['quality']):
                    index = old['source_indices'][0]
                    channels['chassis.' + axis]['quality'][position] = ChannelQuality(origin='derived', valid=pose_valid[index], processing=['derived'],
                        source_indices=[index], method=f'b041.{profile.status}.se2_{profile.chassis.velocity_frame}_velocity.v1').model_dump(mode='json')
        metadata = payload['metadata']
        for descriptor in metadata['channel_descriptors']:
            qualities = [ChannelQuality.model_validate(value) for value in channels[descriptor['name']]['quality']]
            chosen = next((quality for quality in qualities if quality.valid), qualities[0])
            operations = list(dict.fromkeys(operation for quality in qualities for operation in quality.processing))
            if any(operation in operations for operation in ('derived', 'forward_filled', 'interpolated', 'defaulted')): operations = [operation for operation in operations if operation != 'original']
            descriptor['quality'] = ChannelQuality(origin=chosen.origin, valid=any(quality.valid for quality in qualities), processing=operations, method=chosen.method).model_dump(mode='json')
            descriptor['source_field'] = channels[descriptor['name']]['source_field']
            if 'gripper_finger' in descriptor['name']:
                descriptor['source_unit'] = 'native_opening'
        mapped = [name for name in CANONICAL_JOINTS if any(quality['valid'] for quality in channels[name]['quality'])]
        if not mapped: raise AdapterError('NO_JOINT_CHANNELS', 'Selected B041 basis has no valid canonical joint observations')
        metadata['stats']['joints_mapped'] = mapped; metadata['stats']['joints_missing'] = [name for name in CANONICAL_JOINTS if name not in mapped]
        metadata['issues'].extend(notes)
        if len(metadata['issues']) > self.limits.max_issues: raise LimitError('B041 diagnostics exceed issue budget')
        metadata['b041'] = {'source': recording.source.model_dump(mode='json'), 'profile': profile.model_dump(mode='json'),
            'source_frame_count': len(recording.samples) if recording.source.format == 'full23_parquet' else None,
            'samples': [record.model_dump(mode='json') for record in recording.samples], 'quality_counts': dict(counters),
            'derivation_provenance': 'Gripper coordinates share one scalar source per hand. Integrated pose at index i depends on the valid prefix 0..i; source_indices name the current endpoint, not an expanded prefix.',
            'input_diagnostics': recording.diagnostics, 'raw_originals_unchanged': 'source hashes are declarations in the uploaded bundle; local prepare tool verifies originals',
            'clock_note': 'source_origin_exact_s and original string clocks retain precision; TimeBasis.source_origin is approximate display seconds'}
        metadata['log_annotations'] = annotation_payload(recording, origin, factor, metadata['stats']['duration_s'], assumptions, profile.status == 'provisional')
        try:
            LogAnnotations.model_validate({**metadata['log_annotations'], 'log_id': data.log_id})
        except ValidationError as exc:
            raise AdapterError('INVALID_B041_ANNOTATIONS', 'Recording annotations violate the bounded API contract') from exc
        metadata['source_origin_policy'] = 'B041 full23 state/action group provenance; provisional transforms always derived'
        return ReplayData.model_validate(payload)
