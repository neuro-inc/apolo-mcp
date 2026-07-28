# Security policy

## Supported versions

Security fixes are provided for the latest released minor version.

## Reporting

Do not open a public issue for a suspected vulnerability. Report it privately through
GitHub's **Security → Report a vulnerability** flow for `neuro-inc/apolo-mcp`, or use
the security contact published by Apolo. Include reproduction steps, affected version,
and impact, but never include live tokens or customer data.

The server uses the configured Apolo identity and RBAC. It does not accept token
arguments, expose secret values, or provide a generic shell/HTTP tool. Mutations are
disabled by default; `managed` and `full` are operator-selected guardrails, not a
replacement for least-privilege credentials or client approval. Never run `full` with
a broadly privileged personal identity. See the [safety model](docs/getting-started/safety.md)
and [full-mode service-account guide](docs/guides/full-mode-service-account.md).
