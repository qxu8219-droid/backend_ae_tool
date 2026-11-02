import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'replay'))
from app.replay.timeline import Timeline
from timeline_support import ManualClock,FakeWorker,build_sampler,pump


def test_target_retains_its_own_state_and_speed_after_later_controls():
    clock=ManualClock();sampler=build_sampler(times=(0.,10.));t=Timeline('s',lambda _:sampler,clock=clock)
    w=FakeWorker('s',clock)
    t.load('log');request=t.take_rpc();t.receive_rpc(w.handle_rpc(request));loaded=t.take_rpc()
    assert loaded.command.frame.state=='ready' and loaded.command.frame.speed==1.
    t.receive_rpc(w.handle_rpc(loaded));t.play(speed=2.);pump(t,w)
    clock.advance(.1);t.tick();old=t.take_target().frame
    t.pause()
    assert old.state=='playing' and old.speed==2.
    barrier=t.take_rpc();t.receive_rpc(w.handle_rpc(barrier));paused=t.take_rpc()
    assert paused.command.frame.state=='paused' and paused.command.frame.speed==2.
