"""Bounded MCP exposure for the optional typed ``apolo-flow`` facade."""

from __future__ import annotations

import dataclasses
import enum
import importlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncContextManager, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..errors import ApoloToolError, normalize_error
from ..ledger import ensure_ledger_writable, record_created_resource, redact_credentials
from ..policy import Policy


READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)

MAX_LIST = 100
MAX_TASKS = 1_000
MAX_LOG_BYTES = 1_000_000
MAX_LOG_CHUNKS = 1_000
MAX_LOG_SECONDS = 300.0
MAX_WAIT_SECONDS = 3_600.0
MAX_POLL_SECONDS = 60.0
MAX_WRITE_SECONDS = 600.0


@dataclasses.dataclass(frozen=True)
class FlowScope:
    cluster: str
    org: str
    project: str
    allowed_workspace_root: Path
    workspace_path: Path
    config_path: Path
    project_path: Path | None

    def context(self) -> dict[str, str]:
        return {"cluster": self.cluster, "org": self.org, "project": self.project}

    def paths(self) -> dict[str, str | None]:
        return {
            "allowed_workspace_root": str(self.allowed_workspace_root),
            "workspace_path": str(self.workspace_path),
            "config_path": str(self.config_path),
            "project_path": str(self.project_path) if self.project_path else None,
        }


class FlowAPIProvider(Protocol):
    """Provide one context-isolated accepted ``apolo_flow.api.FlowAPI`` instance."""

    def api(self, scope: FlowScope) -> AsyncContextManager[Any]: ...


class LocalFlowAPIProvider:
    """Fail when the installed facade has no safe public bootstrap API.

    ``apolo-flow`` 26.7 publishes the accepted facade, but its public constructor
    requires already initialized live and batch adapters.  The package does not expose
    a factory that initializes those adapters for an explicit cluster/org/project.
    Changing the SDK's saved context would make the server unsafe, so embedding
    applications must inject a provider until such a factory is available.
    """

    @asynccontextmanager
    async def api(self, scope: FlowScope) -> AsyncIterator[Any]:
        try:
            module = importlib.import_module("apolo_flow.api")
            getattr(module, "FlowAPI")
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                "Flow tools require apolo-flow exposing apolo_flow.api.FlowAPI; "
                "install a compatible release or inject FlowAPIProvider"
            ) from exc
        raise RuntimeError(
            "Automatic FlowAPI bootstrap is unavailable: the installed apolo-flow "
            "facade requires initialized live and batch adapters but exposes no "
            "public factory for explicit cluster/org/project construction; inject "
            "FlowAPIProvider without changing saved context"
        )
        if False:  # pragma: no cover - makes this an async context manager generator
            yield None


_provider: ContextVar[FlowAPIProvider] = ContextVar(
    "flow_api_provider", default=LocalFlowAPIProvider()
)


def set_flow_api_provider(provider: FlowAPIProvider) -> Token[FlowAPIProvider]:
    """Override the provider in the current context for embedding or tests."""
    return _provider.set(provider)


def reset_flow_api_provider(token: Token[FlowAPIProvider]) -> None:
    _provider.reset(token)


def _bound(value: float, name: str, maximum: float) -> None:
    if not 0 < value <= maximum:
        raise ValueError(f"{name} must be greater than 0 and at most {maximum:g}")


def _path(value: str, name: str, *, directory: bool) -> Path:
    try:
        resolved = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{name} must be an existing local path") from exc
    if directory != resolved.is_dir():
        kind = "directory" if directory else "file"
        raise ValueError(f"{name} must be an existing {kind}")
    return resolved


