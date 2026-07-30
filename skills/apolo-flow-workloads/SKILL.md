---
name: apolo-flow-workloads
description: Validate and operate Apolo Flow live jobs and batch bakes, including run, inspect, bounded logs/wait, failure diagnosis, restart, cancel, persistent outputs, and cleanup. Use when a request references flow YAML, a Flow project, live jobs, batch actions, bakes, attempts, tasks, or executors.
---

# Apolo Flow workloads

1. Resolve context with `$apolo-platform-user-context`. Resolve `workspace_path` as the
   Flow project root. It must contain a real `.apolo` directory. Live configuration is
   `.apolo/live.yml` or `.yaml`; batch configuration is
   `.apolo/<batch>.yml` or `.yaml`; optional project settings are in
   `.apolo/project.yml` or `.yaml`.
2. Use `flow_config_schema` to explore the root and referenced definitions from the
   installed Flow version's release-tagged JSON schema. For new canonical
   `.apolo/live.yml`, `.apolo/<batch>.yml`, or `.apolo/project.yml` files, use
   `flow_config_write`; it validates before writing, creates without overwrite, adds
   the version-pinned YAML-language-server schema URL, then rereads and validates the
   result. Use `flow_config_validate` for existing files before execution. Never put
   literal credentials in Flow configuration; use supported secret references or
   expressions. Validate project, live job or batch name, parameters, tags, executor,
   images, volumes, dependencies, and persistent output paths. Do not parse colored
   human tables for machine state.
3. Run live jobs asynchronously by default. Ensure the selected job has `detach: true`
   before `flow_live_run`; do not keep the MCP start call attached to the workload.
   After start returns, monitor separately with get, bounded logs, and bounded wait.
   Match Flow's project/job/suffix resolution rules.
4. Prefer submission-style handling for bakes: start through Flow orchestration,
   record the returned bake, then inspect or wait in separate bounded calls. Keep the
   start timeout bounded because the current upstream runner may hold the call open
   until its remote executor exits. Never post directly to the Flow persistence API:
   setup must collect configuration, handle images, and launch the supported executor.
5. Record every returned job/bake identifier and context in the session ledger. Keep
   attempt/task status, terminal reason, outputs, and executor evidence bounded.
6. On controlled failure, inspect structured state and logs, correct the source or
   parameter, present the exact correction diff/parameter change, and use the supported
   restart operation. Preserve the failed evidence; restart approval binds to that fix.
7. Treat live kill-all, bake cancel, and bake restart as destructive writes requiring
   explicit user approval plus `managed` or `full` server policy. Scope kill-all to the
   exact validated Flow project and context.
8. Verify persistent outputs before success and clean up only exact ledger-owned
   resources. Preserve a bounded failure path even when the happy path succeeds.

Use a documented bounded CLI fallback only when the installed Flow version lacks the
typed facade. Rely on exit codes and JSON/NDJSON when available; never scrape Rich
tables as an API.
