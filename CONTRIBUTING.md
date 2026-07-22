# Contributing

Use Python 3.10+ and `uv`. Create a focused branch, add tests for every tool/schema or
policy change, and run:

```console
make setup
make lint
make test
make build
```

Never put credentials or private platform data in fixtures, recordings, logs, or pull
requests. Tool results must be serializable and bounded. Write tools need accurate MCP
annotations, explicit resolved context, unit tests, and server-policy enforcement where
high risk. Interactive or high-bandwidth behavior belongs in a documented CLI workflow,
not an unbounded MCP response.

Add a `CHANGELOG.D/<issue>.feature`, `.bugfix`, `.doc`, `.removal`, or `.misc`
fragment for each user-visible change. Maintainers assemble fragments with
`make changelog VERSION=<version>` through the protected release workflow.
