"""Validate inline B041 source clocks before using their full annotation domain.

No recorded file/reference path is opened. The selected replay grid may omit
source frames with no value for its state/action basis, including the last frame.
"""
from decimal import Decimal, InvalidOperation, localcontext
import math


_NS_PER_SECOND = Decimal(1_000_000_000)


def _integer_ns(value, *, seconds=False):
    if not isinstance(value, str) or not 1 <= len(value) <= (128 if seconds else 96):
        raise ValueError('source clocks must be bounded exact strings')
    try:
        number = Decimal(value)
        # Bound exponents before scaling or integer conversion; strings such as
        # 1e999999 must not create enormous integers or float infinities.
        if not number.is_finite() or (number and not (-9 if seconds else 0) <= number.adjusted() <= (86 if seconds else 95)):
            raise ValueError('source clock is not a finite bounded nanosecond value')
        number = number * _NS_PER_SECOND if seconds else number
        if number != number.to_integral_value():
            raise ValueError('source clock must resolve to integer nanoseconds')
        return int(number)
    except InvalidOperation:
        raise ValueError('source clock is invalid') from None


def source_frame_end_s(data):
    """Return the verified complete source's half-open end, or None for legacy data.

    A legacy count alone cannot extend the selected grid's domain. Once source
    clocks are declared, incomplete or incompatible metadata fails closed.
    """
    metadata = data.metadata
    recorded = metadata.get('b041')
    if not isinstance(recorded, dict) or not {'source', 'samples'} & recorded.keys():
        return None
    source, samples = recorded.get('source'), recorded.get('samples')
    count = recorded.get('source_frame_count')
    if not isinstance(source, dict) or (source.get('format'), source.get('time_unit'), source.get('clock')) != (
            'full23_parquet', 'ns', 'source_log_time_ns'):
        raise ValueError('source frames require the declared full23 nanosecond clock')
    if type(count) is not int or not 1 <= count <= 20000 or not isinstance(samples, list) or len(samples) != count:
        raise ValueError('source frame count does not match the bounded complete source')
    annotations = metadata['log_annotations']
    if source.get('id') != annotations.get('source_id') or source.get('sha256') != annotations.get('source_sha256'):
        raise ValueError('annotation identity does not match the declared source')
    with localcontext() as context:
        context.prec = 128
        origin_ns = _integer_ns(metadata.get('source_origin_exact_s'), seconds=True)
        clocks = []
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict) or type(sample.get('frame_index')) is not int or sample['frame_index'] != index:
                raise ValueError('source frames must retain their complete original order')
            clock = _integer_ns(sample.get('time'))
            if clocks and clock <= clocks[-1]:
                raise ValueError('source frame clocks must be strictly increasing')
            clocks.append(clock)
        if origin_ns not in clocks:
            raise ValueError('replay origin must identify an original source frame')
        relative = [float(Decimal(clock - origin_ns) / _NS_PER_SECOND) for clock in clocks]
        if any(not math.isfinite(time) for time in relative) or any(a >= b for a, b in zip(relative, relative[1:])):
            raise ValueError('source frame clocks must remain finite and distinct in replay seconds')
        grid = data.sample_times_s
        source_times = set(relative)
        if not grid or grid[0] != 0 or any(type(time) not in (int, float) or not math.isfinite(time) or time not in source_times for time in grid):
            raise ValueError('selected replay grid must be a subset of the declared source frames')
        if any(a >= b for a, b in zip(grid, grid[1:])):
            raise ValueError('selected replay grid must be strictly increasing')
        terminal_ns = clocks[-1] - clocks[-2] if len(clocks) > 1 else 0
        end = float(Decimal(clocks[-1] + terminal_ns - origin_ns) / _NS_PER_SECOND)
        if not math.isfinite(end):
            raise ValueError('source annotation end must be finite')
        return end
