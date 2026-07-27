# Getting started

## Prerequisites

Before installing Apolo MCP, you need:

- Python 3.10 or newer;
- an Apolo account with access to a cluster, organization, and project;
- the Apolo CLI installed and an active local session created with `apolo login`;
- an MCP client that can start a local stdio server, such as Codex or Claude Code.

Docker is required only for image push and pull operations. File-based plans, protected
credential sinks, and local transfers require a writable workspace. Platform operations
remain subject to the permissions and quotas of the logged-in Apolo user.

## Install

Install the published package with `uv`:

```console
uv tool install apolo-mcp
apolo login
```

Register it with Codex:

```console
codex mcp add apolo -- apolo-mcp
codex mcp list
```

Codex does not need a globally selected Apolo MCP policy. Keep only the variable
forwarding rule in Codex's `config.toml`:

```toml
[mcp_servers.apolo]
command = "apolo-mcp"
env_vars = ["APOLO_MCP_POLICY_MODE"]
```

Choose the policy when launching a session:

```console
# Default safe mode; APOLO_MCP_POLICY_MODE is absent.
codex

# Create resources and manage only their journaled lifecycles.
APOLO_MCP_POLICY_MODE=managed codex

# Permit exact-context mutations of resources not created by this MCP.
APOLO_MCP_POLICY_MODE=full codex
```

The `env_vars` entry forwards the value from the Codex process to the local MCP
subprocess without storing a policy value in Codex configuration. If the variable is
unset, Apolo MCP starts in `read-only` mode. Restart Codex after changing MCP
configuration so that it starts a new server process.

Or register it with Claude Code. The `user` scope makes the server available in all
projects while keeping the policy value dynamic:

```console
claude mcp add apolo \
  --scope user \
  -e 'APOLO_MCP_POLICY_MODE=${APOLO_MCP_POLICY_MODE:-read-only}' \
  -- apolo-mcp
claude mcp list
```

Claude Code expands `${APOLO_MCP_POLICY_MODE:-read-only}` when it starts the MCP
subprocess. Choose a policy for one launch in the same way:

```console
# Default safe mode.
claude

# Create resources and manage only their journaled lifecycles.
APOLO_MCP_POLICY_MODE=managed claude

# Permit exact-context mutations of resources not created by this MCP.
APOLO_MCP_POLICY_MODE=full claude
```

Use `--scope local` instead for private configuration limited to the current project,
or `--scope project` to create a shared `.mcp.json`. The equivalent project file is:

```json
{
  "mcpServers": {
    "apolo": {
      "command": "apolo-mcp",
      "args": [],
      "env": {
        "APOLO_MCP_POLICY_MODE": "${APOLO_MCP_POLICY_MODE:-read-only}"
      }
    }
  }
}
```

Restart Claude Code after changing MCP configuration so it starts a new server
process.

The equivalent module entry point is `python -m apolo_mcp`. The client starts the
server locally over stdio; no Apolo MCP daemon or remote endpoint is required.

## Verify the context

Ask the client to call `get_apolo_context`. Confirm the reported cluster,
organization, and project before requesting an operation. Explicit context supplied to
a tool applies only to that call and never changes the defaults saved by the Apolo CLI.

Next, read the [safety model](safety.md), then review the current
[capabilities](../capabilities/).
