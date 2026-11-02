"""Annotation contracts preserve review provenance and half-open source bounds."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.schemas import rest


def annotation_payload():
    return dict(log_id='log-1', source_id='episode-8', source_sha256='a' * 64,
        alignment='source_frames', provisional=True, assumptions=['temporary travel 0.05 m'],
        reference={}, segments=[dict(id='stage-1', start_s=0., end_s=1., start_frame=0,
            end_frame=2, label='grasp', review_status='pending_review', boundary_status='pending_review',
            training_approved=False, details={'original_label': 'grasp'})],
        events=[dict(id='event-1', time_s=None, kind='request', message='Historical request', details={})])


def test_annotations_keep_half_open_intervals_and_unapproved_provenance():
    model = getattr(rest, 'LogAnnotations', None)
    assert model is not None, 'REST annotation contract must be exported'
    data = model.model_validate(annotation_payload())
    assert data.segments[0].end_frame == 2
    assert data.segments[0].training_approved is False
    assert data.segments[0].review_status == 'pending_review'
    assert data.events[0].time_s is None
    assert data.provisional


@pytest.mark.parametrize('change', [
    {'start_s': 1., 'end_s': 1.}, {'start_s': 2.}, {'start_s': -1.},
    {'end_frame': 0}, {'start_frame': -1}, {'end_frame': 2**53},
    {'training_approved': 'false'}, {'label': 'x' * 4097},
])
def test_annotation_segment_rejects_bad_bounds_and_unbounded_text(change):
    model = getattr(rest, 'LogAnnotations', None)
    assert model is not None, 'REST annotation contract must be exported'
    value = annotation_payload()
    value['segments'][0].update(change)
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize('alignment', ['reference_only', 'none'])
def test_reference_metadata_cannot_claim_current_segments(alignment):
    model = getattr(rest, 'LogAnnotations', None)
    assert model is not None, 'REST annotation contract must be exported'
    value = annotation_payload()
    value['alignment'] = alignment
    with pytest.raises(ValidationError):
        model.model_validate(value)
    value['segments'] = []
    value['reference'] = {'path': '/must/not/be/read', 'review_status': 'pending_review'}
    assert model.model_validate(value).reference == value['reference']


def test_annotations_reject_duplicate_ids_oversized_collections_and_nonfinite_details():
    model = getattr(rest, 'LogAnnotations', None)
    assert model is not None, 'REST annotation contract must be exported'
    for field, invalid in [('segments', annotation_payload()['segments'] * 2),
                           ('events', annotation_payload()['events'] * 2),
                           ('assumptions', ['temporary'] * 129),
                           ('reference', {'bad': float('nan')})]:
        value = deepcopy(annotation_payload()); value[field] = invalid
        with pytest.raises(ValidationError):
            model.model_validate(value)
