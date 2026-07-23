# Safety and operation types

Apolo MCP uses the identity established by `apolo login` and cannot elevate that
user's Apolo permissions. Tool annotations help MCP clients request appropriate
approval; they are not authorization controls.

The user who launches the local stdio server controls `{high_risk_env}`. Setting it
to `true` enables high-risk tools at the MCP policy layer, but does not approve an
operation or bypass Apolo RBAC. `{policy_file_env}` can point to a local JSON policy
file containing an `enable_high_risk` boolean. The environment variable takes
precedence.

Always verify the explicit cluster, organization, and project before a write. Never
put tokens, secret values, cookies, or service-account credentials in prompts or
tool arguments. Tools that accept or retrieve sensitive material use protected local
sources and sinks instead.

Apolo MCP can create service accounts. The generated one-time token is sent directly
to a new protected local file or a named Apolo secret and is never included in the tool
result. This is a high-risk credential-creation operation even though the credential
value remains outside the model-visible interface.

The tools below are grouped by the skill that guides their use, then by their MCP
operation type. Read-only operations inspect state. Platform-mutating Write and
Destructive operations require explicit approval, the local user's `{high_risk_env}`
opt-in, and the user's existing Apolo permissions. Local App planning appears under
Write but is marked separately: it creates review files without mutating Apolo
resources and does not require the high-risk opt-in.

{skill_sections}
