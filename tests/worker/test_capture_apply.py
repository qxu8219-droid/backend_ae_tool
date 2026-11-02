"""One physics/readback refresh per capture, including repeated held targets."""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_chassis_quality import Articulation, Vector, pose
from test_engine import frame
from sim_worker.runtime_62 import Runtime62


def capture_runtime(tmp_path):
    runtime = Runtime62.__new__(Runtime62)
    runtime.output_dir = tmp_path
    runtime.np = SimpleNamespace(array=Vector, zeros=lambda n: Vector([0.] * n),
        max=max, abs=lambda values: [abs(x) for x in values], all=all,
        isfinite=lambda values: [math.isfinite(x) for x in values])
    runtime.initial_pos = Vector([0., 0., .015])
    runtime.initial_quat = Vector([1., 0., 0., 0.])
    runtime.indices = list(range(28))
    steps = []
    runtime.world = SimpleNamespace(step=lambda **kwargs: steps.append(kwargs))
    class Readback(Articulation):
        def get_joint_positions(self, **kwargs):
            result = self.joints.copy()
            result[0] += len(steps) * 1e-7
            return result
    runtime.robot = Readback()
    armed = []
    runtime.writer = SimpleNamespace(arm=lambda capture_id, applied: armed.append((capture_id, applied)), _stream_failed=False)
    runtime.capture_id = 0
    runtime._marker = lambda capture_id: None
    runtime.registry = SimpleNamespace(rendered=lambda capture_id: armed[-1][1])
    runtime.rep = SimpleNamespace(orchestrator=SimpleNamespace(step=lambda **kwargs: None))
    runtime.carb = SimpleNamespace(settings=SimpleNamespace(get_settings=lambda: SimpleNamespace(set=lambda *args: None)))
    return runtime, steps, armed


def test_first_capture_reuses_fresh_verified_readback_and_preserves_quality(tmp_path):
    runtime, steps, armed = capture_runtime(tmp_path)
    source = frame().model_copy(update={'chassis': pose()})
    applied = runtime.apply(source)
    captured = runtime.render(applied)
    assert steps == [{'render': False}]
    assert captured is applied
    assert armed[0][1].joints[0] == pytest.approx(source.joints[0] + 1e-7, abs=1e-12)
    assert captured.quality == source.quality
    assert captured.chassis.quality == source.chassis.quality
    assert captured.source_joints == source.joints


def test_held_capture_refreshes_physics_and_uses_latest_actual_readback(tmp_path):
    runtime, steps, armed = capture_runtime(tmp_path)
    applied = runtime.apply(frame())
    runtime.render(applied)
    captured = runtime.render(applied)
    assert len(steps) == 2
    assert captured is not applied
    assert captured.joints[0] == pytest.approx(applied.joints[0] + 1e-7, abs=1e-12)
    assert [capture_id for capture_id, _ in armed] == [1, 2]


def test_new_target_after_held_capture_needs_only_its_own_apply_step(tmp_path):
    runtime, steps, armed = capture_runtime(tmp_path)
    applied = runtime.apply(frame())
    runtime.render(applied)
    runtime.render(applied)
    next_source = frame(seq=2, t=.5)
    next_applied = runtime.apply(next_source)
    captured = runtime.render(next_applied)
    assert len(steps) == 3
    assert captured is next_applied
    assert captured.target_seq == 2
    assert captured.source_joints == next_source.joints


def test_newest_verified_apply_wins_over_stale_argument_for_same_identity(tmp_path):
    runtime, steps, armed = capture_runtime(tmp_path)
    source = frame()
    stale = runtime.apply(source)
    latest = runtime.apply(source)
    captured = runtime.render(stale)
    assert len(steps) == 2
    assert captured is latest
    assert captured.joints != stale.joints


@pytest.mark.parametrize(('field', 'value'), [
    ('session_id', 'other'), ('log_id', 'other'), ('target_seq', 2),
    ('playback_epoch', 2), ('control_seq', 2),
])
def test_mismatched_capture_identity_does_not_arm_or_consume_fresh_frame(tmp_path, field, value):
    runtime, steps, armed = capture_runtime(tmp_path)
    applied = runtime.apply(frame())
    with pytest.raises(RuntimeError, match='Capture target differs'):
        runtime.render(applied.model_copy(update={field: value}))
    assert not armed
    assert runtime.render(applied) is applied
    assert len(steps) == 1


def test_failed_arm_consumes_fresh_readback_so_retry_refreshes(tmp_path):
    runtime, steps, armed = capture_runtime(tmp_path)
    applied = runtime.apply(frame())
    def fail_arm(*args):
        raise RuntimeError('arm failed')
    runtime.writer.arm = fail_arm
    with pytest.raises(RuntimeError, match='arm failed'):
        runtime.render(applied)
    runtime.writer.arm = lambda capture_id, actual: armed.append((capture_id, actual))
    captured = runtime.render(applied)
    assert len(steps) == 2
    assert captured is not applied


def test_failed_apply_invalidates_previously_cached_readback(tmp_path):
    runtime, steps, armed = capture_runtime(tmp_path)
    source = frame()
    applied = runtime.apply(source)
    original = runtime.robot.get_joint_positions
    runtime.robot.get_joint_positions = lambda **kwargs: Vector([float('nan')] * 28)
    with pytest.raises(RuntimeError, match='Articulation readback mismatch'):
        runtime.apply(source)
    runtime.robot.get_joint_positions = original
    captured = runtime.render(applied)
    assert len(steps) == 3
    assert captured is not applied
    assert captured.joints[0] == pytest.approx(source.joints[0] + 3e-7, abs=1e-12)
