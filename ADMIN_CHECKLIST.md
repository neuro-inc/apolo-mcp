# Repository creation and release checklist

- [ ] Create `neuro-inc/apolo-mcp` with `master` as the protected default branch.
- [ ] Enable repository auto-merge so the tested Dependabot CI job can schedule squash merges.
- [ ] Require CI, review, conversation resolution, signed tags, and no force pushes.
- [ ] Enable private vulnerability reporting, Dependabot alerts, and secret scanning.
- [ ] Configure PyPI trusted publishing for `.github/workflows/release.yml`.
- [ ] Create a protected `pypi` environment with required maintainer approval.
- [ ] Publish only signed `vMAJOR.MINOR.PATCH` tags after changelog review.
- [ ] Set least-privilege org/team access and designate security/release owners.
- [ ] Confirm README, license, security policy, capability matrix, and changelog.
- [ ] Run CI on Python 3.10–3.14 and verify wheel/sdist contents before first release.
- [ ] Keep high-risk tools disabled in approved base images unless explicitly reviewed.
- [ ] Document that local stdio is supported and shared-credential remote service is not.
- [ ] Add the package to `apolo-all` only as an optional dependency after first release.
