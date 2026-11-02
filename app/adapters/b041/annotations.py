from decimal import Decimal, InvalidOperation
from app.adapters.base import AdapterError


def annotation_payload(recording, origin, factor, duration, assumptions, provisional):
    source = recording.source; annotations = recording.annotations
    segments = []
    def bad(): raise AdapterError('INVALID_B041_ANNOTATIONS', 'Annotation source, frame boundary or clock does not match this recording')
    def relative(value):
        try: return float((Decimal(str(value)) - origin) * factor)
        except (InvalidOperation, ValueError): bad()
    if annotations.alignment == 'source_frames':
        samples = recording.samples
        if source.format != 'full23_parquet' or annotations.source_id != source.id: bad()
        if [sample.frame_index for sample in samples] != list(range(len(samples))): bad()
        previous_end = 0; ids = set()
        for item in annotations.segments:
            start, end = item.get('start_frame'), item.get('end_frame')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(samples) or start < previous_end: bad()
            previous_end = end
            if item.get('id') in ids or not isinstance(item.get('id'), str): bad()
            ids.add(item['id'])
            start_raw = Decimal(samples[start].time)
            end_raw = Decimal(samples[end].time) if end < len(samples) else Decimal(samples[-1].time) + (Decimal(samples[-1].time) - Decimal(samples[-2].time) if len(samples) > 1 else Decimal(0))
            try:
                if 'start_log_ns' in item and Decimal(str(item['start_log_ns'])) != start_raw: bad()
                if 'end_log_ns' in item:
                    declared = Decimal(str(item['end_log_ns']))
                    if abs((declared - end_raw) * factor) > Decimal('0.00000001'): bad()
                    end_raw = declared
            except InvalidOperation: bad()
            start_s, end_s = relative(start_raw), relative(end_raw)
            if not 0 <= start_s <= duration or end_s <= start_s: bad()
            segments.append({'id': item['id'], 'start_s': start_s, 'end_s': end_s,
                'start_frame': start, 'end_frame': end, 'label': str(item.get('label') or item.get('subtask') or item.get('skill') or item['id']),
                'review_status': str(item.get('review_status') or 'unknown'), 'boundary_status': str(item.get('boundary_status') or 'unknown'),
                'training_approved': item.get('training_approved'), 'details': item.get('details') or item})
    elif annotations.segments: bad()
    events = []
    for event in recording.events:
        try: time = relative(event.time) if event.time is not None else None
        except AdapterError: time = None
        if time is not None and not 0 <= time <= duration: time = None
        events.append({'id': f'row-{event.row}-{event.kind}', 'time_s': time, 'kind': event.kind,
            'message': str(event.details.get('reason') or event.details.get('text') or event.kind)[:4096],
            'details': {'source_time': event.time, 'source_row': event.row, **event.details}})
    return {'source_id': source.id, 'source_sha256': source.sha256, 'alignment': annotations.alignment,
        'provisional': provisional, 'assumptions': assumptions, 'reference': annotations.reference,
        'segments': segments, 'events': events}
