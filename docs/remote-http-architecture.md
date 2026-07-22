# Deferred remote Streamable HTTP architecture

The 0.x release supports local stdio only. It inherits one interactive user's local
Apolo configuration and permissions and does not deploy a shared service.

A remote transport is a separate, security-reviewed release. Before it can be
enabled, its design and source must provide all of the following:

- per-user OAuth or verified bearer-token authentication with no shared platform
  credential and no cross-user SDK client/cache reuse;
- preservation and explicit display of Apolo RBAC and resolved cluster,
  organization, and project on every operation;
- tenant isolation, request/tool authorization, independently configurable high-risk
  policy, approval binding, rate limits, bounded concurrency, and timeouts;
- structured audit events with credential/header/cookie/traceback redaction and an
  explicit retention/access policy;
- encrypted transport and secret delivery, protected ledger/plan persistence, and
  an incident-response and key-rotation design;
- maintained deployment source (including chart/manifests), pinned images,
  health/readiness probes, resource limits, upgrade compatibility, and rollback;
- adversarial authentication/authorization/isolation tests and an operator runbook.

None of those requirements is implemented by the local stdio package, and no remote
service or deployment chart is part of this release.
