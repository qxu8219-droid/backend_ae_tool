"""CPU readback and media evidence must retain chassis source provenance."""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from replay_protocol import ChassisPose, SnapshotCatalog

sys.path.insert(0, str(Path(__file__).parent))
from test_engine import setup, request, frame
from test_media import Image
from timeline_support import applied_frame, rendered_frame
from sim_worker.media import SnapshotRegistry
from sim_worker.runtime_62 import Runtime62, orientation


def pose(quality=True):
    qualities=[{'origin':'derived','valid':True,'processing':['derived'],
                'source_indices':[i],'method':f'body_twist.{axis}.v1'}
               for i,axis in enumerate(('x','y','yaw'))]
    return ChassisPose(x=1.,y=2.,yaw=.2,quality=qualities if quality else None)


@pytest.mark.parametrize('stage', ['apply', 'render'])
@pytest.mark.parametrize('change', ['missing', 'measured', 'method', 'unavailable', 'invented'])
def test_worker_rejects_chassis_provenance_substitution(stage, change):
    runtime,engine,replies,events=setup()
    source=frame().model_copy(update={'chassis':pose(quality=change!='invented')})
    changed=source.chassis.model_dump()
    if change=='missing':changed['quality']=None
    elif change in ('measured','invented'):
        changed['quality']=[{'origin':'measured','valid':True,'processing':['original']}]*3
    elif change=='method':changed['quality'][0]['method']='another_method.v1'
    elif change=='unavailable':changed=None
    if stage=='apply':runtime.apply=lambda f:applied_frame(f,chassis=changed)
    else:runtime.render=lambda f:rendered_frame(f,capture_id=1,chassis=changed)
    engine.rpc(b'c',request('load_log',log_id='log'))
    engine.rpc(b'c',request('apply_keyframe',n=2,frame=source));engine.step()
    assert engine.failed and not replies[-1].ok
    assert not any(e.event.type=='rendered' for e in events)


class Vector(list):
    """Minimal numeric-array boundary double; no SDK or NumPy dependency."""
    def copy(self):return Vector(self)
    def tolist(self):return list(self)
    def __sub__(self, other):return Vector(a-b for a,b in zip(self,other))


class Articulation:
    def set_world_pose(self, position, quaternion):self.position=position;self.quaternion=quaternion
    def set_joint_positions(self, values, **kwargs):self.joints=values
    def set_joint_velocities(self, values):pass
    def set_linear_velocity(self, values):pass
    def set_angular_velocity(self, values):pass
    def get_joint_positions(self, **kwargs):return self.joints.copy()
    def get_world_pose(self):
        w,x,y,z=self.quaternion
        yaw=math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
        return Vector([self.position[0]+3e-7,self.position[1]-2e-7,self.position[2]]),Vector(orientation(yaw+1e-7,[1.,0.,0.,0.]))


@pytest.mark.parametrize('kind', ['derived','legacy','unavailable'])
def test_actual_runtime_readback_and_encoded_catalog_preserve_chassis_quality(kind):
    source=frame().model_copy(update={'chassis':None if kind=='unavailable' else pose(quality=kind=='derived')})
    runtime=Runtime62.__new__(Runtime62)
    runtime.np=SimpleNamespace(array=Vector,zeros=lambda n:Vector([0.]*n),
        max=max,abs=lambda v:[abs(x) for x in v],all=all,isfinite=lambda v:[math.isfinite(x) for x in v])
    runtime.initial_pos=Vector([0.,0.,.015]);runtime.initial_quat=Vector([1.,0.,0.,0.])
    runtime.indices=list(range(28));runtime.robot=Articulation()
    runtime.world=SimpleNamespace(step=lambda **kwargs:None)
    applied=runtime.apply(source)
    if kind=='unavailable':assert applied.chassis is None
    else:
        assert (applied.chassis.x,applied.chassis.y,applied.chassis.yaw)==pytest.approx((1.0000003,1.9999998,.2000001),abs=1e-12)
        assert applied.chassis.quality==source.chassis.quality
    # Exercise the actual registry's raw-ID gate and serialized encoder catalog.
    registry=SnapshotRegistry([(i%32,i//32) for i in range(64)],[],clock=lambda:applied.applied_wall_s+1)
    registry.arm(1,applied)
    encoded=registry.prepare_frame({'Attribute':1,'rgb':Image(1),'LdrColor':b'encoded','reference_time':(1,20)})
    snapshot=SnapshotCatalog.model_validate_json(encoded.metadata).snapshots[0]
    assert snapshot.chassis==applied.chassis
    assert registry.rendered(1).chassis==applied.chassis
