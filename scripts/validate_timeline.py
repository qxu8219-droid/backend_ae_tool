#!/usr/bin/env python3
"""Actual log import and Timeline probe with an explicitly synthetic worker.

The worker helper lives in test support: no IsaacSim, renderer, ZMQ, or browser
is started. Its rendered receipts test accounting, not real image evidence.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests/replay'))
from replay_protocol import PlaybackSnapshot
from app.services.log_store import LogStore
from app.replay.interpolator import ReplaySampler
from app.replay.timeline import Timeline
from app.replay.timeline_base import TimelineError
from timeline_support import ManualClock, FakeWorker, pump, rpc_response


async def validate(root:Path):
    root.mkdir(parents=True,exist_ok=False)
    raw=root/'synthetic.csv'
    with raw.open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['timestamp','torso_joint1'])
        for index in range(121):writer.writerow([1000+index/60,index/600])
    async def chunks():
        with raw.open('rb') as stream:
            while chunk:=await asyncio.to_thread(stream.read,65536):yield chunk
    store=LogStore(root/'archive')
    first=await store.import_file('timeline-probe',chunks(),raw.name)
    source=await store.get('timeline-probe',first.log_id)
    sampler=ReplaySampler(source,session_id='timeline-probe')
    clock=ManualClock();worker=FakeWorker('timeline-probe',clock)
    timeline=Timeline('timeline-probe',{first.log_id:sampler}.__getitem__,clock=clock)
    trace=[]
    def record(label):
        snapshot=timeline.snapshot()
        assert PlaybackSnapshot.model_validate_json(snapshot.model_dump_json())==snapshot
        trace.append({'phase':label,'monotonic_s':clock(),'snapshot':snapshot.model_dump(mode='json'),'diagnostics':timeline.diagnostics()})
    accepted=timeline.load(first.log_id)
    assert accepted.applied_position is None and accepted.rendered_identity is None
    record('load-accepted-only');pump(timeline,worker);record('load-confirmed-by-fake-worker')
    timeline.play(speed=.5);pump(timeline,worker)
    for _ in range(48):clock.advance(1/120);timeline.tick();pump(timeline,worker)
    assert abs(timeline.snapshot().target_position.log_time_s-.2)<1e-10
    record('playing-half-speed')
    timeline.pause();pump(timeline,worker);held=timeline.snapshot().target_position.log_time_s
    clock.advance(.7);timeline.tick();assert timeline.snapshot().target_position.log_time_s==held
    record('paused-wall-time-advances')
    timeline.set_speed(2.);pump(timeline,worker);timeline.play();pump(timeline,worker)
    clock.advance(.2);timeline.tick();pump(timeline,worker)
    assert abs(timeline.snapshot().target_position.log_time_s-.6)<1e-10
    timeline.seek(time_s=1.);pending=timeline.take_rpc();assert pending.command.cmd=='apply_keyframe'
    timeline.receive_rpc(rpc_response(pending))
    clock.advance(2.);timeline.tick();assert timeline.take_target() is None
    assert timeline.snapshot().target_position.log_time_s==1.
    record('seek-accepted-still-waiting-for-render')
    assert timeline.receive_rpc(worker.handle_rpc(pending))
    clock.advance(.1);timeline.tick();pump(timeline,worker)
    assert abs(timeline.snapshot().target_position.log_time_s-1.2)<1e-10
    record('seek-wait-excluded-from-play-clock')
    timeline.seek(frame_index=30);pump(timeline,worker)
    timeline.step('forward');pump(timeline,worker)
    assert timeline.snapshot().state=='paused' and timeline.snapshot().target_position.source_sample_index==31
    record('original-sample-step')
    timeline.stop();pump(timeline,worker);timeline.set_speed(10.);pump(timeline,worker);timeline.play();pump(timeline,worker)
    clock.advance(1.);ended=timeline.tick();assert ended.state=='ended' and ended.target_position.log_time_s==2.
    assert timeline.take_target() is None
    assert timeline.diagnostics()['critical_pending']
    record('ended-final-keyframe-awaiting-proof');pump(timeline,worker)
    capture=timeline.snapshot().rendered_identity.capture_id
    for _ in range(10):timeline.tick();pump(timeline,worker)
    assert timeline.snapshot().rendered_identity.capture_id==capture
    record('last-frame-confirmed-once')
    timeline.play();pump(timeline,worker);assert timeline.snapshot().target_position.log_time_s==0.
    record('replay-starts-new-epoch-at-zero')
    timeline.seek(time_s=.7);late=timeline.take_rpc();assert late.command.cmd=='apply_keyframe'
    clock.advance(10.);assert timeline.tick().state=='error'
    assert timeline.error.code=='WORKER_TIMEOUT'
    assert not timeline.receive_rpc(worker.handle_rpc(late))
    try:timeline.play()
    except TimelineError as error:assert error.code=='SESSION_ERROR'
    else:raise AssertionError('A failed session must not recover in place')
    record('timeout-is-terminal-late-proof-rejected')
    second=await store.import_file('recreated-session',chunks(),raw.name)
    reloaded=await store.get('recreated-session',second.log_id)
    recovery=Timeline('recreated-session',{second.log_id:ReplaySampler(reloaded,session_id='recreated-session')}.__getitem__,clock=clock)
    recovered_worker=FakeWorker('recreated-session',clock)
    recovery.load(second.log_id);pump(recovery,recovered_worker)
    assert recovery.snapshot().state=='ready' and recovery.snapshot().target_position.log_time_s==0.
    assert second.raw_file_sha256==first.raw_file_sha256 and second.log_id!=first.log_id
    report={'scope':'Actual CPU import/sampling/timeline with test-only synthetic worker receipts; no simulator/video proof',
        'sample_count':121,'duration_s':2.,'target_hz':60.,'phases':len(trace),
        'stream_targets_sent_to_fake':len(worker.targets),'synthetic_render_receipts':len(worker.frames),
        'acceptance_is_not_render_evidence':True,'critical_wait_excluded_from_clock':True,'final_frame_reliable_once':True,
        'timeout_terminal_and_late_proof_rejected':True,'recreated_session_reload_at_zero':True,
        'snapshot_protocol_validation':True,'recovery_snapshot':recovery.snapshot().model_dump(mode='json')}
    (root/'trace.json').write_text(json.dumps(trace,indent=2)+'\n')
    (root/'evidence.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True,help='Must not already exist')
    asyncio.run(validate(parser.parse_args().output_dir))
