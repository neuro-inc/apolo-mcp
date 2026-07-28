# Claude Code inside the R&D job

Install Claude Code and the common toolchain through the canonical process linked from
[runtime-bootstrap.md](runtime-bootstrap.md).

Follow the
[isolated R&D job configuration](../../../docs/getting-started/installation.md#isolated-rd-job-configuration)
for the job-local Claude Code MCP registration, then verify it with `claude mcp list`.

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
