# apolo-mcp

`apolo-mcp` is a local stdio Model Context Protocol server for typed Apolo platform
operations. It is a thin adapter over `apolo-sdk`: it never shells out to the CLI and
uses the identity, permissions, and selected defaults from an existing `apolo login`.

The server exposes bounded context discovery and typed platform operations. Every
operational tool accepts explicit `cluster`, `org`, and `project` inputs where the
SDK supports them; explicit inputs never modify the user's saved context. Secret
values and one-time credentials are intentionally outside the model-visible interface.

## Install and run

Python 3.10 or newer is required.

```console
uv tool install apolo-mcp
apolo login
apolo-mcp
```

For Codex:

```console
codex mcp add apolo -- apolo-mcp
codex mcp list
```

For Claude Code:

```console
claude mcp add apolo --scope user -- apolo-mcp
claude mcp list
```

The equivalent module entry point is `python -m apolo_mcp`. Local stdio is the only
supported transport in this release. A future remote provider must propagate and
validate each user's identity; a shared platform credential is explicitly unsupported.

## Development

```console
make setup
make lint
make test
make build
```

Dependencies are locked with `uv.lock`. Tests use an `ApoloClientProvider` fake and do
not require credentials. Integration tests, when added, must be opt-in and target an
isolated non-production project.

Wheel and source-distribution files are generated release artifacts and must not be
committed. Tagged releases build them in CI and publish the exact outputs to PyPI via
Trusted Publishing; local `dist/` contents are disposable.

## Safety configuration

High-risk operations are disabled by default. Administrators may set
`APOLO_MCP_ENABLE_HIGH_RISK=true` or point `APOLO_MCP_POLICY_FILE` to a JSON document
such as `{"enable_high_risk": true}`. The environment override wins. This policy is
an additional server control; it does not replace client approval or Apolo RBAC.

Apps install/configure/rollback/uninstall use short-lived, checksum-bound plans under
`plans/apps/`. Set `APOLO_MCP_PLAN_ROOT` when plans must live at a specific review
location. Created-resource ownership records use `APOLO_MCP_LEDGER_PATH`; cleanup must
match an exact ledger type, ID, and context and never relies on a resource name.

Interactive job attach and port forwarding, and high-volume binary transfers, remain
local CLI workflows rather than ordinary MCP tool results. See the
[capability matrix](docs/capability-matrix.md) for the complete contract and
[remote HTTP architecture](docs/remote-http-architecture.md) for explicitly deferred
service work.

Licensed under Apache-2.0. See [SECURITY.md](SECURITY.md) for private reporting.
