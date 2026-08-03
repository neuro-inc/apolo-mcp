### Runtime requirements inside an R&D job

For the MVP, use `node:22-bookworm-slim` as an example bootstrap image and record that
exact mutable tag. Apolo job launch does not currently accept Docker image digest
references. This is an upstream convenience image, not an Apolo-supported R&D image;
another glibc-based Debian or Ubuntu image is acceptable when it provides Node.js 22
and Python 3.11 or newer.

Start the bounded job with `sleep infinity`, enter it with `apolo exec`, and install the
runtime there. Before running package-manager or network commands, show the resolved
versions and complete command plan and obtain confirmation. On the suggested image,
install the common system dependencies as the job's privileged user:

```console
apt-get update
apt-get install --yes --no-install-recommends \
  ca-certificates \
  curl \
  git \
  pipx \
  python3 \
  python3-venv
```

Install the Apolo client bundle and packaged skills into the job user's environment.
Replace every version and client placeholder with a resolved value; install only the
selected coding client or clients.

```console
export PATH="$HOME/.local/bin:$PATH"
pipx install apolo-all==<APOLO_ALL_VERSION>
npm config set prefix "$HOME/.local"
npm install --global @openai/codex@<CODEX_VERSION>
npm install --global @anthropic-ai/claude-code@<CLAUDE_CODE_VERSION>
```

Do not run both npm commands unless both clients were requested. Never silently
substitute `latest` or execute an uninspected remote installer through a pipe. Git,
language runtimes, build tools, and similar utilities beyond the common bootstrap are
workload dependencies; add only those required by the target repository.

After installing the selected client, use an ephemeral agent configuration directory
and run the unified setup. Set `CODEX_HOME` only when Codex was selected:

```console
export CODEX_HOME=/tmp/codex-home
apolo-mcp setup <codex|claude|both> --policy-mode full
```

The command registers the job-local MCP server, forwards the complete Apolo environment
contract, and links the packaged skills. Do not point the agent configuration or
authentication home into `/workspace`.

`tmux` is optional. Install it only when an interactive client needs detach/reattach
support:

```console
apt-get install --yes --no-install-recommends tmux
```

`tmux` does not keep the session alive after the Apolo job terminates.

Verify the selected client and the two Apolo entry points before continuing. Take the
Apolo MCP package version from `pipx list`; the current CLI does not expose a version
flag.

```console
node --version
python3 --version
pipx list
apolo --version
apolo-mcp --help
# Run one or both, matching the installation:
codex --version
claude --version
```

### Isolated R&D job configuration

For unattended `full` operation, the job must expose a dedicated service account's
complete token as `APOLO_PASSED_CONFIG`, use a clean `APOLO_CONFIG`, and set
`APOLO_MCP_POLICY_MODE=full`. Never use `--pass-config` or mount the launching user's
`~/.apolo`.

Mount an approved Apolo storage path read-write at `/workspace` and make it the job's
working directory. Keep repositories, generated outputs, non-secret diagnostics, and
a sanitized `/workspace/HANDOFF.md` there so a replacement job can mount the same path
and continue. The handoff should record the current goal, completed work, verification
results, pending work, and artifact paths. Keep Apolo and coding-provider credentials,
agent authentication stores, terminal transcripts, and environment dumps outside the
persistent mount.

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
