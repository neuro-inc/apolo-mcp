"""Bounded MCP exposure for the released typed ``apolo-flow`` facade."""

from __future__ import annotations

import dataclasses
import enum
import os
import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncContextManager, Protocol

import apolo_sdk
from apolo_flow.api import open_flow_api
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .._client import client
from ..errors import ApoloToolError, normalize_error
from ..ledger import (
    ensure_ledger_writable,
    record_created_resource,
    record_resource_action,
    redact_credentials,
)
from ..policy import MutationEffect, PolicyMode, authorize_mutation, current_policy
from ..workspace import allowed_workspace_root, resolve_workspace_path
from . import flow_config


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
_BAKE_ID = re.compile(r"\bbake-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")

_FLOW_SCOPE_HELP = """\
workspace_path is the Flow project root and must contain a real .apolo directory.
"""
_FLOW_LIVE_HELP = """\
flow_live_run reads .apolo/live.yml or .apolo/live.yaml, whose minimum shape is
`kind: live` plus a `jobs` mapping; job_id selects a key in that mapping. Each plain
job needs an image and may define cmd or bash. Set `detach: true` on jobs started by
MCP, then monitor them with separate get, logs, and bounded wait calls. Optional
project settings belong in .apolo/project.yml or .apolo/project.yaml.
"""
_FLOW_BATCH_HELP = """\
flow_bake_start reads .apolo/<batch>.yml or .yaml, whose minimum shape is `kind:
batch` plus a `tasks` list; batch selects that workflow. Each plain task needs an
image and may define cmd or bash. Optional project settings belong in
.apolo/project.yml or .apolo/project.yaml.
"""


def _document_flow_scope(function: Any) -> Any:
    extra = ""
    if function.__name__ == "flow_live_run":
        extra = _FLOW_LIVE_HELP
    elif function.__name__ == "flow_bake_start":
        extra = _FLOW_BATCH_HELP
    function.__doc__ = f"{function.__doc__}\n\n{_FLOW_SCOPE_HELP}{extra}"
    return function


@dataclasses.dataclass(frozen=True)
class FlowScope:
    username: str
    cluster: str
    org: str
    project: str
    allowed_workspace_root: Path
    workspace_path: Path

    def context(self) -> dict[str, str]:
        return {
            "username": self.username,
            "cluster": self.cluster,
            "org": self.org,
            "project": self.project,
        }

    def paths(self) -> dict[str, str | None]:
        return {
            "allowed_workspace_root": str(self.allowed_workspace_root),
            "workspace_path": str(self.workspace_path),
        }


class FlowAPIProvider(Protocol):
    """Provide one context-isolated accepted ``apolo_flow.api.FlowAPI`` instance."""

    def api(self, scope: FlowScope) -> AsyncContextManager[Any]: ...


class LocalFlowAPIProvider:
    """Open the released Flow facade without changing the saved SDK context."""

    @asynccontextmanager
    async def api(self, scope: FlowScope) -> AsyncIterator[Any]:
        config_path = Path(
            os.environ.get("APOLO_CONFIG", apolo_sdk.DEFAULT_CONFIG_PATH)
        ).expanduser()
        async with open_flow_api(
            cluster=scope.cluster,
            org=scope.org,
            project=scope.project,
            allowed_workspace_root=scope.allowed_workspace_root,
            config_path=config_path,
            project_path=scope.workspace_path,
        ) as api:
            yield api


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


def _scope(
    username: str,
    cluster: str,
    org: str,
    project: str,
    workspace_path: str,
) -> FlowScope:
    for value, name in ((cluster, "cluster"), (org, "org"), (project, "project")):
        if not value.strip():
            raise ValueError(f"{name} must be explicit and non-empty")
    root = allowed_workspace_root()
    workspace = resolve_workspace_path(
        workspace_path, name="workspace_path", directory=True
    )
    flow_config = workspace / ".apolo"
    if flow_config.is_symlink() or not flow_config.is_dir():
        raise ValueError(
            "workspace_path must be a Flow root containing a real .apolo directory"
        )
    return FlowScope(username, cluster, org, project, root, workspace)


def _authorize_flow_resource(
    operation: str,
    effect: MutationEffect,
    resource_type: str,
    resource_id: str,
    scope: FlowScope,
) -> None:
    authorize_mutation(
        operation=operation,
        effect=effect,
        resource_type=resource_type,
        resource_id=resource_id,
        username=scope.username,
        cluster=scope.cluster,
        org=scope.org,
        project=scope.project,
    )


def _record_flow_action(
    operation: str,
    action: str,
    resource_type: str,
    resource_id: str,
    scope: FlowScope,
) -> None:
    record_resource_action(
        resource_type=resource_type,
        resource_id=resource_id,
        username=scope.username,
        cluster=scope.cluster,
        org=scope.org,
        project=scope.project,
        operation=operation,
        action=action,
    )


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


