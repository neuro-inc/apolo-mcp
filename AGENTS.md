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

## Workload boundary

- Use **jobs** for bounded R&D, experiments, builds, migrations, batch work, and tests.
- Use **Apps** for long-running deployments and managed services, including databases,
  APIs, and web UIs. Do not emulate a deployment by keeping a job alive.
- A remote image builder may run as a job because the build is bounded; deploy the
  resulting image through an App template such as Service Deployment.

## Tool Surface

`src/apolo_mcp/catalog.py` is the canonical ordered catalog for capability groups,
runtime registration, documentation descriptions, and owning skills. Registered
FastMCP metadata remains canonical for individual tool schemas and operation types. It
generates the complete [tool reference](docs/capabilities/tools/README.md); do not
maintain a duplicate tool table here.

## Package Structure

```
src/apolo_mcp/
  cli.py         — server and packaged-skill command-line interface
  catalog.py     — declarative capability and skill ownership catalog
  server.py      — FastMCP app and stdio server lifecycle
  skill_installer.py — packaged skill installation implementation
  _client.py     — apolo_sdk.get() context manager helper
  workspace.py   — server-controlled local filesystem confinement
  tools/         — registered capability modules; see catalog.py
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
  | Capability order, descriptions, runtime registration, and skill ownership | `src/apolo_mcp/catalog.py` | generated tool navigation, skills catalog, and safety sections |
  | MCP tool names, schemas, annotations, and operation types | registered FastMCP metadata in `src/apolo_mcp/tools/` | generated `docs/capabilities/tools/` and generated sections of the safety model |
  | Policy modes and lifecycle journal behavior | `src/apolo_mcp/policy.py`, `src/apolo_mcp/ledger.py`, and `build-tools/docs-templates/safety.md` | generated `docs/getting-started/safety.md` |
  | Canonical skill names and summaries | `src/apolo_mcp/catalog.py`, skill frontmatter, and `agents/openai.yaml` | generated `docs/capabilities/skills.md` |
  | Complete skill instructions and supporting references | `skills/*/SKILL.md` and `skills/*/references/` | generated `docs/capabilities/skills/*/` |
  | Installation, client registration, policy forwarding, startup-directory confinement, and skill installation | `build-tools/docs-templates/installation.md` | generated `docs/getting-started/installation.md` |
  | Full-mode dedicated-service-account workflow | `build-tools/docs-templates/full-mode-service-account.md` | generated `docs/guides/full-mode-service-account.md` |
  | Self-contained R&D runtime and isolated-job configuration | `build-tools/docs-templates/rnd-runtime.md` | embedded unchanged in the generated full-mode guide and packaged R&D skill reference |
  | Supported, unsupported, and CLI-fallback capabilities | `docs/capabilities/README.md` | the same canonical maintained page |

- Treat MCP code, registered tool metadata, policy constants, and canonical skill
  metadata as documentation inputs. Any change to a tool name, description, schema,
  annotation, safety behavior, policy, skill, supported capability, or CLI fallback
  must include the corresponding documentation update in the same change.
- Edit maintained prose under `docs/` and generator templates under
  `build-tools/docs-templates/`. Never edit generated files under
  `docs/capabilities/tools/`, `docs/capabilities/skills.md`,
  `docs/capabilities/skills/`, or
  `docs/getting-started/installation.md`, `docs/getting-started/safety.md`,
  `docs/guides/full-mode-service-account.md`, or the packaged R&D skill's generated
  `references/installation.md` directly.
- Run `make docs` after relevant code or skill changes, commit the generated Markdown,
  then run `make docs-check` and pre-commit. Generation must not modify GitBook-owned
  navigation such as `docs/SUMMARY.md`.
- Do not create a second installation or client-configuration guide. The canonical
  `build-tools/docs-templates/installation.md` owns all install commands and
  configuration snippets. Generated and maintained pages should link to the published
  `docs/getting-started/installation.md`.
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
- Centralize all local path confinement in `src/apolo_mcp/workspace.py`; document its
  behavior only in `build-tools/docs-templates/safety.md`.
- All tools return plain dicts or strings — no SDK objects cross the boundary.
- Async tools throughout; FastMCP handles the event loop.
- Python ≥ 3.10 (matches `apolo-sdk` minimum).
- Ruff owns formatting and import sorting; `apolo_mcp` is the configured first-party package.
