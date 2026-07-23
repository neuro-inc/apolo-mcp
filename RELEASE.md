# Release Apolo MCP

Releases are built from signed `vYY.MM.NN` tags and published by CI through PyPI
Trusted Publishing. `YY` is the two-digit year, `MM` is the month without a leading
zero, and `NN` is the zero-based sequence of releases in that month. Thus
`v26.7.0` is the first July 2026 release and `v26.7.1` is the second. The Python
package version omits the `v` prefix. Do not publish a locally built package.

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
