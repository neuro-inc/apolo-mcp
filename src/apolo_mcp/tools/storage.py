"""Bounded, context-explicit access to small text objects in Apolo storage."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import apolo_sdk
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from yarl import URL

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import ensure_ledger_writable, record_resource_action
from ..policy import MutationEffect, authorize_mutation
from ..workspace import resolve_new_workspace_file, resolve_workspace_path


READ_ONLY = ToolAnnotations(
    title="Read Apolo storage metadata or bounded text",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
WRITE = ToolAnnotations(
    title="Write an exact Apolo storage target",
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Delete an exact Apolo storage target",
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)

MAX_LIST_RESULTS = 100
MAX_TEXT_BYTES = 1_048_576
_CREDENTIAL = re.compile(
    r"(?i)(authorization|cookie|token|password|secret|api[-_]?key)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_URL_CREDENTIAL = re.compile(r"(://)[^/@\s]+@")


def _storage_uri(path: str, context: ApoloContext, *, allow_root: bool = True) -> URL:
    if not isinstance(path, str) or any(ord(char) < 32 for char in path):
        raise ValueError("storage path must be a string without control characters")
    raw = URL(path) if "://" in path else None
    if raw is not None:
        if raw.scheme not in {"storage", "apolo"}:
            raise ValueError("storage path URI must use storage:// or apolo://")
        if raw.user is not None or raw.password is not None:
            raise ValueError("credentials are prohibited in storage URIs")
        if raw.query_string or raw.fragment:
            raise ValueError("storage URIs must not contain query strings or fragments")
        parts = [part for part in raw.path.split("/") if part]
        if raw.host != context.cluster or len(parts) < 2:
            raise ValueError("full storage URI must include the exact resolved context")
        uri_org = parts[0]
        uri_project = parts[1]
        if (raw.host, uri_org, uri_project) != (
            context.cluster,
            context.org,
            context.project,
        ):
            raise ValueError("storage URI does not belong to the resolved context")
        relative = parts[2:]
    else:
        relative = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in relative):
        raise ValueError("storage path must not contain '.' or '..' components")
    if not allow_root and not relative:
        raise ValueError("project storage root cannot be modified or deleted")
    base = URL.build(
        scheme="storage",
        host=context.cluster,
        path=f"/{context.org}/{context.project}/",
    )
    return base.with_path(base.path + "/".join(relative))


def _file_status(
    item: apolo_sdk.FileStatus, *, canonical_uri: URL | None = None
) -> dict[str, Any]:
    return {
        "name": item.name,
        "path": item.path,
        "uri": str(canonical_uri or item.uri),
        "type": item.type.value,
        "size_bytes": item.size,
        "modification_time": item.modification_time,
        "target": item.target,
    }


async def _exists(sdk: Any, uri: URL) -> bool:
    try:
        await sdk.storage.stat(uri)
    except apolo_sdk.ResourceNotFound:
        return False
    return True


def _transfer_timeout(timeout_seconds: float | None) -> None:
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive when provided")


async def _await_transfer(operation: Any, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None:
        return await operation
    return await asyncio.wait_for(operation, timeout=timeout_seconds)


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_storage(
        path: str = "",
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List a bounded storage directory; the response marks truncation."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved)
                items: list[dict[str, Any]] = []
                async for item in sdk.storage.list(uri):
                    items.append(_file_status(item))
                    if len(items) > limit:
                        break
                return {
                    "path": str(uri),
                    "items": items[:limit],
                    "limit": limit,
                    "truncated": len(items) > limit,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_storage",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def stat_storage(
        path: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Return metadata for one exact storage path."""
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved)
                status = await sdk.storage.stat(uri)
                return {
                    "item": _file_status(status, canonical_uri=uri),
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="stat_storage",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def read_text(
        path: str,
        max_bytes: int = 65_536,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Read a UTF-8 text prefix under a strict byte cap."""
        if not 1 <= max_bytes <= MAX_TEXT_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_TEXT_BYTES}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved)
                chunks: list[bytes] = []
                async for chunk in sdk.storage.open(uri, size=max_bytes + 1):
                    chunks.append(chunk)
                    if sum(map(len, chunks)) > max_bytes:
                        break
                raw = b"".join(chunks)
                truncated = len(raw) > max_bytes
                raw = raw[:max_bytes]
                try:
                    text = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    # A byte cap may bisect an otherwise valid final code point.
                    # Drop only that incomplete suffix; invalid UTF-8 elsewhere fails.
                    if not truncated or exc.end != len(raw):
                        raise
                    raw = raw[: exc.start]
                    text = raw.decode("utf-8", errors="strict")
                redacted_text = _URL_CREDENTIAL.sub(r"\1<redacted>@", text)
                redacted_text = _CREDENTIAL.sub(r"\1\2<redacted>", redacted_text)
                redacted = redacted_text != text
                redacted_raw = redacted_text.encode("utf-8")
                if len(redacted_raw) > max_bytes:
                    redacted_raw = redacted_raw[:max_bytes]
                    redacted_text = redacted_raw.decode("utf-8", errors="ignore")
                    redacted_raw = redacted_text.encode("utf-8")
                    truncated = True
                return {
                    "path": str(uri),
                    "text": redacted_text,
                    "bytes": len(redacted_raw),
                    "max_bytes": max_bytes,
                    "truncated": truncated,
                    "redacted": redacted,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="read_text",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def write_text(
        path: str,
        content: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a small UTF-8 object under server policy."""
        authorize_mutation(operation="write_text", effect=MutationEffect.CREATE)
        encoded = content.encode("utf-8", errors="strict")
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError(f"UTF-8 content must not exceed {MAX_TEXT_BYTES} bytes")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved, allow_root=False)
                exists = await _exists(sdk, uri)
                effect = MutationEffect.UPDATE if exists else MutationEffect.CREATE
                authorize_mutation(
                    operation="write_text",
                    effect=effect,
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                if not exists:
                    ensure_ledger_writable()
                await sdk.storage.create(uri, encoded)
                record_resource_action(
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="write_text",
                    action="updated" if exists else "created",
                )
                return {
                    "status": "written",
                    "path": str(uri),
                    "bytes": len(encoded),
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="write_text",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def upload_storage_file(
        local_path: str,
        path: str,
        timeout_seconds: float | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Upload one local file to an exact storage path.

        File bytes are never serialized through MCP. Existing remote files may be
        overwritten only under full or exact ledger-owned managed policy. An optional
        positive timeout can bound the transfer when requested by the caller.
        """
        authorize_mutation(
            operation="upload_storage_file", effect=MutationEffect.CREATE
        )
        _transfer_timeout(timeout_seconds)
        source = resolve_workspace_path(local_path, name="local_path", directory=False)
        size = source.stat().st_size
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved, allow_root=False)
                exists = await _exists(sdk, uri)
                effect = MutationEffect.UPDATE if exists else MutationEffect.CREATE
                authorize_mutation(
                    operation="upload_storage_file",
                    effect=effect,
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                if not exists:
                    ensure_ledger_writable()
                await _await_transfer(
                    sdk.storage.upload_file(
                        URL(source.as_uri()), uri, update=False, continue_=False
                    ),
                    timeout_seconds,
                )
                status = await sdk.storage.stat(uri)
                if (
                    status.type is not apolo_sdk.FileStatusType.FILE
                    or status.size != size
                ):
                    raise RuntimeError(
                        "uploaded file size does not match local file metadata"
                    )
                record_resource_action(
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="upload_storage_file",
                    action="updated" if exists else "created",
                )
                return {
                    "status": "uploaded",
                    "local_path": str(source),
                    "path": str(uri),
                    "size_bytes": size,
                    "timeout_seconds": timeout_seconds,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="upload_storage_file",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def download_storage_file(
        path: str,
        local_path: str,
        timeout_seconds: float | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Download one storage file to a new confined local file.

        Existing local files are never overwritten and file bytes are never serialized
        through MCP. An optional positive timeout can bound the transfer when requested
        by the caller.
        """
        authorize_mutation(
            operation="download_storage_file", effect=MutationEffect.CREATE
        )
        _transfer_timeout(timeout_seconds)
        destination = resolve_new_workspace_file(
            local_path, name="local_path", create_parents=True
        )
        resolved: ApoloContext | None = None
        completed = False
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved, allow_root=False)
                status = await sdk.storage.stat(uri)
                if status.type is not apolo_sdk.FileStatusType.FILE:
                    raise ValueError("remote storage path must be a file")
                await _await_transfer(
                    sdk.storage.download_file(
                        uri,
                        URL(destination.as_uri()),
                        update=False,
                        continue_=False,
                    ),
                    timeout_seconds,
                )
                actual_size = destination.stat().st_size
                if actual_size != status.size:
                    raise RuntimeError(
                        "downloaded file size does not match remote metadata"
                    )
                completed = True
                return {
                    "status": "downloaded",
                    "path": str(uri),
                    "local_path": str(destination),
                    "size_bytes": actual_size,
                    "timeout_seconds": timeout_seconds,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="download_storage_file",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None
        finally:
            if not completed:
                destination.unlink(missing_ok=True)

    @mcp.tool(annotations=WRITE)
    async def make_directory(
        path: str,
        parents: bool = True,
        exist_ok: bool = True,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create an exact directory under server policy."""
        authorize_mutation(operation="make_directory", effect=MutationEffect.CREATE)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved, allow_root=False)
                exists = await _exists(sdk, uri)
                effect = MutationEffect.UPDATE if exists else MutationEffect.CREATE
                authorize_mutation(
                    operation="make_directory",
                    effect=effect,
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                if not exists:
                    ensure_ledger_writable()
                await sdk.storage.mkdir(uri, parents=parents, exist_ok=exist_ok)
                record_resource_action(
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="make_directory",
                    action="updated" if exists else "created",
                )
                return {
                    "status": "created",
                    "path": str(uri),
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="make_directory",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_storage_path(
        path: str,
        recursive: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact path; recursive deletion removes its entire subtree."""
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                uri = _storage_uri(path, resolved, allow_root=False)
                authorize_mutation(
                    operation="delete_storage_path",
                    effect=MutationEffect.DELETE,
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                await sdk.storage.rm(uri, recursive=recursive)
                record_resource_action(
                    resource_type="storage",
                    resource_id=str(uri),
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="delete_storage_path",
                    action="deleted",
                )
                return {
                    "status": "deleted",
                    "path": str(uri),
                    "recursive": recursive,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_storage_path",
                context=resolved.as_dict() if resolved else None,
                resource=path,
            ) from None
