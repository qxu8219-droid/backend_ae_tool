"""Bounded portable numeric bundle reader. Never follows recorded source paths."""
import json
from pathlib import Path
from pydantic import ValidationError
from app.adapters.base import AdapterError, LimitError
from app.adapters.json_adapter import JSONAdapter
from .models import Recording


def read_recording(path, limits):
    path = Path(path)
    if path.stat().st_size > limits.max_file_bytes: raise LimitError('B041 bundle exceeds byte limit')
    with path.open('rb') as source: body = source.read(limits.max_file_bytes + 1)
    if len(body) > limits.max_file_bytes: raise LimitError('B041 bundle exceeds byte limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(_): raise ValueError('Nonfinite JSON constant')
    try:
        value = json.loads(body.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=invalid)
        JSONAdapter(limits)._check_fields(value)
        pending = [value]; points = 0
        while pending:
            item = pending.pop()
            if isinstance(item, dict): pending.extend(item.values())
            elif isinstance(item, list): pending.extend(item)
            elif isinstance(item, (int, float)):
                points += 1
                if points > limits.max_points: raise LimitError('B041 source numeric points exceed limit')
        data = Recording.model_validate(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError) as exc:
        raise AdapterError('INVALID_B041_BUNDLE', 'Malformed or incompatible B041 recording bundle') from exc
    if len(data.samples) > limits.max_samples or len(data.events) > limits.max_samples:
        raise LimitError('B041 sample/event count exceeds limit')
    return data