def _scope(
    cluster: str,
    org: str,
    project: str,
    allowed_workspace_root: str,
    workspace_path: str,
    config_path: str,
    project_path: str | None,
) -> FlowScope:
    for value, name in ((cluster, "cluster"), (org, "org"), (project, "project")):
        if not value.strip():
            raise ValueError(f"{name} must be explicit and non-empty")
    root = _path(allowed_workspace_root, "allowed_workspace_root", directory=True)
    workspace = _path(workspace_path, "workspace_path", directory=True)
    config = _path(config_path, "config_path", directory=False)
    project_config = (
        _path(project_path, "project_path", directory=False)
        if project_path is not None
        else None
    )
    for candidate, name in (
        (workspace, "workspace_path"),
        (config, "config_path"),
        (project_config, "project_path"),
    ):
        if candidate is None:
            continue
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{name} escapes allowed_workspace_root") from exc
    for candidate, name in ((config, "config_path"), (project_config, "project_path")):
        if candidate is None:
            continue
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"{name} must be inside workspace_path") from exc
    return FlowScope(cluster, org, project, root, workspace, config, project_config)


def _approve(operation: str, approved: bool) -> None:
    if not approved:
        raise PermissionError(f"Operation {operation!r} requires approved=true")
    Policy.load().require_high_risk(operation)


@asynccontextmanager
async def _api(scope: FlowScope, operation: str) -> AsyncIterator[Any]:
    try:
        async with _provider.get().api(scope) as facade:
            yield facade
    except ApoloToolError:
        raise
    except Exception as exc:
        raise normalize_error(
            exc, operation=operation, context=scope.context()
        ) from None


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def _response(scope: FlowScope, **values: Any) -> dict[str, Any]:
    return {
        **{key: _plain(value) for key, value in values.items()},
        "context": scope.context(),
        "paths": scope.paths(),
    }


def _items(value: Any, field: str, limit: int) -> tuple[list[Any], bool]:
    raw = list(getattr(value, field, value))
    return raw[:limit], bool(getattr(value, "truncated", False) or len(raw) > limit)


def _redacted_log(value: Any, max_bytes: int) -> dict[str, Any]:
    raw = bytes(getattr(value, "data", b""))
    decoded = raw.decode("utf-8", errors="replace")
    text = redact_credentials(decoded)
    redacted = text != decoded
    encoded = text.encode("utf-8")
    rebound = len(encoded) > max_bytes
    if rebound:
        text = encoded[:max_bytes].decode("utf-8", errors="ignore")
        encoded = text.encode("utf-8")
    return {
        "raw_id": getattr(value, "raw_id", None),
        "logs": text,
        "bytes": len(encoded),
        "chunks": int(getattr(value, "chunks", 0)),
        "truncated": bool(getattr(value, "truncated", False) or rebound),
        "redacted": redacted,
    }


ScopeArgs = tuple[str, str, str, str, str, str, str | None]


