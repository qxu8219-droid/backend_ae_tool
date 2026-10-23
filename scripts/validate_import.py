#!/usr/bin/env python3
"""Repeatable actual-process synthetic import/heartbeat evidence; no GPU required."""
from __future__ import annotations
import argparse
import asyncio
import csv
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from replay_protocol import CANONICAL_JOINTS
from app.services.log_store import LogStore


async def validate(root: Path, count: int):
    root.mkdir(parents=True,exist_ok=False)
    source=root/'synthetic.csv'
    with source.open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['timestamp',*CANONICAL_JOINTS])
        for index in range(count):
            writer.writerow([index/60,*[(-1 if name.endswith('2') and 'gripper' in name else 1)*(.01 if 'gripper' in name else .5)*math.sin(index/60) for name in CANONICAL_JOINTS]])
    async def chunks():
        with source.open('rb') as stream:
            while chunk:=await asyncio.to_thread(stream.read,65536):yield chunk
    store=LogStore(root/'archive')
    gaps=[];done=False
    async def heartbeat():
        previous=time.perf_counter()
        while not done:
            await asyncio.sleep(.01)
            now=time.perf_counter();gaps.append(now-previous);previous=now
    monitor=asyncio.create_task(heartbeat());started=time.perf_counter()
    try: result=await store.import_file('synthetic-probe',chunks(),source.name)
    finally: done=True;await monitor
    elapsed=time.perf_counter()-started
    data=await store.get('synthetic-probe',result.log_id)
    assert result.raw_file_sha256==hashlib.sha256(source.read_bytes()).hexdigest()
    check=min(30,count-1)
    assert data.channels['left_gripper_finger_joint1'].values[check]==.01*math.sin(check/60)
    assert data.channels['left_gripper_finger_joint2'].values[check]==-.01*math.sin(check/60)
    assert result.stats.sample_count==count
    config=json.loads((Path(__file__).resolve().parents[1]/'configs/robot-asset.json').read_text())
    original=Path(__file__).resolve().parents[1]/config['asset_directory']/config['common_config']
    limits_hash_verified=None
    if original.exists():
        limits_hash_verified=hashlib.sha256(original.read_bytes()).hexdigest()==data.metadata['limits_source_sha256']
        assert limits_hash_verified
    report={'scope':'Actual isolated CPU import of synthetic CSV, not a real robot log',
        'input_bytes':source.stat().st_size,'samples':count,'joint_channels':28,
        'normalized_points':sum(len(channel.values) for channel in data.channels.values()),
        'elapsed_s':elapsed,'heartbeat_count':len(gaps),'max_event_loop_gap_s':max(gaps),
        'child_peak_rss_bytes':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024,
        'source_hash_verified':True,'signed_gripper_values_verified':True,
        'limits_metadata_matches_asset':limits_hash_verified,'log_id':result.log_id}
    (root/'evidence.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True,help='New directory; refuses to overwrite previous evidence')
    parser.add_argument('--samples',type=int,default=1200)
    args=parser.parse_args()
    if not 1<=args.samples<=20000:parser.error('samples must be 1..20000')
    asyncio.run(validate(args.output_dir,args.samples))
