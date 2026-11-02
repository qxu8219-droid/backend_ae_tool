"""Session-scoped, atomic log archives with a bounded asynchronous importer.

The caller supplies an already authorized session ID. This service deliberately
does not create sessions, activate a log, or change playback. One upload may be
in flight per store; additional uploads fail immediately instead of waiting in
an unbounded queue. Persisted archives can be reopened by a later service.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import os
from pathlib import Path
import re
import shutil
import stat
from typing import AsyncIterable
import uuid

from app.adapters.base import AdapterError, LimitError, ParseLimits
from app.adapters.mapping import parse_mapping
from app.schemas import LogChannels, UploadResult
from replay_protocol import ReplayData

from .import_worker import (
    ProcessImportRunner, REQUEST_BYTES, _finish_io, _read_json_bounded,
    _write_json_bounded, result_byte_limit,
)

_LOG_ID = re.compile(r"log-[0-9a-f]{32}\Z")
_RAW_NAME = re.compile(r"source\.[a-z0-9]{1,16}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass
class _Import:
    session_key: str
    task: asyncio.Task
    invalidated: bool = False


def _session_key(session_id: str) -> str:
    if type(session_id) is not str or not 1 <= len(session_id) <= 256:
        raise AdapterError("INVALID_SESSION_ID", "Session ID must be a nonempty identifier of at most 256 characters", status_code=400)
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _file_hash(path: Path, byte_limit: int) -> str:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Archive files must be regular files")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        while value := source.read(1024 * 1024):
            count += len(value)
            if count > byte_limit:
                raise ValueError("Archive file exceeds its byte budget")
            digest.update(value)
    return digest.hexdigest()


def _write_chunk(output, digest, chunk):
    output.write(chunk)
    digest.update(chunk)


def _flush(output):
    output.flush()
    os.fsync(output.fileno())


class LogStore:
    def __init__(self, root: Path, limits: ParseLimits = ParseLimits(), *, runner=None,
                 discover_plugins: bool = False):
        self.root = Path(root).resolve()
        self.limits = limits
        self.runner = runner or ProcessImportRunner(limits, discover_plugins=discover_plugins)
        self._active: _Import | None = None
        self._deleting: dict[str, int] = {}

    async def import_file(self, session_id: str, source: AsyncIterable[bytes], filename: str,
                          *, mapping_config=None, parser: str | None = None) -> UploadResult:
        key = _session_key(session_id)
        if self._deleting.get(key):
            raise AdapterError("SESSION_DELETING", "Session logs are being deleted", status_code=409)
        if self._active is not None:
            raise AdapterError("IMPORT_BUSY", "Another log import is already running", status_code=409)
        if type(filename) is not str or not filename or len(filename) > 4096:
            raise AdapterError("INVALID_FILENAME", "An upload filename of at most 4096 characters is required", status_code=400)
        config = parse_mapping(mapping_config)
        if parser is not None and (type(parser) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", parser) is None):
            raise AdapterError("INVALID_PARSER", "Parser must name a registered adapter", status_code=400)
        task = asyncio.current_task()
        assert task is not None
        active = _Import(key, task)
        self._active = active
        log_id = "log-" + uuid.uuid4().hex
        staging = self.root / ".staging" / uuid.uuid4().hex
        ready = self.root / "sessions" / key / "logs" / log_id
        suffix = Path(filename).suffix.lower()
        raw_name = "source" + (suffix if re.fullmatch(r"\.[a-z0-9]{1,16}", suffix) else ".bin")
        raw = staging / raw_name
        committed = False
        try:
            await _finish_io(staging.mkdir, 0o700, True, False)
            digest = hashlib.sha256()
            count = 0
            with raw.open("xb") as output:
                try:
                    async with asyncio.timeout(self.limits.timeout_s):
                        async for chunk in source:
                            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                                raise AdapterError("INVALID_UPLOAD", "Upload stream must contain byte chunks", status_code=400)
                            count += chunk.nbytes if isinstance(chunk, memoryview) else len(chunk)
                            if count > self.limits.max_file_bytes:
                                raise LimitError("Uploaded file exceeds the configured byte budget")
                            if chunk:
                                await _finish_io(_write_chunk, output, digest, chunk)
                        if count == 0:
                            raise AdapterError("EMPTY_LOG", "Uploaded log file is empty")
                        await _finish_io(_flush, output)
                except TimeoutError as exc:
                    raise AdapterError("IMPORT_TIMEOUT", "Log upload exceeded the configured timeout", status_code=408) from exc
            raw.chmod(0o444)
            run = self.runner.run if hasattr(self.runner, "run") else self.runner
            replay = run(raw, config, parser)
            if not inspect.isawaitable(replay):
                raise TypeError("The import runner must be asynchronous")
            replay = await replay
            result = await _finish_io(self._prepare_archive, staging, ready, key, log_id,
                                      raw_name, digest.hexdigest(), filename, replay)
            if active.invalidated or self._deleting.get(key):
                raise AdapterError("IMPORT_CANCELLED", "The session was deleted during log import", status_code=409)
            # No await between the generation check and rename: deletion either
            # cancels this import first, or removes the complete archive after it.
            ready.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(ready)
            committed = True
            return result
        finally:
            try:
                if not committed:
                    await _finish_io(shutil.rmtree, staging, True)
            finally:
                if self._active is active:
                    self._active = None

    def _prepare_archive(self, staging: Path, ready: Path, key: str, log_id: str,
                         raw_name: str, raw_hash: str, filename: str, replay: ReplayData) -> UploadResult:
        from app.adapters.normalize import upload_result

        # Revalidate the runner result, including an injected trusted runner.
        data = ReplayData.model_validate(replay)
        if data.raw_file_sha256 != raw_hash or _file_hash(staging / raw_name, self.limits.max_file_bytes) != raw_hash:
            raise AdapterError("IMPORT_SOURCE_CHANGED", "Parser result does not identify the uploaded original bytes")
        payload = data.model_dump(mode="json")
        payload.update(log_id=log_id, raw_file_ref=(ready / raw_name).relative_to(self.root).as_posix())
        payload["metadata"]["source_filename"] = filename
        data = ReplayData.model_validate(payload)
        result = upload_result(data)
        files = {
            "mapping.json": (data.mapping_snapshot, REQUEST_BYTES),
            "replay.json": (data.model_dump(mode="json"), result_byte_limit(self.limits)),
            "result.json": (result.model_dump(mode="json"), result_byte_limit(self.limits)),
        }
        hashes = {raw_name: raw_hash}
        for name, (value, budget) in files.items():
            path = staging / name
            _write_json_bounded(path, value, budget)
            hashes[name] = _file_hash(path, budget)
            path.chmod(0o444)
        manifest = {
            "version": 1, "session_key": key, "log_id": log_id,
            "raw_name": raw_name, "files": hashes,
        }
        _write_json_bounded(staging / "manifest.json", manifest, REQUEST_BYTES)
        (staging / "manifest.json").chmod(0o444)
        return result

    def _read_archive(self, key: str, log_id: str) -> tuple[ReplayData, UploadResult]:
        from app.adapters.normalize import upload_result

        directory = self.root / "sessions" / key / "logs" / log_id
        if not directory.exists():
            raise AdapterError("LOG_NOT_FOUND", "No completed log exists in this session", status_code=404)
        try:
            # Refuse symlinks even if a local filesystem corruption has replaced
            # a previously generated path; never follow a manifest-provided path.
            for ancestor in (directory, *directory.parents):
                if ancestor == self.root:
                    break
                if ancestor.is_symlink():
                    raise ValueError("Archive directory cannot be a symlink")
            manifest_path = directory / "manifest.json"
            _file_hash(manifest_path, REQUEST_BYTES)
            manifest = _read_json_bounded(manifest_path, REQUEST_BYTES)
            if manifest.get("version") != 1 or manifest.get("session_key") != key or manifest.get("log_id") != log_id:
                raise ValueError("Archive identity is inconsistent")
            raw_name = manifest.get("raw_name")
            if type(raw_name) is not str or _RAW_NAME.fullmatch(raw_name) is None:
                raise ValueError("Archive raw name is invalid")
            budgets = {raw_name: self.limits.max_file_bytes, "mapping.json": REQUEST_BYTES,
                       "replay.json": result_byte_limit(self.limits), "result.json": result_byte_limit(self.limits)}
            expected = manifest.get("files")
            if not isinstance(expected, dict) or set(expected) != set(budgets):
                raise ValueError("Archive manifest is incomplete")
            for name, budget in budgets.items():
                if type(expected[name]) is not str or _SHA256.fullmatch(expected[name]) is None:
                    raise ValueError("Archive checksum is invalid")
                if _file_hash(directory / name, budget) != expected[name]:
                    raise ValueError("Archive checksum does not match its file")
            data = ReplayData.model_validate(_read_json_bounded(directory / "replay.json", result_byte_limit(self.limits)))
            result = UploadResult.model_validate(_read_json_bounded(directory / "result.json", result_byte_limit(self.limits)))
            mapping = _read_json_bounded(directory / "mapping.json", REQUEST_BYTES)
            raw_ref = (directory / raw_name).relative_to(self.root).as_posix()
            if data.log_id != log_id or data.raw_file_ref != raw_ref or data.raw_file_sha256 != expected[raw_name]:
                raise ValueError("Archive source reference is inconsistent")
            if data.mapping_snapshot != mapping or result != upload_result(data):
                raise ValueError("Archive normalized metadata is inconsistent")
            return data, result
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AdapterError("LOG_ARCHIVE_CORRUPT", "The stored log archive is incomplete or corrupted", status_code=500) from exc

    async def get(self, session_id: str, log_id: str) -> ReplayData:
        key = _session_key(session_id)
        self._validate_log_id(log_id)
        data, _ = await asyncio.to_thread(self._read_archive, key, log_id)
        return data

    async def channels(self, session_id: str, log_id: str) -> LogChannels:
        key = _session_key(session_id)
        self._validate_log_id(log_id)
        _, result = await asyncio.to_thread(self._read_archive, key, log_id)
        return LogChannels(log_id=log_id, channels=result.channels)

    async def list_logs(self, session_id: str) -> list[UploadResult]:
        key = _session_key(session_id)

        def read():
            directory = self.root / "sessions" / key / "logs"
            try:
                if not directory.exists():
                    return []
                return [self._read_archive(key, path.name)[1] for path in sorted(directory.iterdir())
                        if _LOG_ID.fullmatch(path.name)]
            except OSError as exc:
                raise AdapterError("LOG_ARCHIVE_CORRUPT", "The stored log catalog cannot be read", status_code=500) from exc

        return await asyncio.to_thread(read)

    async def delete_session(self, session_id: str) -> None:
        key = _session_key(session_id)
        self._deleting[key] = self._deleting.get(key, 0) + 1
        try:
            active = self._active
            if active is not None and active.session_key == key:
                active.invalidated = True
                if active.task is not asyncio.current_task():
                    active.task.cancel()
                    await asyncio.gather(active.task, return_exceptions=True)
            try:
                await _finish_io(shutil.rmtree, self.root / "sessions" / key)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise AdapterError("LOG_DELETE_FAILED", "The session log archive could not be deleted", status_code=500) from exc
        finally:
            self._deleting[key] -= 1
            if not self._deleting[key]:
                del self._deleting[key]

    @staticmethod
    def _validate_log_id(log_id: str) -> None:
        if type(log_id) is not str or _LOG_ID.fullmatch(log_id) is None:
            raise AdapterError("LOG_NOT_FOUND", "No completed log exists in this session", status_code=404)
