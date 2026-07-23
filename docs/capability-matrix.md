# Apolo CLI/SDK capability parity

This matrix is the release contract for the local stdio server. `Native` means a
typed MCP tool. `Skill/CLI` means a bounded local workflow because the operation is
interactive or high-bandwidth. `Prohibited` means deliberately unavailable to the
model. `Out of scope` means an administrative or local-client concern outside the
least-privilege workload product.

Every native list, log, telemetry, and wait operation has a finite bound. Every native
write is subject to server policy and client approval; destructive operations are also
annotated destructive. Generated credentials go only to approved protected sinks.

## Context, configuration, and access control

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| `config show` | Native | `get_apolo_context`, `list_presets` | Returns sanitized selected context/config metadata and client/server-version availability; never credentials. |
| `config get-clusters` | Native | `list_clusters`, `list_organizations`, `list_projects` | Discovery required before writes. |
| `config aliases` | Skill/CLI | local CLI | Local convenience configuration, not a platform resource. |
| `config docker` | Skill/CLI | local CLI | Modifies local Docker config and is meaningful only when Docker exists. |
| `config login`, `login-headless`, `login-with-token`, root `login` | Prohibited | none | Authentication occurs outside MCP; token arguments/results would expose credentials. |
| `config logout`, root `logout` | Out of scope | local CLI | Local session administration could disrupt the agent host. |
| `config show-token` | Prohibited | none | Direct credential disclosure. |
| `config switch-cluster`, `switch-org`, `switch-project` | Prohibited | explicit context fields | Tools never persistently switch user context. |
| `acl add-role`, `grant`, `list-roles`, `ls`, `remove-role`, `revoke` | Out of scope | none | Identity/RBAC administration is an explicit first-release non-goal. |
| all `admin` commands (cluster/org/project/user/preset/quota create/get/update/remove/set) | Out of scope | none | Autonomous cluster, identity, role, preset, and quota administration is prohibited. A separately reviewed admin product would be required. |
| completion generate/patch | Out of scope | client setup docs | Shell integration, not a platform workload operation. |
| `help` | Skill/CLI | generated CLI/SDK/Flow docs | Exact syntax is routed to authoritative generated references. |
| `share` | Out of scope | none | ACL mutation is not part of the least-privilege workload surface. |

## Jobs

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| job/root `run`; `generate-run-command` | Native / Skill | `run_job`; planning skill for CLI rendering | Native typed start covers image/preset/entrypoint/command/workdir/env/volumes/secrets/disks/HTTP/lifecycle/scheduling/context. Command rendering is local CLI convenience. |
| job/root `ls`, `ps` | Native | `list_jobs` | Bounded filters and resolved context. |
| job/root `status` | Native | `get_job` | Structured lifecycle/container/context. |
| job/root `logs` | Native | `get_job_logs` | Byte/line/time bound with explicit truncation marker. |
| `job top`, root `top` | Native | `get_job_telemetry` | Strict sample/duration cap plus summary; optional bounded raw samples. |
| `job bump-life-span` | Native | `bump_job_life_span` | Approval-gated idempotent write with positive bound. |
| signal API | Native | `send_job_signal` | Approval-gated bounded SDK operation. |
| job/root `save` | Native | `save_job_image` | Exact target image, approval, policy, bounded progress summary. |
| job/root `kill` | Native | `kill_job` | Destructive annotation, approval/policy, exact job ID. |
| job/root `exec` | Skill/CLI initially | bounded local CLI | SDK stream needs explicit output/time termination. Add native only after reliable bounded exit/output semantics are tested. |
| job/root `attach` | Manual CLI | local `apolo job attach` | Interactive bidirectional bytes stay in the user's terminal and outside MCP/model results. This package does not wrap or automate the command. |
| job/root `port-forward` | Manual CLI | local `apolo job port-forward` | The local stream stays outside MCP/model results. The user owns target/port selection and termination. |
| job `browse` | Skill/CLI | local CLI | Host browser/UI operation. |
| capacity API | Native | `get_job_capacity` | Bounded read-only cluster capacity metadata. |
| bounded terminal polling | Native | `wait_for_job` | MCP-added deadline/poll interval and terminal summary. |

