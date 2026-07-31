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

## Apolo CLI coverage audit

Audited against every visible leaf command in Apolo CLI `26.7.1`. Root shortcuts such
as `apolo run`, `ps`, `status`, `logs`, `kill`, `save`, `ls`, `mkdir`, `rm`, `images`,
`push`, and `pull` are aliases of grouped commands and do not require separate MCP
implementations. Commands not listed below are already mapped in the capabilities
matrix to native MCP tools.

### Planned native coverage

- Complete ACL coverage described above:
  `apolo acl add-role`, `grant`, `list-roles`, `ls`, `remove-role`, and `revoke`, plus
  the `apolo share` alias.
- Add true storage usage/quota metadata for `apolo storage df`; `stat_storage` describes
  one path and is not an equivalent.
- Add bounded metadata operations for `apolo storage glob` and `apolo storage tree`,
  with explicit pattern/root, result limit, and truthful truncation.
- Add an exact same-context move/rename operation for `apolo storage mv`. Managed mode
  must authorize the source lifecycle and must not replace an unowned destination.
- Add exact glob semantics for `apolo blob glob`; the current prefix/recursive blob
  listing is not a complete glob equivalent.
- Consider a typed rerun plan for `apolo job generate-run-command`. Return a structured,
  credential-safe job specification suitable for review rather than shell command text.
- Consider native recursive/multi-source modes for `apolo storage cp` and
  `apolo blob cp` only after defining file-count, overwrite, symlink, partial-cleanup,
  and progress semantics. Single-file transfer is already native.

### Deliberately manual or local-client-only

- Interactive/local streams stay in the user's terminal:
  `apolo job attach` / `apolo attach` and `apolo job browse`.
- Local client customization and documentation remain outside MCP:
  `apolo config aliases`, `apolo config docker`, `apolo completion generate`,
  `apolo completion patch`, and `apolo help`.
- Authentication and saved-context mutation remain operator-owned:
  `apolo config login`, `login-headless`, `login-with-token`, `logout`, `show-token`,
  `switch-cluster`, `switch-org`, `switch-project`, plus the root `apolo login` and
  `apolo logout` aliases. `show-token` and token-taking login commands must never be
  exposed as model-visible MCP operations.
### Administrative surface

- Promote the experimental Apolo SDK `_admin` facade used by both `apolo-cli` and the
  MCP read-only admin tools to a stable public typed API, then migrate both callers.
- Administrative mutations remain outside the workload MCP: `add-cluster`,
  `add-cluster-user`, `add-org`, `add-org-cluster`, `add-org-credits`, `add-org-user`,
  `add-project`, `add-project-user`, `add-resource-preset`, `add-user-credits`,
  `remove-cluster`, `remove-cluster-user`, `remove-org`, `remove-org-cluster`,
  `remove-org-user`, `remove-project`, `remove-project-user`,
  `remove-resource-preset`, `set-org-cluster-defaults`, `set-org-cluster-quota`,
  `set-org-credits`, `set-org-defaults`, `set-user-credits`, `set-user-quota`,
  `update-cluster`, `update-cluster-user`, `update-org-cluster`, `update-project`,
  `update-project-user`, and `update-resource-preset`.
- Virtual-cluster credential administration remains excluded:
  `apolo vcluster activate-service-account`, `create-service-account`,
  `delete-service-account`, `list-service-accounts`, and
  `regenerate-service-account`.

## Skills knowledge

- Add a typed MCP binding over the public `apolo-extras` image-build API for the
  non-Flow fallback, replacing the skill's documented `apolo-extras image build` CLI
  command without shelling out. Preserve its remote Kaniko semantics, exact `image:`
  target, build-context confinement, build-argument safety, returned builder-job
  identity, asynchronous monitoring, and lifecycle journal ownership.
- Add a typed Apolo Flow image-build facade when the public Flow API supports it, then
  replace the skill's `apolo-flow build <component>` CLI call without changing its
  dedicated component repositories or `${{ hash_files(...) }}` tags.
