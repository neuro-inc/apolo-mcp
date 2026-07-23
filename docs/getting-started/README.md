# Getting started

## Prerequisites

Before installing Apolo MCP, you need:

- Python 3.10 or newer;
- an Apolo account with access to a cluster, organization, and project;
- the Apolo CLI installed and an active local session created with `apolo login`;
- an MCP client that can start a local stdio server, such as Codex or Claude Code.

Docker is required only for image push and pull operations. File-based plans, protected
credential sinks, and local transfers require a writable workspace. Platform operations
remain subject to the permissions and quotas of the logged-in Apolo user.

## Install

Install the published package with `uv`:

```console
uv tool install apolo-mcp
apolo login
```

Register it with Codex:

```console
codex mcp add apolo -- apolo-mcp
codex mcp list
```

Or register it with Claude Code:

```console
claude mcp add apolo --scope user -- apolo-mcp
claude mcp list
```

The equivalent module entry point is `python -m apolo_mcp`. The client starts the
server locally over stdio; no Apolo MCP daemon or remote endpoint is required.

## Verify the context

Ask the client to call `get_apolo_context`. Confirm the reported cluster,
organization, and project before requesting an operation. Explicit context supplied to
a tool applies only to that call and never changes the defaults saved by the Apolo CLI.

Next, read the [safety model](safety.md), then review the current
[capabilities](../capabilities/).
