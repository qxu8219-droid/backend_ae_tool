"""Deterministic normalization of declared source channels, without replay resampling."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from replay_protocol import CANONICAL_JOINTS, ChannelQuality, ChannelSeries, ReplayData, TimeBasis
from app.schemas import ChannelDescriptor, JointMetadata, ParseIssue, UploadResult, UploadStats
from .base import AdapterError, LimitError, ParseLimits, RawColumn, RawTable
from .mapping import JointOverride, MappingConfig, PRISMATIC, parse_mapping

_TIME_FACTORS = {unit: Decimal(factor) for unit, factor in {'s':'1','ms':'0.001','us':'0.000001','ns':'0.000000001'}.items()}
_FACTORS = {'rad':1.0, 'deg':math.pi/180, 'm':1.0, 'mm':0.001, 'cm':0.01}


def _robot():
    path = Path(__file__).resolve().parents[1] / 'assets/robots/r1pro_2026_joints.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    digest = raw.pop('source_sha256')
    return JointMetadata.model_validate(raw), digest


def _digest(path: Path, limits: ParseLimits):
    hasher = hashlib.sha256()
    size = 0
    with path.open('rb') as source:
        while chunk := source.read(65536):
            size += len(chunk)
            if size > limits.max_file_bytes:
                raise LimitError('Source file exceeds max_file_bytes')
            hasher.update(chunk)
    return hasher.hexdigest()


def _decimal(value):
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def normalize_table(table: RawTable, path: Path, config: MappingConfig, *, limits: ParseLimits, parser: str) -> ReplayData:
    config = parse_mapping(config)
    robot, limits_digest = _robot()
    source_hash = _digest(Path(path), limits)
    issues: list[ParseIssue] = []

    def issue(code, message, *, severity='warning', channel=None, line=None, details=None):
        if len(issues) >= limits.max_issues:
            raise LimitError('Issue budget exhausted; reduce malformed input or raise max_issues', issues)
        value = ParseIssue(code=code, message=message, severity=severity, channel=channel, line=line, details=details or {})
        issues.append(value)
        return value

    def fatal(code, message, channel=None, line=None):
        raise AdapterError(code, message, [issue(code, message, severity='error', channel=channel, line=line)])

    if len(table.columns) > limits.max_channels:
        raise LimitError('Source column count exceeds max_channels')
    for name, column in table.columns.items():
        if len(column.values) > limits.max_samples:
            raise LimitError(f'Source sample count exceeds max_samples: {name}')
        if column.timestamps is not None and len(column.timestamps) != len(column.values):
            fatal('COLUMN_LENGTH_MISMATCH', 'Independent timestamps and values differ in length', name)
        if column.lines is not None and len(column.lines) != len(column.values):
            fatal('COLUMN_LENGTH_MISMATCH', 'Source row trace and values differ in length', name)
    reference = table.columns.get(config.timestamp.field)
    if reference is None or not reference.values:
        fatal('NO_TIMESTAMP', 'No reference timestamp column was found', config.timestamp.field)
    if reference.timestamps is not None:
        fatal('INVALID_TIMESTAMP', 'The reference timestamp column must be a plain value array', config.timestamp.field)
    factor = _TIME_FACTORS[config.timestamp.unit]
    offset = Decimal(str(config.timestamp.start_offset))
    origin: Decimal | None = None

    def row_lines(column):
        return column.lines if column.lines is not None else list(range(1, len(column.values)+1))

    def clock(values, lines, name, *, domain=None):
        nonlocal origin
        kept = []
        previous: Decimal | None = None
        previous_relative = None
        # 50 digits preserve ns epochs and subtraction before a bounded float conversion.
        with localcontext() as context:
            context.prec = 50
            for index, value in enumerate(values):
                raw = _decimal(value)
                try:
                    seconds = raw * factor if raw is not None else None
                    seconds_float = float(seconds) if seconds is not None else float('nan')
                except (ArithmeticError, ValueError):
                    seconds = None
                    seconds_float = float('nan')
                if seconds is None or not math.isfinite(seconds_float):
                    issue('INVALID_TIMESTAMP', 'Dropped invalid or non-finite timestamp', channel=name, line=lines[index])
                    continue
                if previous is not None and seconds <= previous:
                    issue('TIMESTAMP_NOT_MONOTONIC', 'Dropped duplicate or backward timestamp in input order', channel=name, line=lines[index])
                    continue
                if origin is None:
                    origin = seconds
                relative = float(seconds - origin + offset)
                if not math.isfinite(relative):
                    issue('INVALID_TIMESTAMP', 'Relative timestamp is not representable', channel=name, line=lines[index])
                    continue
                if domain is not None and not domain[0] <= relative <= domain[1]:
                    issue('TIMESTAMP_OUTSIDE_REFERENCE', 'Dropped channel timestamp outside reference clock domain', channel=name, line=lines[index])
                    continue
                if previous_relative is not None and relative <= previous_relative:
                    issue('TIMESTAMP_PRECISION_LOSS', 'Dropped timestamp indistinguishable in normalized seconds', channel=name, line=lines[index])
                    continue
                previous, previous_relative = seconds, relative
                kept.append((relative, index))
        return kept

    reference_rows = clock(reference.values, row_lines(reference), config.timestamp.field)
    if not reference_rows:
        fatal('NO_TIMESTAMP', 'No finite increasing timestamps remain', config.timestamp.field)
    reference_times = [time for time, _ in reference_rows]
    domain = reference_times[0], reference_times[-1]
    source_rows: dict[str, list[int]] = {config.timestamp.field: row_lines(reference)}
    conversions: dict[str, dict] = {}
    composite_sources: dict[str, list[dict[str, list[int]]]] = {}
    channels: dict[str, ChannelSeries] = {}
    descriptor_specs: dict[str, dict] = {}
    allocated_points = 0

    def reserve(count):
        nonlocal allocated_points
        allocated_points += count
        if allocated_points > limits.max_points:
            raise LimitError('Normalized channel samples exceed max_points')

    def number(value, name, line):
        if value is None or isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            fatal('FIELD_NOT_NUMERIC', 'Mapped value must be numeric, not a boolean or container', name, line)
        try:
            result = float(value)
        except (ValueError, TypeError, OverflowError):
            fatal('FIELD_NOT_NUMERIC', 'Mapped value is not a numeric token', name, line)
        return result if math.isfinite(result) else None

    def series(name, field, unit, source_unit, semantics, origin_kind, scale=1.0, affine_offset=0.0):
        column = table.columns.get(field)
        if column is None:
            return None
        lines = row_lines(column)
        if column.timestamps is None:
            if len(column.values) != len(reference.values):
                fatal('COLUMN_LENGTH_MISMATCH', 'Shared-clock values must match the reference length', name)
            rows = reference_rows
        else:
            rows = clock(column.timestamps, lines, name, domain=domain)
        source_rows[name] = lines
        if not rows:
            issue('ALL_VALUES_INVALID', 'No channel samples remain within the reference domain', channel=name)
            return None
        reserve(len(rows))
        # Custom channel units are declared output units. Only canonical robot
        # channels request a different SI output unit from their source unit.
        unit_factor = _FACTORS.get(source_unit, 1.0) if unit != source_unit else 1.0
        converted = unit_factor != 1 or scale != 1 or affine_offset != 0
        operations = ['original'] + (['unit_converted'] if converted else [])
        conversions[name] = {'field':field, 'source_unit':source_unit, 'unit':unit, 'unit_factor':unit_factor,
                             'scale':scale, 'offset_output_unit':affine_offset, 'offset_unit':unit, 'origin':origin_kind,
                             'order':'source * unit_factor * scale + offset_output_unit'}
        if converted:
            issue('UNIT_CONVERTED', 'Applied declared unit/scale/offset transform', severity='info', channel=name,
                  details=conversions[name])
        values, qualities, times = [], [], []
        last_value = None
        last_time = None
        last_index = None
        for time, index in rows:
            value = number(column.values[index], name, lines[index])
            processing = list(operations)
            indices = [index]
            valid = value is not None
            if valid:
                value = value * unit_factor * scale + affine_offset
                if not math.isfinite(value):
                    fatal('NUMERIC_OVERFLOW', 'Unit/scale/offset transform overflowed', name, lines[index])
                last_value, last_time, last_index = value, time, index
            else:
                gap = time - last_time if last_time is not None else None
                can_fill = last_time is not None and config.options.max_forward_fill_s > 0 and gap <= config.options.max_forward_fill_s + 1e-12
                if can_fill:
                    value, valid, indices = last_value, True, [last_index]
                    processing = [item for item in operations if item != 'original'] + ['forward_filled']
                    issue('VALUE_FORWARD_FILLED', 'Filled from the last finite source sample within the configured gap', channel=name, line=lines[index], details={'source_index':last_index, 'gap_s':gap})
                else:
                    issue('VALUE_UNAVAILABLE', 'Leading invalid value or gap beyond forward-fill budget remains null', channel=name, line=lines[index])
            times.append(time)
            values.append(value)
            qualities.append(ChannelQuality(origin=origin_kind, valid=valid, processing=processing, source_indices=indices))
        if not any(q.valid for q in qualities):
            issue('ALL_VALUES_INVALID', 'Mapped column has no valid values; it is unavailable', channel=name)
        return ChannelSeries(timestamps_s=times, values=values, quality=qualities, source_field=field, unit=unit,
                             angle_semantics=semantics, wrap_period=2*math.pi if semantics == 'angular_wrapped' else None)

    absent = []
    for index, descriptor in enumerate(robot.joints):
        name = descriptor.name
        override = config.joints.per_joint_overrides.get(name, JointOverride())
        source_unit = override.unit or ('m' if name in PRISMATIC else config.joints.unit)
        field = config.joints.map.get(name, '')
        semantics = override.angle_semantics or ('linear' if name in PRISMATIC else 'angular_unwrapped' if descriptor.type == 'continuous' else 'bounded')
        result = series(name, field, descriptor.unit, source_unit, semantics, override.origin or config.joints.origin, override.scale, override.offset) if field else None
        if result is None:
            absent.append(name)
            issue('MISSING_JOINT', 'Missing joint source; replay default is zero with invalid quality', channel=name)
        else:
            channels[name] = result
        descriptor_specs[name] = {'name':name, 'label':name, 'kind':'joint', 'source_field':field or None,
                                  'source_unit':source_unit, 'joint_index':index, 'lower':descriptor.lower, 'upper':descriptor.upper,
                                  'unit':descriptor.unit, 'angle_semantics':semantics,
                                  'wrap_period':2*math.pi if semantics == 'angular_wrapped' else None}
    if not any(any(q.valid for q in ch.quality) for ch in channels.values()):
        fatal('NO_JOINT_CHANNELS', 'At least one canonical joint must have a valid source value')

    for name, custom in config.custom_channels.items():
        result = series(name, custom.field, custom.unit, custom.unit, 'linear', custom.origin, custom.scale, custom.offset)
        if result is None:
            issue('CUSTOM_CHANNEL_MISSING', 'Custom source channel is absent', channel=name)
            continue
        channels[name] = result
        descriptor_specs[name] = {'name':name,'label':name,'kind':'custom','source_field':custom.field,
                                  'source_unit':custom.unit,'unit':custom.unit,'angle_semantics':'linear'}

    chassis_available = False
    chassis_fields = []
    if config.chassis.available and all(field in table.columns for field in config.chassis.map.values()):
        cs = config.chassis
        axes = {axis:series(f'chassis.{axis}', cs.map[axis], 'rad' if axis == 'yaw' else 'm', cs.yaw_unit if axis == 'yaw' else cs.position_unit,
                           cs.yaw_semantics if axis == 'yaw' else 'linear', cs.origin) for axis in ('x','y','yaw')}
        if all(value is not None for value in axes.values()):
            if axes['x'].timestamps_s != axes['y'].timestamps_s or axes['x'].timestamps_s != axes['yaw'].timestamps_s:
                fatal('INCOMPATIBLE_CHASSIS_GRID', 'Chassis axis grids differ; explicit alignment belongs to the sampler')
            values = {axis:[] for axis in axes}
            qualities = {axis:[] for axis in axes}
            for axis in axes:
                composite_sources[f'chassis.{axis}'] = []
            transform = cs.transform
            rotation = transform.rotation_rad
            transformed = cs.frame != 'world' or any((rotation, transform.translation_x_m, transform.translation_y_m))
            for i in range(len(axes['x'].timestamps_s)):
                valid = all(axes[axis].quality[i].valid for axis in axes)
                xy_yaw = [axes[axis].values[i] for axis in ('x','y','yaw')]
                if valid:
                    x, y, yaw = xy_yaw
                    xy_yaw = [math.cos(rotation)*x-math.sin(rotation)*y+transform.translation_x_m,
                              math.sin(rotation)*x+math.cos(rotation)*y+transform.translation_y_m, yaw+rotation]
                    if not all(math.isfinite(v) for v in xy_yaw):
                        fatal('NUMERIC_OVERFLOW', 'Chassis transform overflowed')
                    chassis_available = True
                for axis, value in zip(('x','y','yaw'), xy_yaw):
                    q = axes[axis].quality[i]
                    contributors = ('x','y') if axis != 'yaw' and transformed else (axis,)
                    processing = list(dict.fromkeys(p for contributor in contributors for p in axes[contributor].quality[i].processing))
                    if transformed:
                        processing = [p for p in processing if p != 'original'] + ['derived']
                    # Planar x/y depend on both axes; preserve all contributing raw indices.
                    trace = {cs.map[a]:list(axes[a].quality[i].source_indices) for a in contributors}
                    composite_sources[f'chassis.{axis}'].append(trace)
                    indices = sorted({j for a in contributors for j in axes[a].quality[i].source_indices})
                    if any(p in processing for p in ('interpolated','forward_filled','defaulted','derived')):
                        processing = [p for p in processing if p != 'original']
                    qualities[axis].append(ChannelQuality(origin='derived' if transformed else q.origin, valid=valid,
                        processing=list(dict.fromkeys(processing)), source_indices=indices,
                        method='planar_rigid_transform.v1' if transformed else q.method))
                    values[axis].append(value if valid else None)
            for axis in axes:
                name = f'chassis.{axis}'
                original = axes[axis]
                channels[name] = ChannelSeries(timestamps_s=original.timestamps_s, values=values[axis], quality=qualities[axis],
                    source_field=original.source_field, unit=original.unit, angle_semantics=original.angle_semantics, wrap_period=original.wrap_period)
                descriptor_specs[name] = {'name':name,'label':name,'kind':'chassis','source_field':original.source_field,
                    'source_unit':cs.yaw_unit if axis=='yaw' else cs.position_unit,'unit':original.unit,
                    'angle_semantics':original.angle_semantics,'wrap_period':original.wrap_period}
            chassis_fields = [cs.map[axis] for axis in ('x','y','yaw')]
    if not chassis_available:
        issue('CHASSIS_MISSING', 'No complete available chassis source; no world pose is invented')

    sample_times = sorted(set(reference_times).union(*(ch.timestamps_s for ch in channels.values())))
    if len(sample_times) > limits.max_samples:
        raise LimitError('Union of original sample timestamps exceeds max_samples')
    for name in absent:
        reserve(len(sample_times))
        spec = descriptor_specs[name]
        channels[name] = ChannelSeries(timestamps_s=list(sample_times), values=[0.0]*len(sample_times),
            quality=[ChannelQuality(origin='missing', valid=False, processing=['defaulted']) for _ in sample_times],
            source_field=spec['source_field'], unit=spec['unit'], angle_semantics=spec['angle_semantics'],
            wrap_period=spec['wrap_period'])
        source_rows[name] = []
    if len(channels) > limits.max_channels:
        raise LimitError('Normalized channel count exceeds max_channels')
    mapped = [name for name in CANONICAL_JOINTS if any(q.valid for q in channels[name].quality)]
    missing = [name for name in CANONICAL_JOINTS if name not in mapped]
    deltas = [b-a for a,b in zip(reference_times,reference_times[1:])]
    median_interval = median(deltas) if deltas else None
    nominal = 1/median_interval if median_interval is not None else None
    if nominal is not None and not math.isfinite(nominal):
        fatal('INVALID_TIMESTAMP', 'Sample interval is too small to represent a rate')
    if deltas and any(abs(delta-median_interval) > max(1e-9,median_interval*0.1) for delta in deltas):
        issue('RATE_UNEVEN', 'Reference sampling intervals are irregular; original times were retained')
    stats = UploadStats(sample_count=len(sample_times), duration_s=sample_times[-1]-sample_times[0], nominal_rate_hz=nominal,
        joints_mapped=mapped, joints_missing=missing, chassis={'available':chassis_available, 'frame':'world' if chassis_available else None, 'source_channels':chassis_fields})
    descriptors = []
    for name, ch in channels.items():
        # Descriptor quality summarizes the entire channel; current values use per-sample quality.
        valid = any(q.valid for q in ch.quality)
        origin_kind = next((q.origin for q in ch.quality if q.valid), 'missing')
        processing = list(dict.fromkeys(p for q in ch.quality for p in q.processing))
        if any(p in processing for p in ('interpolated','forward_filled','defaulted','derived')):
            processing = [p for p in processing if p != 'original']
        quality = ChannelQuality(origin=origin_kind, valid=valid, processing=processing,
            method=next((q.method for q in ch.quality if q.method), None) if origin_kind=='derived' else None)
        descriptors.append(ChannelDescriptor(**descriptor_specs[name], quality=quality))
    snapshot = config.model_dump(mode='json')
    mapping_hash = hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return ReplayData(log_id='log-'+hashlib.sha256((source_hash+mapping_hash).encode()).hexdigest()[:32], robot_id=robot.robot_id,
        raw_file_ref='unarchived/'+source_hash, raw_file_sha256=source_hash, mapping_snapshot_id='mapping-'+mapping_hash,
        mapping_snapshot=snapshot, time_basis=TimeBasis(source_origin=float(origin), relative_origin_s=config.timestamp.start_offset),
        canonical_order=list(CANONICAL_JOINTS), sample_times_s=sample_times, channels=channels,
        metadata={'parser':parser,'source_layout':table.layout,'issues':[i.model_dump(mode='json') for i in issues],
            'stats':stats.model_dump(mode='json'),'channel_descriptors':[d.model_dump(mode='json') for d in descriptors],
            'source_rows':source_rows,'conversion_steps':conversions,'composite_sources':composite_sources,'source_timestamp_unit':config.timestamp.unit,
            'source_origin_exact_s':str(origin),'limits_source':robot.limits_source,'limits_source_sha256':limits_digest,
            'sampling':'original; no resampling or virtual wheel derivation','source_origin_policy':'origin is declared by mapping, not inferred from file names'})


def upload_result(data: ReplayData) -> UploadResult:
    try:
        return UploadResult(log_id=data.log_id, parser=data.metadata['parser'], stats=UploadStats.model_validate(data.metadata['stats']),
            issues=[ParseIssue.model_validate(issue) for issue in data.metadata['issues']], mapping_snapshot_id=data.mapping_snapshot_id,
            mapping_snapshot=data.mapping_snapshot, raw_file_ref=data.raw_file_ref, raw_file_sha256=data.raw_file_sha256,
            channels=[ChannelDescriptor.model_validate(channel) for channel in data.metadata['channel_descriptors']])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise AdapterError('INVALID_ADAPTER_OUTPUT', 'Adapter result lacks valid importer metadata') from exc
