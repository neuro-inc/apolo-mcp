# Capabilities

This matrix is the current contract for the local stdio server. `Native` means a
typed MCP tool. `Skill/CLI` means a bounded local workflow because the operation is
interactive or high-bandwidth. `Prohibited` means deliberately unavailable to the
model. `Out of scope` means an administrative or local-client concern outside the
least-privilege workload product.

Start with the [tool reference](tools/README.md) for exact inputs and results, or the
[skills catalog](skills.md) for agent workflows that combine tools safely. The
[platform-context](../guides/platform-context.md),
[workload](../guides/workflows.md), and
[Applications](../guides/applications.md) guides show common tasks end to end.

Every native list, log, telemetry, and wait operation has a finite bound. Every native
platform write is subject to `read-only`, `managed`, or `full` server policy and Apolo
RBAC; destructive operations are also annotated destructive. Successful mutations are
recorded in an append-only lifecycle journal. Generated credentials go only to
protected sinks.

## Context, configuration, and access control

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo config show` | Native | `get_apolo_context`, `list_presets` | Returns sanitized selected context/config metadata and client/server-version availability; never credentials. |
| `apolo config get-clusters` | Native | `list_clusters`, `list_organizations`, `list_projects` | Discovery required before writes. |
| `apolo config aliases` | Skill/CLI | `apolo config aliases` | Local convenience configuration, not a platform resource. |
| `apolo config docker` | Skill/CLI | `apolo config docker` | Modifies local Docker config and is meaningful only when Docker exists. |
| `apolo config login`, `apolo config login-headless`, `apolo config login-with-token`, `apolo login` | Prohibited | none | Authentication occurs outside MCP; token arguments/results would expose credentials. |
| `apolo config logout`, `apolo logout` | Out of scope | `apolo logout` | Local session administration could disrupt the agent host. |
| `apolo config show-token` | Prohibited | none | Direct credential disclosure. |
| `apolo config switch-cluster`, `apolo config switch-org`, `apolo config switch-project` | Prohibited | explicit context fields | Tools never persistently switch user context. |
| `apolo acl add-role`, `apolo acl grant`, `apolo acl list-roles`, `apolo acl ls`, `apolo acl remove-role`, `apolo acl revoke` | Out of scope | none | Identity/RBAC administration is not part of the least-privilege workload surface. |
| `apolo admin <command>` | Out of scope | none | Autonomous cluster, organization, project, user, preset, and quota administration is unavailable. |
| `apolo completion generate`, `apolo completion patch` | Out of scope | client setup docs | Shell integration, not a platform workload operation. |
| `apolo help` | Skill/CLI | generated CLI/SDK/Flow docs | Exact syntax is routed to authoritative generated references. |
| `apolo share` | Out of scope | none | ACL mutation is not part of the least-privilege workload surface. |

## Jobs

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo job run`, `apolo run`; `apolo job generate-run-command` | Native / Skill | `run_job`; planning skill for CLI rendering | Native typed start covers image/preset/entrypoint/command/workdir/env/volumes/secrets/disks/HTTP/lifecycle/scheduling/context. Command rendering is local CLI convenience. |
| `apolo job ls`, `apolo ps` | Native | `list_jobs` | Bounded filters and resolved context. |
| `apolo job status`, `apolo status` | Native | `get_job` | Structured lifecycle/container/context. |
| `apolo job logs`, `apolo logs` | Native | `get_job_logs` | Byte/line/time bound with explicit truncation marker. |
| `apolo job top`, `apolo top` | Native | `get_job_telemetry` | Strict sample/duration cap plus summary; optional bounded raw samples. |
| `apolo job bump-life-span` | Native | `bump_job_life_span` | Policy-governed idempotent write with positive bound. |
| Apolo SDK job signal operation | Native | `send_job_signal` | Policy-governed bounded SDK operation. |
| `apolo job save`, `apolo save` | Native | `save_job_image` | Exact target image, policy, journal, and bounded progress summary. |
| `apolo job kill`, `apolo kill` | Native | `kill_job` | Destructive annotation, policy, journal, and exact job ID. |
| `apolo job exec`, `apolo exec` | Skill/CLI | bounded local CLI | The SDK stream lacks the bounded exit and output semantics required for a native tool. |
| `apolo job attach`, `apolo attach` | Manual CLI | `apolo job attach` | Interactive bidirectional bytes stay in the user's terminal and outside MCP/model results. This package does not wrap or automate the command. |
| `apolo job port-forward`, `apolo port-forward` | Manual CLI | `apolo job port-forward` | The local stream stays outside MCP/model results. The user owns target/port selection and termination. |
| `apolo job browse` | Skill/CLI | `apolo job browse` | Host browser/UI operation. |
| Apolo SDK capacity operation | Native | `get_job_capacity` | Bounded read-only cluster capacity metadata. |
| MCP bounded job polling | Native | `wait_for_job` | MCP-added deadline/poll interval and terminal summary. |