## Applications

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| app-template `list`/`ls` | Native | `list_app_templates` | Bounded template discovery. |
| app-template `list-versions`/`ls-versions` | Native | `list_app_template_versions` | Bounded exact-version discovery. |
| app-template `get` | Native | `get_app_template` | Returns current input schema; source for CLI-compatible YAML. |
| app `list`/`ls` | Native | `list_apps` | Bounded state/context filters. |
| app `get-status` | Native | `get_app` | Structured app/context/endpoints. |
| app `get-input` | Native | `get_app_input` | Seed for safe reconfiguration; secret references remain references. |
| app `get-values` | Native | `get_app_values` | Bounded non-credential app values; sensitive-looking values are redacted. |
| app output SDK | Native | `get_app_output` | Bounded structured output. |
| app `logs` | Native | `get_app_logs` | Byte/line/time bound and truncation marker. |
| app events SDK | Native | `get_app_events` | Bounded event/resource health output. |
| app `get-revisions` | Native | `list_app_revisions` | Bounded exact revision metadata. |
| app `install` | Native plan/apply | `plan_app_install`, `apply_app_install` | Stable YAML + JSON/Markdown plan, checksum/context/version/expiry binding, explicit approval, exact unchanged file, single use. |
| app `configure` | Native plan/apply | `plan_app_configure`, `apply_app_configure` | Seed from current input, revision drift rejection, exact reviewed file. |
| app `rollback` | Native plan/apply | `plan_app_rollback`, `apply_app_rollback` | Destructive/high-risk policy, exact app/revision/current-state binding; no fake YAML. |
| app `uninstall` | Native plan/apply | `plan_app_uninstall`, `apply_app_uninstall` | Destructive/high-risk policy and fresh single-use plan. |
| app bounded health polling | Native | `wait_for_app` | Deadline, poll interval, terminal/health summary. |

## Storage and disks

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| storage/root `ls` | Native | `list_storage` | Bounded entries under canonical `storage:` URI. |
| storage `df` | Native | `stat_storage`/usage metadata | Structured size/usage. |
| storage `mkdir`, root `mkdir` | Native | `make_directory` | Idempotent write, explicit resolved context. |
| small UTF-8 reads/writes | Native | `read_text`, `write_text` | Strict byte bound; binary rejected. |
| storage/root `rm` | Native | `delete_storage_path` | Exact path; recursive mode destructive, policy/approval/ledger rules. |
| storage/root `cp`; storage `glob`, `tree` | Manual CLI | local `apolo storage` commands | High-volume/recursive traversal stays outside MCP/model context. This package does not wrap those commands; a future typed metadata glob may be added after bounded SDK support exists. |
| storage/root `mv` | Future-scoped | none | A safe move needs exact source/destination preflight and failure/rollback semantics across local and remote boundaries; use a separately reviewed manual CLI operation in v1. |
| disk `ls` | Native | `list_disks` | Bounded explicit context. |
| disk `get` | Native | `get_disk` | Exact ID/name and context. |
| disk `create` | Native | `create_disk` | Size/context bounds, unused timeout up to 10 years, approval/policy, ledger. |
| disk `rm` | Native | `delete_disk` | Destructive approval/policy and exact ID/name; automatic cleanup only for ledger-owned IDs. |

## Images

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| image/root `ls`, root `images`; image `tags` | Native | `list_image_repositories`, `list_image_tags` | Bounded metadata only. |
| image `push`, image `pull` | Native | `push_image`, `pull_image` | Uses the Docker engine on the MCP host; explicit context plus approval/policy and a 30-minute deadline, and pushed images are ledgered. Transfer size is not limited. |
| image `digest`, `size` | Native | `get_image` | Exact tag/digest metadata. |
| image `rm` | Native | `delete_image` | Exact tag/digest, destructive policy/approval. |
| job/root `save` | Native | `save_job_image` | Listed under Jobs; exact platform image and bounded progress. |

