"""Schema-guided authoring and validation for local Apolo Flow configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextvars import ContextVar, Token
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal, Protocol

import aiohttp
import yaml
from jsonschema import Draft202012Validator
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..policy import MutationEffect, authorize_mutation
from ..security import ensure_secret_references_only
from ..workspace import resolve_new_workspace_file, resolve_workspace_path


FlowConfigType = Literal["live", "batch", "project"]
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
MAX_SCHEMA_BYTES = 1_000_000
MAX_CONFIG_BYTES = 1_000_000
MAX_ERRORS = 20
SCHEMA_TIMEOUT_SECONDS = 15.0
_FLOW_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_FLOW_REPOSITORY = "https://raw.githubusercontent.com/neuro-inc/neuro-flow"
_SCHEMA_FILE = {
    "live": "flow-schema.json",
    "batch": "flow-schema.json",
    "project": "project-schema.json",
}
_ROOT_DEFINITION = {"live": "LiveFlow", "batch": "BatchFlow"}


@dataclass(frozen=True)
class SchemaResource:
    document: dict[str, Any]
    url: str
    flow_version: str
    sha256: str


class FlowSchemaProvider(Protocol):
    async def get(self, config_type: FlowConfigType) -> SchemaResource: ...


def _installed_flow_version() -> str:
    try:
        value = version("apolo-flow")
    except PackageNotFoundError as exc:  # pragma: no cover - required dependency
        raise RuntimeError("apolo-flow is not installed") from exc
    match = re.match(r"^(\d+\.\d+\.\d+)", value)
    if not match:
        raise RuntimeError(f"Cannot derive a release tag from apolo-flow {value!r}")
    return match.group(1)


def flow_schema_url(config_type: FlowConfigType) -> str:
    """Return the immutable release-tag schema URL for the installed Flow version."""
    flow_version = _installed_flow_version()
    filename = _SCHEMA_FILE[config_type]
    return (
        f"{_FLOW_REPOSITORY}/refs/tags/v{flow_version}/"
        f"src/apolo_flow/{filename}"
    )


class RemoteFlowSchemaProvider:
    """Fetch fixed schema paths from the installed Flow version's Git tag."""

    def __init__(self) -> None:
        self._cache: dict[str, SchemaResource] = {}

    async def get(self, config_type: FlowConfigType) -> SchemaResource:
        url = flow_schema_url(config_type)
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        timeout = aiohttp.ClientTimeout(total=SCHEMA_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Flow schema fetch returned HTTP {response.status} for {url}"
                    )
                declared = response.content_length
                if declared is not None and declared > MAX_SCHEMA_BYTES:
                    raise RuntimeError("Flow schema exceeds the maximum allowed size")
                raw = await response.content.read(MAX_SCHEMA_BYTES + 1)
        if len(raw) > MAX_SCHEMA_BYTES:
            raise RuntimeError("Flow schema exceeds the maximum allowed size")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise RuntimeError("Flow schema response is not a JSON object")
        Draft202012Validator.check_schema(document)
        resource = SchemaResource(
            document=document,
            url=url,
            flow_version=_installed_flow_version(),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        self._cache[url] = resource
        return resource


_provider: ContextVar[FlowSchemaProvider] = ContextVar(
    "flow_schema_provider", default=RemoteFlowSchemaProvider()
)


def set_flow_schema_provider(provider: FlowSchemaProvider) -> Token[FlowSchemaProvider]:
    """Override the schema provider in the current context for embedding or tests."""
    return _provider.set(provider)


def reset_flow_schema_provider(token: Token[FlowSchemaProvider]) -> None:
    _provider.reset(token)


def _selected_schema(
    resource: SchemaResource, config_type: FlowConfigType
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    document = resource.document
    definitions = document.get("$defs", {})
    if config_type in _ROOT_DEFINITION:
        root_name = _ROOT_DEFINITION[config_type]
        root = definitions[root_name]
        validation_schema = {"$defs": definitions, **root}
    else:
        root_name = "Project"
        root = {key: value for key, value in document.items() if key != "$defs"}
        validation_schema = document
    return root_name, root, definitions, validation_schema


def _workspace(value: str) -> Path:
    return resolve_workspace_path(value, name="workspace_path", directory=True)


def _config_stem(config_type: FlowConfigType, batch_name: str | None) -> str:
    if config_type == "batch":
        if not batch_name or not _FLOW_NAME.fullmatch(batch_name):
            raise ValueError(
                "batch_name must contain 1-80 letters, numbers, underscores, or "
                "hyphens and start with a letter or number"
            )
        if batch_name in {"live", "project"}:
            raise ValueError("batch_name must not be live or project")
        return batch_name
    if batch_name is not None:
        raise ValueError("batch_name is only valid for batch configuration")
    return config_type


def _existing_config(
    workspace: Path, config_type: FlowConfigType, batch_name: str | None
) -> Path:
    stem = _config_stem(config_type, batch_name)
    directory = workspace / ".apolo"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("workspace_path must contain a real .apolo directory")
    candidates = [directory / f"{stem}{suffix}" for suffix in (".yml", ".yaml")]
    found = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(found) > 1:
        raise ValueError(f"Both {stem}.yml and {stem}.yaml exist; keep exactly one")
    if not found:
        raise FileNotFoundError(f"Flow configuration .apolo/{stem}.yml was not found")
    path = found[0]
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Flow configuration must be a real regular file")
    return path.resolve(strict=True)


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Flow configuration exceeds the maximum allowed size")
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = (
            f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        )
        raise ValueError(f"Flow configuration is invalid YAML{location}") from None
    if not isinstance(payload, dict):
        raise ValueError("Flow configuration must contain a YAML mapping")
    ensure_secret_references_only(payload, "config")
    return payload


def _validation_errors(
    payload: dict[str, Any], validation_schema: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    validator = Draft202012Validator(validation_schema)
    errors = []
    for error in validator.iter_errors(payload):
        errors.append(error)
        if len(errors) > MAX_ERRORS:
            break
    errors.sort(key=lambda item: tuple(str(part) for part in item.absolute_path))
    truncated = len(errors) > MAX_ERRORS
    return [
        {
            "path": [str(part) for part in error.absolute_path],
            "schema_path": [str(part) for part in error.absolute_schema_path],
            "message": error.message[:500],
        }
        for error in errors[:MAX_ERRORS]
    ], truncated


def _metadata(resource: SchemaResource) -> dict[str, str]:
    return {
        "url": resource.url,
        "apolo_flow_version": resource.flow_version,
        "sha256": resource.sha256,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def flow_config_schema(
        config_type: FlowConfigType,
        definition: str | None = None,
    ) -> dict[str, Any]:
        """Explore a bounded root or definition from the version-pinned Flow schema."""
        resource = await _provider.get().get(config_type)
        root_name, root, definitions, _ = _selected_schema(resource, config_type)
        if definition is None:
            selected_name = root_name
            selected = root
        else:
            if definition not in definitions:
                raise ValueError(
                    f"Unknown definition {definition!r}; use available_definitions"
                )
            selected_name = definition
            selected = definitions[definition]
        return {
            "config_type": config_type,
            "definition": selected_name,
            "schema": selected,
            "available_definitions": sorted(definitions),
            "source": _metadata(resource),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def flow_config_validate(
        workspace_path: str,
        config_type: FlowConfigType,
        batch_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate one canonical .apolo YAML file against its pinned Flow schema."""
        workspace = _workspace(workspace_path)
        path = _existing_config(workspace, config_type, batch_name)
        payload = _load_yaml(path)
        resource = await _provider.get().get(config_type)
        _, _, _, validation_schema = _selected_schema(resource, config_type)
        errors, errors_truncated = _validation_errors(payload, validation_schema)
        return {
            "valid": not errors,
            "config_type": config_type,
            "path": str(path),
            "errors": errors,
            "errors_truncated": errors_truncated,
            "source": _metadata(resource),
        }

    @mcp.tool(annotations=WRITE)
    async def flow_config_write(
        workspace_path: str,
        config_type: FlowConfigType,
        config: dict[str, Any],
        batch_name: str | None = None,
    ) -> dict[str, Any]:
        """Validate and create one new canonical .apolo YAML file without overwrite."""
        authorize_mutation(
            operation="flow_config_write", effect=MutationEffect.CREATE
        )
        workspace = _workspace(workspace_path)
        stem = _config_stem(config_type, batch_name)
        ensure_secret_references_only(config, "config")
        resource = await _provider.get().get(config_type)
        _, _, _, validation_schema = _selected_schema(resource, config_type)
        errors, _ = _validation_errors(config, validation_schema)
        if errors:
            first = errors[0]
            dotted = ".".join(first["path"]) or "<root>"
            raise ValueError(
                f"Flow configuration failed schema validation at {dotted}: "
                f"{first['message']}"
            )
        target = workspace / ".apolo" / f"{stem}.yml"
        alternate = target.with_suffix(".yaml")
        if alternate.exists() or alternate.is_symlink():
            raise FileExistsError(
                f"destination must not already exist; {alternate.name} exists"
            )
        path = resolve_new_workspace_file(
            str(target), name="Flow configuration", create_parents=True
        )
        body = yaml.safe_dump(
            config,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        content = f"# yaml-language-server: $schema={resource.url}\n{body}"
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_CONFIG_BYTES:
            raise ValueError("Flow configuration exceeds the maximum allowed size")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            written = _load_yaml(path)
            post_errors, _ = _validation_errors(written, validation_schema)
            if post_errors:
                raise RuntimeError(
                    "Created Flow configuration failed post-write schema validation"
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {
            "created": True,
            "valid": True,
            "config_type": config_type,
            "path": str(path),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "source": _metadata(resource),
        }
