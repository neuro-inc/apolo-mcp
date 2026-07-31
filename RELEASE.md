# Release Apolo MCP

Releases are built from signed `vYY.MM.NN` tags and published by CI through PyPI
Trusted Publishing. `YY` is the two-digit year, `MM` is the month without a leading
zero, and `NN` is the zero-based sequence of releases in that month. Thus
`v26.7.0` is the first July 2026 release and `v26.7.1` is the second. The Python
package version omits the `v` prefix. Do not publish a locally built package.

## Release-candidate validation

Use three test layers. Unit tests run on every pull request. In-process MCP integration
tests exercise the real FastMCP protocol against a stateful fake SDK and cover tool
registration, schemas, serialization, policy propagation, error normalization,
lifecycle sequences, workspace confinement, and credential non-disclosure. Live tests
run in a dedicated allowlisted Apolo project before a release and cover behavior that
mocks cannot prove: mounts, secret injection, provider streaming, registry behavior,
Apps, Flow, and RBAC enforcement.

Every defect found by an ad-hoc smoke test must gain the narrowest useful automated
regression test. Prefer public SDK seams and stateful fakes over mocks of SDK internals.
Keep expensive deployment scenarios out of the per-PR suite; run them nightly or for a
release candidate. The current consolidated status and accepted limitations are in
[`tests-adhoc/apolo-mcp-full-smoke-test.md`](tests-adhoc/apolo-mcp-full-smoke-test.md).

Before the first release, complete one deterministic full-mode R&D scenario using the
[dedicated-service-account workflow](docs/guides/full-mode-service-account.md):

1. From a trusted local `managed` session, create a unique service account whose
   one-time configuration is written directly to a new secret. Create only disposable
   permitted and denied-control fixtures.
2. Present and explicitly approve an exact least-privilege ACL diff. Grant the account
   `read` or `write` only on the permitted test scope; never grant `manage` or access to
   its own RBAC.
3. Launch a short-lived job from a reviewed slim-image digest with a small preset,
   clean `APOLO_CONFIG`, the secret-mounted `APOLO_PASSED_CONFIG`, and
   `APOLO_MCP_POLICY_MODE=full`. Never use `--pass-config` or mount the operator's
   Apolo configuration.
4. Inside the job, first use a deterministic MCP client: verify the service-account
   identity and context, perform create/read/delete in the permitted scope, verify
   denied reads and writes outside it, and confirm no credentials cross MCP, logs, or
   the lifecycle journal.
5. Prove the policy/RBAC boundary with a disposable harness-created fixture: local
   `managed` mode must reject mutation when it is not journal-owned, while job-local
   `full` mode may mutate it only where the service account has explicit RBAC.
6. Install pinned Apolo and agent tooling plus packaged skills. Run one small real
   Codex or Claude Code acceptance task that creates and removes a permitted marker and
   reports a denied read without requesting broader access.
7. From the trusted operator session, terminate the job, revoke the exact grants, and
   remove every created fixture, secret, and service account. Record any cleanup
   failure by exact identifier and context.

The deterministic R&D scenario is a first-release gate. The real-agent step is a
manual acceptance test; alternate clients between releases if running both is too
costly. `import_external_bucket` may remain an explicitly documented unvalidated
capability until a disposable external provider fixture is available.

## Prepare the release

1. Start from an up-to-date `master` branch and confirm the working tree is clean.
2. Inspect this month's existing tags and choose the next sequence number. Use `0` if
   this is the month's first release. For example, choose `26.7.0` for the first July
   2026 release.
3. Build the changelog and review the result:

   ```console
   make changelog VERSION=26.7.0
   git diff -- CHANGELOG.md CHANGELOG.D
   ```

4. Run the same checks used by CI and inspect the package contents:

   ```console
   make lint
   make test
   make build
   uv run python -m zipfile --list dist/*.whl
   tar -tzf dist/*.tar.gz
   ```

5. Commit the generated changelog through the normal pull-request workflow. Merge only
   after the required `Check` job and review requirements pass.

## Tag and publish

From the merged release commit on `master`, create and verify a signed tag:

```console
git switch master
git pull --ff-only origin master
git tag -s v26.7.0 -m "Release v26.7.0"
git tag -v v26.7.0
git push origin v26.7.0
```

The tag starts `.github/workflows/ci.yaml`. Its release job verifies that the tag,
package version, changelog heading, and empty fragment directory agree; builds the
wheel and source distribution; publishes them to PyPI through the protected `pypi`
environment; and signs the distributions with Sigstore.

After CI succeeds, verify the new version and files on PyPI. If publishing fails, fix
the cause and create a new version; never move or replace a published release tag.
