"""Register MCP tools from the declarative capability catalog."""

from mcp.server.fastmcp import FastMCP

from .catalog import CAPABILITY_SPECS


def register_tools(mcp: FastMCP) -> None:
    """Register every tool in the canonical documentation/runtime order."""
    for capability in CAPABILITY_SPECS:
        capability.register(mcp)
