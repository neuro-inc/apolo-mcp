"""Registry metadata tools built on the public Apolo SDK Images API."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import apolo_sdk
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from yarl import URL

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import ensure_ledger_writable, record_created_resource
from ..policy import Policy


READ_ONLY = ToolAnnotations(
    title="Read Apolo registry metadata",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    title="Transfer an image between the local Docker engine and Apolo",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Remove an exact Apolo image manifest",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

MAX_LIST_RESULTS = 100
MAX_TRANSFER_SECONDS = 1800.0
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _context(
    sdk: Any, cluster: str | None, org: str | None, project: str | None
) -> ApoloContext:
    return resolve_context(sdk.config, cluster=cluster, org=org, project=project)


def _repository_uri(repository: str, context: ApoloContext) -> str:
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError("repository must not be empty")
    if any(ord(char) < 32 for char in repository):
        raise ValueError("repository must not contain control characters")
    if repository.startswith("image:") and not repository.startswith("image://"):
        repository = repository[len("image:") :]
    if "://" in repository:
        uri = URL(repository)
        if uri.scheme != "image":
            raise ValueError("repository URI must use image://")
        if uri.user is not None or uri.password is not None:
            raise ValueError("credentials are prohibited in image URIs")
        if uri.query_string or uri.fragment:
            raise ValueError("image URIs must not contain query strings or fragments")
        parts = [part for part in uri.path.split("/") if part]
        if len(parts) < 3:
            raise ValueError("image URI must include cluster, org, project, and name")
        if (uri.host, parts[0], parts[1]) != (
            context.cluster,
            context.org,
            context.project,
        ):
            raise ValueError("image URI does not belong to the resolved context")
        name_parts = parts[2:]
        if ":" in name_parts[-1]:
            raise ValueError("repository must not include a tag")
        name = "/".join(name_parts)
    else:
        name = repository.strip().strip("/")
        if "@" in name or ":" in name:
            raise ValueError("repository must not include credentials, digest, or tag")
    if not name or any(part in {".", ".."} for part in name.split("/")):
        raise ValueError("repository name is invalid")
    return f"image://{context.cluster}/{context.org}/{context.project}/{name}"


def _remote(
    sdk: Any, repository: str, context: ApoloContext, tag: str | None = None
) -> apolo_sdk.RemoteImage:
    uri = _repository_uri(repository, context)
    if tag is not None:
        if not tag.strip() or "/" in tag or "@" in tag or ":" in tag:
            raise ValueError("tag must be one exact image tag")
        uri += f":{tag}"
    remote = sdk.parse.remote_image(
        uri,
        cluster_name=context.cluster,
        tag_option=(
            apolo_sdk.TagOption.DENY if tag is None else apolo_sdk.TagOption.ALLOW
        ),
    )
    actual = (remote.cluster_name, remote.org_name, remote.project_name)
    expected = (context.cluster, context.org, context.project)
    if actual != expected:
        raise ValueError("image does not belong to the exact resolved context")
    return remote


def _image(remote: apolo_sdk.RemoteImage) -> dict[str, Any]:
    return {
        "repository": remote.name,
        "tag": remote.tag,
        "uri": str(remote),
        "cluster": remote.cluster_name,
        "org": remote.org_name,
        "project": remote.project_name,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=WRITE)
    async def push_image(
        local_image: str,
        repository: str,
        tag: str,
        timeout_seconds: float = 1800,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Push a local Docker image to one exact Apolo repository and tag."""
        Policy.load().require_high_risk("push_image")
        if not approved:
            raise PermissionError("push_image requires approved=true")
        if not 1 <= timeout_seconds <= MAX_TRANSFER_SECONDS:
            raise ValueError("timeout_seconds must be between 1 and 1800")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                ensure_ledger_writable()
                local = sdk.parse.local_image(local_image)
                remote = _remote(sdk, repository, resolved, tag)
                pushed = await asyncio.wait_for(
                    sdk.images.push(local, remote), timeout_seconds
                )
                record_created_resource(
                    resource_type="image",
                    resource_id=str(pushed),
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="push_image",
                )
                return {
                    "status": "pushed",
                    "image": _image(pushed),
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="push_image",
                context=resolved.as_dict() if resolved else None,
                resource=f"{repository}:{tag}",
            ) from None

    @mcp.tool(annotations=WRITE)
    async def pull_image(
        repository: str,
        tag: str,
        local_image: str | None = None,
        timeout_seconds: float = 1800,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Pull one exact Apolo image into the MCP host's local Docker engine."""
        Policy.load().require_high_risk("pull_image")
        if not approved:
            raise PermissionError("pull_image requires approved=true")
        if not 1 <= timeout_seconds <= MAX_TRANSFER_SECONDS:
            raise ValueError("timeout_seconds must be between 1 and 1800")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                remote = _remote(sdk, repository, resolved, tag)
                local = sdk.parse.local_image(local_image) if local_image else None
                pulled = await asyncio.wait_for(
                    sdk.images.pull(remote, local), timeout_seconds
                )
                return {
                    "status": "pulled",
                    "local_image": str(pulled),
                    "source": _image(remote),
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="pull_image",
                context=resolved.as_dict() if resolved else None,
                resource=f"{repository}:{tag}",
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_image_repositories(
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List repositories in one exact context under a strict output bound."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                candidates = await sdk.images.list(cluster_name=resolved.cluster)
                matching = [
                    item
                    for item in candidates
                    if (item.cluster_name, item.org_name, item.project_name)
                    == (resolved.cluster, resolved.org, resolved.project)
                ]
                return {
                    "items": [_image(item) for item in matching[:limit]],
                    "limit": limit,
                    "truncated": len(matching) > limit,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_image_repositories",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_image_tags(
        repository: str,
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List tags for one exact repository under a strict output bound."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                remote = _remote(sdk, repository, resolved)
                tags = await sdk.images.tags(remote)
                return {
                    "repository": _image(remote),
                    "items": [_image(item) for item in tags[:limit]],
                    "limit": limit,
                    "truncated": len(tags) > limit,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_image_tags",
                context=resolved.as_dict() if resolved else None,
                resource=repository,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def inspect_image(
        repository: str,
        tag: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Inspect exact tag metadata: digest and aggregate layer size only."""
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                remote = _remote(sdk, repository, resolved, tag)
                digest = await sdk.images.digest(remote)
                info = await sdk.images.tag_info(remote)
                return {
                    "image": _image(remote),
                    "digest": digest,
                    "size_bytes": info.size,
                    "tag": info.name,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="inspect_image",
                context=resolved.as_dict() if resolved else None,
                resource=f"{repository}:{tag}",
            ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def remove_image(
        repository: str,
        tag: str,
        digest: str,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Remove an exact tag digest after approval and server policy checks."""
        Policy.load().require_high_risk("remove_image")
        if not approved:
            raise PermissionError("remove_image requires approved=true")
        if not _DIGEST.fullmatch(digest):
            raise ValueError("digest must be an exact lowercase sha256 digest")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                remote = _remote(sdk, repository, resolved, tag)
                actual_digest = await sdk.images.digest(remote)
                if actual_digest != digest:
                    raise ValueError(
                        "approved digest no longer matches the exact image tag; "
                        "inspect and approve the current digest"
                    )
                await sdk.images.rm(remote, digest)
                return {
                    "status": "deleted",
                    "image": _image(remote),
                    "digest": digest,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="remove_image",
                context=resolved.as_dict() if resolved else None,
                resource=f"{repository}:{tag}@{digest}",
            ) from None
