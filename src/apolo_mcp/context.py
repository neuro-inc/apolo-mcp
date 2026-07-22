"""Explicit, non-persisted Apolo context selection."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ApoloContext:
    cluster: str
    org: str
    project: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def resolve_context(
    config: Any,
    *,
    cluster: str | None = None,
    org: str | None = None,
    project: str | None = None,
) -> ApoloContext:
    """Resolve overrides against config without invoking any switch method."""
    resolved_cluster = cluster or config.cluster_name
    if resolved_cluster not in config.clusters:
        raise ValueError(f"Unknown cluster: {resolved_cluster}")
    resolved_org = org or config.org_name
    cluster_orgs = config.clusters[resolved_cluster].orgs
    if resolved_org not in cluster_orgs:
        raise ValueError(
            f"Organization {resolved_org!r} is unavailable in cluster "
            f"{resolved_cluster!r}"
        )
    resolved_project = project or config.project_name
    if not resolved_project:
        raise ValueError("A project must be selected or provided explicitly")
    matching = any(
        item.cluster_name == resolved_cluster
        and item.org_name == resolved_org
        and item.name == resolved_project
        for item in config.projects.values()
    )
    if not matching:
        raise ValueError(
            f"Project {resolved_project!r} is unavailable in "
            f"{resolved_cluster!r}/{resolved_org!r}"
        )
    return ApoloContext(resolved_cluster, resolved_org, resolved_project)
