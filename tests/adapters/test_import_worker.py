"""Actual child processes parse reference formats and enforce lifecycle limits."""
from __future__ import annotations

import asyncio
import json
import os

import pytest


@pytest.mark.parametrize("suffix,content", [
    (".csv", "timestamp,steer_motor_joint1\n40,0.5\n40.5,0.75\n"),
    (".json", json.dumps({"timestamp": [40, 40.5], "steer_motor_joint1": [0.5, 0.75]})),
])
def test_spawned_parser_loads_actual_csv_json_without_blocking_loop(tmp_path, suffix, content):
    from app.services.import_worker import ProcessImportRunner

    path = tmp_path / ("source" + suffix)
    path.write_text(content)

    async def exercise():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.005)

        task = asyncio.create_task(ticker())
        try:
            data = await ProcessImportRunner().run(path, None, None)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert ticks >= 3
        assert data.sample_times_s == [0.0, 0.5]
        assert data.channels["steer_motor_joint1"].values == [0.5, 0.75]
        assert list(tmp_path.iterdir()) == [path]

    asyncio.run(exercise())


def test_worker_timeout_reaps_process_and_removes_output(tmp_path, monkeypatch):
    from app.adapters.base import ParseLimits
    from app.services.import_worker import ProcessImportRunner

    path = tmp_path / "source.csv"
    path.write_text("timestamp,steer_motor_joint1\n0,0\n")
    processes = []
    spawn = asyncio.create_subprocess_exec

    async def actual_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", actual_spawn)

    async def exercise():
        with pytest.raises(Exception) as timeout:
            await ProcessImportRunner(ParseLimits(timeout_s=0.001)).run(path, None, None)
        assert timeout.value.code == "IMPORT_TIMEOUT"
        assert processes and all(process.returncode is not None for process in processes)
        for process in processes:
            with pytest.raises(ProcessLookupError):
                os.kill(process.pid, 0)
        assert list(tmp_path.iterdir()) == [path]
        # A timed-out child cannot recreate its output after parent cleanup.
        await asyncio.sleep(0.1)
        assert list(tmp_path.iterdir()) == [path]

    asyncio.run(exercise())


def test_worker_cancel_reaps_process_and_removes_output(tmp_path):
    from app.services.import_worker import ProcessImportRunner

    path = tmp_path / "source.csv"
    path.write_text("timestamp,steer_motor_joint1\n0,0\n")

    async def exercise():
        task = asyncio.create_task(ProcessImportRunner().run(path, None, None))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
        assert list(tmp_path.iterdir()) == [path]

    asyncio.run(exercise())


def test_worker_memory_limit_is_a_controlled_failure(tmp_path):
    from app.adapters.base import ParseLimits
    from app.services.import_worker import ProcessImportRunner

    path = tmp_path / "source.csv"
    path.write_text("timestamp,steer_motor_joint1\n0,0\n")

    async def exercise():
        with pytest.raises(Exception) as limited:
            await ProcessImportRunner(ParseLimits(memory_bytes=1024 * 1024)).run(path, None, None)
        assert limited.value.code == "IMPORT_MEMORY_LIMIT"
        assert list(tmp_path.iterdir()) == [path]

    asyncio.run(exercise())


def test_worker_does_not_inherit_incompatible_python_environment(tmp_path, monkeypatch):
    from app.services.import_worker import ProcessImportRunner

    path = tmp_path / "source.csv"
    path.write_text("timestamp,steer_motor_joint1\n0,0.5\n")
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "incompatible-python"))
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/humble/lib/python3.10/site-packages")
    data = asyncio.run(ProcessImportRunner().run(path, None, None))
    assert data.channels["steer_motor_joint1"].values == [0.5]
