# MCP tool reference

This reference is generated from the public metadata exposed by the running MCPServer
registry. Input and output schemas therefore describe the actual MCP tool contract.

Each page corresponds exactly to one entry in the declarative capability catalog.

Operation labels use a consistent visual key:

- <mark style="background-color: blue;">Read-only</mark> inspects state without
  mutation.
- <mark style="background-color: green;">Local planning</mark> writes review files
  only; <mark style="background-color: green;">Write</mark> mutates platform state.
- <mark style="background-color: yellow;">Destructive write</mark> can remove
  resources or end running workloads.

The colors describe operation effects, not policy modes. See the
[safety model](../../getting-started/safety.md) for `read-only`, `managed`, and `full`
policy behavior.

{tool_groups}
