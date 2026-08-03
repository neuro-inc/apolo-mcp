# R&D session grant and launch planning

## RBAC questionnaire

Ask only questions not already answered by the user:

1. Which exact cluster, organization, and project contain the experiment?
2. Which existing storage paths, images, buckets, disks, secrets, jobs, Apps, or Flow
   resources must the agent inspect?
3. Which resource families or exact parent URIs must it create or change?
4. May it delete resources? Apolo `write` includes deletion; if deletion is unacceptable,
   redesign the scope instead of describing `write` as update-only.
5. Must it launch nested jobs or Flow workloads? What preset, quota, lifespan, schedule
   timeout, and output URI bound those workloads?
6. Does it truly need RBAC delegation? The safe answer is no; ordinary Apolo MCP work
   does not require `manage`.
7. Which persistent storage path should be mounted read-write at `/workspace`, and
   which subdirectory should contain final outputs?
8. Should the job be launched now or should setup stop after producing reviewed commands?

## Grant plan

Use complete URIs from the resolved context. Prefer exact resources or narrow prefixes.
Show the plan before changing ACLs:

| URI | Permission | Needed operation | Deletion possible | Reason |
|---|---|---|---|---|
| `<RESOURCE_URI>` | `read` or `write` | `<OPERATION>` | yes/no | `<JUSTIFICATION>` |

Apply and verify only after explicit user confirmation:

```console
apolo acl grant <RESOURCE_URI> <SERVICE_ACCOUNT_ROLE> read
apolo acl grant <WRITABLE_RESOURCE_URI> <SERVICE_ACCOUNT_ROLE> write
apolo acl ls -u <SERVICE_ACCOUNT_ROLE> --full-uri
```

Do not create a separate custom role: every Apolo service account already has the role
returned as `account.role`. The `apolo acl ls -u` option accepts either a user or a role;
use this exact returned role to inspect the account's grants. Do not use `manage` merely
to simplify setup.

## Bounded job template

Suggest `node:22-bookworm-slim` as the MVP bootstrap base and record that exact mutable
tag. Apolo job launch does not currently accept Docker image digest references. This is
not an Apolo-supported R&D image; use another reviewed slim glibc-based image when
needed. Start a long-running workspace job with `sleep infinity`, then follow the shared
[R&D runtime bootstrap](../../apolo-rnd-session-operate/references/installation.md)
through `apolo exec`.

```console
apolo run \
  --cluster <CLUSTER> \
  --org <ORG> \
  --project <PROJECT> \
  --preset <PRESET> \
  --name <RND_JOB_NAME> \
  --life-span <BOUNDED_LIFESPAN> \
  --schedule-timeout <BOUNDED_TIMEOUT> \
  --detach \
  --volume storage:<RND_WORKSPACE_PATH>:/workspace:rw \
  --workdir /workspace \
  --env APOLO_CONFIG=/tmp/apolo-agent-config \
  --env APOLO_MCP_POLICY_MODE=full \
  --env APOLO_PASSED_CONFIG=secret:<SERVICE_ACCOUNT_SECRET> \
  node:22-bookworm-slim -- sleep infinity
```

Create or select the exact storage path before launch. Reuse the same path when a
replacement job must continue the work. Keep repositories, generated artifacts, and a
sanitized `HANDOFF.md` below `/workspace`; keep service-account and coding-provider
credentials outside it. Add separately named provider-secret mounts only when needed.
Never add `--pass-config`; it forwards the launching user's credentials.

## Handoff

Use `exec_job` for bounded non-interactive bootstrap, inventory, and verification
commands in the MCP-owned running job. Return local CLI commands with the exact job ID
for interactive shell/tmux access and operator-controlled lifecycle actions:

```console
apolo status <JOB_ID>
apolo exec <JOB_ID> -- bash
apolo logs <JOB_ID>
apolo kill <JOB_ID>
```

When the in-job session uses optional `tmux`, also return:

```console
apolo exec <JOB_ID> -- tmux list-sessions
apolo exec <JOB_ID> -- tmux attach-session -t <SESSION_NAME>
```

Also return a non-secret handoff object for the in-job skill:

```json
{
  "job_id": "<EXACT_JOB_ID>",
  "service_account_id": "<EXACT_ACCOUNT_ID>",
  "service_account_role": "<EXACT_ROLE>",
  "context": {"cluster": "<CLUSTER>", "org": "<ORG>", "project": "<PROJECT>"},
  "grants": [{"uri": "<RESOURCE_URI>", "permission": "read-or-write"}],
  "workspace": "<ABSOLUTE_WORKSPACE>",
  "output_uri": "<EXACT_OUTPUT_URI>"
}
```

Return the credential secret's name separately, plus lifespan and cleanup commands for
grants, secret, and account. Never return the token.
