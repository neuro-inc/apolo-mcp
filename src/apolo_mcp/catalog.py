"""Declarative catalog for MCP capability groups and workflow skills."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from .tools import (
    apps,
    buckets,
    context,
    disks,
    flow,
    images,
    jobs,
    secrets,
    service_accounts,
    storage,
)


ToolRegistrar = Callable[[FastMCP], None]


@dataclass(frozen=True)
class SkillSpec:
    """One packaged workflow skill and its documentation identity."""

    name: str
    display_name: str


@dataclass(frozen=True)
class CapabilitySpec:
    """One registered MCP tool group and its documentation ownership."""

    title: str
    slug: str
    description: str
    register: ToolRegistrar
    skill: SkillSpec


PLATFORM_CONTEXT = SkillSpec(
    "apolo-platform-user-context", "Apolo Platform User Context"
)
RESEARCH_JOB = SkillSpec("apolo-research-job", "Apolo Research Job")
FLOW_WORKLOADS = SkillSpec("apolo-flow-workloads", "Apolo Flow Workloads")
APPLICATIONS = SkillSpec("apolo-applications", "Apolo Applications")
RESOURCE_MANAGEMENT = SkillSpec(
    "apolo-resource-management", "Apolo Resource Management"
)
RND_SESSION_SETUP = SkillSpec("apolo-rnd-session-setup", "Apolo R&D Session Setup")
RND_SESSION_OPERATE = SkillSpec(
    "apolo-rnd-session-operate", "Apolo R&D Session Operations"
)

SKILL_SPECS: tuple[SkillSpec, ...] = (
    PLATFORM_CONTEXT,
    RESEARCH_JOB,
    FLOW_WORKLOADS,
    APPLICATIONS,
    RESOURCE_MANAGEMENT,
    RND_SESSION_SETUP,
    RND_SESSION_OPERATE,
)

CAPABILITY_SPECS: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        "Context",
        "context",
        "Platform context, discovery, presets, and resource resolution.",
        context.register,
        PLATFORM_CONTEXT,
    ),
    CapabilitySpec(
        "Jobs",
        "jobs",
        "Job lifecycle, logs, telemetry, signals, and image snapshots.",
        jobs.register,
        RESEARCH_JOB,
    ),
    CapabilitySpec(
        "Flow",
        "flow",
        "Live Flow jobs and batch bake lifecycle operations.",
        flow.register,
        FLOW_WORKLOADS,
    ),
    CapabilitySpec(
        "Applications",
        "applications",
        "App discovery, inspection, planning, and lifecycle operations.",
        apps.register,
        APPLICATIONS,
    ),
    CapabilitySpec(
        "Storage",
        "storage",
        "Remote storage listing, metadata, text files, and directories.",
        storage.register,
        RESOURCE_MANAGEMENT,
    ),
    CapabilitySpec(
        "Disks",
        "disks",
        "Persistent disk discovery, creation, and deletion.",
        disks.register,
        RESOURCE_MANAGEMENT,
    ),
    CapabilitySpec(
        "Images",
        "images",
        "Container image discovery, inspection, transfer, and removal.",
        images.register,
        RESOURCE_MANAGEMENT,
    ),
    CapabilitySpec(
        "Buckets",
        "buckets",
        "Bucket and blob metadata, transfer, access, and lifecycle operations.",
        buckets.register,
        RESOURCE_MANAGEMENT,
    ),
    CapabilitySpec(
        "Secrets",
        "secrets",
        "Protected secret discovery, file retrieval, creation, and deletion.",
        secrets.register,
        RESOURCE_MANAGEMENT,
    ),
    CapabilitySpec(
        "Service accounts",
        "service-accounts",
        "Service-account metadata, protected creation, and deletion.",
        service_accounts.register,
        RESOURCE_MANAGEMENT,
    ),
)


def _validate_catalog() -> None:
    skill_names = [item.name for item in SKILL_SPECS]
    capability_titles = [item.title for item in CAPABILITY_SPECS]
    capability_slugs = [item.slug for item in CAPABILITY_SPECS]
    if len(skill_names) != len(set(skill_names)):
        raise ValueError("duplicate skill name in catalog")
    if len(capability_titles) != len(set(capability_titles)):
        raise ValueError("duplicate capability title in catalog")
    if len(capability_slugs) != len(set(capability_slugs)):
        raise ValueError("duplicate capability slug in catalog")
    if any(item.skill not in SKILL_SPECS for item in CAPABILITY_SPECS):
        raise ValueError("capability owner is not a canonical skill")


_validate_catalog()