## Buckets / blob storage

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| blob `lsbucket`, `statbucket`, `mkbucket`, `importbucket`, `du`, `set-bucket-publicity` | Native | bucket list/get/create/import/usage/publicity tools | Metadata-oriented; writes use policy/approval and resolved context. |
| blob `ls`, `glob`, blob stat SDK | Native | `list_bucket_blobs`, `stat_bucket_blob` | Bounded object metadata. |
| blob `sign-url` | Native secure-sink only | `create_bucket_signed_url` | Bounded expiry; the temporary access grant is written only to a new protected `0600` workspace file and never returned. |
| blob `cp` | Native single-file / Manual CLI for recursive | `upload_bucket_file`, `download_bucket_file`; local `apolo blob cp` for recursive work | Native single-file transfers enforce workspace, byte, duration, exact-key, and no-overwrite bounds. Recursive CLI transfer stays outside MCP/model results and is not automated by this package. |
| blob `rm`, `rmbucket` | Native | exact blob/bucket delete tools | Destructive, exact target, approval/policy/ledger checks. |
| blob `lscredentials`, `statcredentials` | Prohibited in v1 | none | The supported SDK surface can expose persistent bucket credentials. Add metadata-only tools only after a contract proves values cannot serialize. |
| blob `mkcredentials` | Prohibited in v1 | none | Persistent credential generation is omitted; temporary signed URLs use a protected short-lived sink instead. |
| blob `rmcredentials` | Prohibited in v1 | none | Credential-record mutation remains unavailable until metadata-only identity and exact-delete semantics are independently reviewed. |

## Secrets and service accounts

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| secret `ls` | Native | `list_secrets` | Names/owners/context only. |
| secret `get` | Native | `get_secret_to_file` | Writes only to a new mode-0600 file beneath the allowed workspace; never returns the value to the model. |
| secret `add` | Native secure-source only | `create_secret_from_source` with an environment name, protected file path, or same-context secret key | Value never appears in MCP arguments/results/logs; protected source validation. |
| secret `rm` | Native | `delete_secret` | Exact key, destructive approval/policy. |
| service-account `ls`, `get` | Native | list/get service account | Metadata only. |
| service-account `create` | Native secure-sink only | `create_service_account` | One-time token atomically stored in named Apolo secret or `0600` file; high-risk policy and approval; result contains metadata/destination only. |
| service-account `rm` | Native | `delete_service_account` | High-risk destructive policy/approval. |
| vcluster service-account list/create/delete/regenerate/activate | Out of scope | none | Virtual-cluster administration and credential activation are not workload-level service accounts. Revisit only under a separately approved admin contract. |

## Apolo Flow

| Public capability | Classification | MCP/fallback | Rationale and future plan |
|---|---|---|---|
| flow `ps`, `status`, `logs` | Native | `flow_live_list`, `flow_live_get`, `flow_live_logs` | Bounded typed facade results through `apolo-flow>=26.7.1` explicit-context lifecycle. |
| flow `run` | Native | `flow_live_run` | Project root confinement, explicit context, Flow-compatible suffix/parameter resolution, policy, and approval. |
| flow `kill` | Native | `flow_live_kill`, `flow_live_kill_all` | Destructive approval/policy; exact/all semantics explicit. |
| flow `bake`, `bakes`, `show`, `inspect`, `logs` | Native | `flow_bake_start`, `flow_bake_list`, `flow_bake_get`, `flow_bake_logs` | Start uses Flow orchestration, never raw persistence API. |
| flow `cancel`, `restart` | Native | `flow_bake_cancel`, `flow_bake_restart` | Approval-gated writes with exact bake/attempt state. |
| live/bake terminal polling | Native | `flow_live_wait`, `flow_bake_wait` | MCP-added deadline and machine result. |
| flow `build`, `upload`, `download`, `mkvolumes`, `clean`, `clear-cache`, `delete-flow` | Skill/CLI | bounded local CLI | Build/data/cache/project-maintenance operations are local/high-bandwidth or destructive; allowed-root/duration/write controls. |
| flow `init` | Skill/CLI | local scaffolding workflow | Repository authoring, not a platform API. |
| flow completion | Out of scope | client setup | Shell integration. |

## Deliberately absent generic capabilities

No arbitrary shell tool, arbitrary HTTP request tool, generic Kubernetes tool,
credential retrieval tool, interactive attach/port-forward stream, or unbounded binary
transfer is planned. A future remote Streamable HTTP transport may reuse these business
operations only after per-user OAuth/bearer verification, RBAC preservation, isolation,
auditing/redaction, rate limits, deployment packaging, and immutable Flow-source design
are approved.
