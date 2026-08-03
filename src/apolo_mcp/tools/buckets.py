"""Bounded bucket and blob metadata tools built on apolo-sdk 26.3."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

import apolo_sdk
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from yarl import URL

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import (
    ensure_ledger_writable,
    record_created_resource,
    record_resource_action,
)
from ..policy import MutationEffect, authorize_mutation
from ..workspace import resolve_new_workspace_file, resolve_workspace_path
from .secrets import MAX_SECRET_BYTES, _key as _secret_key, _source
from .service_accounts import _atomic_sink, _reserve_file


READ_ONLY = ToolAnnotations(
    title="Read Apolo bucket metadata",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    title="Change an Apolo bucket",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Delete an exact Apolo bucket resource",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

MAX_LIST_RESULTS = 100
MAX_USAGE_OBJECTS = 100_000
MAX_WAIT_SECONDS = 300.0
MAX_SIGNED_URL_SECONDS = 3600
MAX_CREDENTIALS_SCAN = 1000
MAX_CREDENTIAL_BUCKETS = 100


def _exact(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "://" in value
        or "/" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{field} must be one exact opaque identifier")
    return value


def _key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.endswith("/")
        or "\x00" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("key must be one exact non-directory blob key")
    return value


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _bucket(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "owner": item.owner,
        "provider": item.provider.value,
        "created_at": _iso(item.created_at),
        "imported": item.imported,
        "public": item.public,
        "uri": str(item.uri),
        "cluster": item.cluster_name,
        "org": item.org_name,
        "project": item.project_name,
    }


def _entry(item: Any) -> dict[str, Any]:
    return {
        "key": item.key,
        "name": item.name,
        "size_bytes": item.size,
        "is_file": item.is_file(),
        "is_dir": item.is_dir(),
        "created_at": _iso(item.created_at),
        "modified_at": _iso(item.modified_at),
        "uri": str(item.uri),
    }


def _assert_context(item: Any, context: ApoloContext) -> None:
    actual = (item.cluster_name, item.org_name, item.project_name)
    expected = (context.cluster, context.org, context.project)
    if actual != expected:
        raise ValueError("bucket does not belong to the exact resolved context")


def _authorize_bucket(
    operation: str,
    effect: MutationEffect,
    item: Any,
    context: ApoloContext,
) -> None:
    authorize_mutation(
        operation=operation,
        effect=effect,
        resource_type="bucket",
        resource_id=item.id,
        username=context.username,
        cluster=context.cluster,
        org=context.org,
        project=context.project,
    )


def _record_bucket_action(
    operation: str,
    action: str,
    item: Any,
    context: ApoloContext,
) -> None:
    record_resource_action(
        resource_type="bucket",
        resource_id=item.id,
        username=context.username,
        cluster=context.cluster,
        org=context.org,
        project=context.project,
        operation=operation,
        action=action,
    )


async def _get_exact(sdk: Any, value: str, context: ApoloContext) -> Any:
    item = await sdk.buckets.get(
        value,
        cluster_name=context.cluster,
        org_name=context.org,
        project_name=context.project,
    )
    _assert_context(item, context)
    return item


async def _credential_bucket_metadata(
    sdk: Any, item: Any, context: ApoloContext
) -> list[dict[str, str]]:
    buckets: list[dict[str, str]] = []
    if not 1 <= len(item.credentials) <= MAX_CREDENTIAL_BUCKETS:
        raise ValueError("bucket credential has an invalid number of bucket bindings")
    for binding in item.credentials:
        bucket = await _get_exact(sdk, binding.bucket_id, context)
        if bucket.id != binding.bucket_id:
            raise ValueError("bucket credential contains a non-exact bucket binding")
        buckets.append(
            {
                "id": bucket.id,
                "name": bucket.name,
                "provider": binding.provider.value,
                "uri": str(bucket.uri),
            }
        )
    return buckets


def _credential_metadata(
    item: Any, buckets: list[dict[str, str]], context: ApoloContext
) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "owner": item.owner,
        "cluster": context.cluster,
        "read_only": item.read_only,
        "buckets": buckets,
    }


def _credential_payload(item: Any, context: ApoloContext) -> bytes:
    payload = {
        "id": item.id,
        "name": item.name,
        "owner": item.owner,
        "cluster": context.cluster,
        "read_only": item.read_only,
        "credentials": [
            {
                "bucket_id": binding.bucket_id,
                "provider": binding.provider.value,
                "credentials": dict(binding.credentials),
            }
            for binding in item.credentials
        ],
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def _blob_uri(bucket: Any, key: str = "") -> URL:
    suffix = f"/{key}" if key else "/"
    return URL(str(bucket.uri) + suffix)


def _transfer_timeout(timeout_seconds: float | None) -> None:
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive when provided")


async def _await_transfer(operation: Any, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None:
        return await operation
    return await asyncio.wait_for(operation, timeout=timeout_seconds)


def _credential_mapping(payload: bytes) -> dict[str, str]:
    if not payload or len(payload) > MAX_SECRET_BYTES:
        raise ValueError("credential source has an invalid size")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("credential source must be a UTF-8 JSON object") from exc
    if not isinstance(decoded, dict) or not 1 <= len(decoded) <= 32:
        raise ValueError("credential source must contain 1 to 32 JSON fields")
    if not all(
        isinstance(key, str) and key and isinstance(value, str) and value
        for key, value in decoded.items()
    ):
        raise ValueError("credential JSON fields and values must be non-empty strings")
    return decoded


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_buckets(
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List bucket metadata under a strict result bound."""
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                items: list[dict[str, Any]] = []
                async with sdk.buckets.list(
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                ) as iterator:
                    async for item in iterator:
                        _assert_context(item, resolved)
                        items.append(_bucket(item))
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
                operation="list_buckets",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_bucket(
        bucket_id_or_name: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get safe metadata for one exact bucket ID or name."""
        value = _exact(bucket_id_or_name, "bucket_id_or_name")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                return {"bucket": _bucket(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_bucket",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_bucket(
        name: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create and journal a bucket when server policy permits writes."""
        authorize_mutation(operation="create_bucket", effect=MutationEffect.CREATE)
        if name is not None:
            _exact(name, "name")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                ensure_ledger_writable()
                item = await sdk.buckets.create(
                    name=name,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _assert_context(item, resolved)
                record_created_resource(
                    resource_type="bucket",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="create_bucket",
                )
                return {"bucket": _bucket(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="create_bucket",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def import_external_bucket(
        provider: Literal["aws", "minio", "azure", "gcp", "open_stack"],
        provider_bucket_name: str,
        credential_source_type: Literal["env", "file", "secret"],
        credential_source_name: str,
        name: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Import using bounded JSON credentials from a protected internal source."""
        authorize_mutation(
            operation="import_external_bucket", effect=MutationEffect.CREATE
        )
        external_name = _exact(provider_bucket_name, "provider_bucket_name")
        if name is not None:
            _exact(name, "name")
        resolved: ApoloContext | None = None
        try:
            raw: bytes | None = None
            if credential_source_type != "secret":
                raw = _source(credential_source_type, credential_source_name)
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                ensure_ledger_writable()
                if credential_source_type == "secret":
                    source_key = _secret_key(credential_source_name)
                    raw = await sdk.secrets.get(
                        source_key,
                        cluster_name=resolved.cluster,
                        org_name=resolved.org,
                        project_name=resolved.project,
                    )
                assert raw is not None
                credentials = _credential_mapping(raw)
                try:
                    item = await sdk.buckets.import_external(
                        apolo_sdk.Bucket.Provider(provider),
                        external_name,
                        credentials,
                        name=name,
                        cluster_name=resolved.cluster,
                        org_name=resolved.org,
                        project_name=resolved.project,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"{type(exc).__name__} while importing external bucket"
                    ) from None
                _assert_context(item, resolved)
                record_created_resource(
                    resource_type="bucket",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="import_external_bucket",
                )
                return {"bucket": _bucket(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="import_external_bucket",
                context=resolved.as_dict() if resolved else None,
                resource=external_name,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_bucket_disk_usage(
        bucket_id_or_name: str,
        max_objects: int = 10_000,
        timeout_seconds: float = 60.0,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Scan bounded usage; complete=false means the object cap was reached."""
        value = _exact(bucket_id_or_name, "bucket_id_or_name")
        if not 1 <= max_objects <= MAX_USAGE_OBJECTS:
            raise ValueError(f"max_objects must be between 1 and {MAX_USAGE_OBJECTS}")
        if not 0 < timeout_seconds <= MAX_WAIT_SECONDS:
            raise ValueError(f"timeout_seconds must be > 0 and <= {MAX_WAIT_SECONDS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)

                async def scan() -> tuple[int, int, bool]:
                    latest_bytes = 0
                    latest_count = 0
                    complete = True
                    async with sdk.buckets.get_disk_usage(
                        item.id,
                        cluster_name=resolved.cluster,
                        org_name=resolved.org,
                        project_name=resolved.project,
                    ) as iterator:
                        async for usage in iterator:
                            latest_bytes = usage.total_bytes
                            latest_count = usage.object_count
                            if latest_count >= max_objects:
                                complete = False
                                break
                    return latest_bytes, latest_count, complete

                total, count, complete = await asyncio.wait_for(
                    scan(), timeout=timeout_seconds
                )
                return {
                    "bucket": _bucket(item),
                    "total_bytes": total,
                    "object_count": count,
                    "complete": complete,
                    "max_objects": max_objects,
                    "timeout_seconds": timeout_seconds,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_bucket_disk_usage",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def stat_bucket_blob(
        bucket_id_or_name: str,
        key: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Return metadata only for one exact blob key."""
        value = _exact(bucket_id_or_name, "bucket_id_or_name")
        exact_key = _key(key)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                entry = await sdk.buckets.head_blob(
                    item.id,
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                return {"blob": _entry(entry), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="stat_bucket_blob",
                context=resolved.as_dict() if resolved else None,
                resource=f"{value}/{exact_key}",
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_bucket_blobs(
        bucket_id_or_name: str,
        prefix: str = "",
        recursive: bool = False,
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List blob metadata under an SDK-enforced and result-enforced bound."""
        value = _exact(bucket_id_or_name, "bucket_id_or_name")
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        if prefix.startswith("/") or any(ord(char) < 32 for char in prefix):
            raise ValueError("prefix must be a relative blob prefix")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                entries: list[dict[str, Any]] = []
                async with sdk.buckets.list_blobs(
                    _blob_uri(item, prefix), recursive=recursive, limit=limit + 1
                ) as iterator:
                    async for entry in iterator:
                        entries.append(_entry(entry))
                        if len(entries) > limit:
                            break
                return {
                    "bucket": _bucket(item),
                    "items": entries[:limit],
                    "limit": limit,
                    "truncated": len(entries) > limit,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_bucket_blobs",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def set_bucket_public_access(
        bucket_id: str,
        public: bool,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Set public state for one exact immutable bucket ID."""
        value = _exact(bucket_id, "bucket_id")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                current = await _get_exact(sdk, value, resolved)
                if current.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                _authorize_bucket(
                    "set_bucket_public_access", MutationEffect.UPDATE, current, resolved
                )
                item = await sdk.buckets.set_public_access(
                    value,
                    public,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _assert_context(item, resolved)
                _record_bucket_action(
                    "set_bucket_public_access", "updated", item, resolved
                )
                return {"bucket": _bucket(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="set_bucket_public_access",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_bucket_credentials(
        limit: int = 50,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List safe persistent credential metadata for exact-context buckets.

        Provider credential values returned internally by the SDK are discarded and
        never serialized through MCP.
        """
        if not 1 <= limit <= MAX_LIST_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_RESULTS}")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                items: list[dict[str, Any]] = []
                scanned = 0
                scan_limited = False
                result_limited = False
                async with sdk.buckets.persistent_credentials_list(
                    cluster_name=resolved.cluster
                ) as iterator:
                    async for item in iterator:
                        scanned += 1
                        try:
                            bucket_metadata = await _credential_bucket_metadata(
                                sdk, item, resolved
                            )
                        except (ValueError, apolo_sdk.ResourceNotFound):
                            if scanned >= MAX_CREDENTIALS_SCAN:
                                scan_limited = True
                                break
                            continue
                        items.append(
                            _credential_metadata(item, bucket_metadata, resolved)
                        )
                        if len(items) > limit:
                            result_limited = True
                            break
                        if scanned >= MAX_CREDENTIALS_SCAN:
                            scan_limited = True
                            break
                return {
                    "items": items[:limit],
                    "limit": limit,
                    "scanned": scanned,
                    "scan_limit": MAX_CREDENTIALS_SCAN,
                    "truncated": result_limited or scan_limited,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_bucket_credentials",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_bucket_credentials(
        bucket_ids: list[str],
        destination_file: str,
        name: str | None = None,
        read_only: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create persistent credentials and atomically sink them to a 0600 file.

        Provider credential values are never returned through MCP.
        """
        authorize_mutation(
            operation="create_bucket_credentials", effect=MutationEffect.CREATE
        )
        if not 1 <= len(bucket_ids) <= MAX_CREDENTIAL_BUCKETS:
            raise ValueError(
                f"bucket_ids must contain between 1 and {MAX_CREDENTIAL_BUCKETS} IDs"
            )
        values = [_exact(value, "bucket_ids item") for value in bucket_ids]
        if len(values) != len(set(values)):
            raise ValueError("bucket_ids must not contain duplicates")
        if name is not None:
            _exact(name, "name")
        resolved: ApoloContext | None = None
        reserved_fd: int | None = None
        reserved_path: Path | None = None
        completed = False
        try:
            reserved_fd, reserved_path = _reserve_file(destination_file)
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                ensure_ledger_writable()
                for bucket_id in values:
                    bucket = await _get_exact(sdk, bucket_id, resolved)
                    if bucket.id != bucket_id:
                        raise ValueError(
                            "bucket_ids must contain exact immutable bucket IDs"
                        )
                item = await sdk.buckets.persistent_credentials_create(
                    bucket_ids=values,
                    name=name,
                    cluster_name=resolved.cluster,
                    read_only=read_only,
                )
                bucket_metadata = await _credential_bucket_metadata(sdk, item, resolved)
                record_created_resource(
                    resource_type="bucket_credential",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="create_bucket_credentials",
                )
                try:
                    _atomic_sink(reserved_path, _credential_payload(item, resolved))
                except Exception as sink_error:
                    try:
                        await sdk.buckets.persistent_credentials_rm(
                            item.id, cluster_name=resolved.cluster
                        )
                    except Exception:
                        raise RuntimeError(
                            "credential sink failed and automatic removal also "
                            f"failed; remove exact credential ID {item.id}"
                        ) from None
                    record_resource_action(
                        resource_type="bucket_credential",
                        resource_id=item.id,
                        username=resolved.username,
                        cluster=resolved.cluster,
                        org=resolved.org,
                        project=resolved.project,
                        operation="create_bucket_credentials",
                        action="deleted",
                    )
                    raise RuntimeError(
                        "credential sink failed; the newly created credential was "
                        "removed"
                    ) from sink_error
                completed = True
                return {
                    "credential": _credential_metadata(item, bucket_metadata, resolved),
                    "destination": {
                        "type": "file",
                        "path": str(reserved_path),
                        "mode": "0600",
                    },
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="create_bucket_credentials",
                context=resolved.as_dict() if resolved else None,
            ) from None
        finally:
            if reserved_fd is not None:
                os.close(reserved_fd)
            if reserved_path is not None and not completed:
                reserved_path.unlink(missing_ok=True)

    @mcp.tool(annotations=WRITE)
    async def export_bucket_credentials(
        credential_id: str,
        destination_file: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Write one exact persistent credential to a new protected local file.

        Provider credential values are never returned through MCP.
        """
        value = _exact(credential_id, "credential_id")
        resolved: ApoloContext | None = None
        reserved_fd: int | None = None
        reserved_path: Path | None = None
        completed = False
        try:
            reserved_fd, reserved_path = _reserve_file(destination_file)
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await sdk.buckets.persistent_credentials_get(
                    value, cluster_name=resolved.cluster
                )
                if item.id != value:
                    raise ValueError(
                        "credential_id must be the exact immutable credential ID"
                    )
                bucket_metadata = await _credential_bucket_metadata(sdk, item, resolved)
                authorize_mutation(
                    operation="export_bucket_credentials",
                    effect=MutationEffect.UPDATE,
                    resource_type="bucket_credential",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                _atomic_sink(reserved_path, _credential_payload(item, resolved))
                record_resource_action(
                    resource_type="bucket_credential",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="export_bucket_credentials",
                    action="updated",
                )
                completed = True
                return {
                    "credential": _credential_metadata(item, bucket_metadata, resolved),
                    "destination": {
                        "type": "file",
                        "path": str(reserved_path),
                        "mode": "0600",
                    },
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="export_bucket_credentials",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None
        finally:
            if reserved_fd is not None:
                os.close(reserved_fd)
            if reserved_path is not None and not completed:
                reserved_path.unlink(missing_ok=True)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_bucket_credentials(
        credential_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact persistent bucket credential under lifecycle policy."""
        value = _exact(credential_id, "credential_id")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await sdk.buckets.persistent_credentials_get(
                    value, cluster_name=resolved.cluster
                )
                if item.id != value:
                    raise ValueError(
                        "credential_id must be the exact immutable credential ID"
                    )
                await _credential_bucket_metadata(sdk, item, resolved)
                authorize_mutation(
                    operation="delete_bucket_credentials",
                    effect=MutationEffect.DELETE,
                    resource_type="bucket_credential",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                await sdk.buckets.persistent_credentials_rm(
                    item.id, cluster_name=resolved.cluster
                )
                record_resource_action(
                    resource_type="bucket_credential",
                    resource_id=item.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="delete_bucket_credentials",
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
                operation="delete_bucket_credentials",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_bucket_signed_url(
        bucket_id: str,
        key: str,
        destination_file: str,
        expires_in_seconds: int = 900,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create a short-lived blob URL and write it to a protected local file.

        The URL is never returned through MCP.
        """
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
        if not 1 <= expires_in_seconds <= MAX_SIGNED_URL_SECONDS:
            raise ValueError(
                f"expires_in_seconds must be between 1 and {MAX_SIGNED_URL_SECONDS}"
            )
        resolved: ApoloContext | None = None
        reserved_fd: int | None = None
        reserved_path: Path | None = None
        sunk = False
        try:
            reserved_fd, reserved_path = _reserve_file(destination_file)
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                _authorize_bucket(
                    "create_bucket_signed_url", MutationEffect.UPDATE, item, resolved
                )
                await sdk.buckets.head_blob(
                    item.id,
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                url = await sdk.buckets.make_signed_url(
                    _blob_uri(item, exact_key),
                    expires_in_seconds=expires_in_seconds,
                )
                _atomic_sink(reserved_path, str(url).encode())
                _record_bucket_action(
                    "create_bucket_signed_url", "updated", item, resolved
                )
                sunk = True
                return {
                    "expires_in_seconds": expires_in_seconds,
                    "bucket_id": item.id,
                    "key": exact_key,
                    "destination": {
                        "type": "file",
                        "path": str(reserved_path),
                        "mode": "0600",
                    },
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="create_bucket_signed_url",
                context=resolved.as_dict() if resolved else None,
                resource=f"{value}/{exact_key}",
            ) from None
        finally:
            if reserved_fd is not None:
                os.close(reserved_fd)
            if reserved_path is not None and not sunk:
                reserved_path.unlink(missing_ok=True)

    @mcp.tool(annotations=WRITE)
    async def upload_bucket_file(
        local_path: str,
        bucket_id: str,
        key: str,
        timeout_seconds: float | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Upload one local file.

        Object bytes are never serialized through MCP. An optional positive timeout
        can bound the transfer when requested by the caller.
        """
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
        _transfer_timeout(timeout_seconds)
        source = resolve_workspace_path(local_path, name="local_path", directory=False)
        size = source.stat().st_size
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                _authorize_bucket(
                    "upload_bucket_file", MutationEffect.UPDATE, item, resolved
                )
                await _await_transfer(
                    sdk.buckets.upload_file(
                        URL(source.as_uri()), _blob_uri(item, exact_key), update=False
                    ),
                    timeout_seconds,
                )
                _record_bucket_action("upload_bucket_file", "updated", item, resolved)
                record_resource_action(
                    resource_type="bucket_blob",
                    resource_id=f"{item.id}/{exact_key}",
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="upload_bucket_file",
                    action="created",
                )
                return {
                    "status": "uploaded",
                    "local_path": str(source),
                    "bucket_id": item.id,
                    "key": exact_key,
                    "size_bytes": size,
                    "timeout_seconds": timeout_seconds,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="upload_bucket_file",
                context=resolved.as_dict() if resolved else None,
                resource=f"{value}/{exact_key}",
            ) from None

    @mcp.tool(annotations=WRITE)
    async def download_bucket_file(
        bucket_id: str,
        key: str,
        local_path: str,
        timeout_seconds: float | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Download one blob to a new local file.

        Existing files are never overwritten. Blob bytes are never serialized through
        MCP. An optional positive timeout can bound the transfer when requested by the
        caller.
        """
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
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
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                _authorize_bucket(
                    "download_bucket_file", MutationEffect.UPDATE, item, resolved
                )
                entry = await sdk.buckets.head_blob(
                    item.id,
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                await _await_transfer(
                    sdk.buckets.download_file(
                        _blob_uri(item, exact_key),
                        URL(destination.as_uri()),
                        update=False,
                        continue_=False,
                    ),
                    timeout_seconds,
                )
                actual_size = destination.stat().st_size
                if actual_size != entry.size:
                    raise RuntimeError(
                        "downloaded file size does not match blob metadata"
                    )
                completed = True
                _record_bucket_action("download_bucket_file", "updated", item, resolved)
                return {
                    "status": "downloaded",
                    "local_path": str(destination),
                    "bucket_id": item.id,
                    "key": exact_key,
                    "size_bytes": actual_size,
                    "timeout_seconds": timeout_seconds,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="download_bucket_file",
                context=resolved.as_dict() if resolved else None,
                resource=f"{value}/{exact_key}",
            ) from None
        finally:
            if not completed:
                destination.unlink(missing_ok=True)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_bucket_blob(
        bucket_id: str,
        key: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact blob key; recursive/prefix deletion is not exposed."""
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                _authorize_bucket(
                    "delete_bucket_blob", MutationEffect.UPDATE, item, resolved
                )
                await sdk.buckets.head_blob(
                    item.id,
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                await sdk.buckets.delete_blob(
                    item.id,
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _record_bucket_action("delete_bucket_blob", "updated", item, resolved)
                record_resource_action(
                    resource_type="bucket_blob",
                    resource_id=f"{item.id}/{exact_key}",
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="delete_bucket_blob",
                    action="deleted",
                )
                return {
                    "status": "deleted",
                    "bucket_id": item.id,
                    "key": exact_key,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_bucket_blob",
                context=resolved.as_dict() if resolved else None,
                resource=f"{value}/{exact_key}",
            ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_bucket(
        bucket_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Recursively delete one exact bucket under full or owned managed policy."""
        value = _exact(bucket_id, "bucket_id")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                _authorize_bucket(
                    "delete_bucket", MutationEffect.DELETE, item, resolved
                )
                await sdk.buckets.blob_rm(item.uri, recursive=True)
                await sdk.buckets.rm(
                    item.id,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _record_bucket_action("delete_bucket", "deleted", item, resolved)
                return {
                    "status": "deleted",
                    "id": item.id,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_bucket",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None
