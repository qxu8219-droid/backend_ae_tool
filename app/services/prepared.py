from dataclasses import dataclass
from app.analysis.service import ReplayAnalysis
from app.replay.interpolator import ReplaySampler


@dataclass(frozen=True)
class PreparedLog:
    sampler: ReplaySampler
    analysis: ReplayAnalysis


def prepare_log(data, session_id, metadata, max_gap_s):
    return PreparedLog(ReplaySampler(data, session_id=session_id, max_gap_s=max_gap_s),
                       ReplayAnalysis(data, metadata=metadata, max_gap_s=max_gap_s))
