"""Local operator policy for Apolo MCP mutations."""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass

from .ledger import LedgerEntry, authorize_owned_resource, ensure_ledger_writable


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
        value = os.environ.get(POLICY_MODE_ENV, PolicyMode.READ_ONLY)
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
        username: str | None = None,
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
        values = (resource_type, resource_id, username, cluster, org, project)
        if any(value is None for value in values):
            raise PermissionError(
                f"Operation {operation!r} requires an exact ledger-owned resource "
                "in managed policy mode"
            )
        return authorize_owned_resource(
            resource_type=resource_type,  # type: ignore[arg-type]
            resource_id=resource_id,  # type: ignore[arg-type]
            username=username,  # type: ignore[arg-type]
            cluster=cluster,  # type: ignore[arg-type]
            org=org,  # type: ignore[arg-type]
            project=project,  # type: ignore[arg-type]
        )


_active_policy: Policy | None = None


def initialize_policy() -> Policy:
    """Load and freeze the policy for this MCP server process."""
    global _active_policy
    if _active_policy is None:
        _active_policy = Policy.load()
    return _active_policy


def current_policy() -> Policy:
    """Return the immutable policy selected when this server process started."""
    return initialize_policy()


def _reset_policy_for_tests() -> None:
    """Clear process policy state between isolated unit tests."""
    global _active_policy
    _active_policy = None


def authorize_mutation(
    *,
    operation: str,
    effect: MutationEffect,
    resource_type: str | None = None,
    resource_id: str | None = None,
    username: str | None = None,
    cluster: str | None = None,
    org: str | None = None,
    project: str | None = None,
) -> LedgerEntry | None:
    """Load current policy and authorize one exact mutation."""
    result = current_policy().authorize(
        operation=operation,
        effect=effect,
        resource_type=resource_type,
        resource_id=resource_id,
        username=username,
        cluster=cluster,
        org=org,
        project=project,
    )
    ensure_ledger_writable()
    return result
