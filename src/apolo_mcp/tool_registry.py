"""Canonical, ordered registration table for the Apolo MCP tools."""

from collections.abc import Callable

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

TOOL_REGISTRARS: tuple[tuple[str, ToolRegistrar], ...] = (
    ("Context", context.register),
    ("Jobs", jobs.register),
    ("Flow", flow.register),
    ("Applications", apps.register),
    ("Storage", storage.register),
    ("Disks", disks.register),
    ("Images", images.register),
    ("Buckets", buckets.register),
    ("Secrets", secrets.register),
    ("Service accounts", service_accounts.register),
)


def register_tools(mcp: FastMCP) -> None:
    """Register every tool in the canonical documentation/runtime order."""
    for _, register in TOOL_REGISTRARS:
        register(mcp)
