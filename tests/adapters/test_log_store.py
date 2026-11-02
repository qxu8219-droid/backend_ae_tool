"""Archive tests exercise persistence, isolation and cancellation boundaries."""
from __future__ import annotations

import asyncio
from array import array
import hashlib
import os

import pytest


CSV = b"timestamp,steer_motor_joint1\n10,0.125\n10.5,0.25\n"


async def chunks(value: bytes, size: int = 7):
    for start in range(0, len(value), size):
        yield value[start:start + size]


def test_archive_preserves_bytes_mapping_and_reopens_scoped_logs(tmp_path):
    from app.services.log_store import LogStore

    async def exercise():
        store = LogStore(tmp_path)
        result = await store.import_file("session/../one", chunks(CSV), "../../unsafe.csv")
        assert result.raw_file_sha256 == hashlib.sha256(CSV).hexdigest()
        raw_path = tmp_path / result.raw_file_ref
        assert raw_path.is_relative_to(tmp_path)
        assert raw_path.read_bytes() == CSV
        assert raw_path.name != "unsafe.csv"
        assert raw_path.stat().st_mode & 0o222 == 0
        reopened = LogStore(tmp_path)
        replay = await reopened.get("session/../one", result.log_id)
        assert replay.log_id == result.log_id
        assert replay.raw_file_ref == result.raw_file_ref
        assert replay.sample_times_s == [0.0, 0.5]
        assert replay.channels["steer_motor_joint1"].values == [0.125, 0.25]
        assert replay.mapping_snapshot == result.mapping_snapshot
        assert replay.mapping_snapshot_id == result.mapping_snapshot_id
        assert await reopened.list_logs("session/../one") == [result]
        assert (await reopened.channels("session/../one", result.log_id)).channels == result.channels
        with pytest.raises(Exception) as denied:
            await reopened.get("different-session", result.log_id)
        assert denied.value.code == "LOG_NOT_FOUND"
        for log_id in ("../../outside", "", "log-" + "g" * 32):
            with pytest.raises(Exception) as invalid:
                await reopened.get("session/../one", log_id)
            assert invalid.value.code == "LOG_NOT_FOUND"
        await reopened.delete_session("session/../one")
        assert await reopened.list_logs("session/../one") == []
        assert not raw_path.exists()

    asyncio.run(exercise())


def test_upload_limit_and_failed_parser_leave_no_visible_archive(tmp_path):
    from app.adapters.base import ParseLimits
    from app.services.log_store import LogStore

    async def exercise():
        store = LogStore(tmp_path, limits=ParseLimits(max_file_bytes=20))
        with pytest.raises(Exception) as oversized:
            await store.import_file("s", chunks(CSV), "source.csv")
        assert oversized.value.code == "IMPORT_LIMIT_EXCEEDED"
        assert await store.list_logs("s") == []
        assert not list(tmp_path.rglob("source.csv"))
        store = LogStore(tmp_path)
        with pytest.raises(Exception):
            await store.import_file("s", chunks(b"timestamp,steer_motor_joint1\n0,nope\n"), "source.csv")
        assert await store.list_logs("s") == []
        assert not list(tmp_path.rglob("source.csv"))

    asyncio.run(exercise())


def test_byte_budget_counts_buffer_bytes_instead_of_array_elements(tmp_path):
    from app.adapters.base import ParseLimits
    from app.services.log_store import LogStore

    async def source():
        yield memoryview(array("Q", [0, 1]))

    async def runner(path, config, parser):
        raise AssertionError("An oversized upload must not reach parsing")

    async def exercise():
        store = LogStore(tmp_path, limits=ParseLimits(max_file_bytes=10), runner=runner)
        with pytest.raises(Exception) as oversized:
            await store.import_file("s", source(), "source.csv")
        assert oversized.value.code == "IMPORT_LIMIT_EXCEEDED"
        assert await store.list_logs("s") == []

    asyncio.run(exercise())


def test_pending_import_is_hidden_busy_rejected_and_delete_cannot_resurrect(tmp_path):
    from app.services.log_store import LogStore

    async def exercise():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending_runner(path, config, parser):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        store = LogStore(tmp_path, runner=pending_runner)
        pending = asyncio.create_task(store.import_file("s", chunks(CSV), "data.csv"))
        await started.wait()
        assert await store.list_logs("s") == []
        with pytest.raises(Exception) as busy:
            await store.import_file("other", chunks(CSV), "data.csv")
        assert busy.value.code == "IMPORT_BUSY"
        await store.delete_session("s")
        assert cancelled.is_set()
        assert pending.cancelled()
        assert await store.list_logs("s") == []
        assert not list(tmp_path.rglob("source.csv"))

    asyncio.run(exercise())


