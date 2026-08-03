"""Typed and bounded Apolo job lifecycle tools."""

import asyncio
import re
import shlex
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Any, Literal

import apolo_sdk
from async_timeout import timeout
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .._client import client
from ..context import ApoloContext, resolve_context
from ..errors import normalize_error
from ..ledger import (
    ensure_ledger_writable,
    record_created_resource,
    record_resource_action,
)
from ..policy import MutationEffect, authorize_mutation
from ..security import redact_log_credentials


READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)

MAX_LIST = 100
MAX_LOG_BYTES = 1_000_000
MAX_LOG_LINES = 10_000
MAX_WAIT_SECONDS = 86_400.0
MAX_STREAM_SECONDS = 300.0
MAX_TELEMETRY_SAMPLES = 100
MAX_EXEC_SECONDS = 3_600.0
MAX_EXEC_OUTPUT_BYTES = 1_000_000
MAX_EXEC_ARGUMENTS = 256
MAX_EXEC_COMMAND_CHARS = 32_768
MAX_ACTIVE_PORT_FORWARDS = 16
TERMINAL = {
    apolo_sdk.JobStatus.SUCCEEDED,
    apolo_sdk.JobStatus.FAILED,
    apolo_sdk.JobStatus.CANCELLED,
}
_SENSITIVE_ENV_NAME = re.compile(
    r"(?i)(authorization|cookie|token|password|secret|api[-_]?key)"
)


@dataclass
class _PortForward:
    stack: AsyncExitStack
    forwarding_id: str
    job_id: str
    local_port: int
    remote_port: int
    username: str
    cluster: str
    org: str
    project: str

    def metadata(self) -> dict[str, Any]:
        return {
            "forwarding_id": self.forwarding_id,
            "job_id": self.job_id,
            "local_host": "localhost",
            "local_port": self.local_port,
            "remote_port": self.remote_port,
            "context": {
                "username": self.username,
                "cluster": self.cluster,
                "org": self.org,
                "project": self.project,
            },
        }


_port_forwards: dict[str, _PortForward] = {}
_port_forwards_lock = asyncio.Lock()


async def close_all_port_forwards() -> None:
    """Close every process-owned local listener during MCP shutdown."""
    async with _port_forwards_lock:
        entries = list(_port_forwards.values())
        _port_forwards.clear()
    await asyncio.gather(
        *(entry.stack.aclose() for entry in entries), return_exceptions=True
    )


class StorageVolumeInput(BaseModel):
    storage: str = Field(description="Storage URI/reference, never a credential")
    container_path: str
    read_only: bool = False


class SecretFileInput(BaseModel):
    secret: str = Field(description="secret: reference only; never a secret value")
    container_path: str


class DiskVolumeInput(BaseModel):
    disk: str = Field(description="Disk URI/reference")
    container_path: str
    read_only: bool = False


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _job(job: Any) -> dict[str, Any]:
    history = job.history
    return {
        "id": job.id,
        "name": job.name,
        "owner": getattr(job, "owner", None),
        "status": job.status.value,
        "image": str(job.container.image),
        "entrypoint": getattr(job.container, "entrypoint", None),
        "command": job.container.command,
        "workdir": getattr(job.container, "working_dir", None),
        "preset": job.preset_name,
        "scheduler_enabled": getattr(job, "scheduler_enabled", None),
        "restart_policy": str(getattr(job, "restart_policy", "never")),
        "life_span_seconds": getattr(job, "life_span", None),
        "schedule_timeout_seconds": getattr(job, "schedule_timeout", None),
        "energy_schedule": getattr(job, "energy_schedule_name", None),
        "priority": getattr(getattr(job, "priority", None), "name", None),
        "created_at": _iso(history.created_at),
        "started_at": _iso(history.started_at),
        "finished_at": _iso(history.finished_at),
        "exit_code": history.exit_code,
        "reason": history.reason,
        "description": job.description,
        "tags": list(job.tags),
    }


