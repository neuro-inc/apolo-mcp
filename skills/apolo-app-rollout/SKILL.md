---
name: apolo-app-rollout
description: Discover and compare Apolo App templates, then plan, review, install, verify, reconfigure, roll back, or uninstall Apps with immutable checksum-bound files and state-drift protection. Use for any Apps request, including template selection, revisions, rollout health, endpoints, reconfiguration, rollback, or uninstall.
---

# Apolo app rollout

1. Resolve the exact context and call `list_app_templates` before selecting a template.
   Compare the available templates against the user's requested outcome using titles,
   descriptions, tags, current versions, and known operational tradeoffs. Do not assume
   a template before discovery or narrow the initial search.
2. Present the best matching candidate and meaningful alternatives, including why each
   fits, important limitations, and user-facing consequences. Ask the user to confirm
   the template when that choice determines or changes the user-facing deployment. Do
   not plan or install until confirmed. A user who already named an exact template and
   version has confirmed that choice unless discovery shows it is missing or unsuitable.
3. Call `list_app_template_versions` and retrieve the selected version's current input
   schema with `get_app_template`. Never hard-code a remembered schema. For
   Validate only fields defined by that schema and report unsupported requested fields.
4. Create an install or configure plan. Seed configure input from the app's current
   input before applying a requested patch. Do not mutate the app while planning.
5. Review the stable `plans/apps/<target>/<timestamp>/inputs.yaml`, `plan.json`, and
   `PLAN.md`. Show exact template version, context, app/current revision, dependencies,
   resources, endpoints, validation, destructive effects, expiry, and SHA-256.
   Permit secret references only; never resolve secret values.
6. Apply only with explicit approval, enabled server high-risk policy, and the exact
   unexpired single-use plan. Recheck checksum, parsed YAML, context, template, and
   current revision. Reject edits or drift and create a fresh plan.
7. Wait with a bound, then verify app state, rollout logs, events, output, and
   endpoints. For a service, verify public authenticated access when possible and a
   same-project in-cluster endpoint. If public exposure is unavailable, record the
   exact platform/schema reason and the in-cluster evidence instead. Record bounded
   failure evidence.
8. Create rollback and uninstall as fresh no-payload plans bound to exact app and
   revision state. Require stricter destructive approval/policy and never invent YAML
   for an operation whose SDK/CLI has no file input.
9. Record created app IDs in the ledger and automatically clean up only exact
   ledger-owned IDs. Preserve reviewed inputs and the sanitized execution result.

Never reuse a consumed or failed plan, accept approval for a changed file, or declare
rollout success from submission alone.
