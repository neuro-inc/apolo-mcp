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

## Read-only operations

These operations inspect platform state and do not modify local or remote resources.

{read_only_tools}

## Local planning operations

These operations create review files in the configured workspace but do not mutate
Apolo resources.

{planning_tools}

## Write operations

These operations can create or modify resources. They require explicit user approval,
the local user's `{high_risk_env}` opt-in, and the user's existing Apolo permissions.

{write_tools}

## Destructive operations

These operations can remove resources or stop work. They require explicit approval,
the local user's `{high_risk_env}` opt-in, and the user's existing Apolo permissions.

{destructive_tools}
