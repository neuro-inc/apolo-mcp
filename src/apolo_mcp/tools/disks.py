"""Bounded, context-explicit persistent disk tools."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import apolo_sdk
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import (
    ensure_ledger_writable,
    record_created_resource,
    record_resource_action,
)
from ..policy import MutationEffect, authorize_mutation


READ_ONLY = ToolAnnotations(
    title="Read persistent disks",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    title="Create a persistent disk",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Delete an exact persistent disk",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

MAX_LIST_RESULTS = 100
MIN_DISK_GB = 1
MAX_DISK_GB = 16_384
MIN_UNUSED_HOURS = 1.0 / 60.0
MAX_UNUSED_HOURS = 24.0 * 365.0 * 10.0


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _disk(item: apolo_sdk.Disk) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "owner": item.owner,
        "status": item.status.value,
        "storage_bytes": item.storage,
        "used_bytes": item.used_bytes,
        "uri": str(item.uri),
        "cluster": item.cluster_name,
        "org": item.org_name,
        "project": item.project_name,
        "created_at": _iso(item.created_at),
        "last_usage": _iso(item.last_usage),
        "timeout_unused_seconds": (
            item.timeout_unused.total_seconds() if item.timeout_unused else None
        ),
    }


def _assert_context(item: apolo_sdk.Disk, context: ApoloContext) -> None:
    actual = (item.cluster_name, item.org_name, item.project_name)
    expected = (context.cluster, context.org, context.project)
    if actual != expected:
        raise ValueError("disk does not belong to the exact resolved context")


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_disks(
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List persistent disks under a strict result bound."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                items: list[dict[str, Any]] = []
                async for item in sdk.disks.list(
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                ):
                    _assert_context(item, resolved)
                    items.append(_disk(item))
                    if len(items) > limit:
                        break
                return {
                    "items": items[:limit],
                    "limit": limit,
                    "truncated": len(items) > limit,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_disks",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_disk(
        size_gb: int,
        name: str | None = None,
        timeout_unused_hours: float | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create and journal a bounded disk when server policy permits writes."""
        authorize_mutation(operation="create_disk", effect=MutationEffect.CREATE)
        if isinstance(size_gb, bool) or not MIN_DISK_GB <= size_gb <= MAX_DISK_GB:
            raise ValueError(f"size_gb must be between {MIN_DISK_GB} and {MAX_DISK_GB}")
        if name is not None and (not name.strip() or len(name) > 255):
            raise ValueError("name must contain 1 to 255 non-whitespace characters")
        if timeout_unused_hours is not None and not (
            MIN_UNUSED_HOURS <= timeout_unused_hours <= MAX_UNUSED_HOURS
        ):
            raise ValueError(
                "timeout_unused_hours must be between 1 minute and 10 years"
            )
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                ensure_ledger_writable()
                item = await sdk.disks.create(
                    storage=size_gb * 1024**3,
                    timeout_unused=(
                        timedelta(hours=timeout_unused_hours)
                        if timeout_unused_hours is not None
                        else None
                    ),
                    name=name,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _assert_context(item, resolved)
                record_created_resource(
                    resource_type="disk",
                    resource_id=item.id,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="create_disk",
                )
                return {"disk": _disk(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="create_disk",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_disk(
        disk_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact disk ID under full or ledger-owned managed policy."""
        if not disk_id.strip() or "://" in disk_id or "/" in disk_id:
            raise ValueError("disk_id must be one exact opaque disk ID")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await sdk.disks.get(
                    disk_id,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _assert_context(item, resolved)
                if item.id != disk_id:
                    raise ValueError(
                        "disk_id resolved as a name or alias; deletion requires "
                        "the exact immutable disk ID"
                    )
                authorize_mutation(
                    operation="delete_disk",
                    effect=MutationEffect.DELETE,
                    resource_type="disk",
                    resource_id=item.id,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                await sdk.disks.rm(
                    item.id,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                record_resource_action(
                    resource_type="disk",
                    resource_id=item.id,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="delete_disk",
                    action="deleted",
                )
                return {
                    "status": "deleted",
                    "id": item.id,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_disk",
                context=resolved.as_dict() if resolved else None,
                resource=disk_id,
            ) from None
