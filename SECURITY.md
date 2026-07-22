# Security policy

## Supported versions

Security fixes are provided for the latest released minor version.

## Reporting

Do not open a public issue for a suspected vulnerability. Report it privately through
GitHub's **Security → Report a vulnerability** flow for `neuro-inc/apolo-mcp`, or use
the security contact published by Apolo. Include reproduction steps, affected version,
and impact, but never include live tokens or customer data.

The server inherits the local user's Apolo identity and RBAC. It does not accept token
arguments, expose secret values, or provide a generic shell/HTTP tool. High-risk tools
must be disabled by default at the server and separately approved by the client.
