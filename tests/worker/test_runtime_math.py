import math
import pytest
from sim_worker.runtime_62 import quat_error,orientation


def test_readback_orientation_ignores_float32_quaternion_norm_roundoff():
    assert quat_error([.9999999,0,0,0],[1,0,0,0])<1e-12
    assert quat_error([-1,0,0,0],[1,0,0,0])==0
    assert quat_error([math.cos(.05),0,0,math.sin(.05)],[1,0,0,0])==pytest.approx(.1)
    with pytest.raises(ValueError):quat_error([0,0,0,0],[1,0,0,0])
