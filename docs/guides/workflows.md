# Workloads and Flow

Apolo MCP supports bounded research jobs and Apolo Flow live jobs and batch bakes. Use
the native tools for structured lifecycle operations; keep interactive or high-volume
terminal workflows in the user's local CLI.

## Research jobs

1. Resolve and display the exact context, then use `list_presets` to select compute.
2. Review the image, preset, command, mounts, secret reference names, lifecycle limits,
   target context, and expected output location before starting the job.
3. Run with an explicit lifespan and scheduling timeout. Record the returned job ID.
4. Monitor with bounded wait, logs, and telemetry calls. Preserve terminal state,
   truncation markers, and exit information.
5. Verify the expected output and delete only an exact resource created by the current
   workflow and recorded with an active lifecycle in its append-only journal.

Credentials belong in named secret references, never commands, ordinary environment
values, evidence, or logs. Move large or binary data through mounted storage or a
user-reviewed CLI transfer rather than through model context.

## Apolo Flow

For a Flow project, first inspect its configuration and validate the requested project,
action, parameters, executor, images, volumes, and outputs. Local path restrictions are
defined by the [safety model](../getting-started/safety.md).

Use the typed live-job operations to list, run, inspect, read bounded logs, wait, and
kill. Start batch bakes through Flow orchestration, then inspect attempts and tasks with
the typed bake operations. On failure, preserve the failed state and logs, identify the
specific source or parameter correction, and use the supported restart operation only
after that correction is reviewed.

Interactive attach, Flow project scaffolding, bulk upload/download, and local cache or
project maintenance remain local CLI activities. For a running MCP-owned job, use the
native loopback-only port-forward start/list/stop lifecycle; forwarded bytes never
enter ordinary tool results and every listener closes when stopped or when MCP exits.

See the [research-job and Flow skills](../capabilities/skills.md) and the exact
[tool reference](../capabilities/tools/README.md).
