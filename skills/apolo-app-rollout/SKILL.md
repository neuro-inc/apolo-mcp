---
name: apolo-app-rollout
description: Discover, plan, review, apply, verify, roll back, and uninstall Apolo Apps with immutable checksum-bound files and state-drift protection. Use for app templates, revisions, service-deployment, HTTP services, rollout health, endpoints, reconfiguration, rollback, or uninstall requests.
---

# Apolo app rollout

1. Resolve exact context, then discover template versions and retrieve the selected
   version's current input schema. Never hard-code a remembered schema.
2. For `service-deployment`, validate schema paths for image/tag, preset,
   command/args/env, storage and secret references, ports/ingress/auth/exposure,
   autoscaling, and startup/readiness/liveness probes. Report unsupported fields.
3. Create an install or configure plan. Seed configure input from the app's current
   input before applying a requested patch. Do not mutate the app while planning.
4. Review the stable `plans/apps/<target>/<timestamp>/inputs.yaml`, `plan.json`, and
   `PLAN.md`. Show exact template version, context, app/current revision, dependencies,
   resources, endpoints, validation, destructive effects, expiry, and SHA-256.
   Permit secret references only; never resolve secret values.
5. Apply only with explicit approval, enabled server high-risk policy, and the exact
   unexpired single-use plan. Recheck checksum, parsed YAML, context, template, and
   current revision. Reject edits or drift and create a fresh plan.
6. Wait with a bound, then verify app state, rollout logs, events, output, and
   endpoints. For a service, verify public authenticated access when possible and a
   same-project in-cluster endpoint. If public exposure is unavailable, record the
   exact platform/schema reason and the in-cluster evidence instead. Record bounded
   failure evidence.
7. Create rollback and uninstall as fresh no-payload plans bound to exact app and
   revision state. Require stricter destructive approval/policy and never invent YAML
   for an operation whose SDK/CLI has no file input.
8. Record created app IDs in the ledger and automatically clean up only exact
   ledger-owned IDs. Preserve reviewed inputs and the sanitized execution result.

Never reuse a consumed or failed plan, accept approval for a changed file, or declare
rollout success from submission alone.
