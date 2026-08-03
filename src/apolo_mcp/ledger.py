"""Durable append-only lifecycle journal for resources managed by apolo-mcp."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


LEDGER_ENV = "APOLO_MCP_LEDGER_PATH"
_SAFE_FIELDS = {
    "resource_type",
    "resource_id",
    "username",
    "cluster",
    "org",
    "project",
    "created_at",
    "operation",
    "action",
}
_LIFECYCLE_STATES = {"created", "updated", "deleted"}
_CREDENTIAL = re.compile(
    r"(?i)(?:"
    r"\b(?:authorization|cookie|token|password|secret|api[-_]?key)\s*[:=]\s*\S+"
    r"|\b(?:bearer|basic)\s+\S+"
    r"|://[^/\s:]+:[^@\s]+@"
    r"|-----BEGIN [^-]*PRIVATE KEY-----"
    r")"
)


def _default_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local/state"
    return base / "apolo-mcp" / "ledger.jsonl"


def ledger_path() -> Path:
    """Return the configured ledger path, or the per-user state default."""
    configured = os.environ.get(LEDGER_ENV)
    return Path(configured).expanduser() if configured else _default_path()


def redact_credentials(value: str) -> str:
    """Redact recognizable credential material without echoing it."""
    return _CREDENTIAL.sub("<redacted>", value)


def _safe_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ledger {field} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"ledger {field} contains control characters")
    if redact_credentials(value) != value:
        raise ValueError(f"ledger {field} contains prohibited credential material")
    return value


@dataclass(frozen=True)
class LedgerEntry:
    """The complete, deliberately small set of persisted ownership metadata."""

    resource_type: str
    resource_id: str
    username: str
    cluster: str
    org: str
    project: str
    created_at: str
    operation: str
    action: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LedgerEntry:
        if set(value) != _SAFE_FIELDS:
            raise ValueError("ledger entry has unexpected or missing fields")
        entry = cls(
            **{field: _safe_text(value[field], field) for field in _SAFE_FIELDS}
        )
        try:
            parsed = datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("ledger created_at is not an ISO-8601 datetime") from exc
        if parsed.tzinfo is None:
            raise ValueError("ledger created_at must include a timezone")
        if entry.action not in _LIFECYCLE_STATES:
            raise ValueError("ledger action must be created, updated, or deleted")
        return entry

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class Ledger:
    """Append-only JSONL ledger with exact, context-bound ownership lookup."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else ledger_path()

    def _open(self, *, create: bool) -> int:
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_mode = parent.lstat().st_mode
        if stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(parent_mode):
            raise ValueError("ledger parent must be a real directory, not a symlink")
        os.chmod(parent, 0o700)
        flags = os.O_RDWR | os.O_APPEND
        if create:
            flags |= os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            os.close(fd)
            raise ValueError("ledger path must be a regular file")
        os.fchmod(fd, 0o600)
        return fd

    def ensure_writable(self) -> None:
        """Create and lock the ledger so creation can fail before an SDK write."""
        fd = self._open(create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def append(
        self,
        *,
        resource_type: str,
        resource_id: str,
        username: str,
        cluster: str,
        org: str,
        project: str,
        operation: str,
        action: str,
        created_at: datetime | None = None,
    ) -> LedgerEntry:
        """Validate and durably append one resource ownership record."""
        timestamp = created_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("ledger created_at must include a timezone")
        entry = LedgerEntry.from_dict(
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "username": username,
                "cluster": cluster,
                "org": org,
                "project": project,
                "created_at": timestamp.astimezone(timezone.utc).isoformat(),
                "operation": operation,
                "action": action,
            }
        )
        payload = (
            json.dumps(entry.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        fd = self._open(create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            offset = 0
            while offset < len(payload):
                offset += os.write(fd, payload[offset:])
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return entry

    def entries(self) -> list[LedgerEntry]:
        """Read all entries, failing closed if the audit trail is malformed."""
        if not self.path.exists():
            return []
        fd = self._open(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            with os.fdopen(os.dup(fd), encoding="utf-8") as stream:
                records = [
                    LedgerEntry.from_dict(json.loads(line))
                    for line in stream
                    if line.strip()
                ]
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return records

    def history(
        self,
        *,
        resource_type: str,
        resource_id: str,
        username: str,
        cluster: str,
        org: str,
        project: str,
    ) -> list[LedgerEntry]:
        """Return lifecycle history for one exact resource and context."""
        expected = (
            resource_type,
            resource_id,
            username,
            cluster,
            org,
            project,
        )
        result: list[LedgerEntry] = []
        for entry in self.entries():
            actual = (
                entry.resource_type,
                entry.resource_id,
                entry.username,
                entry.cluster,
                entry.org,
                entry.project,
            )
            if actual == expected:
                result.append(entry)
        return result

    def lookup(
        self,
        *,
        resource_type: str,
        resource_id: str,
        username: str,
        cluster: str,
        org: str,
        project: str,
    ) -> LedgerEntry | None:
        """Return the latest lifecycle entry for one exact resource."""
        history = self.history(
            resource_type=resource_type,
            resource_id=resource_id,
            username=username,
            cluster=cluster,
            org=org,
            project=project,
        )
        return history[-1] if history else None

    def authorize_owned_resource(
        self,
        *,
        resource_type: str,
        resource_id: str,
        username: str,
        cluster: str,
        org: str,
        project: str,
    ) -> LedgerEntry:
        """Require an active creation lifecycle in this exact context."""
        history = self.history(
            resource_type=resource_type,
            resource_id=resource_id,
            username=username,
            cluster=cluster,
            org=org,
            project=project,
        )
        last_deleted = max(
            (index for index, entry in enumerate(history) if entry.action == "deleted"),
            default=-1,
        )
        created = any(
            entry.action == "created" for entry in history[last_deleted + 1 :]
        )
        if not history or not created or history[-1].action == "deleted":
            raise PermissionError(
                "managed mutation denied: no active creation lifecycle for exact "
                "resource and context"
            )
        return history[-1]


def record_created_resource(
    *,
    resource_type: str,
    resource_id: str,
    username: str,
    cluster: str,
    org: str,
    project: str,
    operation: str,
) -> LedgerEntry:
    """Record a resource creation in the configured ledger."""
    return Ledger().append(
        resource_type=resource_type,
        resource_id=resource_id,
        username=username,
        cluster=cluster,
        org=org,
        project=project,
        operation=operation,
        action="created",
    )


def record_resource_action(
    *,
    resource_type: str,
    resource_id: str,
    username: str,
    cluster: str,
    org: str,
    project: str,
    operation: str,
    action: str,
) -> LedgerEntry:
    """Append one successful created, updated, or deleted lifecycle action."""
    return Ledger().append(
        resource_type=resource_type,
        resource_id=resource_id,
        username=username,
        cluster=cluster,
        org=org,
        project=project,
        operation=operation,
        action=action,
    )


def ensure_ledger_writable() -> None:
    """Preflight the configured ledger before creating a remote resource."""
    Ledger().ensure_writable()


def authorize_owned_resource(
    *,
    resource_type: str,
    resource_id: str,
    username: str,
    cluster: str,
    org: str,
    project: str,
) -> LedgerEntry:
    """Authorize a managed mutation against an active creation lifecycle."""
    return Ledger().authorize_owned_resource(
        resource_type=resource_type,
        resource_id=resource_id,
        username=username,
        cluster=cluster,
        org=org,
        project=project,
    )
