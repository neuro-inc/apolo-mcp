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

To select the policy per Codex session instead of fixing it globally, add only
the environment-variable allowlist to Codex's `config.toml`:

```toml
[mcp_servers.apolo]
command = "apolo-mcp"
env_vars = ["APOLO_MCP_POLICY_MODE"]
```

Then choose the mode when starting each session:

```console
APOLO_MCP_POLICY_MODE=managed codex
```

Leaving the variable unset keeps the server in its default `read-only` mode.

For Claude Code:

```console
claude mcp add apolo \
  --scope user \
  -e 'APOLO_MCP_POLICY_MODE=${APOLO_MCP_POLICY_MODE:-read-only}' \
  -- apolo-mcp
claude mcp list
```

Claude Code expands that environment value when it starts the MCP server, so the
policy can also be selected per launch with `APOLO_MCP_POLICY_MODE=managed claude`
or `APOLO_MCP_POLICY_MODE=full claude`. An ordinary `claude` launch remains
`read-only`.

The equivalent module entry point is `python -m apolo_mcp`. Local stdio is the only
supported transport. Shared-credential remote service operation is unsupported.

See the [Apolo MCP documentation](docs/README.md) for prerequisites, safety controls,
the capability matrix, generated tools and skills catalogs, and task-oriented guides.

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
Trusted Publishing; local `dist/` contents are disposable. Maintainers should follow
[RELEASE.md](RELEASE.md) for changelog, validation, signing, and publication.

Release versions use `YY.MM.NN`: the two-digit year, the month without a leading zero,
and a zero-based release sequence within that month. For example, `v26.7.0` is the
first July 2026 release, and its Python package version is `26.7.0`.

## Safety configuration

The server defaults to `APOLO_MCP_POLICY_MODE=read-only`. Use `managed` to create
resources and manage only their exact journaled lifecycles, or `full` to permit
mutations of any exact-context resource. Policy never grants Apolo permissions or
replaces Apolo RBAC, and it is not a security boundary against an agent with direct
CLI or SDK access. **Never run `full` with a personal administrator, owner, or other
broadly privileged Apolo account.** Use a dedicated least-privileged service account
and follow the [full-mode service-account guide](docs/guides/full-mode-service-account.md).
For Codex, forward the variable name with `env_vars`; for Claude
Code, use its `${APOLO_MCP_POLICY_MODE:-read-only}` environment expansion. Select the
value per launch rather than permanently storing `managed` or `full`, unless that is
intentionally the user's default. Read the generated
[safety model](docs/getting-started/safety.md) before enabling writes, and consult the
[capability matrix](docs/capabilities/) for the complete current contract.

Licensed under Apache-2.0. See [SECURITY.md](SECURITY.md) for private reporting.
