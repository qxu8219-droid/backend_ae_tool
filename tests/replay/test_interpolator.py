"""Target sampling checks use literal source values and independent expectations."""
import math
import random

import pytest
from replay_protocol import CANONICAL_JOINTS, ChannelSeries, ReplayData, TargetFrame
from app.replay.base import SamplingError
from app.replay.interpolator import ReplaySampler, sample_at


def series(times, values, *, unit='rad', semantics='bounded', origin='measured', quality=None):
    return ChannelSeries(timestamps_s=times,values=values,unit=unit,angle_semantics=semantics,
        wrap_period=2*math.pi if semantics=='angular_wrapped' else None,
        quality=quality or [{'origin':origin,'valid':value is not None,'processing':['original'],'source_indices':[i]} for i,value in enumerate(values)])


def replay(channels=None, times=None, mapping=None):
    channels=channels or {'torso_joint1':series([0.,1.],[0.,2.])}
    times=times or sorted(set(t for channel in channels.values() for t in channel.timestamps_s))
    return ReplayData(log_id='test-log',robot_id='galaxea_r1pro_2026',raw_file_ref='synthetic.csv',raw_file_sha256='0'*64,
        mapping_snapshot_id='synthetic-mapping',mapping_snapshot=mapping or {},time_basis={'source_origin':1000.,'relative_origin_s':times[0]},
        canonical_order=list(CANONICAL_JOINTS),sample_times_s=times,channels=channels)


def test_irregular_multirate_channels_sample_one_requested_time_with_independent_brackets():
    data=replay({'torso_joint1':series([0.,.4,1.],[0.,4.,10.]),
                 'left_arm_joint1':series([0.,1.],[10.,30.]),
                 'left_gripper_finger_joint1':series([0.,.25,1.],[0.,.02,.05],unit='m',semantics='linear')})
    frame=ReplaySampler(data,session_id='s').sample_at(.5,playback_epoch=2,control_seq=3,target_seq=4)
    assert frame.joints[6]==pytest.approx(5.)
    assert frame.joints[10]==pytest.approx(20.)
    assert frame.joints[17]==pytest.approx(.03)
    assert frame.source_sample_index is None
    assert (frame.session_id,frame.log_id,frame.playback_epoch,frame.control_seq,frame.target_seq)==('s','test-log',2,3,4)
    assert frame.progress==.5
    assert 'interpolated' in frame.quality[6].processing
    assert frame.quality[6].source_indices==(1,2)
    assert TargetFrame.model_validate_json(frame.model_dump_json())==frame


def test_nonzero_source_origin_does_not_shift_playback_positions_or_step_indices():
    sampler=ReplaySampler(replay({'torso_joint1':series([5.,5.5,7.],[0.,1.,4.])}),session_id='s')
    assert sampler.duration_s==2.
    assert sampler.sample_at(.5).joints[6]==1.
    assert sampler.sample_at(.5).source_sample_index==1
    assert sampler.sample_at(-9).log_time_s==0.
    assert sampler.sample_at(99).log_time_s==2.
    assert sampler.frame_time(-100)==0.
    assert sampler.frame_time(100)==2.
    assert sampler.step_time(.4,'forward')==.5
    assert sampler.step_time(.4,'backward')==0.
    assert sampler.step_time(.5,'forward')==2.
    assert sampler.step_time(.5,'backward')==0.
    assert sampler.step_time(0.,'backward')==0.
    assert sampler.step_time(2.,'forward')==2.


def test_missing_and_invalid_values_have_deterministic_invalid_fallback_not_previous_query_pose():
    data=replay({'torso_joint1':series([0.,1.,2.,3.,4.],[None,2.,None,None,8.])})
    sampler=ReplaySampler(data,session_id='s')
    sampler.sample_at(4.)
    for time in [0.,.5]:
        frame=sampler.sample_at(time);assert frame.joints[6]==0.
        assert frame.quality[6].origin=='missing' and not frame.quality[6].valid
    for time in [1.5,2.,2.5,3.,3.5]:
        frame=sampler.sample_at(time);assert frame.joints[6]==2.
        assert not frame.quality[6].valid and 'forward_filled' in frame.quality[6].processing
        assert frame.quality[6].source_indices==(1,)
    assert sampler.sample_at(4.).quality[6].valid
    assert sampler.sample_at(3.).joints[0]==0.
    assert sampler.sample_at(3.).quality[0].origin=='missing'


def test_random_seek_order_replay_and_mutating_caller_data_cannot_change_prepared_targets():
    data=replay({'torso_joint1':series([0.,1.,2.],[0.,3.,-1.])})
    sampler=ReplaySampler(data,session_id='s')
    times=[i/20 for i in range(41)]
    expected={t:sampler.sample_at(t) for t in times}
    data.channels['torso_joint1'].values[0]=999.
    data.sample_times_s.append(999.)
    rng=random.Random(731);rng.shuffle(times)
    for t in times+times[::-1]: assert sampler.sample_at(t)==expected[t]


def test_bounded_large_excursion_and_all_four_signed_grippers_are_not_wrapped_or_clamped():
    names=['left_gripper_finger_joint1','left_gripper_finger_joint2','right_gripper_finger_joint1','right_gripper_finger_joint2']
    channels={name:series([0.,1.],[0.,value],unit='m',semantics='linear') for name,value in zip(names,[.08,-.08,.12,-.12])}
    channels['torso_joint1']=series([0.,1.],[-10.,10.])
    frame=ReplaySampler(replay(channels),session_id='s').sample_at(.5)
    assert [frame.joints[i] for i in [17,18,26,27]]==pytest.approx([.04,-.04,.06,-.06])
    assert frame.joints[6]==0.
    assert ReplaySampler(replay(channels),session_id='s').sample_at(1.).joints[6]==10.