def _positive(value: float, name: str, maximum: float) -> float:
    if not 0 < value <= maximum:
        raise ValueError(f"{name} must be greater than 0 and at most {maximum:g}")
    return value


def _secret_ref(value: str) -> str:
    if not value.startswith(("secret:", "secret://")):
        raise ValueError("secret inputs must be secret: references, never values")
    if any(char.isspace() for char in value) or "@" in value:
        raise ValueError("secret reference contains prohibited credential syntax")
    return value


def _resource_ref(value: str, kind: str) -> str:
    if not value.strip() or "@" in value:
        raise ValueError(f"{kind} reference is empty or contains credentials")
    return value


def _platform_ref(value: str, kind: str, resolved: ApoloContext) -> str:
    """Qualify short platform references and reject cross-context full URIs."""
    full_prefix = f"{kind}://"
    short_prefix = f"{kind}:"
    if value.startswith(full_prefix):
        remainder = value[len(full_prefix) :]
        authority, separator, path = remainder.partition("/")
        parts = path.split("/") if separator else []
        if (
            authority != resolved.cluster
            or len(parts) < 3
            or parts[0] != resolved.org
            or parts[1] != resolved.project
        ):
            raise ValueError(f"{kind} URI does not belong to the resolved context")
        return value
    if value.startswith(short_prefix):
        value = value[len(short_prefix) :]
    path = _resource_ref(value, kind).lstrip("/")
    return f"{kind}://{resolved.cluster}/{resolved.org}/{resolved.project}/{path}"


def _image_ref(value: str, resolved: ApoloContext) -> str:
    """Resolve platform image references while preserving external Docker names."""
    if value.startswith(("image:", "image://")):
        return _platform_ref(value, "image", resolved)
    if re.search(r"://[^/]+@", value):
        raise ValueError("external image contains prohibited credential syntax")
    return value


def _redact_logs(value: str) -> str:
    return redact_log_credentials(value)


def _exec_command(executable: str, arguments: list[str] | None) -> str:
    parts = [executable, *(arguments or [])]
    if len(parts) > MAX_EXEC_ARGUMENTS + 1:
        raise ValueError(f"arguments must contain at most {MAX_EXEC_ARGUMENTS} items")
    for index, value in enumerate(parts):
        name = "executable" if index == 0 else f"arguments[{index - 1}]"
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{name} must not contain control characters")
        if redact_log_credentials(value) != value:
            raise ValueError(
                f"{name} appears to contain credential material; use a mounted "
                "secret or secret environment reference"
            )
    command = shlex.join(parts)
    if len(command) > MAX_EXEC_COMMAND_CHARS:
        raise ValueError(
            f"encoded command must contain at most {MAX_EXEC_COMMAND_CHARS} characters"
        )
    return command


async def _collect_exec_output(
    stream: Any,
    max_output_bytes: int,
    stdout: bytearray,
    stderr: bytearray,
) -> tuple[int | None, bool]:
    exit_code: int | None = None
    truncated = False
    accepted = 0
    while True:
        try:
            message = await stream.read_out()
        except apolo_sdk.StdStreamError as exc:
            exit_code = exc.exit_code
            break
        if message is None:
            break
        target = (
            stdout if message.fileno == 1 else stderr if message.fileno == 2 else None
        )
        if target is None:
            continue
        remaining = max_output_bytes - accepted
        if remaining > 0:
            chunk = message.data[:remaining]
            target.extend(chunk)
            accepted += len(chunk)
        if len(message.data) > remaining:
            truncated = True
    return exit_code, truncated


