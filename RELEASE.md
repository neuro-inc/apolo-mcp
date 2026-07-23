# Release Apolo MCP

Releases are built from signed `vMAJOR.MINOR.PATCH` tags and published by CI through
PyPI Trusted Publishing. Do not publish a locally built package.

## Prepare the release

1. Start from an up-to-date `master` branch and confirm the working tree is clean.
2. Choose the release version without the `v` prefix, for example `0.1.0`.
3. Build the changelog and review the result:

   ```console
   make changelog VERSION=0.1.0
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
git tag -s v0.1.0 -m "Release v0.1.0"
git tag -v v0.1.0
git push origin v0.1.0
```

The tag starts `.github/workflows/ci.yaml`. Its release job verifies that the tag,
package version, changelog heading, and empty fragment directory agree; builds the
wheel and source distribution; publishes them to PyPI through the protected `pypi`
environment; and signs the distributions with Sigstore.

After CI succeeds, verify the new version and files on PyPI. If publishing fails, fix
the cause and create a new version; never move or replace a published release tag.
