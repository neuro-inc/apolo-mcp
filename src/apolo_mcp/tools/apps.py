"""Bounded Apps discovery, observation, and reviewed plan/apply operations."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from datetime import datetime
from functools import wraps
from time import monotonic
from typing import Any, Awaitable, Callable

import apolo_sdk
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .._client import client
from ..app_plans import (
    DEFAULT_TTL_SECONDS,
    claim_for_apply,
    create_plan,
    deep_patch,
    ensure_secret_references_only,
    record_failure,
    record_success,
)
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error, sanitize_message
from ..ledger import ensure_ledger_writable, record_created_resource
from ..policy import Policy


READ_ONLY = ToolAnnotations(
    title="Read-only Apps operation",
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
PLAN = ToolAnnotations(
    title="Create a local Apps review plan",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
WRITE = ToolAnnotations(
    title="Apply an approved Apps plan",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    title="Apply an approved destructive Apps plan",
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

MAX_RESULTS = 100
MAX_LOG_BYTES = 1_048_576
MAX_LOG_LINES = 10_000
MAX_WAIT_SECONDS = 600.0
MAX_STRUCTURED_CHARS = 100_000
TERMINAL_STATES = {"healthy", "degraded", "errored", "uninstalled"}
_SENSITIVE = re.compile(
    r"(?i)(password|passwd|token|secret(?:_?value)?|api[-_]?key|private[-_]?key)"
)
_LOG_CREDENTIAL = re.compile(
    r"(?i)\b(authorization|cookie|token|password|secret|api[-_]?key)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
_LOG_URL_CREDENTIAL = re.compile(r"(://)[^/@\s]+@")


def _bounded(limit: int) -> int:
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    return limit


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _context(
    sdk: Any, cluster: str | None, org: str | None, project: str | None
) -> ApoloContext:
    return resolve_context(sdk.config, cluster=cluster, org=org, project=project)


def _app_dict(app: Any) -> dict[str, Any]:
    return {
        "id": app.id,
        "name": app.name,
        "display_name": app.display_name,
        "template_name": app.template_name,
        "template_version": app.template_version,
        "state": str(getattr(app.state, "value", app.state)),
        "endpoints": list(app.endpoints),
        "namespace": app.namespace,
        "cluster": app.cluster_name,
        "org": app.org_name,
        "project": app.project_name,
        "created_at": _iso(app.created_at),
        "updated_at": _iso(app.updated_at),
    }


def _assert_app_context(app: Any, context: ApoloContext) -> None:
    actual = (app.cluster_name, app.org_name, app.project_name)
    expected = (context.cluster, context.org, context.project)
    if actual != expected:
        raise ValueError(
            "App exists outside the resolved context; provide its exact cluster, "
            "organization, and project"
        )


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if _SENSITIVE.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return sanitize_message(value, limit=2000)
    return value


def _redact_log(value: str) -> str:
    value = _LOG_URL_CREDENTIAL.sub(r"\1<redacted>@", value)
    return _LOG_CREDENTIAL.sub(r"\1\2<redacted>", value)


_EXPECTED_SDK_ERRORS = (
    apolo_sdk.ClientError,
    apolo_sdk.ConfigError,
    apolo_sdk.IllegalArgumentError,
    apolo_sdk.NotSupportedError,
    apolo_sdk.ResourceNotFound,
    apolo_sdk.ServerNotAvailable,
    apolo_sdk.BadGateway,
)


def _normalize_sdk_tool(
    operation: str, function: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return await function(*args, **kwargs)
        except _EXPECTED_SDK_ERRORS as exc:
            supplied = {
                "cluster": kwargs.get("cluster"),
                "org": kwargs.get("org"),
                "project": kwargs.get("project"),
            }
            context = (
                {key: str(value) for key, value in supplied.items()}
                if all(supplied.values())
                else None
            )
            resource = kwargs.get("app_id") or kwargs.get("template_name")
            raise normalize_error(
                exc,
                operation=operation,
                context=context,
                resource=str(resource) if resource else None,
            ) from None

    return wrapped


def _bounded_value(value: Any, max_chars: int) -> dict[str, Any]:
    if not 100 <= max_chars <= MAX_STRUCTURED_CHARS:
        raise ValueError("max_chars must be between 100 and 100000")
    redacted = _redact(value)
    serialized = json.dumps(redacted, default=str, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return {"value": redacted, "text": None, "truncated": False}
    return {
        "value": None,
        "text": serialized[:max_chars],
        "truncated": True,
    }


def _recorded_effects(payload: Mapping[str, Any]) -> dict[str, list[Any]]:
    """Extract review summaries without assuming a template schema version."""
    dependencies: list[Any] = []
    resources: list[Any] = []
    endpoints: list[Any] = []

    def visit(value: Any, path: str = "input") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = f"{path}.{key}"
                lowered = str(key).lower()
                entry = {"path": child, "value": _redact(item)}
                if any(term in lowered for term in ("depend", "app_ref", "instance")):
                    dependencies.append(entry)
                if any(
                    term in lowered
                    for term in ("image", "preset", "storage", "volume", "secret")
                ):
                    resources.append(entry)
                if any(
                    term in lowered
                    for term in ("endpoint", "port", "ingress", "exposure", "public")
                ):
                    endpoints.append(entry)
                visit(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload)
    return {
        "dependencies": dependencies[:50],
        "resources": resources[:50],
        "endpoints": endpoints[:50],
    }


def _resolve_ref(
    schema: Mapping[str, Any], root: Mapping[str, Any]
) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    current: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(current, Mapping):
            return schema
        current = current.get(part.replace("~1", "/").replace("~0", "~"))
    return current if isinstance(current, Mapping) else schema


def _validate_schema(
    value: Any,
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any] | None = None,
    path: str = "input",
) -> list[str]:
    """Validate the common JSON Schema subset emitted by current Apps templates."""
    root = root or schema
    schema = _resolve_ref(schema, root)
    errors: list[str] = []
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list):
        if not any(
            not _validate_schema(value, item, root=root, path=path)
            for item in alternatives
        ):
            errors.append(f"{path} does not match any allowed schema alternative")
        return errors
    expected = schema.get("type")
    type_map: dict[str, Any] = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    allowed: Any
    if isinstance(expected, list):
        allowed = tuple(type_map[item] for item in expected if item in type_map)
    else:
        allowed = type_map.get(str(expected))
    if allowed and (
        not isinstance(value, allowed)
        or expected == "integer"
        and isinstance(value, bool)
    ):
        return [f"{path} must be {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                errors.append(f"{path}.{name} is required")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for name, item in value.items():
                child = properties.get(name)
                if isinstance(child, Mapping):
                    errors.extend(
                        _validate_schema(item, child, root=root, path=f"{path}.{name}")
                    )
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            errors.extend(f"{path}.{name} is not allowed" for name in sorted(unknown))
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            errors.extend(
                _validate_schema(
                    item, schema["items"], root=root, path=f"{path}[{index}]"
                )
            )
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path} is shorter than minLength")
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            errors.append(f"{path} does not match its schema pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} exceeds its maximum")
    return errors


def _schema_paths(
    schema: Mapping[str, Any],
    prefix: str = "",
    *,
    root: Mapping[str, Any] | None = None,
    followed_refs: frozenset[str] = frozenset(),
) -> list[str]:
    root = root or schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in followed_refs:
            return []
        schema = _resolve_ref(schema, root)
        followed_refs = followed_refs | {ref}
    paths: list[str] = []
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(alternatives, list):
        for alternative in alternatives:
            if isinstance(alternative, Mapping):
                paths.extend(
                    _schema_paths(
                        alternative,
                        prefix,
                        root=root,
                        followed_refs=followed_refs,
                    )
                )
    properties = schema.get("properties", {})
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            paths.append(path.lower())
            if isinstance(child, Mapping):
                paths.extend(
                    _schema_paths(
                        child,
                        path,
                        root=root,
                        followed_refs=followed_refs,
                    )
                )
    return paths


def _service_validation(template_name: str, schema: Mapping[str, Any]) -> list[str]:
    if template_name != "service-deployment":
        return ["input validated against the discovered template schema"]
    paths = _schema_paths(schema)
    categories = {
        "image": ("image", "repository", "tag"),
        "preset": ("preset",),
        "commands/env": ("command", "args", "env"),
        "storage/secrets": ("storage", "volume", "secret"),
        "ports/ingress/auth/exposure": (
            "port",
            "ingress",
            "auth",
            "exposure",
            "public",
        ),
        "autoscaling": ("autoscal", "replica"),
        "startup/readiness/liveness probes": (
            "startup",
            "readiness",
            "liveness",
            "probe",
        ),
    }
    validation: list[str] = []
    for label, terms in categories.items():
        matched = sorted(
            {path for path in paths if any(term in path for term in terms)}
        )
        if not matched:
            validation.append(
                f"service-deployment {label}: not present in discovered schema"
            )
            continue
        selected = ", ".join(matched[:20])
        if len(matched) > 20:
            selected += f" (+{len(matched) - 20} more)"
        validation.append(
            f"service-deployment {label}: derived from schema fields {selected}"
        )
    return validation


async def _revision(sdk: Any, app_id: str) -> int | None:
    revisions = await sdk.apps.get_revisions(app_id=app_id)
    return max((item.revision_number for item in revisions), default=None)


def _plan_result(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan["id"],
        "kind": plan["kind"],
        "status": plan["status"],
        "context": plan["context"],
        "app_id": plan.get("app_id"),
        "current_revision": plan.get("current_revision"),
        "template_name": plan.get("template_name"),
        "template_version": plan.get("template_version"),
        "inputs_path": plan.get("inputs_path"),
        "inputs_sha256": plan.get("inputs_sha256"),
        "inputs": plan.get("payload"),
        "dependencies": plan.get("dependencies", []),
        "resources": plan.get("resources", []),
        "endpoints": plan.get("endpoints", []),
        "validation": plan.get("validation", []),
        "destructive_effects": plan.get("destructive_effects", []),
        "expires_at": plan["expires_at"],
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def list_app_templates(
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List templates in an explicitly resolved context, bounded to 100."""
        bounded = _bounded(limit)
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            items = []
            async with sdk.apps.list_templates(
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            ) as iterator:
                async for template in iterator:
                    items.append(
                        {
                            "name": template.name,
                            "title": template.title,
                            "version": template.version,
                            "short_description": template.short_description,
                            "tags": list(template.tags),
                        }
                    )
                    if len(items) > bounded:
                        break
        return {
            "items": items[:bounded],
            "limit": bounded,
            "truncated": len(items) > bounded,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_app_template_versions(
        template_name: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List bounded versions of one Apps template."""
        bounded = _bounded(limit)
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            items = []
            async with sdk.apps.list_template_versions(
                name=template_name,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            ) as iterator:
                async for template in iterator:
                    items.append(
                        {
                            "name": template.name,
                            "version": template.version,
                            "title": template.title,
                            "tags": list(template.tags),
                        }
                    )
                    if len(items) > bounded:
                        break
        return {
            "items": items[:bounded],
            "limit": bounded,
            "truncated": len(items) > bounded,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_app_template(
        template_name: str,
        template_version: str = "latest",
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get the exact template version and current input schema."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            template = await sdk.apps.get_template(
                name=template_name,
                version=template_version,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            )
            if template is None:
                raise ValueError(
                    f"App template not found: {template_name}@{template_version}"
                )
        return {
            "name": template.name,
            "version": template.version,
            "title": template.title,
            "description": template.description,
            "short_description": template.short_description,
            "tags": list(template.tags),
            "input_schema": template.input or {},
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_apps(
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        states: list[str] | None = None,
        include_inactive: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List Apps with state filtering and a strict result bound."""
        bounded = _bounded(limit)
        state_filter = (
            [apolo_sdk.AppState(item) for item in states]
            if states
            else (None if include_inactive else apolo_sdk.AppState.get_active_states())
        )
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            items = []
            async with sdk.apps.list(
                states=state_filter,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            ) as iterator:
                async for app in iterator:
                    items.append(_app_dict(app))
                    if len(items) > bounded:
                        break
        return {
            "items": items[:bounded],
            "limit": bounded,
            "truncated": len(items) > bounded,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_app(
        app_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get one App and verify it belongs to the resolved context."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            app = await sdk.apps.get(app_id)
            _assert_app_context(app, context)
        return {"app": _app_dict(app), "context": context.as_dict()}

    @mcp.tool(annotations=READ_ONLY)
    async def wait_for_app(
        app_id: str,
        timeout_seconds: float = 120,
        poll_interval_seconds: float = 2,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Wait a bounded time for an App to reach a terminal health state."""
        if not 1 <= timeout_seconds <= MAX_WAIT_SECONDS:
            raise ValueError("timeout_seconds must be between 1 and 600")
        if not 0.25 <= poll_interval_seconds <= 30:
            raise ValueError("poll_interval_seconds must be between 0.25 and 30")
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            started = monotonic()
            while True:
                app = await sdk.apps.get(app_id)
                _assert_app_context(app, context)
                state = str(getattr(app.state, "value", app.state))
                if state in TERMINAL_STATES:
                    return {
                        "app": _app_dict(app),
                        "terminal": True,
                        "timed_out": False,
                        "elapsed_seconds": monotonic() - started,
                        "context": context.as_dict(),
                    }
                remaining = timeout_seconds - (monotonic() - started)
                if remaining <= 0:
                    return {
                        "app": _app_dict(app),
                        "terminal": False,
                        "timed_out": True,
                        "elapsed_seconds": monotonic() - started,
                        "context": context.as_dict(),
                    }
                await asyncio.sleep(min(poll_interval_seconds, remaining))

    @mcp.tool(annotations=READ_ONLY)
    async def get_app_logs(
        app_id: str,
        max_bytes: int = 32768,
        max_lines: int = 1000,
        timeout_seconds: float = 15,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        since: str | None = None,
        timestamps: bool = False,
    ) -> dict[str, Any]:
        """Read bounded UTF-8 logs with timeout and explicit truncation metadata."""
        if not 1 <= max_bytes <= MAX_LOG_BYTES or not 1 <= max_lines <= MAX_LOG_LINES:
            raise ValueError("max_bytes/max_lines exceed Apps log safety bounds")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        since_dt = datetime.fromisoformat(since) if since else None
        buffer = bytearray()
        lines_seen = 0
        truncated = False
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            app = await sdk.apps.get(app_id)
            _assert_app_context(app, context)

            async def collect() -> None:
                nonlocal lines_seen, truncated
                async with sdk.apps.logs(
                    app_id=app_id,
                    cluster_name=context.cluster,
                    org_name=context.org,
                    project_name=context.project,
                    since=since_dt,
                    timestamps=timestamps,
                ) as iterator:
                    async for chunk in iterator:
                        remaining = max_bytes - len(buffer)
                        accepted = chunk[:remaining]
                        buffer.extend(accepted)
                        lines_seen += accepted.count(b"\n")
                        if len(accepted) < len(chunk) or lines_seen >= max_lines:
                            truncated = True
                            break

            try:
                await asyncio.wait_for(collect(), timeout_seconds)
            except asyncio.TimeoutError:
                truncated = True
        text = bytes(buffer).decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            truncated = True
        encoded = "".join(lines).encode()
        if len(encoded) > max_bytes:
            encoded = encoded[-max_bytes:]
            truncated = True
        redacted_bytes = _redact_log(
            encoded.decode("utf-8", errors="replace")
        ).encode()[:max_bytes]
        redacted = redacted_bytes.decode("utf-8", errors="replace")
        return {
            "text": redacted,
            "bytes": len(redacted_bytes),
            "lines": len(lines),
            "truncated": truncated,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_app_events(
        app_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return bounded, credential-redacted App status events."""
        bounded = _bounded(limit)
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            items = []
            async with sdk.apps.get_events(
                app_id=app_id,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            ) as iterator:
                async for event in iterator:
                    items.append(
                        _redact(
                            {
                                "created_at": _iso(event.created_at),
                                "state": event.state,
                                "reason": event.reason,
                                "message": event.message,
                                "resources": [
                                    {
                                        "kind": resource.kind,
                                        "name": resource.name,
                                        "uid": resource.uid,
                                        "health_status": resource.health_status,
                                        "health_message": resource.health_message,
                                    }
                                    for resource in event.resources
                                ],
                            }
                        )
                    )
                    if len(items) > bounded:
                        break
        return {
            "items": items[:bounded],
            "limit": bounded,
            "truncated": len(items) > bounded,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_app_output(
        app_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        """Return bounded, credential-redacted output for one App."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            output = await sdk.apps.get_output(
                app_id=app_id,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            )
        return {
            "output": _bounded_value(output, max_chars),
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_app_input(
        app_id: str,
        revision: int | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        max_chars: int = 20_000,
    ) -> dict[str, Any]:
        """Return bounded App input with likely credential values redacted."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            value = await sdk.apps.get_input(
                app_id=app_id,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
                revision=revision,
            )
        return {
            "input": _bounded_value(value, max_chars),
            "revision": revision,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def list_app_revisions(
        app_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List bounded configuration revisions after verifying App context."""
        bounded = _bounded(limit)
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            app = await sdk.apps.get(app_id)
            _assert_app_context(app, context)
            revisions = await sdk.apps.get_revisions(app_id=app_id)
        selected = revisions[: bounded + 1]
        return {
            "items": [
                {
                    "revision_number": item.revision_number,
                    "creator": item.creator,
                    "comment": item.comment,
                    "created_at": _iso(item.created_at),
                    "end_at": _iso(item.end_at),
                }
                for item in selected[:bounded]
            ],
            "limit": bounded,
            "truncated": len(selected) > bounded,
            "context": context.as_dict(),
        }

    @mcp.tool(annotations=PLAN)
    async def plan_app_install(
        template_name: str,
        template_version: str = "latest",
        input_values: dict[str, Any] | None = None,
        app_name: str | None = None,
        display_name: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Write exact install YAML plus atomic plan.json/PLAN.md for review."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            template = await sdk.apps.get_template(
                name=template_name,
                version=template_version,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            )
            if template is None:
                raise ValueError(
                    f"App template not found: {template_name}@{template_version}"
                )
            input_data = input_values or {}
            ensure_secret_references_only(input_data)
            errors = (
                _validate_schema(input_data, template.input or {})
                if template.input
                else []
            )
            if errors:
                raise ValueError(
                    "Template input validation failed: " + "; ".join(errors[:20])
                )
            payload: dict[str, Any] = {
                "template_name": template.name,
                "template_version": template.version,
                "input": input_data,
            }
            if app_name is not None:
                payload["name"] = app_name
            if display_name is not None:
                payload["display_name"] = display_name
            effects = _recorded_effects(payload)
            plan = create_plan(
                kind="install",
                target=app_name or template.name,
                context=context.as_dict(),
                payload=payload,
                ttl_seconds=ttl_seconds,
                details={
                    "template_name": template.name,
                    "template_version": template.version,
                    "app_id": None,
                    "current_revision": None,
                    **effects,
                    "validation": [
                        "schema version derived from the resolved template",
                        *_service_validation(template.name, template.input or {}),
                    ],
                    "destructive_effects": [
                        "creates a new application and its declared resources"
                    ],
                },
            )
        return _plan_result(plan)

    @mcp.tool(annotations=PLAN)
    async def plan_app_configure(
        app_id: str,
        input_patch: dict[str, Any],
        display_name: str | None = None,
        comment: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Seed exact YAML with SDK get_input, patch it, and write a review plan."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            app = await sdk.apps.get(app_id)
            _assert_app_context(app, context)
            seeded = await sdk.apps.get_input(
                app_id=app_id,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            )
            payload = deep_patch(
                seeded, {"input": deep_patch(seeded.get("input", {}), input_patch)}
            )
            payload["template_name"] = app.template_name
            payload["template_version"] = app.template_version
            if display_name is not None:
                payload["display_name"] = display_name
            ensure_secret_references_only(payload)
            template = await sdk.apps.get_template(
                name=app.template_name,
                version=app.template_version,
                cluster_name=context.cluster,
                org_name=context.org,
                project_name=context.project,
            )
            schema = template.input if template and template.input else {}
            errors = (
                _validate_schema(payload.get("input", {}), schema) if schema else []
            )
            if errors:
                raise ValueError(
                    "Template input validation failed: " + "; ".join(errors[:20])
                )
            current_revision = await _revision(sdk, app_id)
            effects = _recorded_effects(payload)
            plan = create_plan(
                kind="configure",
                target=app_id,
                context=context.as_dict(),
                payload=payload,
                ttl_seconds=ttl_seconds,
                details={
                    "template_name": app.template_name,
                    "template_version": app.template_version,
                    "app_id": app_id,
                    "current_revision": current_revision,
                    "comment": comment,
                    **effects,
                    "endpoints": [
                        *effects["endpoints"],
                        *list(app.endpoints),
                    ][:50],
                    "validation": [
                        "configuration seeded from SDK get_input",
                        "schema version derived from the installed template",
                        *_service_validation(app.template_name, schema),
                    ],
                    "destructive_effects": [
                        "creates a new configuration revision and may replace "
                        "running resources"
                    ],
                },
            )
        return _plan_result(plan)

    @mcp.tool(annotations=PLAN)
    async def plan_app_rollback(
        app_id: str,
        revision_number: int,
        comment: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Write a no-YAML rollback plan bound to current and target revisions."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            app = await sdk.apps.get(app_id)
            _assert_app_context(app, context)
            revisions = await sdk.apps.get_revisions(app_id=app_id)
            numbers = {item.revision_number for item in revisions}
            if revision_number not in numbers:
                raise ValueError(f"Unknown revision {revision_number} for App {app_id}")
            current_revision = max(numbers, default=None)
            plan = create_plan(
                kind="rollback",
                target=app_id,
                context=context.as_dict(),
                payload=None,
                ttl_seconds=ttl_seconds,
                details={
                    "template_name": app.template_name,
                    "template_version": app.template_version,
                    "app_id": app_id,
                    "current_revision": current_revision,
                    "revision_number": revision_number,
                    "comment": comment,
                    "dependencies": [],
                    "resources": [],
                    "endpoints": list(app.endpoints),
                    "validation": ["target revision exists"],
                    "destructive_effects": [
                        "replaces current configuration with revision "
                        f"{revision_number}"
                    ],
                },
            )
        return _plan_result(plan)

    @mcp.tool(annotations=PLAN)
    async def plan_app_uninstall(
        app_id: str,
        force: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Write a no-YAML uninstall plan bound to exact App/current revision."""
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            app = await sdk.apps.get(app_id)
            _assert_app_context(app, context)
            current_revision = await _revision(sdk, app_id)
            plan = create_plan(
                kind="uninstall",
                target=app_id,
                context=context.as_dict(),
                payload=None,
                ttl_seconds=ttl_seconds,
                details={
                    "template_name": app.template_name,
                    "template_version": app.template_version,
                    "app_id": app_id,
                    "current_revision": current_revision,
                    "force": force,
                    "dependencies": [],
                    "resources": [],
                    "endpoints": list(app.endpoints),
                    "validation": ["App identity and current revision recorded"],
                    "destructive_effects": [
                        "uninstalls the App and may delete all resources managed by it"
                    ],
                },
            )
        return _plan_result(plan)

    @mcp.tool(annotations=WRITE)
    async def install_app(
        plan_id: str,
        approved: bool,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Apply one approved, unexpired, unchanged install plan exactly once."""
        if approved is not True:
            raise PermissionError("approved=true is required to apply an Apps plan")
        Policy.load().require_high_risk("install_app")
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            path, plan, payload = claim_for_apply(
                plan_id, kind="install", context=context.as_dict()
            )
            try:
                assert payload is not None
                template = await sdk.apps.get_template(
                    name=plan["template_name"],
                    version=plan["template_version"],
                    cluster_name=context.cluster,
                    org_name=context.org,
                    project_name=context.project,
                )
                if template is None or template.version != plan["template_version"]:
                    raise ValueError("Template version drifted since planning")
                ensure_ledger_writable()
                app = await sdk.apps.install(
                    app_data=payload,
                    cluster_name=context.cluster,
                    org_name=context.org,
                    project_name=context.project,
                )
                record_created_resource(
                    resource_type="app",
                    resource_id=app.id,
                    cluster=context.cluster,
                    org=context.org,
                    project=context.project,
                    operation="install_app",
                )
                result = {"app": _app_dict(app), "context": context.as_dict()}
                record_success(path, plan, result)
                return {**result, "plan_id": plan_id, "plan_status": "applied"}
            except Exception as exc:
                record_failure(path, plan, sanitize_message(exc))
                raise RuntimeError(
                    str(
                        normalize_error(
                            exc,
                            operation="install_app",
                            context=context.as_dict(),
                            resource=plan_id,
                        )
                    )
                ) from None

    @mcp.tool(annotations=WRITE)
    async def configure_app(
        plan_id: str,
        approved: bool,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Apply one approved, unchanged configure plan after revision drift check."""
        if approved is not True:
            raise PermissionError("approved=true is required to apply an Apps plan")
        Policy.load().require_high_risk("configure_app")
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            path, plan, payload = claim_for_apply(
                plan_id, kind="configure", context=context.as_dict()
            )
            try:
                assert payload is not None
                app = await sdk.apps.get(plan["app_id"])
                _assert_app_context(app, context)
                if (
                    app.template_name != plan["template_name"]
                    or app.template_version != plan["template_version"]
                ):
                    raise ValueError(
                        "App template changed since planning; create a new plan"
                    )
                if await _revision(sdk, plan["app_id"]) != plan["current_revision"]:
                    raise ValueError(
                        "App revision changed since planning; create a new plan"
                    )
                configured = await sdk.apps.configure(
                    app_id=plan["app_id"],
                    app_data=payload,
                    comment=plan.get("comment"),
                )
                result = {"app": _app_dict(configured), "context": context.as_dict()}
                record_success(path, plan, result)
                return {**result, "plan_id": plan_id, "plan_status": "applied"}
            except Exception as exc:
                record_failure(path, plan, sanitize_message(exc))
                raise RuntimeError(
                    str(
                        normalize_error(
                            exc,
                            operation="configure_app",
                            context=context.as_dict(),
                            resource=plan["app_id"],
                        )
                    )
                ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def rollback_app(
        plan_id: str,
        approved: bool,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Apply an approved rollback plan; server high-risk policy must allow it."""
        if approved is not True:
            raise PermissionError("approved=true is required to apply an Apps plan")
        Policy.load().require_high_risk("rollback_app")
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            path, plan, _ = claim_for_apply(
                plan_id, kind="rollback", context=context.as_dict()
            )
            try:
                app = await sdk.apps.get(plan["app_id"])
                _assert_app_context(app, context)
                if (
                    app.template_name != plan["template_name"]
                    or app.template_version != plan["template_version"]
                ):
                    raise ValueError(
                        "App template changed since planning; create a new plan"
                    )
                if await _revision(sdk, plan["app_id"]) != plan["current_revision"]:
                    raise ValueError(
                        "App revision changed since planning; create a new plan"
                    )
                rolled_back = await sdk.apps.rollback(
                    app_id=plan["app_id"],
                    revision_number=plan["revision_number"],
                    cluster_name=context.cluster,
                    org_name=context.org,
                    project_name=context.project,
                    comment=plan.get("comment"),
                )
                result = {"app": _app_dict(rolled_back), "context": context.as_dict()}
                record_success(path, plan, result)
                return {**result, "plan_id": plan_id, "plan_status": "applied"}
            except Exception as exc:
                record_failure(path, plan, sanitize_message(exc))
                raise RuntimeError(
                    str(
                        normalize_error(
                            exc,
                            operation="rollback_app",
                            context=context.as_dict(),
                            resource=plan["app_id"],
                        )
                    )
                ) from None

    @mcp.tool(annotations=DESTRUCTIVE)
    async def uninstall_app(
        plan_id: str,
        approved: bool,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Apply an approved uninstall plan; server high-risk policy must allow it."""
        if approved is not True:
            raise PermissionError("approved=true is required to apply an Apps plan")
        Policy.load().require_high_risk("uninstall_app")
        async with client() as sdk:
            context = _context(sdk, cluster, org, project)
            path, plan, _ = claim_for_apply(
                plan_id, kind="uninstall", context=context.as_dict()
            )
            try:
                app = await sdk.apps.get(plan["app_id"])
                _assert_app_context(app, context)
                if (
                    app.template_name != plan["template_name"]
                    or app.template_version != plan["template_version"]
                ):
                    raise ValueError(
                        "App template changed since planning; create a new plan"
                    )
                if await _revision(sdk, plan["app_id"]) != plan["current_revision"]:
                    raise ValueError(
                        "App revision changed since planning; create a new plan"
                    )
                await sdk.apps.uninstall(
                    app_id=plan["app_id"],
                    cluster_name=context.cluster,
                    org_name=context.org,
                    project_name=context.project,
                    force=bool(plan.get("force")),
                )
                result = {
                    "id": plan["app_id"],
                    "status": "uninstalling",
                    "context": context.as_dict(),
                }
                record_success(path, plan, result)
                return {**result, "plan_id": plan_id, "plan_status": "applied"}
            except Exception as exc:
                record_failure(path, plan, sanitize_message(exc))
                raise RuntimeError(
                    str(
                        normalize_error(
                            exc,
                            operation="uninstall_app",
                            context=context.as_dict(),
                            resource=plan["app_id"],
                        )
                    )
                ) from None

    for operation in (
        "list_app_templates",
        "list_app_template_versions",
        "get_app_template",
        "list_apps",
        "get_app",
        "wait_for_app",
        "get_app_logs",
        "get_app_events",
        "get_app_output",
        "get_app_input",
        "list_app_revisions",
        "plan_app_install",
        "plan_app_configure",
        "plan_app_rollback",
        "plan_app_uninstall",
        "install_app",
        "configure_app",
        "rollback_app",
        "uninstall_app",
    ):
        registered = mcp._tool_manager.get_tool(operation)
        if registered is None:  # pragma: no cover - registration invariant
            raise RuntimeError(f"Expected Apps tool was not registered: {operation}")
        registered.fn = _normalize_sdk_tool(operation, registered.fn)
