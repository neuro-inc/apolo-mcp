# Flow-first container image builds

Prefer Apolo Flow image builds when the workspace already has a valid `.apolo`
project. Flow owns image definitions, content-derived tags, build reuse, and the
builder-job lifecycle in the same project context as its workloads.

## Flow project available

1. Validate the existing live, batch, and project configuration before editing it.
2. Define one image per independently deployable component. Do not reuse a single
   repository for unrelated API, worker, migration, or web components.
3. Derive each tag with Flow's `${{ hash_files(...) }}` expression from that
   component's Dockerfile and dependency/build inputs. Include only files and glob
   patterns that actually affect the image.
4. Reference the declared image as `${{ images.<component>.ref }}` from jobs or tasks.
5. Run `apolo-flow build <component>` from the Flow project root. Do not use
   `--force-overwrite` unless the user explicitly approves replacing that exact tag.
6. Record and monitor the bounded remote builder job separately. Verify the exact
   resulting `image:` URI before using it in a job or App.

For a Flow project whose ID is `asd`, this live-workflow fragment produces a dedicated
API image such as `image:asd/api:<sha256>` and a separate web image:

```yaml
kind: live

images:
  api:
    ref: image:${{ flow.project_id }}/api:${{ hash_files('api/Dockerfile', 'api/uv.lock') }}
    context: api
    dockerfile: api/Dockerfile
  web:
    ref: image:${{ flow.project_id }}/web:${{ hash_files('web/Dockerfile', 'web/package-lock.json') }}
    context: web
    dockerfile: web/Dockerfile

jobs:
  api:
    image: ${{ images.api.ref }}
    preset: cpu-small
    cmd: python -m api
    detach: true
  web:
    image: ${{ images.web.ref }}
    preset: cpu-small
    cmd: npm start
    detach: true
```

For a Python component using `requirements.txt` instead of `uv.lock`, hash the
Dockerfile and the actual requirements inputs, for example:
`${{ hash_files('api/Dockerfile', 'api/requirements*.txt') }}`.

## No Flow project available

Use the remote Kaniko builder supplied by `apolo-extras`:

```bash
apolo-extras image build \
  --file Dockerfile \
  --preset cpu-small \
  --build-tag purpose=component-build \
  LOCAL_CONTEXT \
  image:PROJECT_ID/COMPONENT:DEPENDENCY_SHA256
```

`LOCAL_CONTEXT` is uploaded to the remote builder; `--file` is relative to that
context. The command uses Kaniko cache by default. Compute `DEPENDENCY_SHA256`
deterministically from the Dockerfile and dependency/build inputs before launch.
Avoid `latest`, do not pass credentials through `--build-arg` or `--env`, and do not
use `--force-overwrite` for a content-addressed tag. Record the returned builder job
and exact image URI, monitor the job separately, and clean only artifacts created by
that build.

In both paths, retain the regular `image:` URI when launching Apolo jobs or configuring
Service Deployment Apps so the platform can resolve registry credentials.

TODO: replace the non-Flow CLI fallback with the planned typed MCP binding over the
public `apolo-extras` image-build API while preserving this workflow's inputs and
lifecycle evidence.
