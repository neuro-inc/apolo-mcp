---
name: apolo-platform-context
description: Discover and explain Apolo cluster, organization, project, presets, resource hierarchy, terminology, aliases, and URI resolution. Use before Apolo operations, when a target context or resource reference is ambiguous, or when routing questions to authoritative CLI, SDK, Flow, Apps, or platform documentation.
---

# Apolo platform context

1. Call `get_apolo_context` before an operational workflow. Display the resolved
   cluster, organization, and project. Never change the user's persisted context.
2. If any target is ambiguous, call `list_clusters`, `list_organizations`, and
   `list_projects` in hierarchy order. Pass explicit context to every later tool.
3. Call `list_presets` before selecting compute. Compare CPU, memory, accelerator,
   scheduler/preemptibility, and credits/hour where reported; do not invent quota.
   If quota or price is unavailable, report it as unavailable instead of ranking on it.
4. Resolve short storage, image, secret, and disk references with
   `resolve_resource_uri`. Reject a fully qualified URI outside the selected context
   unless the user explicitly changes the target context.
5. Treat `apolo` as the current product name. Recognize `neuro`, `neuro-inc`, and
   historical `platform` names as retrieval aliases, not different products.
6. Use the resource hierarchy `cluster / organization / project / resource`. Common
   resources include job, storage path, image/tag/digest, secret name, disk, bucket,
   app/template/revision, Flow project/live job, and bake/attempt/task.
7. Route exact SDK signatures to SDK references, CLI flags to generated CLI pages,
   Flow syntax to Flow references, app journeys to GitBook, and MCP contracts to the
   server capability matrix. State the source and tested version when exactness matters.

Never request, display, or persist passed configuration, tokens, cookies, secret
values, or service-account credentials. Discovery results may include identity and
version metadata but never authentication material.
