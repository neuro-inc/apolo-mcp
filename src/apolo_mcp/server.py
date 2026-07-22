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

context.register(mcp)
jobs.register(mcp)
flow.register(mcp)
apps.register(mcp)
storage.register(mcp)
disks.register(mcp)
images.register(mcp)
buckets.register(mcp)
secrets.register(mcp)
service_accounts.register(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
