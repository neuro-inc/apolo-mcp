# Installation and client configuration

Set up Apolo MCP in three steps:

1. Install the Apolo command-line tools and Codex, Claude Code, or Xum (formerly Mux).
2. Use `apolo-mcp setup` to register the MCP server and link its skills into the agent.
3. Set any optional Apolo environment variables before launching the agent; the setup
   command has already configured the client to forward them when present.

## 1. Install the command-line tools

You need Python 3.12 or newer, `pipx`, access to an Apolo cluster, and Codex, Claude
Code, or Xum with local stdio MCP support. Docker is required only for image push and pull.

Choose one Apolo installation. Install the complete client toolkit:

```console
pipx install apolo-all
apolo login
```

Or install Apolo MCP alone when the Apolo CLI is already installed separately:

```console
pipx install apolo-mcp
apolo login
```

Do not install both packages with `pipx`, and do not inject `apolo-mcp` into the
`apolo-all` environment. Upgrade the selected package with `pipx upgrade apolo-all` or
`pipx upgrade apolo-mcp`.

Install and authenticate the selected coding agent according to its vendor's
instructions, then verify `codex --version`, `claude --version`, or `xum --version`.

## 2. Configure the agent

Run one command for the client you use:

```console
apolo-mcp setup codex --policy-mode read-only
apolo-mcp setup claude --policy-mode read-only
apolo-mcp setup xum --policy-mode read-only
apolo-mcp setup both --policy-mode read-only
```

Use only one of these commands. It registers the `apolo-mcp` stdio server at user
scope, configures the full environment-forwarding contract below, and symlinks every
packaged skill into the client's user skill directory. The links point into the
installed package, so `pipx upgrade` updates the skills without a separate import.
Start a new agent session after setup or package upgrade.
`both` continues to configure Codex and Claude Code only; configure Xum separately.

`--policy-mode` is required in the documented workflow and selects the safe fallback
used when the parent process does not define `APOLO_MCP_POLICY_MODE`. Choose
`read-only`, `managed`, or `full`; use `read-only` unless you deliberately need writes.
A forwarded per-launch value overrides this fallback.

The installer preserves unrelated client settings. It updates only the `apolo` MCP
entry. It refuses to replace a locally modified skill directory; review that directory
before replacing it explicitly:

```console
apolo-mcp skills install --client <CLIENT> --overwrite
```

### Minimal manual configuration

The setup command is preferred. For manual Codex registration, this slim example shows
only the required policy variable; add the optional names from the forwarding contract
when you use them:

```toml
[mcp_servers.apolo]
command = "apolo-mcp"
args = ["serve", "--default-policy", "read-only"]
env_vars = ["APOLO_MCP_POLICY_MODE"]
```

`env_vars` contains names only. Codex reads their current values from its launch
environment instead of freezing values or credentials in `config.toml`.

The equivalent minimal Claude Code project configuration is:

```json
{
  "mcpServers": {
    "apolo": {
      "type": "stdio",
      "command": "apolo-mcp",
      "args": ["serve", "--default-policy", "read-only"],
      "env": {
        "APOLO_MCP_POLICY_MODE": "${APOLO_MCP_POLICY_MODE:-}"
      }
    }
  }
}
```

Claude Code expands environment references when it launches the server. Use user scope
for private cross-project configuration, local scope for private current-project
configuration, or project scope for a shared `.mcp.json`. Never commit expanded
credentials.

Xum's user configuration is `~/.xum/mcp.jsonc`. When `XUM_ROOT` is set (or its legacy
alias `MUX_ROOT`), setup uses that directory for both MCP configuration and user skills.
Setup preserves comments, trailing commas, and unrelated settings. Its minimal manual
registration is:

```json
{
  "servers": {
    "apolo": "apolo-mcp serve --default-policy read-only"
  }
}
```

Xum runs this command in a shell and inherits the launch environment, including the
variables below. Setup does not write their values into the configuration. For repo
overrides, Xum reads `.xum/mcp.jsonc` in the project. In Settings → MCP, enable the
`apolo` server for the workspace. See Xum's
[MCP server documentation](https://github.com/coder/xum/blob/main/docs/config/mcp-servers.mdx).

## 3. Forward Apolo environment variables

The setup command configures all variables in this contract. You set values only in
the shell or protected job environment that launches Codex, Claude Code, or Xum.

- **Required: `APOLO_MCP_POLICY_MODE`.** Selects `read-only`, `managed`, or `full` for
  this launch. When it is absent, the `--policy-mode` fallback chosen during setup is
  used. The server freezes the result at startup.
- **Optional: `APOLO_CONFIG`.** Selects an on-disk Apolo configuration and identity.
  Set it before launching the agent when the default `~/.apolo` is not the intended
  identity.
- **Optional and sensitive: `APOLO_PASSED_CONFIG`.** Supplies a complete
  service-account configuration inside an isolated job. Forward it only from that
  protected environment; never copy its value into client configuration, prompts, or
  logs.
- **Optional: `APOLO_MCP_LEDGER_PATH`.** Overrides the append-only lifecycle journal
  path.
- **Optional: `APOLO_MCP_PLAN_ROOT`.** Overrides the local directory for immutable App
  review plans.

`apolo-mcp` uses `apolo-sdk` directly, and its Flow tools use the programmatic
`apolo-flow` API, so these variables select the same identity and context throughout.
Do not forward `APOLO_API_TOKEN`, `APOLO_API_URL`, or other credential fragments; use
one of the complete configuration mechanisms above.

Tools that deliberately read a protected source from another environment variable,
such as secret creation or external-bucket import, require that exact source variable
for that operation. Add it narrowly and remove it afterward; never configure a blanket
credential-variable allowlist.

## Advanced skill installation

The setup command symlinks the complete catalogue. The lower-level command can install
selected skills or target a project instead:

```console
apolo-mcp skills install --client codex <SKILL_NAME>
apolo-mcp skills install --client claude --target project
apolo-mcp skills install --client xum --target project
```

Symlink mode is the default except for Xum project targets, which copy skills because
Xum rejects project skill symlinks outside the checkout.
`--mode copy` creates a snapshot and replaces an existing
installation by default. Use `--root <PROJECT>` with a project target. Codex installs
skills in `.agents/skills`; Claude Code uses `.claude/skills`; Xum uses `.xum/skills`.
User targets use these directories under the home directory. Xum also discovers the
shared `.agents/skills` locations; see its
[agent skills documentation](https://github.com/coder/xum/blob/main/docs/agents/agent-skills.mdx).

## Verify the installation

1. Restart the client and ask it to call `get_apolo_context`.
2. Confirm the reported identity, cluster, organization, and project.
3. Verify the selected policy in the returned context.
4. Review the [safety model](safety.md) before enabling `managed` or `full`.

Explicit context supplied to a tool applies only to that call and never changes saved
Apolo CLI defaults. For unattended `full` operation, continue with
[Full mode with a dedicated service account](../guides/full-mode-service-account.md).
