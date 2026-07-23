# Apolo MCP documentation

Apolo MCP is a local [Model Context Protocol](https://modelcontextprotocol.io/)
server that gives an AI client typed, bounded access to Apolo platform operations. It
uses the identity and permissions from the user's existing `apolo login`; it does not
shell out to the CLI, return credential values to the model, or grant additional
permissions. It can create service accounts, but their one-time tokens are written
directly to an approved protected file or Apolo secret instead of entering the model
conversation.

The server is intended for discovering platform context, running and observing
workloads, operating Applications, and managing supported project resources. It does
not provide arbitrary shell or HTTP access, Kubernetes administration, identity or RBAC
administration, model-visible secrets, interactive terminal streams, or a shared remote
service.

## Start here

1. [Install and configure Apolo MCP](getting-started/) and check its prerequisites.
2. Read the generated [safety model](getting-started/safety.md) before enabling writes.
3. Explore the [capability matrix](capabilities/) to understand what is supported.
4. Use the generated [tool reference](capabilities/tools.md) and
   [skills catalog](capabilities/skills.md) for exact interfaces and workflows.
5. Follow a guide for [platform context](guides/platform-context.md),
   [workloads](guides/workflows.md), or [Applications](guides/applications.md).

The repository [README](../README.md) contains the short project summary. This
documentation is the maintained usage and capability contract for the current release.