def _exec_text(
    stdout: bytearray, stderr: bytearray, max_output_bytes: int
) -> tuple[str, str, int, bool]:
    redacted_stdout = redact_log_credentials(
        bytes(stdout).decode("utf-8", errors="replace")
    ).encode()
    redacted_stderr = redact_log_credentials(
        bytes(stderr).decode("utf-8", errors="replace")
    ).encode()
    bounded_stdout = redacted_stdout[:max_output_bytes]
    remaining = max_output_bytes - len(bounded_stdout)
    bounded_stderr = redacted_stderr[:remaining]
    truncated = len(redacted_stdout) + len(redacted_stderr) > max_output_bytes
    return (
        bounded_stdout.decode("utf-8", errors="ignore"),
        bounded_stderr.decode("utf-8", errors="ignore"),
        len(bounded_stdout) + len(bounded_stderr),
        truncated,
    )


def _safe_env(value: dict[str, str] | None) -> dict[str, str]:
    result = value or {}
    if any(_SENSITIVE_ENV_NAME.search(name) for name in result):
        raise ValueError(
            "sensitive environment names must use secret_env with secret references"
        )
    return result


def _ensure_job_context(job: Any, resolved: ApoloContext) -> None:
    actual = (
        getattr(job, "cluster_name", resolved.cluster),
        getattr(job, "org_name", resolved.org),
        getattr(job, "project_name", resolved.project),
    )
    if actual != (resolved.cluster, resolved.org, resolved.project):
        raise ValueError("job does not belong to the resolved context")


