"""Register MCP tools from the declarative capability catalog."""

from mcp.server import MCPServer

from .catalog import CAPABILITY_SPECS


def register_tools(mcp: MCPServer) -> None:
    """Register every tool in the canonical documentation/runtime order."""
    for capability in CAPABILITY_SPECS:
        capability.register(mcp)