## Applications

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo app-template list`, `apolo app-template ls` | Native | `list_app_templates` | Bounded template discovery. |
| `apolo app-template list-versions`, `apolo app-template ls-versions` | Native | `list_app_template_versions` | Bounded exact-version discovery. |
| `apolo app-template get` | Native | `get_app_template` | Returns current input schema; source for CLI-compatible YAML. |
| `apolo app list`, `apolo app ls` | Native | `list_apps` | Bounded state/context filters. |
| `apolo app get-status` | Native | `get_app` | Structured app/context/endpoints. |
| `apolo app get-input` | Native | `get_app_input` | Seed for safe reconfiguration; secret references remain references. |
| `apolo app get-values` | Native | `get_app_values` | Bounded non-credential app values; sensitive-looking values are redacted. |
| Apolo Apps SDK output operation | Native | `get_app_output` | Bounded structured output. |
| `apolo app logs` | Native | `get_app_logs` | Byte/line/time bound and truncation marker. |
| Apolo Apps SDK events operation | Native | `get_app_events` | Bounded event/resource health output. |
| `apolo app get-revisions` | Native | `list_app_revisions` | Bounded exact revision metadata. |
| `apolo app install` | Native plan/apply | `plan_app_install`, `install_app` | Stable YAML + JSON/Markdown plan, checksum/context/version/expiry binding, exact unchanged file, single use, and policy enforcement. |
| `apolo app configure` | Native plan/apply | `plan_app_configure`, `configure_app` | Seed from current input, revision drift rejection, exact reviewed file, policy, and lifecycle journal. |
| `apolo app rollback` | Native plan/apply | `plan_app_rollback`, `rollback_app` | Exact app/revision/current-state binding, destructive annotation, and policy; no fake YAML. |
| `apolo app uninstall` | Native plan/apply | `plan_app_uninstall`, `uninstall_app` | Destructive annotation, policy, and a fresh single-use plan. |
| MCP bounded App polling | Native | `wait_for_app` | Deadline, poll interval, terminal/health summary. |

## Storage and disks

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo storage ls`, `apolo ls` | Native | `list_storage` | Bounded entries under canonical `storage:` URI. |
| `apolo storage df` | Native | `stat_storage`/usage metadata | Structured size/usage. |
| `apolo storage mkdir`, `apolo mkdir` | Native | `make_directory` | Idempotent write, explicit resolved context. |
| small UTF-8 reads/writes | Native | `read_text`, `write_text` | Strict byte bound; binary rejected. |
| `apolo storage rm`, `apolo rm` | Native | `delete_storage_path` | Exact path; recursive mode destructive, with policy and lifecycle-journal rules. |
| `apolo storage cp`, `apolo cp`; `apolo storage glob`; `apolo storage tree` | Manual CLI | the listed CLI commands | High-volume/recursive traversal stays outside MCP/model context. This package does not wrap those commands. |
| `apolo storage mv`, `apolo mv` | Manual CLI | `apolo storage mv` | Moving across local and remote boundaries stays a user-reviewed CLI operation. |
| `apolo disk ls` | Native | `list_disks` | Bounded explicit context. |
| `apolo disk get` | Native | `get_disk` | Exact ID/name and context. |
| `apolo disk create` | Native | `create_disk` | Size/context bounds, unused timeout up to 10 years, policy, and journal. |
| `apolo disk rm` | Native | `delete_disk` | Destructive policy and exact immutable ID; managed mode requires an active journaled lifecycle. |

