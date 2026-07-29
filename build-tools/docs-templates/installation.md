# Installation and client configuration

## Prerequisites

You need:

- Python 3.10 or newer;
- `pipx` for installing Python command-line applications;
- access to an Apolo cluster, organization, and project;
- Codex or Claude Code with local stdio MCP support.

Docker is required only for image push and pull operations. Platform operations remain
subject to the permissions and quotas of the authenticated Apolo identity.

## Install Apolo MCP

Choose one installation. For the complete Apolo client toolkit, including Apolo CLI
and Apolo MCP, install `apolo-all`:

```console
pipx install apolo-all
apolo login
```

To install only Apolo MCP when Apolo CLI is installed separately:

```console
pipx install apolo-mcp
apolo login
```

Do not install both packages with `pipx`, and do not inject `apolo-mcp` into the
`apolo-all` environment. `apolo-all` owns the bundled installation; use the standalone
package only for an MCP-only installation. Upgrade the package selected above with
`pipx upgrade apolo-all` or `pipx upgrade apolo-mcp`.

The installed stdio entry point is `apolo-mcp`; `python -m apolo_mcp` is equivalent in
an environment where the package is installed.

## Configure Codex

Register the server:

```console
codex mcp add apolo -- apolo-mcp
codex mcp list
```

To select policy per Codex launch, keep only the environment-variable forwarding rule
in Codex's `config.toml`:

```toml
[mcp_servers.apolo]
command = "apolo-mcp"
env_vars = ["APOLO_MCP_POLICY_MODE"]
```

Choose the mode when starting Codex:

```console
# Default safe mode; the variable is absent.
codex

# Create resources and manage only their journaled lifecycles.
APOLO_MCP_POLICY_MODE=managed codex

# Permit all supported mutations allowed by the authenticated Apolo identity.
APOLO_MCP_POLICY_MODE=full codex
```

The server reads the policy once when its process starts. Restart Codex after changing
MCP configuration or policy environment.

## Configure Claude Code

Register a dynamic environment expansion rather than a permanently elevated value:

```console
claude mcp add apolo \
  --scope user \
  -e 'APOLO_MCP_POLICY_MODE=${APOLO_MCP_POLICY_MODE:-read-only}' \
  -- apolo-mcp
claude mcp list
```

Then select policy per launch:

```console
claude
APOLO_MCP_POLICY_MODE=managed claude
APOLO_MCP_POLICY_MODE=full claude
```

Claude Code expands `${APOLO_MCP_POLICY_MODE:-read-only}` when starting the MCP
subprocess. Use `--scope local` for private current-project configuration, `--scope
project` for a shared `.mcp.json`, or `--scope user` across projects. The equivalent
project configuration is:

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

Restart Claude Code after changing MCP configuration or policy environment.

## Install the workflow skills

Skills guide Codex and Claude Code through supported multi-step workflows but add no
tools or permissions. The Python package includes every canonical skill.

Install all skills for one or both clients:

```console
apolo-mcp skills install --client codex
apolo-mcp skills install --client claude
apolo-mcp skills install --client both
```

Use only one of those commands. User installation copies skills to `~/.agents/skills`
for Codex and `~/.claude/skills` for Claude Code. To install into the current project,
add `--target project`; use `--root <PROJECT>` to target a different project directory.
You may name individual skills after the options instead of installing the complete
catalog.

The installer leaves a matching installation unchanged and refuses to replace a
locally modified skill. After reviewing the differences, add `--overwrite` to replace
it. In a source checkout, `--mode symlink` keeps development installations synchronized
with the checkout. Codex and current Claude Code releases discover skill updates
automatically; start a new session if a client does not show the newly installed skill.

## Verify the installation

1. Ask the client to call `get_apolo_context`.
2. Confirm the reported identity, cluster, organization, and project.
3. Start without `APOLO_MCP_POLICY_MODE` and verify that a harmless invalid create
   request is rejected by `read-only` policy before any platform mutation.
4. Review the [safety model](safety.md) before enabling `managed` or `full`.

Explicit context supplied to a tool applies only to that call and never changes saved
Apolo CLI defaults.

For unattended `full` operation inside an isolated R&D job, continue with
[Full mode with a dedicated service account](../guides/full-mode-service-account.md).