def test_world_chassis_is_sampled_without_second_transform_and_wrapped_yaw_uses_short_arc():
    channels={'torso_joint1':series([0.,1.],[0.,1.]),'chassis.x':series([0.,1.],[2.,4.],unit='m',semantics='linear'),
              'chassis.y':series([0.,1.],[-1.,1.],unit='m',semantics='linear'),
              'chassis.yaw':series([0.,1.],[math.radians(179),math.radians(-179)],semantics='angular_wrapped')}
    mapping={'chassis':{'available':True,'frame':'odom','transform':{'rotation_rad':1.,'translation_x_m':20.}}}
    frame=ReplaySampler(replay(channels,mapping=mapping),session_id='s').sample_at(.5)
    assert frame.chassis is not None
    assert frame.chassis.x==3. and frame.chassis.y==0.
    assert frame.chassis.yaw==pytest.approx(math.pi)
    assert frame.chassis.quality[0].processing==('interpolated',)
    assert frame.chassis.quality[2].origin=='derived'
    channels['chassis.y']=series([0.,1.],[-1.,None],unit='m',semantics='linear')
    assert ReplaySampler(replay(channels,mapping=mapping),session_id='s').sample_at(.5).chassis is None


def test_chassis_derived_quality_reaches_targets_without_becoming_measured():
    channels={}
    for axis,values in [('x',[0.,2.]),('y',[0.,4.]),('yaw',[0.,.4])]:
        qualities=[{'origin':'derived','valid':True,'processing':['derived'],
                    'source_indices':[index],'method':f'body_twist.{axis}.v1'} for index in [0,1]]
        channels[f'chassis.{axis}']=series([0.,1.],values,
            unit='rad' if axis=='yaw' else 'm',semantics='angular_unwrapped' if axis=='yaw' else 'linear',quality=qualities)
    sampler=ReplaySampler(replay(channels),session_id='s')
    first=sampler.sample_at(0.)
    assert [q.method for q in first.chassis.quality]==['body_twist.x.v1','body_twist.y.v1','body_twist.yaw.v1']
    middle=sampler.sample_at(.5)
    assert (middle.chassis.x,middle.chassis.y,middle.chassis.yaw)==(1.,2.,.2)
    assert all(q.origin=='derived' and q.processing==('derived','interpolated') and q.source_indices==(0,1)
               for q in middle.chassis.quality)
    assert all(q.origin=='missing' and not q.valid for q in middle.quality[:6])
    assert TargetFrame.model_validate_json(middle.model_dump_json()).chassis.quality==middle.chassis.quality


def test_accumulated_continuous_angles_are_preserved_and_explicit_wrapped_angle_is_lifted():
    channels={'wheel_motor_joint1':series([0.,1.],[0.,4*math.pi],semantics='angular_unwrapped'),
              'wheel_motor_joint2':series([0.,1.],[math.radians(170),math.radians(-170)],semantics='angular_wrapped')}
    sampler=ReplaySampler(replay(channels),session_id='s')
    assert sampler.sample_at(.75).joints[1]==pytest.approx(3*math.pi)
    assert sampler.sample_at(.5).joints[3]==pytest.approx(math.pi)
    assert sampler.sample_at(1.).joints[3]==pytest.approx(math.radians(190))


def test_single_sample_is_zero_duration_and_optional_gap_budget_does_not_fake_validity():
    sampler=ReplaySampler(replay({'torso_joint1':series([7.],[3.])}),session_id='s')
    frame=sampler.sample_at(999.)
    assert (frame.log_time_s,frame.duration_s,frame.progress,frame.source_sample_index)==(0.,0.,0.,0)
    assert sampler.step_time(99.,'forward')==0.
    gapped=ReplaySampler(replay(),session_id='s',max_gap_s=.2).sample_at(.5)
    assert gapped.joints[6]==0. and not gapped.quality[6].valid


@pytest.mark.parametrize('time',[True,False,float('nan'),float('inf'),float('-inf'),'1'])
def test_bad_sample_times_are_rejected(time):
    with pytest.raises(SamplingError): ReplaySampler(replay(),session_id='s').sample_at(time)


def test_bad_units_indices_directions_and_identities_are_rejected():
    with pytest.raises(SamplingError):ReplaySampler(replay({'torso_joint1':series([0.,1.],[0.,1.],unit='deg')}),session_id='s')
    sampler=ReplaySampler(replay(),session_id='s')
    for index in [True,.5,'1']:
        with pytest.raises(SamplingError):sampler.frame_time(index)
    with pytest.raises(SamplingError):sampler.step_time(.5,'next')
    with pytest.raises(SamplingError):sampler.sample_at(0.,control_seq=True)
    with pytest.raises(SamplingError):ReplaySampler(replay(),session_id='')


def test_offline_convenience_helper_identifies_itself_and_can_receive_runtime_identity():
    assert sample_at(replay(),.5).session_id=='offline-sampling'
    assert sample_at(replay(),.5,session_id='real-session',target_seq=17).target_seq==17
