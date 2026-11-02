"""Actual isolated import -> archive -> deterministic target integration."""
import asyncio
import json
import math
import random

import pytest
from app.replay.interpolator import ReplaySampler
from app.services.log_store import LogStore
from replay_protocol import TargetFrame


async def chunks(value):
    yield value


def test_csv_archive_preserves_raw_wrapped_angles_while_runtime_derives_only_missing_wheels(tmp_path):
    async def run():
        raw=b'timestamp,q,roll_deg,x,y,yaw\n1000,0,170,0,0,0\n1001,2,-170,1,0,0\n'
        mapping={'timestamp':{'start_offset':2.},'joints':{'map':{'torso_joint1':'q','wheel_motor_joint1':'roll_deg'},
            'per_joint_overrides':{'wheel_motor_joint1':{'unit':'deg','angle_semantics':'angular_wrapped'}}},
            'chassis':{'available':True,'frame':'odom','transform':{'rotation_rad':math.pi/2,'translation_x_m':2.,'translation_y_m':-1.}},
            'virtual_channels':{'wheel_roll_from_chassis':{'enabled':True,'wheel_radius_m':.1,
                'joint_signs':{'wheel_motor_joint1':1,'wheel_motor_joint2':-1,'wheel_motor_joint3':1}}}}
        store=LogStore(tmp_path)
        result=await store.import_file('s',chunks(raw),'synthetic.csv',mapping_config=mapping)
        data=await store.get('s',result.log_id)
        original=data.model_dump_json()
        sampler=ReplaySampler(data,session_id='s')
        middle=sampler.sample_at(.5,playback_epoch=2,control_seq=3,target_seq=4)
        assert middle.joints[1]==pytest.approx(math.pi)
        assert middle.joints[3]==pytest.approx(-5.) and middle.joints[5]==pytest.approx(5.)
        assert middle.quality[3].origin=='derived' and 'derived' in middle.quality[3].processing
        assert middle.chassis.x==pytest.approx(2.) and middle.chassis.y==pytest.approx(-.5)
        assert middle.chassis.yaw==pytest.approx(math.pi/2)
        assert middle.source_sample_index is None
        assert sampler.sample_at(1.).source_sample_index==1
        assert data.channels['wheel_motor_joint1'].values==pytest.approx([math.radians(170),math.radians(-170)])
        assert data.model_dump_json()==original
        assert (tmp_path/result.raw_file_ref).read_bytes()==raw
        assert (await store.get('s',result.log_id)).model_dump_json()==original
        assert TargetFrame.model_validate_json(middle.model_dump_json())==middle
        times=[i/40 for i in range(41)]
        expected={t:sampler.sample_at(t) for t in times}
        random.Random(41).shuffle(times)
        assert all(sampler.sample_at(t)==expected[t] for t in times+times[::-1])
    asyncio.run(run())


def test_json_independent_clock_union_remains_original_step_clock_after_sampling(tmp_path):
    async def run():
        raw=json.dumps({'channels':{'timestamp':[1000,1001],
            'torso_joint1':{'timestamps':[1000,1000.25,1001],'values':[0,2,5]},
            'left_arm_joint1':{'timestamps':[1000,1000.5,1001],'values':[0,10,20]}}}).encode()
        store=LogStore(tmp_path)
        result=await store.import_file('s',chunks(raw),'synthetic.json')
        data=await store.get('s',result.log_id)
        sampler=ReplaySampler(data,session_id='s')
        assert sampler.sample_times_s==(0.,.25,.5,1.)
        assert sampler.sample_at(.5).joints[6]==pytest.approx(3.)
        assert sampler.sample_at(.5).joints[10]==10.
        assert sampler.sample_at(.5).source_sample_index==2
        assert sampler.step_time(.3,'forward')==.5 and sampler.step_time(.3,'backward')==.25
        assert sampler.metadata['virtual_wheels']['enabled'] is False
    asyncio.run(run())
