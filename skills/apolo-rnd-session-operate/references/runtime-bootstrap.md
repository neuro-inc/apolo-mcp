# Runtime bootstrap inside an R&D job

## Preferred path

Rebuild the job from a pinned image containing the complete toolchain. Runtime package
installation increases startup time, requires broader network/root access, and makes
the session harder to reproduce. Record the image digest and each tool version.

## MVP runtime-install path

Use this path only when the user accepts an ephemeral, mutable job. First identify the
Linux distribution, package manager, architecture, Python, Node.js, permissions, and
available disk space without changing the system.

Resolve exact versions before presenting an installation plan. Use two reviewed stages
when resolver tools are missing. Stage 1 installs only the minimum resolver prerequisites
from the image's configured OS repositories; stage 2 resolves and installs the agent
toolchain. Obtain confirmation before each stage. If OS package metadata is unavailable
and cannot be refreshed, stop and require a rebuilt pinned image.

Depending on the base image and available tools, use read-only package queries such as:

```console
apt-cache policy ca-certificates curl git tmux nodejs npm python3 python3-pip
npm view @openai/codex version
python3 -m pip index versions uv
python3 -m pip index versions apolo-cli
python3 -m pip index versions apolo-mcp
```

If package indexes must be refreshed or network access is needed, state that and obtain
confirmation first. If `pip` is absent, resolve its exact OS-package version in stage 1,
install it after approval, and run the Python package queries in stage 2. Never silently
install `latest`. Present the resolved versions, download sources, root requirements,
and commands as one reviewable plan.

For a Debian/Ubuntu image, the approved plan will usually have this shape, with every
placeholder replaced by a resolved version:

```console
apt-get update
apt-get install -y \
  ca-certificates=<CA_CERTIFICATES_VERSION> \
  curl=<CURL_VERSION> \
  git=<GIT_VERSION> \
  tmux=<TMUX_VERSION> \
  nodejs=<NODEJS_VERSION> \
  npm=<NPM_VERSION> \
  python3=<PYTHON3_VERSION> \
  python3-pip=<PYTHON3_PIP_VERSION>
python3 -m pip install --user uv==<UV_VERSION>
export PATH="$HOME/.local/bin:$PATH"
uv tool install apolo-cli==<APOLO_CLI_VERSION>
uv tool install apolo-mcp==<APOLO_MCP_VERSION>
```

Then install only the selected coding client using its client reference. Pin Codex as
`@openai/codex@<VERSION>`. Claude Code's native installer is not an OS-package version
pin: download it to a file, inspect it, obtain confirmation, run it, and record the
resulting `claude --version`. For stricter reproducibility, use a prebuilt image instead.

Verify the final inventory without exposing environment values:

```console
apolo --version
uv --version
uv tool list
tmux -V
git --version
node --version
npm --version
codex --version
claude --version
```

Run only the selected client's version command. If any requested version cannot be
installed exactly, stop and offer a revised image or version plan rather than silently
substituting another release.

## Required trusted handoff

Before starting the coding client, require a non-secret handoff from the local setup
phase with this shape:

```json
{
  "job_id": "<EXACT_JOB_ID>",
  "service_account_id": "<EXACT_ACCOUNT_ID>",
  "service_account_role": "<EXACT_ROLE>",
  "context": {"cluster": "<CLUSTER>", "org": "<ORG>", "project": "<PROJECT>"},
  "grants": [{"uri": "<RESOURCE_URI>", "permission": "read-or-write"}],
  "workspace": "<ABSOLUTE_WORKSPACE>",
  "output_uri": "<EXACT_OUTPUT_URI>"
}
```

Verify without displaying credentials:

```console
apolo config show
apolo service-account get <SERVICE_ACCOUNT_ID>
apolo acl ls -u <SERVICE_ACCOUNT_ROLE> --full-uri
```

`apolo acl ls -u` accepts a user or role. Querying `<SERVICE_ACCOUNT_ROLE>` here is
intentional because Apolo grants service-account resource permissions to the exact role
returned by the account metadata. First verify that `apolo service-account get` maps the
handoff's account ID to that same role. Then compare identity, defaults, and every role
grant to the handoff. Stop if the handoff is missing, the account-to-role mapping differs,
or any grant is unexpectedly broad.
