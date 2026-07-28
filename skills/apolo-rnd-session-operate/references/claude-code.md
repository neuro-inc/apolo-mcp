# Claude Code inside the R&D job

Prefer a pinned image containing Claude Code. If it is absent, download the official
installer to a file, inspect it, and run it only after user confirmation. This avoids
executing an uninspected network response through a shell pipe:

```console
curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh
less /tmp/claude-install.sh
bash /tmp/claude-install.sh
claude --version
```

Install and verify Apolo MCP separately:

```console
uv tool install apolo-mcp==<APOLO_MCP_VERSION>
uv tool list
```

Register a job-local MCP server with the isolated Apolo environment forwarded:

```console
claude mcp add apolo \
  --scope local \
  -e 'APOLO_CONFIG=${APOLO_CONFIG}' \
  -e 'APOLO_MCP_POLICY_MODE=full' \
  -e 'APOLO_PASSED_CONFIG=${APOLO_PASSED_CONFIG}' \
  -- apolo-mcp
claude mcp list
```

Use a protected provider secret or supported interactive authentication. For scripted
print mode, Claude Code's `--bare` mode requires API-key or configured helper auth and
skips plugins and skill discovery, so do not use `--bare` when this skill must remain
active.

After reviewing permissions, start a persistent terminal session:

```console
tmux new-session -d -s rnd-claude -c <WORKSPACE> -- claude
tmux attach-session -t rnd-claude
```

Use `Ctrl-b d` to detach. Inspect or stop the session without attaching:

```console
tmux has-session -t rnd-claude
tmux list-panes -t rnd-claude -F '#{pane_current_command} #{pane_dead}'
tmux kill-session -t rnd-claude
```

Do not enable `pipe-pane` or terminal transcript capture by default. Inspect expected
outputs at their approved storage URI and use bounded Apolo job status/log operations
for monitoring.

Do not use permission-bypass flags merely because Apolo MCP is in `full` mode.
