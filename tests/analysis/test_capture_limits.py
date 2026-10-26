import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'worker'))
from test_engine import frame
from test_media import Image
from timeline_support import applied_frame
from replay_protocol import ChannelQuality, SnapshotCatalog
from sim_worker.media import SnapshotRegistry


def test_real_snapshot_registry_uses_quality_policy_and_tolerance_before_setting_bits():
    pixels = [(i % 32, i // 32) for i in range(64)]
    registry = SnapshotRegistry(pixels, [{'type': 'revolute', 'lower': -1., 'upper': 1.}] * 3)
    value = frame()
    joints = list(value.joints); joints[:3] = [2., 1.0000005, 2.]
    qualities = list(value.quality)
    qualities[:3] = [ChannelQuality(origin='derived', valid=True, processing=['derived'], method='test.synthetic'),
                     ChannelQuality(origin='measured', valid=True, processing=['original']),
                     ChannelQuality(origin='commanded', valid=True, processing=['original'])]
    value = value.model_copy(update={'joints': tuple(joints), 'quality': tuple(qualities)})
    registry.arm(1, applied_frame(value))
    encoded = registry.prepare_frame({'Attribute': 1, 'rgb': Image(1), 'LdrColor': b'fixture', 'reference_time': (1, 20)})
    snapshot = SnapshotCatalog.model_validate_json(encoded.metadata).snapshots[0]
    assert snapshot.limit_flags == 4
    assert snapshot.limit_warn_flags == 2
