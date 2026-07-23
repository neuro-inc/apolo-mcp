# Applications

Apolo MCP supports Application discovery, inspection, troubleshooting, installation,
configuration, rollback, and deletion. Lifecycle changes use immutable, checksum-bound
plans so the applied operation is the operation that was reviewed.

## Discover and inspect

Start with `list_app_templates` rather than assuming a template from memory. Compare
the available titles, descriptions, tags, versions, and operational tradeoffs against
the requested outcome. Confirm a selection when it changes user-facing behavior, then
retrieve that exact version's current input schema.

For an installed Application, inspect its status, input, values, revisions, endpoints,
bounded logs, events, and output. Secret references remain references and sensitive
values are not returned.

## Plan and apply a change

1. Create an install, configure, rollback, or uninstall plan. Planning is local and
   does not mutate the platform.
2. Review the generated input and plan files, including exact context, template
   version, current revision, resources, endpoints, effects, expiry, and SHA-256.
3. Apply the exact unchanged, unexpired plan only after client approval. A write also
   requires the server's user-controlled high-risk policy to be enabled.
4. The server rechecks the checksum, parsed input, context, target, and current state.
   An edit, expired plan, reused plan, or revision drift requires a new plan.
5. Wait with a finite bound, then verify state, logs, events, outputs, and endpoints.

Configuration starts from the current Application input before applying requested
changes. Rollback and uninstall plans bind to the exact Application and revision and
are treated as destructive operations. Automatic cleanup is permitted only for an
exact Application ID recorded in the workflow's ownership ledger.

See the [Applications skill](../capabilities/skills.md), the
[safety model](../getting-started/safety.md), and the generated
[tool reference](../capabilities/tools.md).
