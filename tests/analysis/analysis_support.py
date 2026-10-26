"""Synthetic observations with literal limits; no hardware or image proof."""
from replay_protocol import CANONICAL_JOINTS, ChannelQuality, ChannelSeries, ReplayData, TargetFrame, AppliedFrame, RenderedFrame
from app.analysis.metadata import MetadataArtifact, load_joint_metadata

NAME = CANONICAL_JOINTS[0]


def metadata():
    artifact = load_joint_metadata()
    raw = artifact.metadata.model_dump()
    raw['joints'][0].update(lower=-1., upper=1.)
    return MetadataArtifact(type(artifact.metadata).model_validate(raw), 'a' * 64)


def replay(values, *, times=None, qualities=None, origin=0., extra=None):
    times = list(times if times is not None else range(len(values)))
    qualities = qualities or [ChannelQuality(origin='measured' if value is not None else 'missing', valid=value is not None,
        processing=['original'] if value is not None else ['defaulted'], source_indices=[i]) for i, value in enumerate(values)]
    channels = {NAME: ChannelSeries(timestamps_s=times, values=values, quality=qualities, unit='rad', angle_semantics='bounded')}
    channels.update(extra or {})
    return ReplayData(log_id='analysis-log', robot_id='galaxea_r1pro_2026', raw_file_ref='fixture.csv', raw_file_sha256='1' * 64,
        mapping_snapshot_id='fixture-map', mapping_snapshot={}, time_basis={'source_origin': 100., 'relative_origin_s': origin},
        canonical_order=list(CANONICAL_JOINTS), sample_times_s=times, channels=channels)


def rendered(t, capture, *, epoch=1, control=1, sid='s', log='analysis-log', duration=10.):
    frame = TargetFrame(session_id=sid, log_id=log, playback_epoch=epoch, control_seq=control, target_seq=capture,
        log_time_s=t, duration_s=duration, progress=t / duration if duration else 0., state='playing', speed=10.,
        joints=[0.] * 28, quality=[{'origin': 'measured', 'valid': True, 'processing': ['original']}] * 28, chassis=None)
    applied = AppliedFrame(**frame.model_dump(), source_joints=frame.joints, applied_wall_s=1000.)
    return RenderedFrame(**applied.model_dump(), capture_id=capture, render_seq=capture,
                         captured_wall_s=1001., raw_identity_verified=True)
