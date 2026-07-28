# Full mode with a dedicated service account

`APOLO_MCP_POLICY_MODE=full` removes Apolo MCP's ownership restriction and allows the
agent to mutate any supported resource that its Apolo identity may access. **Never run
full mode using a personal owner, administrator, or otherwise broadly privileged Apolo
account.** A prompt mistake, malicious repository content, compromised dependency, or
shell command could then exercise all of that account's permissions, including outside
the intended experiment.

For unattended or headless use, create a dedicated service account, grant its role only
the permissions required for one use case, store its one-time token in an Apolo secret,
and run the agent inside a bounded R&D job. The MCP policy remains useful as an
operational guardrail, while the service account's RBAC becomes the actual security
boundary.

Use `$apolo-rnd-session-setup` from the trusted local `managed` session to plan and
provision this workflow. Use `$apolo-rnd-session-operate` inside the resulting job to
verify isolation, configure Codex or Claude Code, start `tmux`, and hand monitoring
instructions back to the operator.

## 1. Design the least-privilege grant set

Resolve one exact cluster, organization, and project before creating anything. Answer
these questions for the specific experiment:

| Question | Safe default |
|---|---|
| Which exact resource URIs must the agent inspect? | Grant `read` only on those resources or narrow prefixes. |
| Which resources must it create, change, or delete? | Grant `write` only on the narrowest applicable parent URI. Apolo `write` includes deletion. |
| Must it change sharing or RBAC? | No. Do not grant `manage`; Apolo MCP does not expose ACL administration. |
| Must it launch nested jobs or Flow runs? | Grant workload access only in the experiment project and enforce job lifetime and quota limits separately. |
| Must it handle storage, images, disks, buckets, or secrets? | Grant each resource family independently; do not grant a whole project merely for convenience. |
| Does it need the bootstrap-token secret after the job starts? | No additional MCP grant is normally needed. The trusted launcher mounts that secret into the job. |

Use complete Apolo resource URIs. The service account has its own role, returned as
`account.role` when it is created. Grant resources directly to that role:

```console
apolo acl grant <RESOURCE_URI> <SERVICE_ACCOUNT_ROLE> read
apolo acl grant <WRITABLE_RESOURCE_URI> <SERVICE_ACCOUNT_ROLE> write
apolo acl ls -u <SERVICE_ACCOUNT_ROLE> --full-uri
```

Review the final `apolo acl ls` output before launching the agent. Prefer several exact
grants over one broad grant. Use `manage` only when the use case genuinely requires the
service account to delegate permissions; ordinary full-mode MCP operation does not.

## 2. Create the account without exposing its token

Perform this setup in a trusted interactive session, not in the headless agent being
provisioned. Start Apolo MCP in `managed` mode and call
[`create_service_account`](../capabilities/tools/service-accounts.md#create_service_account)
with values equivalent to:

```json
{
  "destination_type": "secret",
  "destination_name": "mcp-full-mode-config",
  "name": "mcp-full-mode-agent",
  "cluster": "<CLUSTER>",
  "org": "<ORG>",
  "project": "<PROJECT>"
}
```

The tool writes the complete one-time service-account token directly to the named
Apolo secret and returns only safe account metadata, including `account.role`. The
complete token embeds the API URL and default context and is accepted by Apolo SDK as
`APOLO_PASSED_CONFIG`. It must never be pasted into a prompt, shell command, log, source
file, or MCP configuration.

Grant the reviewed resource set to the returned role using the trusted operator's
`apolo acl grant` commands. ACL administration intentionally remains outside the MCP
tool surface, so the agent cannot grant itself more access through Apolo MCP.

## 3. Configure MCP inside the agent job

Use the canonical installation guide's
[isolated R&D job configuration](../getting-started/installation.md#isolated-rd-job-configuration).
It defines the job-local Codex and Claude Code environment forwarding once. Keep that
configuration inside the isolated image or workspace; never make `full` the user's
global desktop default.

## 4. Launch a bounded R&D job

Build or select an agent image containing the chosen agent, `apolo-mcp`, and the Apolo
SDK. Supply the coding-agent provider credentials separately according to that
provider's headless-operation guidance. Launch the job with explicit context, a bounded
lifetime, a clean Apolo configuration directory, and the service-account secret:

```console
apolo run \
  --cluster <CLUSTER> \
  --org <ORG> \
  --project <PROJECT> \
  --preset <PRESET> \
  --name mcp-full-mode-agent \
  --life-span 8h \
  --detach \
  --env APOLO_CONFIG=/tmp/apolo-agent-config \
  --env APOLO_MCP_POLICY_MODE=full \
  --env APOLO_PASSED_CONFIG=secret:mcp-full-mode-config \
  <AGENT_IMAGE> -- <AGENT_COMMAND>
```

Do **not** use `--pass-config`: that would pass the launching user's current Apolo
credentials into the job and defeat the service-account isolation. Do not mount the
launcher's `~/.apolo` directory. Verify from inside the job that Apolo reports the
service-account identity and expected context before starting autonomous work.

The token is available to processes inside the job, including the agent, because that
is required for authentication. It is therefore not protected from the agent itself;
the protection comes from the token belonging to a narrowly permissioned service
account. Restrict outbound network access where practical, avoid printing environment
variables, and terminate the job when the experiment completes.

## 5. Audit and clean up

After the run:

1. Inspect the service account's grants and the resources changed during the run.
2. Remove or revoke grants that are no longer needed.
3. Delete the R&D job and experiment resources according to the project's retention
   policy.
4. Delete the bootstrap secret and service account when the identity is no longer
   required. Treat persistent service accounts as long-lived credentials that need
   ownership, review, and rotation procedures.

The local Apolo MCP lifecycle journal can help explain cooperating MCP actions, but it
is not a tamper-proof security or compliance audit log. Use Apolo-side audit facilities
and organizational controls when authoritative records are required.
