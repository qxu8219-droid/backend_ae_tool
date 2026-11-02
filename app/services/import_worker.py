"""Run trusted installed adapters in a bounded, separately spawned Python process.

Only the server constructs requests for this module. Uploaded filenames and
mapping objects cannot select a Python file or executable. Bulk results use a
bounded temporary file, so cancellation never waits for a child blocked on a
full multiprocessing pipe.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import errno
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.base import ParseLimits
    from replay_protocol import ReplayData

REQUEST_BYTES = 2 * 1024 * 1024
ERROR_BYTES = 1024 * 1024


def result_byte_limit(limits: ParseLimits) -> int:
    return max(1, min(256 * 1024 * 1024, limits.memory_bytes // 3))


def _write_json_bounded(path: Path, value, limit: int) -> None:
    from app.adapters.base import LimitError

    total = 0
    with path.open("xb") as output:
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for fragment in encoder.iterencode(value):
            encoded = fragment.encode("utf-8")
            total += len(encoded)
            if total > limit:
                raise LimitError("Serialized import result exceeds its byte budget")
            output.write(encoded)
        output.flush()
        os.fsync(output.fileno())


def _read_json_bounded(path: Path, limit: int):
    if path.stat().st_size > limit:
        raise ValueError("Serialized import file exceeds its byte budget")
    with path.open("rb") as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Serialized import file exceeds its byte budget")
    return json.loads(value)


async def _finish_task(task):
    """Repeated cancellation cannot abandon a filesystem mutation or child reap."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve failures to avoid unobserved task exceptions; cancellation is
        # the caller's outcome, but only after the underlying operation finishes.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def _finish_io(function, *args):
    """Finish filesystem mutations before cancellation allows staging cleanup."""
    return await _finish_task(asyncio.create_task(asyncio.to_thread(function, *args)))


async def _stop_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


class ProcessImportRunner:
    def __init__(self, limits: ParseLimits | None = None, *, discover_plugins: bool = False):
        from app.adapters.base import ParseLimits

        self.limits = limits or ParseLimits()
        self.discover_plugins = discover_plugins

    async def run(self, path: Path, config=None, parser: str | None = None) -> ReplayData:
        from app.adapters.base import AdapterError
        from app.adapters.mapping import parse_mapping

        effective = parse_mapping(config).model_dump(mode="json")
        work = Path(tempfile.mkdtemp(prefix=".parse-", dir=path.parent))
        request = work / "request.json"
        process = None
        spawn = None
        try:
            await _finish_io(_write_json_bounded, request, {
                "path": str(path.resolve()), "mapping": effective, "parser": parser,
                "limits": asdict(self.limits), "discover_plugins": self.discover_plugins,
            }, REQUEST_BYTES)
            try:
                async with asyncio.timeout(self.limits.timeout_s):
                    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                        sys.executable, "-m", "app.services.import_worker", str(request),
                        cwd=Path(__file__).resolve().parents[2],
                        env={key: value for key, value in os.environ.items()
                             if key not in {"PYTHONHOME", "PYTHONPATH"}},
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    ))
                    process = await asyncio.shield(spawn)
                    code = await process.wait()
                    return await _finish_io(self._read_result, work, code)
            except TimeoutError as exc:
                raise AdapterError("IMPORT_TIMEOUT", "Log parsing exceeded the configured timeout", status_code=408) from exc
        finally:
            # A cancellation during process creation must still retrieve and reap
            # the new process before deleting files that it could otherwise write.
            async def cleanup():
                nonlocal process
                try:
                    if process is None and spawn is not None:
                        try:
                            process = await spawn
                        except Exception:
                            pass
                    await _stop_process(process)
                finally:
                    await _finish_io(shutil.rmtree, work, True)
            await _finish_task(asyncio.create_task(cleanup()))

    def _read_result(self, work: Path, code: int):
        from app.adapters.base import AdapterError
        from app.schemas import ParseIssue
        from replay_protocol import ReplayData

        error = work / "error.json"
        try:
            if error.is_file() and error.stat().st_size:
                detail = _read_json_bounded(error, ERROR_BYTES)
                issues = [ParseIssue.model_validate(item) for item in detail.get("issues", [])]
                raise AdapterError(detail["code"], detail["message"], issues, detail.get("status_code", 422))
            if code != 0:
                raise AdapterError("IMPORT_WORKER_FAILED", "The isolated log parser exited before producing a valid result", status_code=422)
            return ReplayData.model_validate(_read_json_bounded(work / "result.json", result_byte_limit(self.limits)))
        except AdapterError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AdapterError("IMPORT_WORKER_FAILED", "The isolated log parser produced an invalid result", status_code=422) from exc


def _child_main(request_path: Path) -> int:
    # The request is a small, server-generated file; read it before applying the
    # memory cap so even tiny configured caps yield a structured failure.
    request = _read_json_bounded(request_path, REQUEST_BYTES)
    work = request_path.parent
    memory_error = b'{"code":"IMPORT_MEMORY_LIMIT","message":"The isolated log parser exceeded its memory budget","status_code":413}'
    generic_error = b'{"code":"IMPORT_WORKER_FAILED","message":"The isolated log parser failed","status_code":422}'
    error_fd = os.open(work / "error.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        import resource
        import signal

        memory = request["limits"]["memory_bytes"]
        byte_limit = max(1, min(256 * 1024 * 1024, memory // 3))
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max(ERROR_BYTES, byte_limit), max(ERROR_BYTES, byte_limit)))
        cpu_seconds = max(1, math.ceil(request["limits"]["timeout_s"]) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)

        from app.adapters.base import AdapterError, ParseLimits
        from app.adapters.registry import AdapterRegistry

        limits = ParseLimits(**request["limits"])
        registry = AdapterRegistry(limits=limits)
        if request["discover_plugins"]:
            registry.discover_plugins()
        data = registry.load(Path(request["path"]), request["mapping"], parser=request["parser"])
        _write_json_bounded(work / "result.json", data.model_dump(mode="json"), byte_limit)
        return 0
    except MemoryError:
        os.write(error_fd, memory_error)
        return 1
    except BaseException as exc:
        try:
            if isinstance(exc, OSError) and exc.errno in (errno.ENOMEM,):
                os.write(error_fd, memory_error)
            elif hasattr(exc, "code") and hasattr(exc, "issues"):
                message = json.dumps({
                    "code": exc.code, "message": exc.message, "status_code": exc.status_code,
                    "issues": [issue.model_dump(mode="json") for issue in exc.issues],
                }, allow_nan=False).encode("utf-8")
                os.write(error_fd, message if len(message) <= ERROR_BYTES else generic_error)
            else:
                os.write(error_fd, generic_error)
        except BaseException:
            os.write(error_fd, memory_error)
        return 1
    finally:
        os.close(error_fd)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    raise SystemExit(_child_main(Path(sys.argv[1])))