def _make_scope(args: ScopeArgs) -> FlowScope:
    return _scope(*args)


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def flow_live_list(
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List Flow live jobs within explicit context and local path scope."""
        _bound(limit, "limit", MAX_LIST)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_live_list") as api:
            result = await api.live_list(limit=limit)
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=READ_ONLY)
    async def flow_live_get(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        suffix: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Resolve one logical Flow job, with a bounded multi-job result."""
        _bound(limit, "limit", MAX_LIST)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_live_get") as api:
            result = await api.live_get(job_id, suffix)
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=WRITE)
    async def flow_live_run(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        suffix: str | None = None,
        params: dict[str, str] | None = None,
        args: list[str] | None = None,
        timeout_seconds: float = 300,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Start a configured Flow live job after explicit approval and policy."""
        _approve("flow_live_run", approved)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        ensure_ledger_writable()
        async with _api(scope, "flow_live_run") as api:
            result = await api.live_run(
                job_id,
                suffix=suffix,
                params=params,
                args=tuple(args) if args else None,
                timeout=timeout_seconds,
            )
            for item in getattr(result, "jobs", ()):
                raw_id = getattr(item, "raw_id", None)
                if raw_id:
                    record_created_resource(
                        resource_type="job",
                        resource_id=raw_id,
                        cluster=cluster,
                        org=org,
                        project=project,
                        operation="flow_live_run",
                    )
            return _response(scope, result=result)

    @mcp.tool(annotations=READ_ONLY)
    async def flow_live_logs(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        suffix: str | None = None,
        timeout_seconds: float = 60,
        max_chunks: int = 100,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        """Read bounded Flow live logs with MCP-side credential redaction."""
        _bound(timeout_seconds, "timeout_seconds", MAX_LOG_SECONDS)
        _bound(max_chunks, "max_chunks", MAX_LOG_CHUNKS)
        _bound(max_bytes, "max_bytes", MAX_LOG_BYTES)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_live_logs") as api:
            result = await api.live_logs(
                job_id,
                suffix=suffix,
                timeout=timeout_seconds,
                max_chunks=max_chunks,
                max_bytes=max_bytes,
            )
            return _response(scope, log=_redacted_log(result, max_bytes))

    @mcp.tool(annotations=READ_ONLY)
    async def flow_live_wait(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        suffix: str | None = None,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 2,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Wait a bounded time for a Flow live job to terminate."""
        _bound(timeout_seconds, "timeout_seconds", MAX_WAIT_SECONDS)
        _bound(poll_interval_seconds, "poll_interval_seconds", MAX_POLL_SECONDS)
        _bound(limit, "limit", MAX_LIST)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_live_wait") as api:
            result = await api.live_wait(
                job_id,
                suffix=suffix,
                timeout=timeout_seconds,
                poll_interval=poll_interval_seconds,
            )
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def flow_live_kill(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        suffix: str | None = None,
        timeout_seconds: float = 300,
        limit: int = 20,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Kill a Flow live job after explicit approval and policy."""
        _approve("flow_live_kill", approved)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        _bound(limit, "limit", MAX_LIST)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_live_kill") as api:
            result = await api.live_kill(job_id, suffix=suffix, timeout=timeout_seconds)
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def flow_live_kill_all(
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        timeout_seconds: float = 300,
        limit: int = 20,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Kill all jobs in exactly one explicit Flow context."""
        _approve("flow_live_kill_all", approved)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        _bound(limit, "limit", MAX_LIST)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_live_kill_all") as api:
            result = await api.live_kill_all(limit=limit, timeout=timeout_seconds)
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=WRITE)
    async def flow_bake_start(
        batch: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        params: dict[str, str] | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
        local_executor: bool = False,
        task_limit: int = 100,
        timeout_seconds: float = 300,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Start a bake only through FlowAPI BatchRunner orchestration."""
        _approve("flow_bake_start", approved)
        _bound(task_limit, "task_limit", MAX_TASKS)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        ensure_ledger_writable()
        async with _api(scope, "flow_bake_start") as api:
            result = await api.bake_start(
                batch,
                params=params,
                name=name,
                tags=tags or (),
                local_executor=local_executor,
                task_limit=task_limit,
                timeout=timeout_seconds,
            )
            bake_id = getattr(result, "id", None)
            if not bake_id:
                raise ValueError("FlowAPI bake_start returned no bake ID")
            record_created_resource(
                resource_type="bake",
                resource_id=bake_id,
                cluster=cluster,
                org=org,
                project=project,
                operation="flow_bake_start",
            )
            return _response(scope, bake=result)

    @mcp.tool(annotations=READ_ONLY)
    async def flow_bake_list(
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        limit: int = 20,
        task_limit: int = 100,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """List bakes and bounded task state in one explicit context."""
        _bound(limit, "limit", MAX_LIST)
        _bound(task_limit, "task_limit", MAX_TASKS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_bake_list") as api:
            result = await api.bake_list(
                limit=limit, task_limit=task_limit, tags=frozenset(tags or ())
            )
            items, truncated = _items(result, "bakes", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=READ_ONLY)
    async def flow_bake_get(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        attempt_no: int = -1,
        task_limit: int = 100,
    ) -> dict[str, Any]:
        """Get structured bake, attempt, and bounded task state."""
        _bound(task_limit, "task_limit", MAX_TASKS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_bake_get") as api:
            result = await api.bake_get(
                bake_id_or_name, attempt_no=attempt_no, task_limit=task_limit
            )
            return _response(scope, bake=result)

    @mcp.tool(annotations=READ_ONLY)
    async def flow_bake_logs(
        bake_id_or_name: str,
        task_id: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        attempt_no: int = -1,
        timeout_seconds: float = 60,
        max_chunks: int = 100,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        """Read bounded bake task logs with MCP-side credential redaction."""
        _bound(timeout_seconds, "timeout_seconds", MAX_LOG_SECONDS)
        _bound(max_chunks, "max_chunks", MAX_LOG_CHUNKS)
        _bound(max_bytes, "max_bytes", MAX_LOG_BYTES)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_bake_logs") as api:
            result = await api.bake_logs(
                bake_id_or_name,
                task_id,
                attempt_no=attempt_no,
                timeout=timeout_seconds,
                max_chunks=max_chunks,
                max_bytes=max_bytes,
            )
            return _response(scope, log=_redacted_log(result, max_bytes))

    @mcp.tool(annotations=READ_ONLY)
    async def flow_bake_wait(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        attempt_no: int = -1,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 2,
        task_limit: int = 100,
    ) -> dict[str, Any]:
        """Wait a bounded time for a bake attempt to terminate."""
        _bound(timeout_seconds, "timeout_seconds", MAX_WAIT_SECONDS)
        _bound(poll_interval_seconds, "poll_interval_seconds", MAX_POLL_SECONDS)
        _bound(task_limit, "task_limit", MAX_TASKS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        async with _api(scope, "flow_bake_wait") as api:
            result = await api.bake_wait(
                bake_id_or_name,
                attempt_no=attempt_no,
                timeout=timeout_seconds,
                poll_interval=poll_interval_seconds,
                task_limit=task_limit,
            )
            return _response(scope, bake=result)

    async def bake_mutation(
        operation: str,
        call: Callable[[Any], Any],
        scope: FlowScope,
    ) -> dict[str, Any]:
        async with _api(scope, operation) as api:
            result = await call(api)
            return _response(scope, bake=result)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def flow_bake_cancel(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        attempt_no: int = -1,
        task_limit: int = 100,
        timeout_seconds: float = 300,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Cancel a bake attempt after explicit approval and policy."""
        _approve("flow_bake_cancel", approved)
        _bound(task_limit, "task_limit", MAX_TASKS)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        return await bake_mutation(
            "flow_bake_cancel",
            lambda api: api.bake_cancel(
                bake_id_or_name,
                attempt_no=attempt_no,
                task_limit=task_limit,
                timeout=timeout_seconds,
            ),
            scope,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def flow_bake_restart(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        allowed_workspace_root: str,
        workspace_path: str,
        config_path: str,
        project_path: str | None = None,
        attempt_no: int = -1,
        from_failed: bool = True,
        local_executor: bool = False,
        task_limit: int = 100,
        timeout_seconds: float = 300,
        approved: bool = False,
    ) -> dict[str, Any]:
        """Restart a bake through BatchRunner after approval and policy."""
        _approve("flow_bake_restart", approved)
        _bound(task_limit, "task_limit", MAX_TASKS)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = _make_scope(
            (
                cluster,
                org,
                project,
                allowed_workspace_root,
                workspace_path,
                config_path,
                project_path,
            )
        )
        return await bake_mutation(
            "flow_bake_restart",
            lambda api: api.bake_restart(
                bake_id_or_name,
                attempt_no=attempt_no,
                from_failed=from_failed,
                local_executor=local_executor,
                task_limit=task_limit,
                timeout=timeout_seconds,
            ),
            scope,
        )
