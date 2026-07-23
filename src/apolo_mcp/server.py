from mcp.server.fastmcp import FastMCP

from .tool_registry import register_tools


mcp = FastMCP(
    "apolo",
    instructions=(
        "Before any write, discover and show the resolved cluster, organization, and "
        "project; use explicit context arguments and never change saved context. Never "
        "request, return, or log tokens, cookies, secret values, or service-account "
        "credentials. Treat annotations as approval hints, not authorization. Keep "
        "outputs bounded and require both explicit user approval and enabled server "
        "policy for high-risk operations. The user must already be authenticated with "
        "`apolo login`."
    ),
)

register_tools(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
