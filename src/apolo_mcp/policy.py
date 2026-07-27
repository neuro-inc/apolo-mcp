"""Local operator policy for Apolo MCP mutations."""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ledger import LedgerEntry, authorize_owned_resource, ensure_ledger_writable


POLICY_FILE_ENV = "APOLO_MCP_POLICY_FILE"
POLICY_MODE_ENV = "APOLO_MCP_POLICY_MODE"


class PolicyMode(str, enum.Enum):
    """Mutation authority granted by the operator who launches the server."""

    READ_ONLY = "read-only"
    MANAGED = "managed"
    FULL = "full"


class MutationEffect(str, enum.Enum):
    """Lifecycle effect used by the centralized policy decision."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class Policy:
    """Server-side mutation policy; it never elevates Apolo RBAC."""

    mode: PolicyMode = PolicyMode.READ_ONLY

    @classmethod
    def load(cls) -> "Policy":
        path = os.environ.get(POLICY_FILE_ENV)
        file_value: str | None = None
        if path:
            raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("mode"), str):
                raise ValueError("Policy must contain a string 'mode'")
            file_value = raw["mode"]
        value = os.environ.get(POLICY_MODE_ENV, file_value or PolicyMode.READ_ONLY)
        try:
            mode = PolicyMode(value.strip().lower())
        except (AttributeError, ValueError) as exc:
            choices = ", ".join(item.value for item in PolicyMode)
            raise ValueError(f"{POLICY_MODE_ENV} must be one of: {choices}") from exc
        return cls(mode=mode)

    def authorize(
        self,
        *,
        operation: str,
        effect: MutationEffect,
        resource_type: str | None = None,
        resource_id: str | None = None,
        cluster: str | None = None,
        org: str | None = None,
        project: str | None = None,
    ) -> LedgerEntry | None:
        """Authorize one mutation, requiring exact ownership in managed mode."""
        if self.mode is PolicyMode.READ_ONLY:
            raise PermissionError(
                f"Operation {operation!r} is disabled by read-only server policy; "
                f"set {POLICY_MODE_ENV}=managed or {POLICY_MODE_ENV}=full"
            )
        if self.mode is PolicyMode.FULL or effect is MutationEffect.CREATE:
            return None
        values = (resource_type, resource_id, cluster, org, project)
        if any(value is None for value in values):
            raise PermissionError(
                f"Operation {operation!r} requires an exact ledger-owned resource "
                "in managed policy mode"
            )
        return authorize_owned_resource(
            resource_type=resource_type,  # type: ignore[arg-type]
            resource_id=resource_id,  # type: ignore[arg-type]
            cluster=cluster,  # type: ignore[arg-type]
            org=org,  # type: ignore[arg-type]
            project=project,  # type: ignore[arg-type]
        )


def authorize_mutation(
    *,
    operation: str,
    effect: MutationEffect,
    resource_type: str | None = None,
    resource_id: str | None = None,
    cluster: str | None = None,
    org: str | None = None,
    project: str | None = None,
) -> LedgerEntry | None:
    """Load current policy and authorize one exact mutation."""
    result = Policy.load().authorize(
        operation=operation,
        effect=effect,
        resource_type=resource_type,
        resource_id=resource_id,
        cluster=cluster,
        org=org,
        project=project,
    )
    ensure_ledger_writable()
    return result
