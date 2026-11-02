"""Regression for cumulative estimates crossing a forbidden source interval."""
import pytest
from app.replay.channel import PreparedChannel
from app.replay.interpolator import ReplaySampler
from test_interpolator import replay, series


def test_virtual_wheels_cannot_cross_a_rejected_chassis_interval_even_without_a_union_point_inside_it():
    mapping={'virtual_channels':{'wheel_roll_from_chassis':{'enabled':True,'wheel_radius_m':1.,
        'joint_signs':{'wheel_motor_joint1':1,'wheel_motor_joint2':1,'wheel_motor_joint3':1}}}}
    for torso_times in [[0.,2.,3.],[0.,1.,2.,3.]]:
        channels={'torso_joint1':series(torso_times,[0.]*len(torso_times)),
            'chassis.x':series([0.,2.,3.],[0.,2.,3.],unit='m',semantics='linear'),
            'chassis.y':series([0.,2.,3.],[0.,0.,0.],unit='m',semantics='linear'),
            'chassis.yaw':series([0.,2.,3.],[0.,0.,0.],semantics='angular_unwrapped')}
        sampler=ReplaySampler(replay(channels,mapping=mapping),session_id='s',max_gap_s=1.)
        assert sampler.sample_at(1.).chassis is None
        final=sampler.sample_at(3.)
        assert final.chassis.x==3.
        assert final.joints[1]==0. and not final.quality[1].valid
        assert sampler.metadata['virtual_wheels']['status']=='partial'
        assert sampler.sample_at(0.).quality[1].valid
        channels['wheel_motor_joint1']=series([0.,2.,3.],[0.,2.,3.],semantics='angular_unwrapped')
        preserved=ReplaySampler(replay(channels,mapping=mapping),session_id='s',max_gap_s=1.).sample_at(3.)
        assert preserved.joints[1]==3. and preserved.quality[1].valid
        assert not preserved.quality[3].valid


def test_interval_eligibility_checks_every_source_edge_and_exact_coverage_boundaries():
    prepared=PreparedChannel(series([0.,1.,2.,3.,4.],[0.,1.,None,3.,4.]))
    assert prepared.interval_available(0.,1.)
    assert prepared.interval_available(.2,.8)
    assert prepared.interval_available(3.,4.)
    assert prepared.interval_available(3.,3.)
    assert not prepared.interval_available(0.,4.)
    assert not prepared.interval_available(1.,2.)
    assert not prepared.interval_available(2.,2.)
    assert not prepared.interval_available(-1.,1.)
    assert not prepared.interval_available(3.,5.)
    sparse=PreparedChannel(series([0.,2.,3.],[0.,2.,3.]),max_gap_s=1.)
    assert not sparse.interval_available(0.,2.)
    assert not sparse.interval_available(.5,1.)
    assert sparse.interval_available(2.,3.)
    assert sparse.interval_available(2.,2.)
