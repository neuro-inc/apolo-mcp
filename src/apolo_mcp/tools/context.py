"""Read-only context discovery tools; no operation persists context changes."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

import apolo_sdk
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .. import __version__
from .._client import client
from ..context import resolve_context
from ..errors import normalize_error


READ_ONLY = ToolAnnotations(
    title="Read-only Apolo discovery",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
MAX_RESULTS = 100


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _bounded(limit: int) -> int:
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    return limit


def _accelerator(preset: Any) -> dict[str, Any] | None:
    for name in ("nvidia_gpu", "amd_gpu", "intel_gpu", "tpu"):
        value = getattr(preset, name, None)
        if value is not None:
            return {
                "kind": name,
                "count": getattr(value, "count", None),
                "model": getattr(value, "model", None),
                "memory_bytes": getattr(value, "memory", None),
                "type": getattr(value, "type", None),
            }
    return None


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_apolo_context() -> dict[str, Any]:
        """Return selected context and safe local metadata; never tokens or cookies."""
        try:
            async with client() as sdk:
                config = sdk.config
                return {
                    "username": config.username,
                    "cluster": config.cluster_name,
                    "org": config.org_name,
                    "project": config.project_name,
                    "config": {
                        "path": str(config.path),
                        "exists": config.path.exists(),
                    },
                    "versions": {
                        "apolo_mcp": __version__,
                        "apolo_sdk": apolo_sdk.__version__,
                        "mcp": _package_version("mcp"),
                        "server": None,
                        "server_note": (
                            "apolo-sdk 26.3 has no public server-version accessor"
                        ),
                    },
                }
        except Exception as exc:
            raise normalize_error(exc, operation="get_apolo_context") from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_clusters(limit: int = 100) -> dict[str, Any]:
        """List clusters visible to the authenticated user (bounded to 100)."""
        bounded = _bounded(limit)
        try:
            async with client() as sdk:
                selected = sdk.config.cluster_name
                items = [
                    {"name": item.name, "selected": item.name == selected}
                    for item in sorted(
                        sdk.config.clusters.values(), key=lambda value: value.name
                    )[:bounded]
                ]
                return {
                    "items": items,
                    "limit": bounded,
                    "truncated": len(sdk.config.clusters) > bounded,
                }
        except Exception as exc:
            raise normalize_error(exc, operation="list_clusters") from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_organizations(
        cluster: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """List organizations for an explicit or selected cluster."""
        bounded = _bounded(limit)
        try:
            async with client() as sdk:
                selected_cluster = cluster or sdk.config.cluster_name
                if selected_cluster not in sdk.config.clusters:
                    raise ValueError(f"Unknown cluster: {selected_cluster}")
                orgs = sorted(sdk.config.clusters[selected_cluster].orgs)
                return {
                    "context": {"cluster": selected_cluster},
                    "items": orgs[:bounded],
                    "limit": bounded,
                    "truncated": len(orgs) > bounded,
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_organizations",
                context={"cluster": cluster or "selected"},
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_projects(
        cluster: str | None = None,
        org: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List projects for explicit context without changing saved selection."""
        bounded = _bounded(limit)
        try:
            async with client() as sdk:
                selected_cluster = cluster or sdk.config.cluster_name
                selected_org = org or sdk.config.org_name
                projects = sorted(
                    (
                        item
                        for item in sdk.config.projects.values()
                        if item.cluster_name == selected_cluster
                        and item.org_name == selected_org
                    ),
                    key=lambda item: item.name,
                )
                return {
                    "context": {"cluster": selected_cluster, "org": selected_org},
                    "items": [
                        {"name": item.name, "role": item.role}
                        for item in projects[:bounded]
                    ],
                    "limit": bounded,
                    "truncated": len(projects) > bounded,
                }
        except Exception as exc:
            raise normalize_error(exc, operation="list_projects") from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_presets(
        cluster: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """List bounded compute preset capabilities for a cluster."""
        bounded = _bounded(limit)
        try:
            async with client() as sdk:
                selected_cluster = cluster or sdk.config.cluster_name
                if selected_cluster not in sdk.config.clusters:
                    raise ValueError(f"Unknown cluster: {selected_cluster}")
                presets = sdk.config.clusters[selected_cluster].presets
                items = []
                for name, preset in sorted(presets.items())[:bounded]:
                    items.append(
                        {
                            "name": name,
                            "cpu": preset.cpu,
                            "memory_bytes": preset.memory,
                            "credits_per_hour": str(preset.credits_per_hour),
                            "accelerator": _accelerator(preset),
                            "scheduler_enabled": preset.scheduler_enabled,
                            "preemptible": preset.preemptible_node,
                            "resource_pools": list(preset.resource_pool_names),
                        }
                    )
                return {
                    "context": {"cluster": selected_cluster},
                    "items": items,
                    "limit": bounded,
                    "truncated": len(presets) > bounded,
                }
        except Exception as exc:
            raise normalize_error(exc, operation="list_presets") from None

    @mcp.tool(annotations=READ_ONLY)
    async def resolve_resource_uri(
        resource: str,
        resource_type: Literal["storage", "image", "secret", "disk"],
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a short resource reference under explicit, non-persisted context."""
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                value = resource.strip()
                prefix = f"{resource_type}:"
                if value.startswith(prefix):
                    value = value[len(prefix) :]
                if "://" in value or "?" in value or "#" in value or "@" in value:
                    raise ValueError(
                        "resource must be a short reference without credentials"
                    )
                path = value.lstrip("/")
                if not path:
                    raise ValueError("resource must not be empty")
                uri = (
                    f"{resource_type}://{resolved.cluster}/{resolved.org}/"
                    f"{resolved.project}/{path}"
                )
                return {"uri": uri, "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc, operation="resolve_resource_uri", resource=resource
            ) from None
