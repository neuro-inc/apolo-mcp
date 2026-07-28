# Installation and client configuration

## Prerequisites

You need:

- Python 3.10 or newer;
- `uv` for installing the server;
- the Apolo CLI and an active local session created with `apolo login`;
- access to an Apolo cluster, organization, and project;
- Codex or Claude Code with local stdio MCP support.

Docker is required only for image push and pull operations. Platform operations remain
subject to the permissions and quotas of the authenticated Apolo identity.

## Install the server

```console
uv tool install apolo-mcp
apolo login
```

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
tools or permissions. During this MVP, the skill installer is available from a source
checkout; the published Python wheel installs the MCP server only.

From the repository root, install all canonical skills for one or both clients:

```console
uv run python scripts/install_skills.py --target user --client codex --mode copy
uv run python scripts/install_skills.py --target user --client claude --mode copy
uv run python scripts/install_skills.py --target user --client both --mode copy
```

Use only one of those commands. For repository development, `--mode symlink` keeps the
installed skills synchronized with the checkout. To replace an existing copied skill
after reviewing local differences, add `--overwrite`. Start a new client session after
installation so it discovers the skills.

## Verify the installation

1. Ask the client to call `get_apolo_context`.
2. Confirm the reported identity, cluster, organization, and project.
3. Start without `APOLO_MCP_POLICY_MODE` and verify that a harmless invalid create
   request is rejected by `read-only` policy before any platform mutation.
4. Review the [safety model](safety.md) before enabling `managed` or `full`.

Explicit context supplied to a tool applies only to that call and never changes saved
Apolo CLI defaults.

## Runtime toolchain inside an R&D job

Prefer a pinned image containing the selected coding client, Apolo CLI, Apolo MCP,
`uv`, `tmux`, Git, and required runtimes. Runtime installation increases startup time,
requires broader network/root access, and is harder to reproduce. Record the image
digest and tool versions.

For an ephemeral MVP job, use two reviewed stages when resolver tools are missing.
Stage 1 installs only minimum resolver prerequisites from configured OS repositories;
stage 2 resolves and installs the agent toolchain. Obtain confirmation before each
stage. If package metadata cannot be refreshed safely, rebuild the image instead.

Resolve exact versions before proposing changes:

```console
apt-cache policy ca-certificates curl git tmux nodejs npm python3 python3-pip
npm view @openai/codex version
python3 -m pip index versions uv
python3 -m pip index versions apolo-cli
python3 -m pip index versions apolo-mcp
```

If `pip` or package indexes are absent, first show the exact resolver-bootstrap plan and
obtain approval. Never silently install `latest`. A reviewed Debian/Ubuntu plan has
this shape, with every placeholder replaced by a resolved version:

```console
apt-get update
apt-get install -y \
  ca-certificates=<CA_CERTIFICATES_VERSION> \
  curl=<CURL_VERSION> \
  git=<GIT_VERSION> \
  tmux=<TMUX_VERSION> \
  nodejs=<NODEJS_VERSION> \
  npm=<NPM_VERSION> \
  python3=<PYTHON3_VERSION> \
  python3-pip=<PYTHON3_PIP_VERSION>
python3 -m pip install --user uv==<UV_VERSION>
export PATH="$HOME/.local/bin:$PATH"
uv tool install apolo-cli==<APOLO_CLI_VERSION>
uv tool install apolo-mcp==<APOLO_MCP_VERSION>
```

Install only the chosen coding client. For Codex, pin the version returned by the
official npm registry:

```console
npm install --global @openai/codex@<CODEX_VERSION>
codex --version
```

Claude Code's native installer is not an OS-package version pin. For an MVP runtime
install, download it to a file, inspect it, obtain confirmation, and record the
resulting version. Use a prebuilt image when strict reproducibility is required.

```console
curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh
less /tmp/claude-install.sh
bash /tmp/claude-install.sh
claude --version
```

Verify only the selected client plus the common toolchain:

```console
apolo --version
uv --version
uv tool list
tmux -V
git --version
node --version
npm --version
```

If any requested version cannot be installed exactly, stop and offer a revised image or
version plan rather than substituting another release.

## Isolated R&D job configuration

For unattended `full` operation, first follow the
[dedicated service-account workflow](../guides/full-mode-service-account.md). The job
must expose a dedicated service account's complete token as `APOLO_PASSED_CONFIG`, use
a clean `APOLO_CONFIG`, and set `APOLO_MCP_POLICY_MODE=full`. Never use `--pass-config`
or mount the launching user's `~/.apolo`.

Forward those job variables to Apolo MCP in job-local Codex configuration:

```toml
[mcp_servers.apolo]
command = "apolo-mcp"
env_vars = [
  "APOLO_CONFIG",
  "APOLO_MCP_POLICY_MODE",
  "APOLO_PASSED_CONFIG",
]
```

For Claude Code, use job-local configuration:

```json
{
  "mcpServers": {
    "apolo": {
      "command": "apolo-mcp",
      "args": [],
      "env": {
        "APOLO_CONFIG": "${APOLO_CONFIG}",
        "APOLO_MCP_POLICY_MODE": "full",
        "APOLO_PASSED_CONFIG": "${APOLO_PASSED_CONFIG}"
      }
    }
  }
}
```

Keep this configuration inside the isolated image or job workspace. Never make `full`
the user's global desktop default.
