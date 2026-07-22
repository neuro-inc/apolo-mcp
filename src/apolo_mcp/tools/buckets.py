"""Bounded bucket and blob metadata tools built on apolo-sdk 26.3."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

import apolo_sdk
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from yarl import URL

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import authorize_cleanup, ensure_ledger_writable, record_created_resource
from ..policy import Policy
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
MAX_TRANSFER_BYTES = 5 * 1024**3
MAX_TRANSFER_SECONDS = 3600.0
ALLOWED_WORKSPACE_ENV = "APOLO_MCP_ALLOWED_WORKSPACE"


def _context(
    sdk: Any, cluster: str | None, org: str | None, project: str | None
) -> ApoloContext:
    return resolve_context(sdk.config, cluster=cluster, org=org, project=project)


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


async def _get_exact(sdk: Any, value: str, context: ApoloContext) -> Any:
    item = await sdk.buckets.get(
        value,
        cluster_name=context.cluster,
        org_name=context.org,
        project_name=context.project,
    )
    _assert_context(item, context)
    return item


def _blob_uri(bucket: Any, key: str = "") -> URL:
    suffix = f"/{key}" if key else "/"
    return URL(str(bucket.uri) + suffix)


def _workspace_root() -> Path:
    configured = os.environ.get(ALLOWED_WORKSPACE_ENV)
    if not configured:
        raise PermissionError(f"{ALLOWED_WORKSPACE_ENV} must be configured")
    configured_root = Path(configured).expanduser()
    if stat.S_ISLNK(configured_root.lstat().st_mode):
        raise ValueError("allowed workspace must not be a symlink")
    root = configured_root.resolve(strict=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("allowed workspace must be a real directory")
    return root


def _upload_path(value: str) -> Path:
    root = _workspace_root()
    requested = Path(value).expanduser()
    if stat.S_ISLNK(requested.lstat().st_mode):
        raise ValueError("local_path must not be a symlink")
    path = requested.resolve(strict=True)
    if path == root or root not in path.parents:
        raise PermissionError("local_path must be beneath the allowed workspace")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("local_path must be a real regular file")
    return path


def _download_path(value: str) -> Path:
    root = _workspace_root()
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    prospective_parent = requested.parent.resolve(strict=False)
    if prospective_parent != root and root not in prospective_parent.parents:
        raise PermissionError("local_path must be beneath the allowed workspace")
    requested.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = requested.parent.resolve(strict=True)
    path = parent / requested.name
    if parent != root and root not in parent.parents:
        raise PermissionError("local_path must be beneath the allowed workspace")
    if not path.name or path.name in {".", ".."}:
        raise ValueError("local_path must name one exact file")
    if path.exists() or path.is_symlink():
        raise FileExistsError("local destination already exists")
    return path


def _transfer_bounds(max_bytes: int, timeout_seconds: float) -> None:
    if isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_TRANSFER_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {MAX_TRANSFER_BYTES}")
    if not 0 < timeout_seconds <= MAX_TRANSFER_SECONDS:
        raise ValueError(f"timeout_seconds must be > 0 and <= {MAX_TRANSFER_SECONDS}")


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
                resolved = _context(sdk, cluster, org, project)
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
                resolved = _context(sdk, cluster, org, project)
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
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create and ledger a bucket after policy and client approval."""
        Policy.load().require_high_risk("create_bucket")
        if not approved:
            raise PermissionError("create_bucket requires approved=true")
        if name is not None:
            _exact(name, "name")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
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
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Import using bounded JSON credentials from a protected internal source."""
        Policy.load().require_high_risk("import_external_bucket")
        if not approved:
            raise PermissionError("import_external_bucket requires approved=true")
        external_name = _exact(provider_bucket_name, "provider_bucket_name")
        if name is not None:
            _exact(name, "name")
        resolved: ApoloContext | None = None
        try:
            raw: bytes | None = None
            if credential_source_type != "secret":
                raw = _source(credential_source_type, credential_source_name)
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
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
                resolved = _context(sdk, cluster, org, project)
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
                resolved = _context(sdk, cluster, org, project)
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
                resolved = _context(sdk, cluster, org, project)
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
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Set public state for one exact immutable bucket ID."""
        Policy.load().require_high_risk("set_bucket_public_access")
        if not approved:
            raise PermissionError("set_bucket_public_access requires approved=true")
        value = _exact(bucket_id, "bucket_id")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                current = await _get_exact(sdk, value, resolved)
                if current.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                item = await sdk.buckets.set_public_access(
                    value,
                    public,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                _assert_context(item, resolved)
                return {"bucket": _bucket(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="set_bucket_public_access",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def create_bucket_signed_url(
        bucket_id: str,
        key: str,
        destination_file: str,
        expires_in_seconds: int = 900,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create a short-lived blob URL; no persistent credentials are returned."""
        Policy.load().require_high_risk("create_bucket_signed_url")
        if not approved:
            raise PermissionError("create_bucket_signed_url requires approved=true")
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
                resolved = _context(sdk, cluster, org, project)
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
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
        max_bytes: int = 1024**3,
        timeout_seconds: float = 300.0,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Upload one bounded workspace file without serializing object bytes."""
        Policy.load().require_high_risk("upload_bucket_file")
        if not approved:
            raise PermissionError("upload_bucket_file requires approved=true")
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
        _transfer_bounds(max_bytes, timeout_seconds)
        source = _upload_path(local_path)
        size = source.stat().st_size
        if size > max_bytes:
            raise ValueError("local file exceeds the approved max_bytes")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                await asyncio.wait_for(
                    sdk.buckets.upload_file(
                        URL(source.as_uri()), _blob_uri(item, exact_key), update=False
                    ),
                    timeout=timeout_seconds,
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
        max_bytes: int = 1024**3,
        timeout_seconds: float = 300.0,
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Download one bounded blob to a new file below the workspace root."""
        Policy.load().require_high_risk("download_bucket_file")
        if not approved:
            raise PermissionError("download_bucket_file requires approved=true")
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
        _transfer_bounds(max_bytes, timeout_seconds)
        destination = _download_path(local_path)
        resolved: ApoloContext | None = None
        completed = False
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                entry = await sdk.buckets.head_blob(
                    item.id,
                    exact_key,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                if entry.size > max_bytes:
                    raise ValueError("remote blob exceeds the approved max_bytes")
                await asyncio.wait_for(
                    sdk.buckets.download_file(
                        _blob_uri(item, exact_key),
                        URL(destination.as_uri()),
                        update=False,
                        continue_=False,
                    ),
                    timeout=timeout_seconds,
                )
                actual_size = destination.stat().st_size
                if actual_size != entry.size or actual_size > max_bytes:
                    raise RuntimeError(
                        "downloaded file size does not match blob metadata"
                    )
                completed = True
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
        approved: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact blob key; recursive/prefix deletion is not exposed."""
        Policy.load().require_high_risk("delete_bucket_blob")
        if not approved:
            raise PermissionError("delete_bucket_blob requires approved=true")
        value = _exact(bucket_id, "bucket_id")
        exact_key = _key(key)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
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
        approved: bool = False,
        automatic_cleanup: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Delete one exact empty bucket ID, optionally as ledger-owned cleanup."""
        Policy.load().require_high_risk("delete_bucket")
        if not automatic_cleanup and not approved:
            raise PermissionError("delete_bucket requires approved=true")
        value = _exact(bucket_id, "bucket_id")
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = _context(sdk, cluster, org, project)
                item = await _get_exact(sdk, value, resolved)
                if item.id != value:
                    raise ValueError("bucket_id must be the exact immutable bucket ID")
                if automatic_cleanup:
                    authorize_cleanup(
                        resource_type="bucket",
                        resource_id=item.id,
                        cluster=resolved.cluster,
                        org=resolved.org,
                        project=resolved.project,
                    )
                await sdk.buckets.rm(
                    item.id,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                )
                return {
                    "status": "deleted",
                    "id": item.id,
                    "automatic_cleanup": automatic_cleanup,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="delete_bucket",
                context=resolved.as_dict() if resolved else None,
                resource=value,
            ) from None
