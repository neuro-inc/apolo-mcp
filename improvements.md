# Future improvements

## Flow validation and planning

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

- Fix the remote bake executor's `Cannot close a running event loop` failure in
  `apolo-flow`, then repeat the native `flow_bake_start` lifecycle smoke test.
- Update `apolo-sdk` bucket streaming for current `aiobotocore`, then remove the
  temporary `aiobotocore>=3.4,<3.5` compatibility constraint from `apolo-mcp`.
- Support OCI manifest media types in the SDK image inspection path.
- Resolve the registry `DIGEST_INVALID` response for exact image-tag removal.

## Transfer capabilities

- Keep recursive storage and bucket transfers as explicit CLI fallbacks for the MVP.
  Consider native MCP directory transfers only after defining enforceable file-count,
  total-byte, duration, symlink, overwrite, and partial-cleanup bounds that do not move
  object contents through model context.

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
