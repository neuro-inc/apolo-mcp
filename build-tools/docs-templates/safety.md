# Safety and operation types

Apolo MCP uses the identity established by `apolo login` and cannot elevate that
user's Apolo permissions. Tool annotations help MCP clients present suitable risk
prompts; they are hints, not authorization controls.

The user who launches the local stdio server controls `{policy_mode_env}`. It accepts
exactly three values:

- `read-only` is the default. Platform mutations are denied; reads and local Apps
  planning remain available.
- `managed` allows creation of new resources. Updates and deletions are allowed only
  for the exact resource type, immutable identifier, cluster, organization, and
  project with an active creation lifecycle in the MCP journal.
- `full` allows mutations of any exact-context resource, subject to the authenticated
  identity's Apolo RBAC. It must not be used with a personal owner, administrator, or
  otherwise broadly privileged account. Use a dedicated, least-privileged service
  account; follow the [full-mode service-account guide](../guides/full-mode-service-account.md).

For example, `APOLO_MCP_POLICY_MODE=managed apolo-mcp` starts the server in managed
mode. The server reads the policy once at process startup; changing its environment
afterward has no effect. There is no policy-file override and no tool argument that can
elevate or bypass the running server policy.

For Codex, configure forwarding without selecting a permanent policy value:

```toml
[mcp_servers.apolo]
command = "apolo-mcp"
env_vars = ["APOLO_MCP_POLICY_MODE"]
```

Then use `APOLO_MCP_POLICY_MODE=managed codex` or
`APOLO_MCP_POLICY_MODE=full codex` for that launch only. An ordinary `codex` launch
leaves the variable unset and therefore starts Apolo MCP in `read-only` mode. Restart
Codex after changing MCP configuration so a new server process receives the setting.

For Claude Code, register the same per-launch behavior with a dynamic environment
expansion:

```console
claude mcp add apolo \
  --scope user \
  -e 'APOLO_MCP_POLICY_MODE=${{APOLO_MCP_POLICY_MODE:-read-only}}' \
  -- apolo-mcp
```

Claude Code expands the expression when starting the MCP subprocess. Use an ordinary
`claude` launch for `read-only`, or prefix the launch with
`APOLO_MCP_POLICY_MODE=managed` or `APOLO_MCP_POLICY_MODE=full`. The `local`, `project`,
and `user` scopes control where the MCP registration applies; they do not select the
policy. Restart Claude Code after changing MCP configuration.

Successful mutations are written to an append-only lifecycle journal as `created`,
`updated`, or `deleted` actions. By default it is stored at
`~/.local/state/apolo-mcp/ledger.jsonl`; `{ledger_env}` overrides the path. The journal
contains only resource identity, exact Apolo context, operation, action, and timestamp,
never credentials. A `deleted` action closes that ownership lifecycle; only a later
MCP-recorded `created` action establishes a new managed lifecycle for the same identity.

"Append-only" describes how a cooperating Apolo MCP process writes the file: it adds
lifecycle records and never edits earlier records during normal operation. The journal
is not cryptographically signed, remotely attested, or protected from another process
running as the same operating-system user. Such a process may replace, truncate, or
forge it. Therefore it supports managed-mode accident containment and operational
diagnostics; it is not a tamper-proof audit log, compliance record, or authorization
boundary against an agent with unrestricted shell access.

The mutation policy as a whole is a guardrail, not a replacement for credential
isolation. An agent that can use the Apolo CLI or SDK directly can bypass MCP policy.
The effective security boundary is the Apolo identity and its RBAC. For unattended or
headless `full` operation, run the agent with only a dedicated service account's token
and grant that role access solely to the required contexts and resources.

Always verify the explicit cluster, organization, and project before a write. Never
put tokens, secret values, cookies, or service-account credentials in prompts or tool
arguments. Tools that accept or retrieve sensitive material use protected local
sources and sinks instead.

Apolo MCP can create service accounts. The generated one-time token is sent directly
to a new protected local file or a named Apolo secret and is never included in the
model-visible tool result. Creation still requires `managed` or `full` policy and the
user's Apolo permissions.

The tools below are grouped by the skill that guides their use, then by their MCP
operation type. Read-only operations inspect state. Write and Destructive operations
are governed by the selected policy mode and Apolo RBAC; an MCP client may additionally
show a confirmation based on annotations. Local Apps planning appears under Write but
only creates review files and remains available in `read-only` mode.

{skill_sections}
