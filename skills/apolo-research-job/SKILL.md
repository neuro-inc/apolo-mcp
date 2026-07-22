---
name: apolo-research-job
description: Stage, launch, monitor, evidence, and clean up bounded Apolo research or experiment jobs. Use for training, evaluation, data processing, reproducible scripts, mounted storage outputs, secret-backed jobs, or short compute tasks that need context, preset, provenance, and cost hygiene.
---

# Apolo research job

1. Use `$apolo-platform-context` to show the exact target and list suitable presets.
   Prefer the least expensive preset that meets stated CPU, memory, and accelerator
   needs. Confirm quota from platform output; never infer it.
2. Keep source, inputs, outputs, and evidence below the allowed workspace or an exact
   project storage URI. Use storage mounts or `apolo cp` for bulk/binary data instead
   of moving it through model context.
3. Make the job bounded: pin an image tag/digest when available, set a lifespan and
   schedule timeout, choose restart/scheduler settings deliberately, and use explicit
   cluster/organization/project fields.
4. Pass ordinary non-sensitive environment values directly. Pass credentials only as
   named secret references through secret environment/file mounts. Never read a
   secret value or place one in command text, evidence, or logs.
5. Before `run_job`, show image, preset, command, mounts, secret reference names,
   HTTP exposure/auth, lifecycle limits, target context, and expected output URI.
   Execute only after the client approves and server high-risk policy permits it.
6. Record the returned exact job ID in the session ledger immediately. Monitor with
   bounded `wait_for_job`, `get_job_logs`, and `get_job_telemetry`; retain truncation
   markers and terminal reason/exit code.
7. Verify the expected storage artifact and record command/config checksum, image,
   preset, context, runtime, cost when reported, job ID, output URI, and failure path.
8. Kill only the exact approved job when needed. Automatic cleanup may act only on an
   exact ledger-owned ID; never infer ownership from a name or prefix.

Use the local CLI for interactive attach or port forwarding and impose explicit
duration/output bounds. Do not wrap those streams as ordinary MCP results.
