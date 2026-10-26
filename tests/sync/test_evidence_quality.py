from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'replay'))
from timeline_support import build_sampler, rendered_frame
from app.replay.timeline import Timeline
from replay_protocol import ChannelQuality, RPCResponse, WorkerEvent


def test_rendered_receipt_cannot_replace_source_quality_while_retaining_target_numbers():
    sampler = build_sampler()
    timeline = Timeline('s', lambda _: sampler)
    timeline.load('log')
    load = timeline.take_rpc()
    assert timeline.receive_rpc(RPCResponse(session_id='s', request_id=load.request_id,
        playback_epoch=load.playback_epoch, control_seq=load.control_seq, stage='accepted', ok=True))
    target = timeline.take_rpc().command.frame
    receipt = rendered_frame(target)
    changed = list(receipt.quality)
    changed[6] = ChannelQuality(origin='commanded', valid=True, processing=['original'])
    receipt = receipt.model_copy(update={'quality': tuple(changed)})
    assert not timeline.receive_event(WorkerEvent(session_id='s', seq=1, event={'type': 'rendered', 'frame': receipt}))
    assert timeline.snapshot().rendered_identity is None