## Images

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo image ls`, `apolo images`; `apolo image tags` | Native | `list_image_repositories`, `list_image_tags` | Bounded metadata only. |
| `apolo image push`, `apolo push`; `apolo image pull`, `apolo pull` | Native | `push_image`, `pull_image` | Uses the Docker engine on the MCP host; explicit context, policy, journal, and a 30-minute deadline. Transfer size is not limited. |
| `apolo image digest`, `apolo image size` | Native | `get_image` | Exact tag/digest metadata. |
| `apolo image rm` | Native | `remove_image` | Exact tag/digest and destructive policy. |
| `apolo job save`, `apolo save` | Native | `save_job_image` | Listed under Jobs; exact platform image and bounded progress. |

## Buckets / blob storage

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo blob lsbucket`, `apolo blob statbucket`, `apolo blob mkbucket`, `apolo blob importbucket`, `apolo blob du`, `apolo blob set-bucket-publicity` | Native | bucket list/get/create/import/usage/publicity tools | Metadata-oriented; writes use policy, journal, and resolved context. |
| `apolo blob ls`, `apolo blob glob`; Apolo SDK blob stat operation | Native | `list_bucket_blobs`, `stat_bucket_blob` | Bounded object metadata. |
| `apolo blob sign-url` | Native secure-sink only | `create_bucket_signed_url` | Bounded expiry; the temporary access grant is written only to a new protected `0600` workspace file and never returned. |
| `apolo blob cp` | Native single-file / Manual CLI for recursive | `upload_bucket_file`, `download_bucket_file`; local `apolo blob cp` for recursive work | Native single-file transfers enforce workspace, byte, duration, exact-key, and no-overwrite bounds. Recursive CLI transfer stays outside MCP/model results and is not automated by this package. |
| `apolo blob rm`, `apolo blob rmbucket` | Native | exact blob/bucket delete tools | Destructive exact targets with policy and journal checks. |
| `apolo blob lscredentials`, `apolo blob statcredentials` | Prohibited | none | The supported SDK surface can expose persistent bucket credentials. |
| `apolo blob mkcredentials` | Prohibited | none | Persistent credential generation is omitted; temporary signed URLs use a protected short-lived sink instead. |
| `apolo blob rmcredentials` | Prohibited | none | Credential-record mutation is unavailable. |

## Secrets and service accounts

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo secret ls` | Native | `list_secrets` | Names/owners/context only. |
| `apolo secret get` | Native | `get_secret_to_file` | Writes only to a new mode-0600 file beneath the allowed workspace; never returns the value to the model. |
| `apolo secret add` | Native secure-source only | `create_secret_from_source` with an environment name, protected file path, or same-context secret key | Value never appears in MCP arguments/results/logs; protected source validation. |
| `apolo secret rm` | Native | `delete_secret` | Exact key and destructive policy. |
| `apolo service-account ls`, `apolo service-account get` | Native | list/get service account | Metadata only. |
| `apolo service-account create` | Native secure-sink only | `create_service_account` | One-time token atomically stored in a named Apolo secret or `0600` file; mutation policy applies and the result contains metadata/destination only. |
| `apolo service-account rm` | Native | `delete_service_account` | Exact ID, destructive policy, and lifecycle journal. |
| `apolo vcluster list-service-accounts`, `apolo vcluster create-service-account`, `apolo vcluster delete-service-account`, `apolo vcluster regenerate-service-account`, `apolo vcluster activate-service-account` | Out of scope | none | Virtual-cluster administration and credential activation are not workload-level service accounts. |

## Apolo Flow

| Public capability | Classification | MCP/fallback | Current behavior |
|---|---|---|---|
| `apolo-flow ps`, `apolo-flow status`, `apolo-flow logs` | Native | `flow_live_list`, `flow_live_get`, `flow_live_logs` | Bounded typed facade results through `apolo-flow>=26.7.1` explicit-context lifecycle. |
| `apolo-flow run` | Native | `flow_live_run` | Project root confinement, explicit context, Flow-compatible suffix/parameter resolution, policy, and journal. |
| `apolo-flow kill` | Native | `flow_live_kill`, `flow_live_kill_all` | Destructive policy; exact/all targets are resolved before managed authorization. |
| `apolo-flow bake`, `apolo-flow bakes`, `apolo-flow show`, `apolo-flow inspect`, `apolo-flow logs` | Native | `flow_bake_start`, `flow_bake_list`, `flow_bake_get`, `flow_bake_logs` | Start uses Flow orchestration, never raw persistence API. |
| `apolo-flow cancel`, `apolo-flow restart` | Native | `flow_bake_cancel`, `flow_bake_restart` | Policy-governed writes with exact bake/attempt state and journaled lifecycle. |
| live/bake terminal polling | Native | `flow_live_wait`, `flow_bake_wait` | MCP-added deadline and machine result. |
| `apolo-flow build`, `apolo-flow upload`, `apolo-flow download`, `apolo-flow mkvolumes`, `apolo-flow clean`, `apolo-flow clear-cache`, `apolo-flow delete-flow` | Skill/CLI | bounded local CLI | Build/data/cache/project-maintenance operations are local/high-bandwidth or destructive; allowed-root/duration/write controls. |
| `apolo-flow init` | Skill/CLI | local scaffolding workflow | Repository authoring, not a platform API. |
| `apolo-flow completion generate`, `apolo-flow completion patch` | Out of scope | client setup | Shell integration. |

## Deliberately absent generic capabilities

Apolo MCP does not expose an arbitrary shell tool, arbitrary HTTP request tool, generic
Kubernetes tool, model-visible credential retrieval, interactive attach or port-forward
stream, or unbounded binary transfer. It supports local stdio only and does not provide
a shared-credential remote service.