async def _journal_failed_bake_start(
    api: Any, correlation_tag: str, error: Exception, scope: FlowScope
) -> tuple[str, ...]:
    """Recover and journal bake IDs when orchestration fails after creation."""
    bake_ids: set[str] = set()
    try:
        result = await api.bake_list(
            limit=2,
            task_limit=1,
            tags=frozenset({correlation_tag}),
        )
        bakes, _ = _items(result, "bakes", 2)
        bake_ids.update(
            bake_id
            for bake in bakes
            if (bake_id := getattr(bake, "id", None)) is not None
        )
    except Exception:
        # The upstream facade currently includes the created ID in its post-create
        # RuntimeError. Use it only as a recovery fallback if structured lookup fails.
        pass
    if not bake_ids:
        bake_ids.update(_BAKE_ID.findall(str(error)))
    for bake_id in sorted(bake_ids):
        record_created_resource(
            resource_type="bake",
            resource_id=bake_id,
            username=scope.username,
            cluster=scope.cluster,
            org=scope.org,
            project=scope.project,
            operation="flow_bake_start",
        )
    return tuple(sorted(bake_ids))


def _live_raw_ids(value: Any) -> set[str]:
    return {
        raw_id
        for item in value
        if (raw_id := getattr(item, "raw_id", None)) is not None
    }


async def _journal_failed_live_run(
    api: Any,
    job_id: str,
    suffix: str | None,
    previous_ids: set[str],
    scope: FlowScope,
) -> tuple[str, ...]:
    """Journal only live jobs that appeared after a failed start attempt."""
    try:
        current = await api.live_get(job_id, suffix)
    except Exception:
        return ()
    created_ids = _live_raw_ids(current) - previous_ids
    for raw_id in sorted(created_ids):
        record_created_resource(
            resource_type="job",
            resource_id=raw_id,
            username=scope.username,
            cluster=scope.cluster,
            org=scope.org,
            project=scope.project,
            operation="flow_live_run",
        )
    return tuple(sorted(created_ids))


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


ScopeArgs = tuple[str, str, str, str]


async def _make_scope(args: ScopeArgs) -> FlowScope:
    async with client() as sdk:
        username = sdk.config.username
    return _scope(username, *args)


