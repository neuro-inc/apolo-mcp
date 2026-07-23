# AGENTS.md

## Overview

**`apolo-mcp`** is an MCP (Model Context Protocol) server that gives AI agents the ability to work with the [Apolo platform](https://apolo.us) — running training jobs, deploying long-lived applications, and managing artifacts in storage.

It is a thin adapter layer over `apolo-sdk`. It does **not** shell out to the `apolo` CLI.

## Architecture Decisions

- **Separate package** from `apolo-cli`/`apolo-sdk` to avoid MCP deps polluting the SDK.
- **Uses `apolo-sdk` directly** (not the CLI subprocess) for structured errors and typed responses.
- **Auth via `~/.apolo/`** — user must already be logged in with `apolo login`. No token params.
- **`apolo_sdk.get()`** is the entry point: an async context manager yielding `Client`.
- **`fastmcp`** (the `mcp` package's high-level API) for tool registration and server lifecycle.

## Tool Surface

| Module | Tool | SDK call |
|---|---|---|
| `jobs` | `run_job` | `client.jobs.start()` |
| `jobs` | `list_jobs` | `client.jobs.list()` |
| `jobs` | `get_job_status` | `client.jobs.status()` |
| `jobs` | `get_job_logs` | `client.jobs.monitor()` |
| `jobs` | `kill_job` | `client.jobs.kill()` |
| `apps` | `list_app_templates` | `client.apps.list_templates()` |
| `apps` | `install_app` | `client.apps.install()` |
| `apps` | `list_apps` | `client.apps.list()` |
| `apps` | `get_app` | `client.apps.get()` |
| `apps` | `uninstall_app` | `client.apps.uninstall()` |
| `storage` | `list_files` | `client.storage.list()` |
| `storage` | `upload_text` | `client.storage.create()` |
| `storage` | `download_text` | `client.storage.open()` |
| `storage` | `make_dir` | `client.storage.mkdir()` |
| `storage` | `delete_path` | `client.storage.rm()` |
| `disks` | `list_disks` | `client.disks.list()` |
| `disks` | `create_disk` | `client.disks.create()` |
| `disks` | `delete_disk` | `client.disks.rm()` |

## Package Structure

```
src/apolo_mcp/
  server.py      — FastMCP app, registers all tools, entry point
  _client.py     — apolo_sdk.get() context manager helper
  tools/
    jobs.py
    apps.py
    storage.py
    disks.py
```

## Tooling

- **uv** for all dependency management, building, and publishing — no pip/setuptools/twine.
- `pyproject.toml` is the single config source (no `setup.cfg` / `setup.py`).
- `uv.lock` is committed for reproducible installs.
- Build backend: **hatchling**.

## Documentation Contract

- Treat MCP code, registered tool metadata, policy constants, and canonical skill
  metadata as documentation inputs. Any change to a tool name, description, schema,
  annotation, safety behavior, policy, skill, supported capability, or CLI fallback
  must include the corresponding documentation update in the same change.
- Edit maintained prose under `docs/` and generator templates under
  `build-tools/docs-templates/`. Never edit generated `docs/capabilities/tools.md`,
  `docs/capabilities/skills.md`, or `docs/getting-started/safety.md` directly.
- Run `make docs` after relevant code or skill changes, commit the generated Markdown,
  then run `make docs-check` and pre-commit. Generation must not modify GitBook-owned
  navigation such as `docs/SUMMARY.md`.
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

## Claude Desktop / Claude Code Config

Add to your MCP config:
```json
{
  "mcpServers": {
    "apolo": {
      "command": "apolo-mcp",
      "args": []
    }
  }
}
```

Requires `apolo login` to have been run first.

## Key Conventions

- Storage paths: accept `apolo://...` URIs or bare paths like `/my/dir` (resolved against the current cluster/org/project from config).
- All tools return plain dicts or strings — no SDK objects cross the boundary.
- Async tools throughout; FastMCP handles the event loop.
- Python ≥ 3.10 (matches `apolo-sdk` minimum).
- Ruff owns formatting and import sorting; `apolo_mcp` is the configured first-party package.
