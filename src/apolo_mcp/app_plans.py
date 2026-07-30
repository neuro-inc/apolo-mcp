"""Durable, checksum-bound plans for Apolo Apps writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from .security import ensure_secret_references_only
from .workspace import ensure_path_beneath


PLAN_ROOT_ENV = "APOLO_MCP_PLAN_ROOT"
DEFAULT_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600
_SAFE_TARGET = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def plan_root() -> Path:
    configured = os.environ.get(PLAN_ROOT_ENV, "plans")
    requested = Path(configured).expanduser()
    if requested.is_symlink():
        raise ValueError("The Apps plan root may not be a symbolic link")
    root = requested.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("The Apps plan root must be a real directory")
    return root


def _safe_target(value: str) -> str:
    safe = _SAFE_TARGET.sub("-", value).strip(".-")
    if not safe or safe in {".", ".."}:
        raise ValueError("Plan target must contain a letter or number")
    return safe[:80]


def _atomic_write(path: Path, content: str) -> None:
    root = plan_root()
    parent = path.parent.resolve()
    ensure_path_beneath(parent, root=root, name="Apps plan path")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def dump_yaml(payload: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(payload), sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def load_yaml_exact(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError("The reviewed inputs YAML must contain a mapping")
    return value


def deep_patch(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            result[key] = deep_patch(existing, value)
        else:
            result[key] = value
    return result


def _markdown(plan: Mapping[str, Any]) -> str:
    context = plan["context"]
    lines = [
        f"# Apps plan {plan['id']}",
        "",
        f"- Kind: `{plan['kind']}`",
        f"- Target: `{plan['target']}`",
        f"- Context: `{context['cluster']}/{context['org']}/{context['project']}`",
        "- Template: `"
        f"{plan.get('template_name') or '-'}@"
        f"{plan.get('template_version') or '-'}`",
        f"- App ID: `{plan.get('app_id') or '-'}`",
        f"- Current revision: `{plan.get('current_revision')}`",
        f"- Expires: `{plan['expires_at']}`",
    ]
    if plan.get("inputs_path"):
        lines.extend(
            [
                f"- Inputs: `{plan['inputs_path']}`",
                f"- SHA-256: `{plan['inputs_sha256']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Validation",
            "",
            *[f"- {item}" for item in plan.get("validation", [])],
            "",
            "## Destructive effects",
            "",
            *[f"- {item}" for item in plan.get("destructive_effects", [])],
        ]
    )
    return "\n".join(lines) + "\n"


def create_plan(
    *,
    kind: str,
    target: str,
    context: Mapping[str, str],
    payload: Mapping[str, Any] | None,
    ttl_seconds: int,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    if not 60 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError("ttl_seconds must be between 60 and 3600")
    now = utc_now()
    plan_id = str(uuid.uuid4())
    timestamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
    directory = plan_root() / "apps" / _safe_target(target) / timestamp
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs_path: Path | None = None
    inputs_sha256: str | None = None
    stored_payload: dict[str, Any] | None = None
    if payload is not None:
        stored_payload = dict(payload)
        ensure_secret_references_only(stored_payload)
        yaml_text = dump_yaml(stored_payload)
        inputs_path = directory / "inputs.yaml"
        _atomic_write(inputs_path, yaml_text)
        inputs_sha256 = sha256_bytes(inputs_path.read_bytes())
    plan: dict[str, Any] = {
        "id": plan_id,
        "kind": kind,
        "status": "planned",
        "single_use": True,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "target": target,
        "context": dict(context),
        "plan_path": str(directory / "PLAN.md"),
        "inputs_path": str(inputs_path) if inputs_path else None,
        "inputs_sha256": inputs_sha256,
        "payload": stored_payload,
        **dict(details),
    }
    _atomic_write(directory / "plan.json", json.dumps(plan, indent=2) + "\n")
    _atomic_write(directory / "PLAN.md", _markdown(plan))
    return plan


def find_plan(plan_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        uuid.UUID(plan_id)
    except ValueError as exc:
        raise ValueError("Invalid Apps plan ID") from exc
    root = plan_root()
    matches = list(root.glob("apps/*/*/plan.json"))
    for path in matches:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("id") == plan_id:
            resolved = path.resolve()
            ensure_path_beneath(resolved, root=root, name="Apps plan path")
            return path, value
    raise ValueError(f"Unknown Apps plan ID: {plan_id}")


def validate_for_apply(
    plan_id: str,
    plan_path: str,
    *,
    kind: str,
    context: Mapping[str, str],
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    path, plan = find_plan(plan_id)
    root = plan_root()
    reviewed_path = Path(plan_path).resolve()
    ensure_path_beneath(reviewed_path, root=root, name="Apps reviewed plan path")
    expected_path = (path.parent / "PLAN.md").resolve()
    if reviewed_path != expected_path or plan.get("plan_path") != str(expected_path):
        raise ValueError("Reviewed PLAN.md path does not match this Apps plan")
    if not reviewed_path.is_file():
        raise ValueError("Reviewed PLAN.md file no longer exists")
    if plan.get("kind") != kind:
        raise ValueError(f"Plan {plan_id} is for {plan.get('kind')}, not {kind}")
    if plan.get("status") != "planned":
        raise ValueError("This single-use plan has already been consumed")
    if datetime.fromisoformat(plan["expires_at"]) <= utc_now():
        raise ValueError("This Apps plan has expired; create a new plan")
    if plan.get("context") != dict(context):
        raise ValueError("Resolved context differs from the reviewed plan")
    payload: dict[str, Any] | None = None
    if plan.get("inputs_path"):
        inputs = Path(plan["inputs_path"])
        ensure_path_beneath(inputs.resolve(), root=root, name="Apps plan inputs path")
        actual_checksum = sha256_bytes(inputs.read_bytes())
        if actual_checksum != plan.get("inputs_sha256"):
            raise ValueError("Reviewed inputs YAML was edited; create a new plan")
        payload = load_yaml_exact(inputs)
        if payload != plan.get("payload"):
            raise ValueError("Reviewed inputs YAML content changed; create a new plan")
        ensure_secret_references_only(payload)
    return path, plan, payload


def claim_for_apply(
    plan_id: str,
    plan_path: str,
    *,
    kind: str,
    context: Mapping[str, str],
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    """Atomically and permanently claim a validated single-use plan."""
    path, initial_plan, _ = validate_for_apply(
        plan_id, plan_path, kind=kind, context=context
    )
    claim_path = path.with_suffix(".claim")
    try:
        descriptor = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise ValueError("This single-use plan is already claimed or consumed") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(plan_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Revalidate after winning the claim to close the check/claim race.
        path, plan, payload = validate_for_apply(
            plan_id, plan_path, kind=kind, context=context
        )
        claimed = {
            **plan,
            "status": "applying",
            "claimed_at": utc_now().isoformat(),
        }
        _atomic_write(path, json.dumps(claimed, indent=2) + "\n")
        return path, claimed, payload
    except Exception:
        # Keep the claim marker: a partially claimed plan is never reusable.
        record_failure(
            path,
            initial_plan,
            "plan claim validation failed; plan permanently consumed",
        )
        raise


def record_success(
    plan_path: Path, plan: dict[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    updated = {
        **plan,
        "status": "applied",
        "applied_at": utc_now().isoformat(),
        "execution_result": dict(result),
    }
    _atomic_write(plan_path, json.dumps(updated, indent=2) + "\n")
    return updated


def record_failure(
    plan_path: Path, plan: dict[str, Any], message: str
) -> dict[str, Any]:
    updated = {
        **plan,
        "status": "failed",
        "consumed": True,
        "failed_at": utc_now().isoformat(),
        "failure": message[:500],
    }
    _atomic_write(plan_path, json.dumps(updated, indent=2) + "\n")
    return updated