def register(mcp: FastMCP) -> None:
    flow_config.register(mcp)

    @mcp.tool(annotations=READ_ONLY)
    @_document_flow_scope
    async def flow_live_list(
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List Flow live jobs within explicit context and local path scope."""
        _bound(limit, "limit", MAX_LIST)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_live_list") as api:
            result = await api.live_list(limit=limit)
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=READ_ONLY)
    @_document_flow_scope
    async def flow_live_get(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        suffix: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Resolve one logical Flow job, with a bounded multi-job result."""
        _bound(limit, "limit", MAX_LIST)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_live_get") as api:
            result = await api.live_get(job_id, suffix)
            items, truncated = _items(result, "jobs", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=WRITE)
    @_document_flow_scope
    async def flow_live_run(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        suffix: str | None = None,
        params: dict[str, str] | None = None,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Start a detached Flow live job under the server mutation policy.

        Set ``detach: true`` on the selected job so this operation returns after
        submission. Monitor it separately with get, logs, and bounded wait. Pre/post
        raw-ID comparison journals only newly created jobs when upstream startup
        times out after submission.
        """
        authorize_mutation(operation="flow_live_run", effect=MutationEffect.CREATE)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        ensure_ledger_writable()
        async with _api(scope, "flow_live_run") as api:
            previous_ids = _live_raw_ids(await api.live_get(job_id, suffix))
            try:
                result = await api.live_run(
                    job_id,
                    suffix=suffix,
                    params=params,
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                created_ids = await _journal_failed_live_run(
                    api, job_id, suffix, previous_ids, scope
                )
                if created_ids:
                    raise RuntimeError(
                        "Flow live orchestration failed after creating and journaling "
                        f"{', '.join(created_ids)}; upstream error: {exc}"
                    ) from exc
                raise
            for item in getattr(result, "jobs", ()):
                raw_id = getattr(item, "raw_id", None)
                if raw_id:
                    record_created_resource(
                        resource_type="job",
                        resource_id=raw_id,
                        username=scope.username,
                        cluster=cluster,
                        org=org,
                        project=project,
                        operation="flow_live_run",
                    )
            return _response(scope, result=result)

    @mcp.tool(annotations=READ_ONLY)
    @_document_flow_scope
    async def flow_live_logs(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        suffix: str | None = None,
        timeout_seconds: float = 60,
        max_chunks: int = 100,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        """Read bounded Flow live logs with MCP-side credential redaction."""
        _bound(timeout_seconds, "timeout_seconds", MAX_LOG_SECONDS)
        _bound(max_chunks, "max_chunks", MAX_LOG_CHUNKS)
        _bound(max_bytes, "max_bytes", MAX_LOG_BYTES)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
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
    @_document_flow_scope
    async def flow_live_wait(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        suffix: str | None = None,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 2,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Wait a bounded time for a Flow live job to terminate."""
        _bound(timeout_seconds, "timeout_seconds", MAX_WAIT_SECONDS)
        _bound(poll_interval_seconds, "poll_interval_seconds", MAX_POLL_SECONDS)
        _bound(limit, "limit", MAX_LIST)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
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
    @_document_flow_scope
    async def flow_live_kill(
        job_id: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        suffix: str | None = None,
        timeout_seconds: float = 300,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Kill a Flow live job under the server mutation policy."""
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        _bound(limit, "limit", MAX_LIST)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_live_kill") as api:
            current = await api.live_get(job_id, suffix)
            jobs, current_truncated = _items(current, "jobs", MAX_LIST)
            if current_truncated:
                raise ValueError("Flow live job resolution exceeded the safety bound")
            if not jobs:
                raise ValueError(f"Flow live job not found: {job_id}")
            for item in jobs:
                raw_id = getattr(item, "raw_id", None)
                if not raw_id:
                    raise ValueError("FlowAPI live_get returned a job without a raw ID")
                _authorize_flow_resource(
                    "flow_live_kill", MutationEffect.UPDATE, "job", raw_id, scope
                )
            result = await api.live_kill(job_id, suffix=suffix, timeout=timeout_seconds)
            items, truncated = _items(result, "jobs", limit)
            for item in jobs:
                _record_flow_action(
                    "flow_live_kill", "updated", "job", item.raw_id, scope
                )
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=DESTRUCTIVE)
    @_document_flow_scope
    async def flow_live_kill_all(
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        timeout_seconds: float = 300,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Kill all jobs in exactly one explicit Flow context."""
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        _bound(limit, "limit", MAX_LIST)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_live_kill_all") as api:
            current = await api.live_list(limit=MAX_LIST)
            jobs, current_truncated = _items(current, "jobs", MAX_LIST)
            policy = current_policy()
            if current_truncated and policy.mode is PolicyMode.MANAGED:
                raise PermissionError(
                    "Managed policy cannot authorize flow_live_kill_all because "
                    "the target list is truncated"
                )
            if policy.mode is PolicyMode.READ_ONLY:
                authorize_mutation(
                    operation="flow_live_kill_all", effect=MutationEffect.UPDATE
                )
            for item in jobs:
                raw_id = getattr(item, "raw_id", None)
                if not raw_id:
                    raise ValueError(
                        "FlowAPI live_list returned a job without a raw ID"
                    )
                _authorize_flow_resource(
                    "flow_live_kill_all", MutationEffect.UPDATE, "job", raw_id, scope
                )
            result = await api.live_kill_all(limit=limit, timeout=timeout_seconds)
            items, truncated = _items(result, "jobs", limit)
            for item in jobs:
                _record_flow_action(
                    "flow_live_kill_all", "updated", "job", item.raw_id, scope
                )
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=WRITE)
    @_document_flow_scope
    async def flow_bake_start(
        batch: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        params: dict[str, str] | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
        local_executor: bool = False,
        task_limit: int = 100,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Start a bake only through FlowAPI BatchRunner orchestration.

        An internal unique correlation tag lets MCP journal a bake even when the
        upstream runner fails after creating it.
        """
        authorize_mutation(operation="flow_bake_start", effect=MutationEffect.CREATE)
        _bound(task_limit, "task_limit", MAX_TASKS)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        ensure_ledger_writable()
        correlation_tag = f"apolo-mcp-correlation-{uuid.uuid4().hex}"
        async with _api(scope, "flow_bake_start") as api:
            try:
                result = await api.bake_start(
                    batch,
                    params=params,
                    name=name,
                    tags=(*tuple(tags or ()), correlation_tag),
                    local_executor=local_executor,
                    task_limit=task_limit,
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                bake_ids = await _journal_failed_bake_start(
                    api, correlation_tag, exc, scope
                )
                if bake_ids:
                    raise RuntimeError(
                        "Flow bake orchestration failed after creating and journaling "
                        f"{', '.join(bake_ids)}; upstream error: {exc}"
                    ) from exc
                raise
            bake_id = getattr(result, "id", None)
            if not bake_id:
                raise ValueError("FlowAPI bake_start returned no bake ID")
            record_created_resource(
                resource_type="bake",
                resource_id=bake_id,
                username=scope.username,
                cluster=cluster,
                org=org,
                project=project,
                operation="flow_bake_start",
            )
            return _response(scope, bake=result)

    @mcp.tool(annotations=READ_ONLY)
    @_document_flow_scope
    async def flow_bake_list(
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        limit: int = 20,
        task_limit: int = 100,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """List bakes and bounded task state in one explicit context."""
        _bound(limit, "limit", MAX_LIST)
        _bound(task_limit, "task_limit", MAX_TASKS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_bake_list") as api:
            result = await api.bake_list(
                limit=limit, task_limit=task_limit, tags=frozenset(tags or ())
            )
            items, truncated = _items(result, "bakes", limit)
            return _response(scope, items=items, limit=limit, truncated=truncated)

    @mcp.tool(annotations=READ_ONLY)
    @_document_flow_scope
    async def flow_bake_get(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        attempt_no: int = -1,
        task_limit: int = 100,
    ) -> dict[str, Any]:
        """Get structured bake, attempt, and bounded task state."""
        _bound(task_limit, "task_limit", MAX_TASKS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_bake_get") as api:
            result = await api.bake_get(
                bake_id_or_name, attempt_no=attempt_no, task_limit=task_limit
            )
            return _response(scope, bake=result)

    @mcp.tool(annotations=READ_ONLY)
    @_document_flow_scope
    async def flow_bake_logs(
        bake_id_or_name: str,
        task_id: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        attempt_no: int = -1,
        timeout_seconds: float = 60,
        max_chunks: int = 100,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        """Read bounded bake task logs with MCP-side credential redaction."""
        _bound(timeout_seconds, "timeout_seconds", MAX_LOG_SECONDS)
        _bound(max_chunks, "max_chunks", MAX_LOG_CHUNKS)
        _bound(max_bytes, "max_bytes", MAX_LOG_BYTES)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
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
    @_document_flow_scope
    async def flow_bake_wait(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        attempt_no: int = -1,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 2,
        task_limit: int = 100,
    ) -> dict[str, Any]:
        """Wait a bounded time for a bake attempt to terminate."""
        _bound(timeout_seconds, "timeout_seconds", MAX_WAIT_SECONDS)
        _bound(poll_interval_seconds, "poll_interval_seconds", MAX_POLL_SECONDS)
        _bound(task_limit, "task_limit", MAX_TASKS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
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

    @mcp.tool(annotations=DESTRUCTIVE)
    @_document_flow_scope
    async def flow_bake_cancel(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        attempt_no: int = -1,
        task_limit: int = 100,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Cancel a bake attempt under the server mutation policy."""
        _bound(task_limit, "task_limit", MAX_TASKS)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_bake_cancel") as api:
            current = await api.bake_get(
                bake_id_or_name, attempt_no=attempt_no, task_limit=task_limit
            )
            bake_id = getattr(current, "id", None)
            if not bake_id:
                raise ValueError("FlowAPI bake_get returned no bake ID")
            _authorize_flow_resource(
                "flow_bake_cancel", MutationEffect.UPDATE, "bake", bake_id, scope
            )
            result = await api.bake_cancel(
                bake_id_or_name,
                attempt_no=attempt_no,
                task_limit=task_limit,
                timeout=timeout_seconds,
            )
            _record_flow_action("flow_bake_cancel", "updated", "bake", bake_id, scope)
            return _response(scope, bake=result)

    @mcp.tool(annotations=DESTRUCTIVE)
    @_document_flow_scope
    async def flow_bake_restart(
        bake_id_or_name: str,
        cluster: str,
        org: str,
        project: str,
        workspace_path: str,
        attempt_no: int = -1,
        from_failed: bool = True,
        local_executor: bool = False,
        task_limit: int = 100,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Restart a bake through BatchRunner under the server mutation policy."""
        _bound(task_limit, "task_limit", MAX_TASKS)
        _bound(timeout_seconds, "timeout_seconds", MAX_WRITE_SECONDS)
        scope = await _make_scope(
            (
                cluster,
                org,
                project,
                workspace_path,
            )
        )
        async with _api(scope, "flow_bake_restart") as api:
            current = await api.bake_get(
                bake_id_or_name, attempt_no=attempt_no, task_limit=task_limit
            )
            bake_id = getattr(current, "id", None)
            if not bake_id:
                raise ValueError("FlowAPI bake_get returned no bake ID")
            _authorize_flow_resource(
                "flow_bake_restart", MutationEffect.UPDATE, "bake", bake_id, scope
            )
            result = await api.bake_restart(
                bake_id_or_name,
                attempt_no=attempt_no,
                from_failed=from_failed,
                local_executor=local_executor,
                task_limit=task_limit,
                timeout=timeout_seconds,
            )
            _record_flow_action("flow_bake_restart", "updated", "bake", bake_id, scope)
            return _response(scope, bake=result)
