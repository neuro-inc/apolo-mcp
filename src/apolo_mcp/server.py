from mcp.server.fastmcp import FastMCP

from .policy import initialize_policy
from .tool_registry import register_tools


mcp = FastMCP(
    "apolo",
    instructions=(
        "Before any write, discover and show the authenticated username and resolved "
        "cluster, organization, and project; use explicit context arguments and never "
        "change saved context. Never "
        "request, return, or log tokens, cookies, secret values, or service-account "
        "credentials. Treat annotations as risk hints, not authorization. Keep "
        "outputs bounded and obey the server's read-only, managed, or full mutation "
        "policy. Local file and Flow paths are confined below the server startup "
        "directory; tool arguments cannot widen it. Authentication must already be "
        "configured "
        "through local Apolo configuration or an isolated passed configuration."
    ),
)

register_tools(mcp)


def main() -> None:
    initialize_policy()
    mcp.run()


if __name__ == "__main__":
    main()
