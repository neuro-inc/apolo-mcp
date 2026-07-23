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

High-risk operations are disabled by default and require an explicit opt-in by the user
running the local server. Enabling them does not grant Apolo permissions or replace
client approval and Apolo RBAC. Read the generated [safety model](docs/getting-started/safety.md)
before enabling writes, and consult the [capability matrix](docs/capabilities/) for the
complete current contract.

Licensed under Apache-2.0. See [SECURITY.md](SECURITY.md) for private reporting.
