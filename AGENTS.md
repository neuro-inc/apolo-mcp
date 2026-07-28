# AGENTS.md

## Overview

**`apolo-mcp`** is an MCP (Model Context Protocol) server that gives AI agents the ability to work with the [Apolo platform](https://apolo.us) — running training jobs, deploying long-lived applications, and managing artifacts in storage.

It is a thin adapter layer over `apolo-sdk`. It does **not** shell out to the `apolo` CLI.

## Architecture Decisions

- **Separate package** from `apolo-cli`/`apolo-sdk` to avoid MCP deps polluting the SDK.
- **Uses `apolo-sdk` directly** (not the CLI subprocess) for structured errors and typed responses.
- **Auth via Apolo configuration** — local use normally reads `~/.apolo/` after
  `apolo login`; isolated jobs may use `APOLO_PASSED_CONFIG`. No tool accepts a token.
- **`apolo_sdk.get()`** is the entry point: an async context manager yielding `Client`.
- **`fastmcp`** (the `mcp` package's high-level API) for tool registration and server lifecycle.

## Tool Surface

`src/apolo_mcp/tool_registry.py` is the canonical module registry. Registered FastMCP
metadata generates the complete [capability matrix](docs/capabilities/README.md) and
[tool reference](docs/capabilities/tools/README.md); do not maintain a duplicate tool
table here.

## Package Structure

```
src/apolo_mcp/
  server.py      — FastMCP app, registers all tools, entry point
  _client.py     — apolo_sdk.get() context manager helper
  tools/         — registered capability modules; see tool_registry.py
```

## Tooling

- **uv** for all dependency management, building, and publishing — no pip/setuptools/twine.
- `pyproject.toml` is the single config source (no `setup.cfg` / `setup.py`).
- `uv.lock` is committed for reproducible installs.
- Build backend: **hatchling**.

## Documentation Contract

- Maintain exactly one authoritative source for each fact, interface, or workflow.
  Prefer generating documentation from executable code and structured metadata. When
  generation is impractical, designate one maintained canonical Markdown page. Every
  other README, guide, skill reference, or example must link to that source instead of
  copying its tables, commands, configuration blocks, or normative prose.
- The current source-ownership map is:

  | Subject | Authoritative source | Published documentation |
  |---|---|---|
  | MCP tools, schemas, annotations, and operation types | registered FastMCP metadata via `src/apolo_mcp/tool_registry.py` | generated `docs/capabilities/tools/` and generated sections of the safety model |
  | Policy modes and lifecycle journal behavior | `src/apolo_mcp/policy.py`, `src/apolo_mcp/ledger.py`, and `build-tools/docs-templates/safety.md` | generated `docs/getting-started/safety.md` |
  | Canonical skill names and summaries | skill frontmatter plus `scripts/install_skills.py` | generated `docs/capabilities/skills.md` |
  | Installation, client registration, policy forwarding, skill installation, and R&D runtime bootstrap | `docs/getting-started/installation.md` | the same canonical maintained page |
  | Supported, unsupported, and CLI-fallback capabilities | `docs/capabilities/README.md` | the same canonical maintained page |

- Treat MCP code, registered tool metadata, policy constants, and canonical skill
  metadata as documentation inputs. Any change to a tool name, description, schema,
  annotation, safety behavior, policy, skill, supported capability, or CLI fallback
  must include the corresponding documentation update in the same change.
- Edit maintained prose under `docs/` and generator templates under
  `build-tools/docs-templates/`. Never edit generated files under
  `docs/capabilities/tools/`, `docs/capabilities/skills.md`, or
  `docs/getting-started/safety.md` directly.
- Run `make docs` after relevant code or skill changes, commit the generated Markdown,
  then run `make docs-check` and pre-commit. Generation must not modify GitBook-owned
  navigation such as `docs/SUMMARY.md`.
- Do not create a second installation or client-configuration guide. The canonical
  `docs/getting-started/installation.md` owns all install commands and configuration
  snippets; automated tests reject duplicated markers elsewhere.
- Keep `docs/capabilities/README.md` current and use complete executable CLI commands,
  including the `apolo` or `apolo-flow` entry point. Do not use internal shorthand such
  as `job/root`.

## Development Commands

```bash
make setup       # uv sync --all-groups + pre-commit install
make format      # pre-commit run --all-files
make lint        # format + mypy
make test        # pytest unit tests
make build       # uv build (sdist + wheel into dist/)
make docs        # regenerate code-derived documentation
make docs-check  # verify committed generated documentation is current
make publish     # uv publish (set UV_PUBLISH_TOKEN)
```

## Running the Server

```bash
# stdio (default for Claude Desktop / Claude Code MCP config)
python -m apolo_mcp

# or via installed entry point
apolo-mcp
```

## Client configuration

Do not duplicate Codex or Claude Code setup here. Use the
[installation and client configuration](docs/getting-started/installation.md).

## Key Conventions

- Storage paths: accept `apolo://...` URIs or bare paths like `/my/dir` (resolved against the current cluster/org/project from config).
- All tools return plain dicts or strings — no SDK objects cross the boundary.
- Async tools throughout; FastMCP handles the event loop.
- Python ≥ 3.10 (matches `apolo-sdk` minimum).
- Ruff owns formatting and import sorting; `apolo_mcp` is the configured first-party package.