def _parse_time(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=WRITE)
    async def run_job(
        image: str,
        preset: str,
        entrypoint: str | None = None,
        command: str | None = None,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        storage_volumes: list[StorageVolumeInput] | None = None,
        secret_env: dict[str, str] | None = None,
        secret_files: list[SecretFileInput] | None = None,
        disk_volumes: list[DiskVolumeInput] | None = None,
        http_port: int | None = None,
        http_auth: bool = True,
        life_span_seconds: float | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        priority: Literal["low", "normal", "high"] | None = None,
        scheduler_enabled: bool | None = None,
        wait_for_jobs_quota: bool = False,
        schedule_timeout_seconds: float | None = None,
        restart_policy: Literal["never", "on-failure", "always"] = "never",
        energy_schedule: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Start a policy-authorized job; direct secret values are forbidden.

        Mount item schemas, included here for MCP clients that do not expand JSON
        Schema references:

        - storage_volumes: {storage, container_path, read_only=false}
        - disk_volumes: {disk, container_path, read_only=false}
        - secret_files: {secret, container_path}

        storage and disk accept short references or exact same-context URIs. secret
        must be a secret: reference. secret_env maps an environment variable name to
        a secret: reference; env accepts non-sensitive literal values only.
        """
        authorize_mutation(operation="run_job", effect=MutationEffect.CREATE)
        if not image.strip() or not preset.strip():
            raise ValueError("image and preset must not be empty")
        if http_port is not None and not 1 <= http_port <= 65535:
            raise ValueError("http_port must be between 1 and 65535")
        if life_span_seconds is not None:
            _positive(life_span_seconds, "life_span_seconds", 365 * 86_400)
        if schedule_timeout_seconds is not None:
            _positive(schedule_timeout_seconds, "schedule_timeout_seconds", 86_400)
        safe_env = _safe_env(env)
        secret_env = secret_env or {}
        for value in secret_env.values():
            _secret_ref(value)
        for secret_file in secret_files or []:
            _secret_ref(secret_file.secret)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                preset_config = sdk.config.clusters[resolved.cluster].presets.get(
                    preset
                )
                if preset_config is None:
                    raise ValueError(
                        f"Preset {preset!r} is unavailable in {resolved.cluster!r}"
                    )
                if (
                    scheduler_enabled is not None
                    and preset_config.scheduler_enabled != scheduler_enabled
                ):
                    raise ValueError(
                        "scheduler_enabled does not match the selected preset; "
                        "SDK preset starts cannot override scheduler enablement"
                    )
                volumes = [
                    apolo_sdk.Volume(
                        sdk.parse.str_to_uri(
                            _platform_ref(item.storage, "storage", resolved)
                        ),
                        item.container_path,
                        item.read_only,
                    )
                    for item in storage_volumes or []
                ]
                secrets = [
                    apolo_sdk.SecretFile(
                        sdk.parse.str_to_uri(
                            _platform_ref(item.secret, "secret", resolved)
                        ),
                        item.container_path,
                    )
                    for item in secret_files or []
                ]
                disks = [
                    apolo_sdk.DiskVolume(
                        sdk.parse.str_to_uri(
                            _platform_ref(item.disk, "disk", resolved)
                        ),
                        item.container_path,
                        item.read_only,
                    )
                    for item in disk_volumes or []
                ]
                ensure_ledger_writable()
                started = await sdk.jobs.start(
                    image=sdk.parse.remote_image(
                        _image_ref(image, resolved), cluster_name=resolved.cluster
                    ),
                    preset_name=preset,
                    cluster_name=resolved.cluster,
                    org_name=resolved.org,
                    project_name=resolved.project,
                    entrypoint=entrypoint,
                    command=command,
                    working_dir=workdir,
                    env=safe_env,
                    volumes=volumes,
                    secret_env={
                        key: sdk.parse.str_to_uri(
                            _platform_ref(value, "secret", resolved)
                        )
                        for key, value in secret_env.items()
                    },
                    secret_files=secrets,
                    disk_volumes=disks,
                    http=(
                        apolo_sdk.HTTPPort(http_port, http_auth)
                        if http_port is not None
                        else None
                    ),
                    life_span=life_span_seconds,
                    name=name,
                    tags=tags or [],
                    description=description,
                    priority=(
                        apolo_sdk.JobPriority[priority.upper()] if priority else None
                    ),
                    wait_for_jobs_quota=wait_for_jobs_quota,
                    schedule_timeout=schedule_timeout_seconds,
                    restart_policy=apolo_sdk.JobRestartPolicy(restart_policy),
                    energy_schedule_name=energy_schedule,
                )
                record_created_resource(
                    resource_type="job",
                    resource_id=started.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation="run_job",
                )
                return {"job": _job(started), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="run_job",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def list_jobs(
        statuses: list[str] | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
        owners: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List jobs using context and bounded status/name/tag/owner/time filters."""
        if not 1 <= limit <= MAX_LIST:
            raise ValueError(f"limit must be between 1 and {MAX_LIST}")
        parsed_since = _parse_time(since, "since")
        parsed_until = _parse_time(until, "until")
        if parsed_since and parsed_until and parsed_since > parsed_until:
            raise ValueError("since must not be later than until")
        status_filter = (
            {apolo_sdk.JobStatus(value) for value in statuses}
            if statuses
            else apolo_sdk.JobStatus.active_items()
        )
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                items = []
                async for item in sdk.jobs.list(
                    statuses=status_filter,
                    name=name or "",
                    tags=tags or [],
                    owners=owners or [],
                    since=parsed_since,
                    until=parsed_until,
                    limit=limit + 1,
                    cluster_name=resolved.cluster,
                    org_names=[resolved.org],
                    project_names=[resolved.project],
                ):
                    items.append(_job(item))
                    if len(items) > limit:
                        break
                truncated = len(items) > limit
                return {
                    "items": items[:limit],
                    "limit": limit,
                    "truncated": truncated,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_jobs",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_job(
        job_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Get one job and its resolved context."""
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                item = await sdk.jobs.status(job_id)
                _ensure_job_context(item, resolved)
                return {"job": _job(item), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_job",
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def exec_job(
        job_id: str,
        executable: str,
        arguments: list[str] | None = None,
        timeout_seconds: float = 60,
        max_output_bytes: int = 100_000,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Execute one non-interactive command in an owned running job.

        The executable and argument list are shell-quoted separately; no stdin or TTY
        is exposed. Output is duration/byte bounded and credential-redacted. Use
        mounted secrets or secret environment references instead of command arguments
        for credentials.
        """
        command = _exec_command(executable, arguments)
        _positive(timeout_seconds, "timeout_seconds", MAX_EXEC_SECONDS)
        if not 1 <= max_output_bytes <= MAX_EXEC_OUTPUT_BYTES:
            raise ValueError(
                f"max_output_bytes must be between 1 and {MAX_EXEC_OUTPUT_BYTES}"
            )
        resolved: ApoloContext | None = None
        stdout = bytearray()
        stderr = bytearray()
        exit_code: int | None = None
        truncated = False
        timed_out = False
        started = False
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                status = await sdk.jobs.status(job_id)
                _ensure_job_context(status, resolved)
                if status.status is not apolo_sdk.JobStatus.RUNNING:
                    raise ValueError("exec_job requires a running job")
                authorize_mutation(
                    operation="exec_job",
                    effect=MutationEffect.UPDATE,
                    resource_type="job",
                    resource_id=status.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                try:
                    async with timeout(timeout_seconds):
                        async with sdk.jobs.exec(
                            job_id,
                            command,
                            tty=False,
                            stdin=False,
                            stdout=True,
                            stderr=True,
                            cluster_name=resolved.cluster,
                        ) as stream:
                            started = True
                            exit_code, truncated = await _collect_exec_output(
                                stream,
                                max_output_bytes,
                                stdout,
                                stderr,
                            )
                except TimeoutError:
                    timed_out = True
                    truncated = True
                if started:
                    record_resource_action(
                        resource_type="job",
                        resource_id=status.id,
                        username=resolved.username,
                        cluster=resolved.cluster,
                        org=resolved.org,
                        project=resolved.project,
                        operation="exec_job",
                        action="updated",
                    )
                safe_stdout, safe_stderr, output_bytes, redaction_truncated = (
                    _exec_text(stdout, stderr, max_output_bytes)
                )
                return {
                    "id": status.id,
                    "exit_code": exit_code,
                    "stdout": safe_stdout,
                    "stderr": safe_stderr,
                    "output_bytes": output_bytes,
                    "truncated": truncated or redaction_truncated,
                    "timed_out": timed_out,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="exec_job",
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def start_job_port_forward(
        job_id: str,
        local_port: int,
        remote_port: int,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Start a loopback-only background forward to one running owned job.

        Forwarded bytes never enter MCP results. The listener remains active until
        stop_job_port_forward is called or the MCP server exits.
        """
        if not 1 <= local_port <= 65535:
            raise ValueError("local_port must be between 1 and 65535")
        if not 1 <= remote_port <= 65535:
            raise ValueError("remote_port must be between 1 and 65535")
        resolved: ApoloContext | None = None
        stack = AsyncExitStack()
        registered = False
        try:
            sdk = await stack.enter_async_context(client())
            resolved = resolve_context(
                sdk.config, cluster=cluster, org=org, project=project
            )
            status = await sdk.jobs.status(job_id)
            _ensure_job_context(status, resolved)
            if status.status is not apolo_sdk.JobStatus.RUNNING:
                raise ValueError("port forwarding requires a running job")
            authorize_mutation(
                operation="start_job_port_forward",
                effect=MutationEffect.UPDATE,
                resource_type="job",
                resource_id=status.id,
                username=resolved.username,
                cluster=resolved.cluster,
                org=resolved.org,
                project=resolved.project,
            )
            async with _port_forwards_lock:
                if len(_port_forwards) >= MAX_ACTIVE_PORT_FORWARDS:
                    raise ValueError(
                        "maximum number of active MCP port forwards has been reached"
                    )
                if any(
                    entry.local_port == local_port for entry in _port_forwards.values()
                ):
                    raise ValueError("local_port is already managed by this MCP server")
                await stack.enter_async_context(
                    sdk.jobs.port_forward(
                        status.id,
                        local_port,
                        remote_port,
                        cluster_name=resolved.cluster,
                    )
                )
                forwarding_id = str(uuid.uuid4())
                entry = _PortForward(
                    stack=stack,
                    forwarding_id=forwarding_id,
                    job_id=status.id,
                    local_port=local_port,
                    remote_port=remote_port,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                _port_forwards[forwarding_id] = entry
                registered = True
            return {"forward": entry.metadata(), "active": True}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="start_job_port_forward",
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None
        finally:
            if not registered:
                await stack.aclose()

    @mcp.tool(annotations=READ_ONLY)
    async def list_job_port_forwards(
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """List active forwards owned by this MCP process and exact context."""
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
            async with _port_forwards_lock:
                items = [
                    entry.metadata()
                    for entry in _port_forwards.values()
                    if (
                        entry.username,
                        entry.cluster,
                        entry.org,
                        entry.project,
                    )
                    == (
                        resolved.username,
                        resolved.cluster,
                        resolved.org,
                        resolved.project,
                    )
                ]
            return {"items": items, "count": len(items), "context": resolved.as_dict()}
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="list_job_port_forwards",
                context=resolved.as_dict() if resolved else None,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def stop_job_port_forward(
        forwarding_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Stop one exact background forward owned by this MCP process."""
        try:
            if str(uuid.UUID(forwarding_id)) != forwarding_id:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise ValueError("forwarding_id must be one exact UUID") from exc
        resolved: ApoloContext | None = None
        entry: _PortForward | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                async with _port_forwards_lock:
                    entry = _port_forwards.get(forwarding_id)
                if entry is None:
                    raise ValueError("forwarding_id is not active in this MCP process")
                if (
                    entry.username,
                    entry.cluster,
                    entry.org,
                    entry.project,
                ) != (
                    resolved.username,
                    resolved.cluster,
                    resolved.org,
                    resolved.project,
                ):
                    raise ValueError(
                        "port forward does not belong to the exact context"
                    )
                authorize_mutation(
                    operation="stop_job_port_forward",
                    effect=MutationEffect.UPDATE,
                    resource_type="job",
                    resource_id=entry.job_id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                async with _port_forwards_lock:
                    removed = _port_forwards.pop(forwarding_id, None)
                if removed is None:
                    raise ValueError("forwarding_id is no longer active")
            await removed.stack.aclose()
            return {
                "status": "stopped",
                "forwarding_id": forwarding_id,
                "context": resolved.as_dict(),
            }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="stop_job_port_forward",
                context=resolved.as_dict() if resolved else None,
                resource=forwarding_id,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def wait_for_job(
        job_id: str,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 2,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Poll until terminal state, always bounded by timeout_seconds."""
        _positive(timeout_seconds, "timeout_seconds", MAX_WAIT_SECONDS)
        _positive(poll_interval_seconds, "poll_interval_seconds", 60)
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout_seconds
                while True:
                    item = await sdk.jobs.status(job_id)
                    _ensure_job_context(item, resolved)
                    if item.status in TERMINAL:
                        return {
                            "job": _job(item),
                            "terminal": True,
                            "timed_out": False,
                            "context": resolved.as_dict(),
                        }
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return {
                            "job": _job(item),
                            "terminal": False,
                            "timed_out": True,
                            "context": resolved.as_dict(),
                        }
                    await asyncio.sleep(min(poll_interval_seconds, remaining))
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="wait_for_job",
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_job_logs(
        job_id: str,
        timeout_seconds: float = 30,
        max_bytes: int = 32_768,
        max_lines: int = 1_000,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Read a bounded log prefix with explicit timeout and truncation metadata."""
        _positive(timeout_seconds, "timeout_seconds", MAX_STREAM_SECONDS)
        if not 1 <= max_bytes <= MAX_LOG_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_LOG_BYTES}")
        if not 1 <= max_lines <= MAX_LOG_LINES:
            raise ValueError(f"max_lines must be between 1 and {MAX_LOG_LINES}")
        resolved: ApoloContext | None = None
        chunks: list[bytes] = []
        size = 0
        truncated = False
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                status = await sdk.jobs.status(job_id)
                _ensure_job_context(status, resolved)
                try:
                    async with timeout(timeout_seconds):
                        async for chunk in sdk.jobs.monitor(
                            job_id, cluster_name=resolved.cluster
                        ):
                            remaining = max_bytes - size
                            chunks.append(chunk[:remaining])
                            size += min(len(chunk), remaining)
                            if len(chunk) > remaining or size >= max_bytes:
                                truncated = True
                                break
                except TimeoutError:
                    truncated = True
                text = b"".join(chunks).decode("utf-8", errors="replace")
                lines = text.splitlines(keepends=True)
                if len(lines) > max_lines:
                    text = "".join(lines[:max_lines])
                    truncated = True
                text = _redact_logs(text)
                encoded = text.encode("utf-8")
                if len(encoded) > max_bytes:
                    text = encoded[:max_bytes].decode("utf-8", errors="ignore")
                    truncated = True
                return {
                    "id": job_id,
                    "logs": text,
                    "bytes": len(text.encode("utf-8")),
                    "lines": min(len(lines), max_lines),
                    "truncated": truncated,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_job_logs",
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None

    @mcp.tool(annotations=READ_ONLY)
    async def get_job_telemetry(
        job_id: str,
        timeout_seconds: float = 10,
        max_samples: int = 10,
        include_raw: bool = False,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Collect a bounded telemetry summary and optionally bounded raw samples."""
        _positive(timeout_seconds, "timeout_seconds", MAX_STREAM_SECONDS)
        if not 1 <= max_samples <= MAX_TELEMETRY_SAMPLES:
            raise ValueError(
                f"max_samples must be between 1 and {MAX_TELEMETRY_SAMPLES}"
            )
        resolved: ApoloContext | None = None
        samples: list[Any] = []
        timed_out = False
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                status = await sdk.jobs.status(job_id)
                _ensure_job_context(status, resolved)
                try:
                    async with timeout(timeout_seconds):
                        async for sample in sdk.jobs.top(
                            job_id, cluster_name=resolved.cluster
                        ):
                            samples.append(sample)
                            if len(samples) >= max_samples:
                                break
                except TimeoutError:
                    timed_out = True
                raw = [
                    {
                        "timestamp": item.timestamp,
                        "cpu": item.cpu,
                        "memory_bytes": item.memory_bytes,
                        "gpu_duty_cycle": item.gpu_duty_cycle,
                        "gpu_memory_bytes": item.gpu_memory_bytes,
                    }
                    for item in samples
                ]
                summary = {
                    "sample_count": len(raw),
                    "cpu": _metric(raw, "cpu"),
                    "memory_bytes": _metric(raw, "memory_bytes"),
                    "gpu_duty_cycle": _metric(raw, "gpu_duty_cycle"),
                    "gpu_memory_bytes": _metric(raw, "gpu_memory_bytes"),
                }
                return {
                    "id": job_id,
                    "summary": summary,
                    "raw": raw if include_raw else None,
                    "timed_out": timed_out,
                    "truncated": len(samples) == max_samples,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation="get_job_telemetry",
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None

    async def mutate(
        operation: str,
        job_id: str,
        action: Any,
        cluster: str | None,
        org: str | None,
        project: str | None,
        target_resource: Any = None,
    ) -> dict[str, Any]:
        resolved: ApoloContext | None = None
        try:
            async with client() as sdk:
                resolved = resolve_context(
                    sdk.config, cluster=cluster, org=org, project=project
                )
                status = await sdk.jobs.status(job_id)
                _ensure_job_context(status, resolved)
                authorize_mutation(
                    operation=operation,
                    effect=MutationEffect.UPDATE,
                    resource_type="job",
                    resource_id=status.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                )
                extra = target_resource(sdk, resolved) if target_resource else None
                extra_exists = False
                if extra is not None:
                    resource_type, resource_id, sdk_resource = extra
                    if resource_type != "image":
                        raise ValueError("unsupported additional mutation resource")
                    try:
                        await sdk.images.digest(sdk_resource)
                    except apolo_sdk.ResourceNotFound:
                        pass
                    else:
                        extra_exists = True
                    authorize_mutation(
                        operation=operation,
                        effect=(
                            MutationEffect.UPDATE
                            if extra_exists
                            else MutationEffect.CREATE
                        ),
                        resource_type=resource_type,
                        resource_id=resource_id,
                        username=resolved.username,
                        cluster=resolved.cluster,
                        org=resolved.org,
                        project=resolved.project,
                    )
                await action(sdk, resolved)
                record_resource_action(
                    resource_type="job",
                    resource_id=status.id,
                    username=resolved.username,
                    cluster=resolved.cluster,
                    org=resolved.org,
                    project=resolved.project,
                    operation=operation,
                    action="updated",
                )
                if extra is not None:
                    resource_type, resource_id, _ = extra
                    record_resource_action(
                        resource_type=resource_type,
                        resource_id=resource_id,
                        username=resolved.username,
                        cluster=resolved.cluster,
                        org=resolved.org,
                        project=resolved.project,
                        operation=operation,
                        action="updated" if extra_exists else "created",
                    )
                return {
                    "id": job_id,
                    "status": operation,
                    "context": resolved.as_dict(),
                }
        except Exception as exc:
            raise normalize_error(
                exc,
                operation=operation,
                context=resolved.as_dict() if resolved else None,
                resource=job_id,
            ) from None

    @mcp.tool(annotations=WRITE)
    async def bump_job_life_span(
        job_id: str,
        additional_seconds: float,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Extend a job lifespan under full or owned managed policy."""
        _positive(additional_seconds, "additional_seconds", 365 * 86_400)
        return await mutate(
            "bump_job_life_span",
            job_id,
            lambda sdk, _: sdk.jobs.bump_life_span(job_id, additional_seconds),
            cluster,
            org,
            project,
        )

    @mcp.tool(annotations=WRITE)
    async def send_job_signal(
        job_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Send the SDK's graceful signal under full or owned managed policy."""
        return await mutate(
            "send_job_signal",
            job_id,
            lambda sdk, ctx: sdk.jobs.send_signal(job_id, cluster_name=ctx.cluster),
            cluster,
            org,
            project,
        )

    @mcp.tool(annotations=WRITE)
    async def save_job_image(
        job_id: str,
        image: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Save an owned job filesystem to a policy-authorized image target."""
        if not image.strip():
            raise ValueError("image must not be empty")
        return await mutate(
            "save_job_image",
            job_id,
            lambda sdk, ctx: sdk.jobs.save(
                job_id,
                sdk.parse.remote_image(
                    _image_ref(image, ctx), cluster_name=ctx.cluster
                ),
                cluster_name=ctx.cluster,
            ),
            cluster,
            org,
            project,
            lambda sdk, ctx: (
                "image",
                str(
                    sdk.parse.remote_image(
                        _image_ref(image, ctx), cluster_name=ctx.cluster
                    )
                ),
                sdk.parse.remote_image(
                    _image_ref(image, ctx), cluster_name=ctx.cluster
                ),
            ),
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def kill_job(
        job_id: str,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Kill a job under full or owned managed policy."""
        return await mutate(
            "kill_job",
            job_id,
            lambda sdk, _: sdk.jobs.kill(job_id),
            cluster,
            org,
            project,
        )


def _metric(samples: list[dict[str, Any]], key: str) -> dict[str, float] | None:
    values = [float(sample[key]) for sample in samples if sample[key] is not None]
    if not values:
        return None
    return {"min": min(values), "max": max(values), "mean": fmean(values)}
