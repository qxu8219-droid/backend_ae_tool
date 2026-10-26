#!/usr/bin/env python3
"""Actual import-to-target deterministic sampling probe; no simulator or HTTP runtime."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from replay_protocol import CANONICAL_JOINTS, TargetFrame
from app.replay.interpolator import ReplaySampler
from app.services.log_store import LogStore


async def validate(root: Path, samples: int, queries: int):
    root.mkdir(parents=True,exist_ok=False)
    raw=root/'synthetic.csv'
    joints=[name for name in CANONICAL_JOINTS if not name.startswith('wheel_motor_joint')]
    with raw.open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['timestamp',*joints,'x','y','yaw'])
        for index in range(samples):
            position=index/60
            values=[position*(.001 if 'gripper' in name else .01)*(-1 if 'gripper' in name and name.endswith('2') else 1) for name in joints]
            writer.writerow([1000+position,*values,position*.1,0,0])
    async def chunks():
        with raw.open('rb') as stream:
            while chunk:=await asyncio.to_thread(stream.read,65536):yield chunk
    # Every wheel parameter below belongs to this synthetic fixture, not R1 hardware.
    mapping={'timestamp':{'start_offset':.5},'chassis':{'available':True},
        'virtual_channels':{'wheel_roll_from_chassis':{'enabled':True,'wheel_radius_m':.1,
            'joint_signs':{'wheel_motor_joint1':1,'wheel_motor_joint2':-1,'wheel_motor_joint3':1}}}}
    store=LogStore(root/'archive')
    uploaded=await store.import_file('synthetic-sampling',chunks(),raw.name,mapping_config=mapping)
    data=await store.get('synthetic-sampling',uploaded.log_id)
    original_hash=hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    began=time.perf_counter();sampler=ReplaySampler(data,session_id='synthetic-sampling');prepare_s=time.perf_counter()-began
    positions=[sampler.duration_s*i/(queries-1) for i in range(queries)]
    began=time.perf_counter();frames=[sampler.sample_at(t) for t in positions];query_s=time.perf_counter()-began
    for position,frame in zip(positions,frames):
        assert math.isclose(frame.joints[1],position,abs_tol=1e-10)
        assert math.isclose(frame.joints[3],-position,abs_tol=1e-10)
        assert math.isclose(frame.joints[5],position,abs_tol=1e-10)
        assert frame.quality[1].origin=='derived' and frame.quality[1].valid
    order=list(range(queries));random.Random(731).shuffle(order)
    for index in order+list(reversed(range(queries))):
        assert sampler.sample_at(positions[index])==frames[index]
    assert TargetFrame.model_validate_json(frames[-1].model_dump_json())==frames[-1]
    assert hashlib.sha256(data.model_dump_json().encode()).hexdigest()==original_hash
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==uploaded.raw_file_sha256
    assert sampler.sample_at(sampler.frame_time(samples-1)).source_sample_index==samples-1
    report={'scope':'CPU source-to-TargetFrame sampling only; no IsaacSim/video/final replay performance',
        'samples':samples,'canonical_joints':28,'queries_per_order':queries,'orders_checked':['forward','seeded-random','reverse'],
        'prepare_s':prepare_s,'query_s':query_s,'queries_per_s':queries/query_s,'mean_query_ms':query_s*1000/queries,
        'source_and_archive_unchanged':True,'original_sample_index_preserved':True,'shared_target_json_valid':True,
        'signed_wheel_estimate_verified':True,'synthetic_wheel_radius_m':.1,'sampler_metadata':sampler.metadata}
    (root/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (root/'last-target.json').write_text(frames[-1].model_dump_json(indent=2)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key!='sampler_metadata'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True,help='New output directory, never overwrite existing evidence')
    parser.add_argument('--samples',type=int,default=1200)
    parser.add_argument('--queries',type=int,default=2000)
    args=parser.parse_args()
    if not 2<=args.samples<=20000:parser.error('samples must be 2..20000')
    if not 2<=args.queries<=10000:parser.error('queries must be 2..10000')
    asyncio.run(validate(args.output_dir,args.samples,args.queries))
