"""Bounded read-only Apolo administration discovery tools."""

from __future__ import annotations

from enum import Enum
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .._client import client
from ..errors import normalize_error


READ_ONLY = ToolAnnotations(
    title="Read Apolo administration metadata",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

MAX_LIST_RESULTS = 1000


def _context(sdk: Any) -> dict[str, Any]:
    """Return selected config metadata without requiring a workload project."""
    return {
        "username": sdk.config.username,
        "cluster": sdk.config.cluster_name,
        "org": sdk.config.org_name,
        "project": sdk.config.project_name,
    }


def _name(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "://" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field} must be one exact non-empty name")
    return value


def _limit(items: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    if not 1 <= limit <= MAX_LIST_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
    return {
        "items": items[:limit],
        "limit": limit,
        "truncated": len(items) > limit,
        "total_returned_by_server": len(items),
    }


def _value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _quota(item: Any) -> dict[str, Any]:
    return {"total_running_jobs": item.total_running_jobs}


def _balance(item: Any) -> dict[str, Any]:
    return {
        "credits": _value(item.credits),
        "spent_credits": _value(item.spent_credits),
    }


def _user_info(item: Any) -> dict[str, Any] | None:
    info = getattr(item, "user_info", None)
    if info is None:
        return None
    return {
        "email": info.email,
        "first_name": info.first_name,
        "last_name": info.last_name,
        "created_at": info.created_at.isoformat() if info.created_at else None,
    }


def _cluster(item: Any) -> dict[str, Any]:
    return {
        "name": item.name,
        "default_credits": _value(item.default_credits),
        "default_quota": _quota(item.default_quota),
        "default_role": _value(item.default_role),
        "maintenance": item.maintenance,
    }


def _cluster_user(item: Any) -> dict[str, Any]:
    return {
        "cluster": item.cluster_name,
        "org": item.org_name,
        "username": item.user_name,
        "role": _value(item.role),
        "quota": _quota(item.quota),
        "balance": _balance(item.balance),
        "user_info": _user_info(item),
    }


def _org(item: Any) -> dict[str, Any]:
    return {
        "name": item.name,
        "balance": _balance(item.balance),
        "user_default_credits": _value(item.user_default_credits),
    }


def _org_user(item: Any) -> dict[str, Any]:
    return {
        "org": item.org_name,
        "username": item.user_name,
        "role": _value(item.role),
        "balance": _balance(item.balance),
        "user_info": _user_info(item),
    }


def _org_cluster(item: Any) -> dict[str, Any]:
    return {
        "org": item.org_name,
        "cluster": item.cluster_name,
        "balance": _balance(item.balance),
        "quota": _quota(item.quota),
        "default_credits": _value(item.default_credits),
        "default_quota": _quota(item.default_quota),
        "default_role": _value(item.default_role),
        "storage_size": item.storage_size,
        "maintenance": item.maintenance,
    }


def _project(item: Any) -> dict[str, Any]:
    return {
        "name": item.name,
        "cluster": item.cluster_name,
        "org": item.org_name,
        "is_default": item.is_default,
        "default_role": _value(item.default_role),
        "has_virtual_kube": item.has_virtual_kube,
    }


def _project_user(item: Any) -> dict[str, Any]:
    return {
        "username": item.user_name,
        "cluster": item.cluster_name,
        "org": item.org_name,
        "project": item.project_name,
        "role": _value(item.role),
        "user_info": _user_info(item),
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_clusters(limit: int = 100) -> dict[str, Any]:
        """List administrative cluster defaults and maintenance state."""
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                result = _limit(
                    [_cluster(item) for item in await sdk._admin.list_clusters()],
                    limit,
                )
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_clusters",
                context=resolved if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_cluster_users(
        cluster_name: str,
        org_name: str | None = None,
        include_details: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List users and roles in one exact cluster and optional organization."""
        cluster_value = _name(cluster_name, "cluster_name")
        org_value = _name(org_name, "org_name") if org_name is not None else None
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                if include_details:
                    serialized = [
                        _cluster_user(item)
                        for item in await sdk._admin.list_cluster_users(
                            cluster_name=cluster_value,
                            with_user_info=True,
                            org_name=org_value,
                        )
                    ]
                else:
                    serialized = [
                        _cluster_user(item)
                        for item in await sdk._admin.list_cluster_users(
                            cluster_name=cluster_value,
                            with_user_info=False,
                            org_name=org_value,
                        )
                    ]
                result = _limit(serialized, limit)
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_cluster_users",
                context=resolved if resolved else None,
                resource=cluster_value,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_orgs(limit: int = 100) -> dict[str, Any]:
        """List administrative organization metadata."""
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                result = _limit(
                    [_org(item) for item in await sdk._admin.list_orgs()], limit
                )
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_orgs",
                context=resolved if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_org_users(
        org_name: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List users, roles, balances, and safe profile fields in one org."""
        org_value = _name(org_name, "org_name")
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                items = await sdk._admin.list_org_users(org_value, with_user_info=True)
                result = _limit([_org_user(item) for item in items], limit)
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_org_users",
                context=resolved if resolved else None,
                resource=org_value,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_cluster_orgs(
        cluster_name: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List organizations and quotas configured in one exact cluster."""
        cluster_value = _name(cluster_name, "cluster_name")
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                items = await sdk._admin.list_org_clusters(cluster_name=cluster_value)
                result = _limit([_org_cluster(item) for item in items], limit)
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_cluster_orgs",
                context=resolved if resolved else None,
                resource=cluster_value,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_admin_org_cluster_quota(
        cluster_name: str,
        org_name: str,
    ) -> dict[str, Any]:
        """Get quota and balance for one organization in one cluster."""
        cluster_value = _name(cluster_name, "cluster_name")
        org_value = _name(org_name, "org_name")
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                item = await sdk._admin.get_org_cluster(
                    cluster_name=cluster_value, org_name=org_value
                )
                return {
                    "org_cluster": _org_cluster(item),
                    "context": resolved,
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_admin_org_cluster_quota",
                context=resolved if resolved else None,
                resource=f"{cluster_value}/{org_value}",
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_projects(
        cluster_name: str,
        org_name: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List projects in one exact cluster and optional organization."""
        cluster_value = _name(cluster_name, "cluster_name")
        org_value = _name(org_name, "org_name") if org_name is not None else None
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                items = await sdk._admin.list_projects(
                    cluster_name=cluster_value, org_name=org_value
                )
                result = _limit([_project(item) for item in items], limit)
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_projects",
                context=resolved if resolved else None,
                resource=cluster_value,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_admin_project_users(
        cluster_name: str,
        project_name: str,
        org_name: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List users and roles in one exact project."""
        cluster_value = _name(cluster_name, "cluster_name")
        project_value = _name(project_name, "project_name")
        org_value = _name(org_name, "org_name") if org_name is not None else None
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                items = await sdk._admin.list_project_users(
                    project_name=project_value,
                    cluster_name=cluster_value,
                    org_name=org_value,
                    with_user_info=True,
                )
                result = _limit([_project_user(item) for item in items], limit)
                return {**result, "context": resolved}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_admin_project_users",
                context=resolved if resolved else None,
                resource=f"{cluster_value}/{org_value or '-'}/{project_value}",
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_admin_user_quota(
        cluster_name: str,
        org_name: str,
        username: str,
    ) -> dict[str, Any]:
        """Get the same user quota and org balance data as apolo admin."""
        cluster_value = _name(cluster_name, "cluster_name")
        org_value = _name(org_name, "org_name")
        user_value = _name(username, "username")
        resolved: dict[str, Any] | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk)
                cluster_user = await sdk._admin.get_cluster_user(
                    cluster_name=cluster_value,
                    user_name=user_value,
                    org_name=org_value,
                )
                org_user = await sdk._admin.get_org_user(
                    org_name=org_value, user_name=user_value
                )
                return {
                    "username": user_value,
                    "cluster": cluster_value,
                    "org": org_value,
                    "quota": _quota(cluster_user.quota),
                    "balance": _balance(org_user.balance),
                    "context": resolved,
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_admin_user_quota",
                context=resolved if resolved else None,
                resource=f"{cluster_value}/{org_value}/{user_value}",
            ) from None
