"""Credential-safe secret metadata and write tools."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import ensure_ledger_writable, record_resource_action
from ..policy import MutationEffect, authorize_mutation
from ..workspace import resolve_new_workspace_file, resolve_workspace_path


READ_ONLY = ToolAnnotations(
    title="List Apolo secret metadata",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    title="Create an Apolo secret from a protected local source",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Delete one exact Apolo secret",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

MAX_LIST_RESULTS = 1000
MAX_SECRET_BYTES = 1024 * 1024


def _key(key: str) -> str:
    if (
        not isinstance(key, str)
        or not key.strip()
        or key != key.strip()
        or "/" in key
        or "://" in key
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        raise ValueError("key must be one exact non-empty secret key")
    return key


def _metadata(item: Any) -> dict[str, str]:
    return {
        "key": item.key,
        "owner": item.owner,
        "cluster": item.cluster_name,
        "org": item.org_name,
        "project": item.project_name,
        "uri": str(item.uri),
    }


def _assert_context(item: Any, context: ApoloContext) -> None:
    actual = (item.cluster_name, item.org_name, item.project_name)
    expected = (context.cluster, context.org, context.project)
    if actual != expected:
        raise ValueError("secret does not belong to the exact resolved context")


def _workspace_path(value: str) -> Path:
    path = resolve_workspace_path(value, name="source_file", directory=False)
    info = path.lstat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("source_file must not be accessible by group or others")
    return path


def _source(source_type: Literal["env", "file"], source_name: str) -> bytes:
    if not source_name or any(ord(char) < 32 for char in source_name):
        raise ValueError("source_name must not be empty or contain control characters")
    if source_type == "env":
        if source_name not in os.environ:
            raise ValueError("the named environment variable is not set")
        value = os.environ[source_name].encode()
    else:
        path = _workspace_path(source_name)
        if path.stat().st_size > MAX_SECRET_BYTES:
            raise ValueError(f"secret source exceeds {MAX_SECRET_BYTES} bytes")
        value = path.read_bytes()
    if not value:
        raise ValueError("the local secret source is empty")
    if len(value) > MAX_SECRET_BYTES:
        raise ValueError(f"secret source exceeds {MAX_SECRET_BYTES} bytes")
    return value


async def _secret_exists(sdk: Any, key: str, context: ApoloContext) -> bool:
    async with sdk.secrets.list(
        cluster_name=context.cluster,
        org_name=context.org,
        project_name=context.project,
    ) as iterator:
        async for item in iterator:
            if item.key == key:
                _assert_context(item, context)
                return True
    return False


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_secrets(
        limit: int = MAX_LIST_RESULTS,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List names, owners, and context only; never retrieve secret values."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                items: list[dict[str, str]] = []
                async with sdk.secrets.list(
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                ) as iterator:
                    async for item in iterator:
                        _assert_context(item, resolved)
                        items.append(_metadata(item))
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
                operation="list_secrets",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def get_secret_to_file(
        key: str,
        destination_file: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Write a secret to a new mode-0600 local file.

        The destination must be new, and the secret value is never returned.
        """
        exact_key = _key(key)
        destination = resolve_new_workspace_file(
            destination_file, name="destination_file", create_parents=False
        )
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                authorize_mutation(
                    operation="get_secret_to_file",
                    effect=MutationEffect.UPDATE,
                    resource_type="secret",
                    resource_id=exact_key,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                value = await sdk.secrets.get(
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                if not value or len(value) > MAX_SECRET_BYTES:
                    raise ValueError("secret has an invalid size")
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(value)
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
                record_resource_action(
                    resource_type="secret",
                    resource_id=exact_key,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="get_secret_to_file",
                    action="updated",
                )
                return {
                    "status": "written",
                    "key": exact_key,
                    "destination_file": str(destination),
                    "bytes": len(value),
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_secret_to_file",
                context=resolved.as_dict() if resolved else None,
                resource=exact_key,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_secret_from_source(
        key: str,
        source_type: Literal["env", "file", "secret"],
        source_name: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create a secret without accepting or returning its value.

        File sources must be private regular files.
        """
        authorize_mutation(
            operation="create_secret_from_source", effect=MutationEffect.CREATE
        )
        exact_key = _key(key)
        resolved: ApoloContext | None = None
        try:
            value: bytes | None = None
            if source_type != "secret":
                value = _source(source_type, source_name)
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                exists = await _secret_exists(sdk, exact_key, resolved)
                effect = MutationEffect.UPDATE if exists else MutationEffect.CREATE
                authorize_mutation(
                    operation="create_secret_from_source",
                    effect=effect,
                    resource_type="secret",
                    resource_id=exact_key,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                if not exists:
                    ensure_ledger_writable()
                if source_type == "secret":
                    source_key = _key(source_name)
                    if source_key == exact_key:
                        raise ValueError(
                            "source and destination secret keys must differ"
                        )
                    value = await sdk.secrets.get(
                        source_key,
                        cluster_name=resolved.cluster,
                        org_name=resolved.org,
                        project_name=resolved.project,
                    )
                    if not value or len(value) > MAX_SECRET_BYTES:
                        raise ValueError("secret source has an invalid size")
                assert value is not None
                try:
                    await sdk.secrets.add(
                        exact_key,
                        value,
                        cluster_name=resolved.cluster,
                        org_name=resolved.org,
                        project_name=resolved.project,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"{type(exc).__name__} while storing secret value"
                    ) from None
                record_resource_action(
                    resource_type="secret",
                    resource_id=exact_key,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="create_secret_from_source",
                    action="updated" if exists else "created",
                )
                return {
                    "status": "created",
                    "key": exact_key,
                    "source": {"type": source_type, "name": source_name},
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="create_secret_from_source",
                context=resolved.as_dict() if resolved else None,
                resource=exact_key,
            ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_secret(
        key: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact secret under full or owned managed policy."""
        exact_key = _key(key)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                authorize_mutation(
                    operation="delete_secret",
                    effect=MutationEffect.DELETE,
                    resource_type="secret",
                    resource_id=exact_key,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                await sdk.secrets.rm(
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                record_resource_action(
                    resource_type="secret",
                    resource_id=exact_key,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="delete_secret",
                    action="deleted",
                )
                return {
                    "status": "deleted",
                    "key": exact_key,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_secret",
                context=resolved.as_dict() if resolved else None,
                resource=exact_key,
            ) from None
