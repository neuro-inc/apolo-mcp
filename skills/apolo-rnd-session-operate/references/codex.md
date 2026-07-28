# Codex inside the R&D job

Prefer a pinned image containing Codex. If it is absent, resolve and obtain approval
for an exact version as described by the runtime-bootstrap plan, then install the
official package:

```console
npm install --global @openai/codex@<CODEX_VERSION>
codex --version
```

Install and verify Apolo MCP separately:

```console
uv tool install apolo-mcp==<APOLO_MCP_VERSION>
uv tool list
```

Create a private job-local home and write configuration to
`$CODEX_HOME/config.toml`:

```toml
sandbox_mode = "workspace-write"
approval_policy = "on-request"
approvals_reviewer = "user"

[mcp_servers.apolo]
command = "apolo-mcp"
env_vars = [
  "APOLO_CONFIG",
  "APOLO_MCP_POLICY_MODE",
  "APOLO_PASSED_CONFIG",
]
```

For an interactive session, use Codex device authentication when available or an
already provisioned job-local authentication store. For noninteractive execution,
Codex supports `CODEX_API_KEY` for `codex exec`; keep it scoped to that process rather
than exporting it into unrelated commands.

Set `CODEX_HOME` to a job-local path before launch. Keep shell network disabled unless
the task requires it; Apolo MCP's own server connection is separate. After reviewing
the workspace, start a persistent terminal session:

```console
tmux new-session -d -s rnd-codex -c <WORKSPACE> -- codex
tmux attach-session -t rnd-codex
```

Use `Ctrl-b d` to detach. Inspect or stop the session without attaching:

```console
tmux has-session -t rnd-codex
tmux list-panes -t rnd-codex -F '#{pane_current_command} #{pane_dead}'
tmux kill-session -t rnd-codex
```

Do not enable `pipe-pane` or terminal transcript capture by default. Inspect expected
outputs at their approved storage URI and use bounded Apolo job status/log operations
for monitoring.

For a bounded noninteractive task, prefer `codex exec` as the main process or inside a
named tmux session. Do not use dangerous sandbox or approval bypass settings merely
because Apolo MCP is in `full` mode.
