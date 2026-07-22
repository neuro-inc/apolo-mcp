"""Durable ownership ledger for resources created by apolo-mcp."""

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
    "cluster",
    "org",
    "project",
    "created_at",
    "operation",
    "state",
}
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
    cluster: str
    org: str
    project: str
    created_at: str
    operation: str
    state: str

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
        cluster: str,
        org: str,
        project: str,
        operation: str,
        state: str = "created",
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
                "cluster": cluster,
                "org": org,
                "project": project,
                "created_at": timestamp.astimezone(timezone.utc).isoformat(),
                "operation": operation,
                "state": state,
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

    def lookup(
        self,
        *,
        resource_type: str,
        resource_id: str,
        cluster: str,
        org: str,
        project: str,
    ) -> LedgerEntry | None:
        """Find ownership by exact type, identifier, and resolved context only."""
        expected = (
            resource_type,
            resource_id,
            cluster,
            org,
            project,
        )
        for entry in reversed(self.entries()):
            actual = (
                entry.resource_type,
                entry.resource_id,
                entry.cluster,
                entry.org,
                entry.project,
            )
            if actual == expected:
                return entry
        return None

    def authorize_cleanup(
        self,
        *,
        resource_type: str,
        resource_id: str,
        cluster: str,
        org: str,
        project: str,
    ) -> LedgerEntry:
        """Require an exact ownership record before destructive cleanup."""
        entry = self.lookup(
            resource_type=resource_type,
            resource_id=resource_id,
            cluster=cluster,
            org=org,
            project=project,
        )
        if entry is None:
            raise PermissionError(
                "cleanup denied: no exact ledger ownership record "
                "for resource and context"
            )
        return entry


def record_created_resource(
    *,
    resource_type: str,
    resource_id: str,
    cluster: str,
    org: str,
    project: str,
    operation: str,
) -> LedgerEntry:
    """Record a resource creation in the configured ledger."""
    return Ledger().append(
        resource_type=resource_type,
        resource_id=resource_id,
        cluster=cluster,
        org=org,
        project=project,
        operation=operation,
    )


def ensure_ledger_writable() -> None:
    """Preflight the configured ledger before creating a remote resource."""
    Ledger().ensure_writable()


def authorize_cleanup(
    *,
    resource_type: str,
    resource_id: str,
    cluster: str,
    org: str,
    project: str,
) -> LedgerEntry:
    """Authorize cleanup against the configured ledger."""
    return Ledger().authorize_cleanup(
        resource_type=resource_type,
        resource_id=resource_id,
        cluster=cluster,
        org=org,
        project=project,
    )
