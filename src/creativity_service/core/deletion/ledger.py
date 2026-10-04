"""独立卷上的删除意图清单；先持久化意图，再提交业务数据库标记。"""

import asyncio
import fcntl
import json
import os
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, canonical_json, digest, new_id, utcnow

maintenance_mode: ContextVar[bool] = ContextVar("deletion_maintenance", default=False)
current_manifest: ContextVar[dict[str, Any] | None] = ContextVar("deletion_manifest", default=None)


class LedgerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CREATIVITY_", env_file=".env", extra="ignore")
    deletion_ledger_path: Path = Path(".local/deletion-ledger")


@lru_cache(maxsize=8)
def _configured_root(env_file: Path, stamp: tuple[int, int, int] | None) -> Path:
    """仅复用进程配置解析；文件变更、工作目录变化会得到新的配置，清单内容仍每次读取。"""
    return LedgerSettings(_env_file=env_file).deletion_ledger_path


def configured_root() -> Path:
    environment = {key.lower(): value for key, value in os.environ.items()}
    override = environment.get("creativity_deletion_ledger_path")
    if override is not None:
        return Path(override)
    path = Path(".env").resolve()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except FileNotFoundError:
        stamp = None
    return _configured_root(path, stamp)


class DeletionLedger:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or configured_root()

    def _operate(
        self,
        channel_id: str,
        entry: dict[str, Any] | None,
        blocked: bool | None,
        required: bool,
        expected_sequence: int | None,
        expected_digest: str | None,
        initialize: bool,
    ) -> dict[str, Any]:
        # 文件锁只用于独立清单；所有文件访问均在数据库事务以外。
        folder = self.root / digest(channel_id)
        if not folder.exists() and entry is None and blocked is None and not initialize:
            if required:
                raise ServiceError("DELETION_LEDGER_UNAVAILABLE", "无法确认最新删除清单", 503)
            return {"channel_id": channel_id, "sequence": 0, "entries": [], "blocked": False}
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (folder / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = folder / "manifest.json"
            if path.exists():
                data: dict[str, Any] = json.loads(path.read_text())
                checksum = data.pop("checksum")
                if data["channel_id"] != channel_id or checksum != digest(data):
                    raise ServiceError("DELETION_LEDGER_INVALID", "删除清单校验失败", 503)
            else:
                if required:
                    raise ServiceError("DELETION_LEDGER_UNAVAILABLE", "无法确认最新删除清单", 503)
                data = {"channel_id": channel_id, "sequence": 0, "entries": [], "blocked": False}
            changed = initialize and not path.exists()
            if expected_sequence is not None and data["sequence"] != expected_sequence:
                raise ServiceError("RECOVERY_PROOF_INVALID", "恢复核对期间删除清单已变化", 503)
            if expected_digest is not None and digest(data) != expected_digest:
                raise ServiceError(
                    "RECOVERY_PROOF_INVALID", "恢复核对期间清单或封锁状态已变化", 503
                )
            if entry and not any(e["id"] == entry["id"] for e in data["entries"]):
                data["entries"].append(entry)
                data["sequence"] += 1
                changed = True
            if blocked is not None:
                data["blocked"] = blocked
                if blocked:
                    data["recovery_id"] = new_id("restore")
                changed = True
            if changed:
                data["updated_at"] = utcnow().isoformat()
                temporary = folder / "manifest.pending"
                with temporary.open("w", encoding="utf-8") as handle:
                    handle.write(canonical_json({**data, "checksum": digest(data)}).decode())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                descriptor = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                for parent in (self.root, self.root.parent):
                    descriptor = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            return data

    async def operate(
        self,
        channel_id: str,
        *,
        entry: dict[str, Any] | None = None,
        blocked: bool | None = None,
        required: bool = False,
        expected_sequence: int | None = None,
        expected_digest: str | None = None,
        initialize: bool = False,
    ) -> dict[str, Any]:
        from creativity_service.core.database import assert_external_io_allowed

        assert_external_io_allowed()
        Scope(channel_id=channel_id, environment="dev")
        try:
            return await asyncio.to_thread(
                self._operate,
                channel_id,
                entry,
                blocked,
                required,
                expected_sequence,
                expected_digest,
                initialize,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ServiceError("DELETION_LEDGER_UNAVAILABLE", "无法确认最新删除清单", 503) from exc

    async def record(
        self, scope: Scope, kind: str, identifier: str, reason: str, actor: str
    ) -> str:
        marker_id = digest([scope.model_dump(), kind, identifier])
        await self.operate(
            scope.channel_id,
            required=True,
            entry={
                "id": marker_id,
                "scope": scope.model_dump(),
                "target_type": kind,
                "target_id": identifier,
                "reason_code": reason,
                "requested_by": actor,
                "requested_at": utcnow().isoformat(),
            },
        )
        return marker_id
