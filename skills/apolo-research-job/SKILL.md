---
name: apolo-research-job
description: Stage, launch, monitor, evidence, and clean up bounded Apolo research or experiment jobs. Use for training, evaluation, data processing, reproducible scripts, mounted storage outputs, secret-backed jobs, or short compute tasks that need context, preset, provenance, and cost hygiene.
---

# Apolo research job

Use jobs for bounded R&D, experiments, builds, migrations, batch work, and tests. Do
not run a database, API, web UI, or other long-running deployment as a job; hand that
workload to `$apolo-applications`. A remote image builder is appropriately a job
because it terminates after producing the image.

1. Use `$apolo-platform-user-context` to show the exact target and list suitable presets.
   Prefer the least expensive preset that meets stated CPU, memory, and accelerator
   needs. Confirm quota from platform output; never infer it.
2. Keep source, inputs, outputs, and evidence in approved local paths or an exact
   project storage URI. Use storage mounts or `apolo cp` for bulk/binary data instead
   of moving it through model context.
3. Make the job bounded: pin an image tag when available, set a lifespan and
   schedule timeout, choose restart/scheduler settings deliberately, and use explicit
   cluster/organization/project fields.
4. Pass ordinary non-sensitive environment values directly. Pass credentials only as
   named secret references through secret environment/file mounts. Never read a
   secret value or place one in command text, evidence, or logs.
5. Before `run_job`, show image, preset, command, mounts, secret reference names,
   HTTP exposure/auth, lifecycle limits, target context, and expected output URI.
   Execute only after the user approves and the selected server policy permits it.
6. Record the returned exact job ID in the session ledger immediately. Monitor with
   bounded `wait_for_job`, `get_job_logs`, and `get_job_telemetry`; retain truncation
   markers and terminal reason/exit code.
   Use `exec_job` for bounded, non-interactive commands in an MCP-owned running job;
   pass an executable and argument list, never credentials or shell-interpolated
   command text. For temporary network access, start a loopback listener with
   `start_job_port_forward`, retain its opaque forwarding ID, inspect it with
   `list_job_port_forwards`, and stop that exact ID with `stop_job_port_forward` when
   finished. Forwarded bytes stay outside model results; listeners also close when
   the MCP process exits. Keep interactive attach in the user's local CLI.
7. Verify the expected storage artifact and record command/config checksum, image,
   preset, context, runtime, cost when reported, job ID, output URI, and failure path.
8. Kill only the exact approved job when needed. Automatic cleanup may act only on an
   exact ledger-owned ID; never infer ownership from a name or prefix.

Use the local CLI for interactive attach. Do not wrap its stream as an ordinary MCP
result.
