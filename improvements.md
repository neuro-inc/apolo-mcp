# Future improvements

## Flow validation and planning

- Add a public launch-only remote-bake operation to `apolo-flow`, then make
  `flow_bake_start` return after executor submission instead of waiting for the remote
  executor to exit. Preserve separate get, logs, and bounded wait monitoring.
- Add a read-only `flow_live_validate` operation with semantics equivalent to
  `apolo-flow run --dry-run`: resolve the selected job, suffix, parameters,
  expressions, image, volumes, environment names, and final launch plan without
  starting a platform job.
- Return a typed, credential-redacted plan instead of forwarding the CLI's rendered
  command. Literal secret values and resolved credentials must never cross MCP.
- Add a read-only `flow_bake_validate` operation that validates batch YAML, required
  parameters, expressions, task dependencies, actions/modules, images, volumes, and
  executor configuration without creating a bake or executor job.
- Extend the public `apolo-flow` programmatic API with structured live dry-run and
  batch validation methods. MCP should consume that API rather than shelling out to
  the CLI or depending on private runner internals.

## Upstream compatibility

- Publish versioned `apolo-sdk` and `apolo-flow` public API documentation to Context7.
  Until those references are available, MCP development must validate against the
  installed packages' public signatures and the repositories' maintained references.
- Support Docker image digest references in Apolo job launch, then restore immutable
  image pinning in the R&D setup and operation workflows.
- Package `flow-schema.json` and `project-schema.json` in the `apolo-flow` wheel, then
  prefer package resources for offline MCP validation while retaining release-pinned
  raw GitHub URLs in YAML language-server comments.
- Support OCI manifest media types in the SDK image inspection path.
- Resolve the registry `DIGEST_INVALID` response for exact image-tag removal.

## Transfer capabilities

- Keep recursive storage and bucket transfers as explicit CLI fallbacks for the MVP.
  Consider native MCP directory transfers only after defining enforceable file-count,
  total-byte, duration, symlink, overwrite, and partial-cleanup bounds that do not move
  object contents through model context.

## Access control capabilities

- Cover the complete public `apolo acl` command family with typed SDK-backed MCP
  operations: ACL listing in both directions, role listing and inspection, role
  creation and removal, and exact grant and revoke operations. Keep the operation
  names and returned models resource-oriented rather than mirroring unstructured CLI
  output.
- Make all discovery and inspection operations bounded and read-only. Support the
  equivalents of `apolo acl ls`, `apolo acl ls -u <PRINCIPAL> [--shared] --full-uri`,
  and `apolo acl list-roles`, returning full resource/role URIs, permissions, and
  principals. Document the distinction between resources a principal shares and
  resources shared with it, plus expected own-role and system-wide shared entries.
- For role creation/removal and
  `apolo acl grant <URI> <PRINCIPAL> <read|write|manage>` / `apolo acl revoke`, require
  exact resource and principal URIs, explicit context, reviewed intent, mutation-policy
  gating, and credential-free results. Never accept tokens or secret values.
- Model a newly created ACL entry as its own append-only lifecycle record. In managed
  mode, refuse to replace or widen an existing grant, journal only an exact new grant,
  and allow revocation only of that exact active journal entry. Likewise, remove only
  roles created and journaled by the same MCP lifecycle; reserve arbitrary ACL or role
  mutation for full mode.
- Use the typed listing operations to show the RBAC diff before mutation and verify it
  immediately afterward. This should remove the R&D setup skill's remaining ACL CLI
  fallback once the full family is implemented.

## Job execution capabilities

- Add a typed, bounded operation equivalent to `apolo exec <JOB_ID> -- <COMMAND>`. It
  must verify the exact job and context, accept an executable plus argument list without
  shell interpolation, enforce timeout and output-byte limits, redact credentials, and
  require managed ownership for mutations. Start without interactive TTY support;
  retain the CLI as the explicit fallback for interactive sessions.

## Skills knowledge

- Teach the packaged workload skills an Apolo-native container build workflow. When
  an agent needs a new image, prefer
  `apolo-extras image build <context> <image-uri>` (remote Kaniko build) or an
  `apolo-flow` live build over host-local Docker. Local Docker should be used only
  when the user explicitly requests it.
- Document how the skill should select a unique image URI, inspect and record the
  remote builder job and produced tag, monitor the build separately, and clean only
  the exact build job and tag created by the workflow.
- Once the Flow programmatic API exposes a suitable typed image-build operation,
  prefer an MCP-backed skill path instead of invoking a CLI fallback.