def test_cancelled_stream_releases_slot_and_cleans_staging(tmp_path):
    from app.services.log_store import LogStore

    async def exercise():
        started = asyncio.Event()

        async def paused_source():
            yield CSV[:5]
            started.set()
            await asyncio.Event().wait()

        store = LogStore(tmp_path)
        pending = asyncio.create_task(store.import_file("s", paused_source(), "data.csv"))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not list(tmp_path.rglob("source.csv"))
        result = await store.import_file("s", chunks(CSV), "data.csv")
        assert len(await store.list_logs("s")) == 1
        assert (tmp_path / result.raw_file_ref).read_bytes() == CSV

    asyncio.run(exercise())


@pytest.mark.parametrize("part", ["raw", "normalized", "mapping", "manifest"])
def test_corrupted_archive_returns_controlled_error(tmp_path, part):
    from app.services.log_store import LogStore

    async def exercise():
        store = LogStore(tmp_path)
        result = await store.import_file("s", chunks(CSV), "data.csv")
        directory = (tmp_path / result.raw_file_ref).parent
        target = {"raw": directory / "source.csv", "normalized": directory / "replay.json",
                  "mapping": directory / "mapping.json", "manifest": directory / "manifest.json"}[part]
        target.chmod(0o644)
        target.write_text("{}", encoding="utf-8")
        with pytest.raises(Exception) as corrupt:
            await store.get("s", result.log_id)
        assert corrupt.value.code == "LOG_ARCHIVE_CORRUPT"
        assert corrupt.value.status_code == 500
        with pytest.raises(Exception) as listed:
            await store.list_logs("s")
        assert listed.value.code == "LOG_ARCHIVE_CORRUPT"

    asyncio.run(exercise())


def test_stream_failure_retains_original_exception_and_does_not_commit(tmp_path):
    from app.services.log_store import LogStore

    async def broken():
        yield CSV[:8]
        raise ConnectionError("upload disconnected")

    async def exercise():
        store = LogStore(tmp_path)
        with pytest.raises(ConnectionError, match="upload disconnected"):
            await store.import_file("s", broken(), "source.csv")
        assert await store.list_logs("s") == []
        assert not list(tmp_path.rglob("source.csv"))

    asyncio.run(exercise())


def test_delete_invalidates_even_a_runner_that_swallows_cancellation(tmp_path):
    from app.adapters.registry import AdapterRegistry
    from app.services.log_store import LogStore

    async def exercise():
        started = asyncio.Event()

        async def stubborn_runner(path, config, parser):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return AdapterRegistry().load(path, config, parser)

        store = LogStore(tmp_path, runner=stubborn_runner)
        pending = asyncio.create_task(store.import_file("s", chunks(CSV), "source.csv"))
        await started.wait()
        await store.delete_session("s")
        assert pending.done() and pending.exception().code == "IMPORT_CANCELLED"
        assert await store.list_logs("s") == []
        assert not list(tmp_path.rglob("source.csv"))

    asyncio.run(exercise())


def test_raw_bytes_changed_by_runner_cannot_be_committed(tmp_path):
    from app.adapters.registry import AdapterRegistry
    from app.services.log_store import LogStore

    async def corrupting_runner(path, config, parser):
        replay = AdapterRegistry().load(path, config, parser)
        path.chmod(0o644)
        path.write_bytes(b"changed original bytes")
        return replay

    async def exercise():
        store = LogStore(tmp_path, runner=corrupting_runner)
        with pytest.raises(Exception) as corrupted:
            await store.import_file("s", chunks(CSV), "source.csv")
        assert corrupted.value.code == "IMPORT_SOURCE_CHANGED"
        assert await store.list_logs("s") == []

    asyncio.run(exercise())


def test_corrupted_catalog_directory_is_a_controlled_error(tmp_path):
    from app.services.log_store import LogStore

    directory = tmp_path / "sessions" / hashlib.sha256(b"s").hexdigest()
    directory.mkdir(parents=True)
    (directory / "logs").write_text("not a directory")

    async def exercise():
        with pytest.raises(Exception) as corrupted:
            await LogStore(tmp_path).list_logs("s")
        assert corrupted.value.code == "LOG_ARCHIVE_CORRUPT"

    asyncio.run(exercise())


@pytest.mark.skipif(os.geteuid() == 0, reason="Root bypasses the file permission failure")
def test_delete_reports_filesystem_failure_instead_of_claiming_success(tmp_path):
    from app.services.log_store import LogStore

    async def exercise():
        store = LogStore(tmp_path)
        result = await store.import_file("s", chunks(CSV), "source.csv")
        directory = (tmp_path / result.raw_file_ref).parent
        directory.chmod(0o500)
        try:
            with pytest.raises(Exception) as denied:
                await store.delete_session("s")
            assert denied.value.code == "LOG_DELETE_FAILED"
        finally:
            directory.chmod(0o700)
        await store.delete_session("s")
        assert await store.list_logs("s") == []

    asyncio.run(exercise())
