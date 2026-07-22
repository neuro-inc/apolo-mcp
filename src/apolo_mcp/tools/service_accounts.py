"""Service-account tools with one-time tokens confined to secure sinks."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import authorize_cleanup, ensure_ledger_writable, record_created_resource
from ..policy import Policy
from .secrets import ALLOWED_WORKSPACE_ENV, _key


READ_ONLY = ToolAnnotations(
    title="Read Apolo service-account metadata",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    title="Create an Apolo service account into a secure token sink",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Delete one exact Apolo service account",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

MAX_LIST_RESULTS = 100
MAX_LIST_SCAN = 1000


def _context(
    sdk: Any, cluster: str | None, org: str | None, project: str | None
) -> ApoloContext:
    return resolve_context(sdk.config, cluster=cluster, org=org, project=project)


def _id(value: str, field: str = "account_id") -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "/" in value
        or "://" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field} must be one exact opaque identifier")
    return value


def _account(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "owner": item.owner,
        "role": item.role,
        "created_at": item.created_at.isoformat(),
        "default_cluster": item.default_cluster,
        "default_org": item.default_org,
        "default_project": item.default_project,
    }


def _assert_context(item: Any, context: ApoloContext) -> None:
    actual = (item.default_cluster, item.default_org or "", item.default_project)
    expected = (context.cluster, context.org, context.project)
    if actual != expected:
        raise ValueError(
            "service account defaults do not match the exact resolved context"
        )


def _reserve_file(destination: str) -> tuple[int, Path]:
    configured = os.environ.get(ALLOWED_WORKSPACE_ENV)
    if not configured:
        raise PermissionError(f"{ALLOWED_WORKSPACE_ENV} must be configured")
    configured_root = Path(configured).expanduser()
    root_info = configured_root.lstat()
    if stat.S_ISLNK(root_info.st_mode):
        raise ValueError("allowed workspace must not be a symlink")
    root = configured_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("allowed workspace must be a real directory")
    requested = Path(destination).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    if not requested.name or requested.name in {".", ".."}:
        raise ValueError("destination_file must name one exact file")
    prospective_parent = requested.parent.resolve(strict=False)
    if prospective_parent != root and root not in prospective_parent.parents:
        raise PermissionError("destination_file must be beneath the allowed workspace")
    lexical_parent = requested.parent
    probe = lexical_parent
    while probe != root:
        if probe.exists() and stat.S_ISLNK(probe.lstat().st_mode):
            raise ValueError("destination_file parents must not be symlinks")
        if probe.parent == probe:
            break
        probe = probe.parent
    requested.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = requested.parent.resolve(strict=True)
    target = parent / requested.name
    if parent != root and root not in parent.parents:
        raise PermissionError("destination_file must be beneath the allowed workspace")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    os.fchmod(fd, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        target.unlink(missing_ok=True)
        raise ValueError("destination_file must be a regular file")
    return fd, target


def _atomic_sink(target: Path, payload: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


async def _secret_available(sdk: Any, key: str, context: ApoloContext) -> None:
    async with sdk.secrets.list(
        cluster_name=context.cluster,
        org_name=context.org,
        project_name=context.project,
    ) as iterator:
        async for item in iterator:
            if (
                item.key == key
                and item.cluster_name == context.cluster
                and item.org_name == context.org
                and item.project_name == context.project
            ):
                raise FileExistsError("destination key already exists")


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_service_accounts(
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List accounts whose defaults match one exact resolved context."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                matching: list[dict[str, Any]] = []
                scanned = 0
                scan_limited = False
                result_limited = False
                async with sdk.service_accounts.list() as iterator:
                    async for item in iterator:
                        scanned += 1
                        actual = (
                            item.default_cluster,
                            item.default_org or "",
                            item.default_project,
                        )
                        expected = (
                            resolved.cluster,
                            resolved.org,
                            resolved.project,
                        )
                        if actual == expected:
                            matching.append(_account(item))
                            if len(matching) > limit:
                                result_limited = True
                                break
                        if scanned >= MAX_LIST_SCAN:
                            scan_limited = True
                            break
                return {
                    "items": matching[:limit],
                    "limit": limit,
                    "truncated": result_limited or scan_limited,
                    "complete": not (result_limited or scan_limited),
                    "scanned": scanned,
                    "scan_limit": MAX_LIST_SCAN,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_service_accounts",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_service_account(
        account_id_or_name: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get safe service-account metadata in one exact default context."""
        value = _id(account_id_or_name, "account_id_or_name")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                item = await sdk.service_accounts.get(value)
                _assert_context(item, resolved)
                return {"account": _account(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_service_account",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_service_account(
        destination_type: Literal["file", "secret"],
        destination_name: str,
        name: str | None = None,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create an account and sink its token without returning token material."""
        Policy.load().require_high_risk("create_service_account")
        if not approved:
            raise PermissionError("create_service_account requires approved=true")
        if name is not None:
            _id(name, "name")
        resolved: ApoloContext | None = None
        reserved_fd: int | None = None
        reserved_path: Path | None = None
        completed = False
        try:
            if destination_type == "file":
                reserved_fd, reserved_path = _reserve_file(destination_name)
            else:
                destination_name = _key(destination_name)
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                ensure_ledger_writable()
                if destination_type == "secret":
                    await _secret_available(sdk, destination_name, resolved)
                item, token = await sdk.service_accounts.create(
                    name=name,
                    default_cluster=resolved.cluster,
                    default_org=resolved.org,
                    default_project=resolved.project,
                )
                _assert_context(item, resolved)
                record_created_resource(
                    resource_type="service_account",
                    resource_id=item.id,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="create_service_account",
                )
                destination: dict[str, Any]
                if destination_type == "file":
                    assert reserved_fd is not None and reserved_path is not None
                    _atomic_sink(reserved_path, token.encode())
                    destination = {
                        "type": "file",
                        "path": str(reserved_path),
                        "mode": "0600",
                    }
                else:
                    try:
                        await sdk.secrets.add(
                            destination_name,
                            token.encode(),
                            cluster_name=resolved.cluster,
                            org_name=resolved.org,
                            project_name=resolved.project,
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            f"{type(exc).__name__} while storing account token"
                        ) from None
                    destination = {
                        "type": "secret",
                        "key": destination_name,
                        "context": resolved.as_dict(),
                    }
                completed = True
                return {
                    "account": _account(item),
                    "destination": destination,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="create_service_account",
                context=resolved.as_dict() if resolved else None,
            ) from None
        finally:
            if reserved_fd is not None:
                os.close(reserved_fd)
            if reserved_path is not None and not completed:
                reserved_path.unlink(missing_ok=True)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_service_account(
        account_id: str,
        approved: bool = False,
        automatic_cleanup: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact immutable account ID after context verification."""
        Policy.load().require_high_risk("delete_service_account")
        if not automatic_cleanup and not approved:
            raise PermissionError("delete_service_account requires approved=true")
        value = _id(account_id)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                item = await sdk.service_accounts.get(value)
                _assert_context(item, resolved)
                if item.id != value:
                    raise ValueError(
                        "account_id must be the exact immutable service-account ID"
                    )
                if automatic_cleanup:
                    authorize_cleanup(
                        resource_type="service_account",
                        resource_id=item.id,
                        cluster=resolved.cluster,
                        org=resolved.org,
                        project=resolved.project,
                    )
                await sdk.service_accounts.rm(item.id)
                return {
                    "status": "deleted",
                    "id": item.id,
                    "automatic_cleanup": automatic_cleanup,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_service_account",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None
