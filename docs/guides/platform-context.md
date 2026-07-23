# Platform context

Every Apolo resource belongs to a context:

```text
cluster / organization / project / resource
```

Start an operational request with `get_apolo_context`. If the target is ambiguous,
discover it in hierarchy order with `list_clusters`, `list_organizations`, and
`list_projects`. Pass the resulting cluster, organization, and project explicitly to
later tools and check the resolved context in their results.

Apolo MCP never persistently switches the user's saved CLI context. It also rejects a
fully qualified resource URI that conflicts with the context selected for a call. This
is an MCP isolation boundary, not a platform limitation.

For a cross-context action that MCP cannot perform safely, ask the agent to explain a
reviewable Apolo CLI command rather than to execute it. For example, copying storage
between projects in the same cluster and organization can be performed by the user:

```console
apolo cp -r 'storage:/source-project/path' 'storage:/target-project/path'
```

Check the generated Apolo CLI reference for the exact URI forms supported by the
installed CLI. For contexts that cannot be addressed in one command, use an explicit
local download and upload sequence.

## Resource references

Use `resolve_resource_uri` for short storage, image, secret, and disk references. Common
resources also include jobs, buckets and blobs, Applications and revisions, Flow
projects and live jobs, and bakes, attempts, and tasks.

Use `list_presets` before selecting compute. Compare reported CPU, memory,
accelerators, scheduler behavior, and credits per hour; do not assume unavailable quota
or pricing information.

The current product name is Apolo. `neuro`, `neuro-inc`, and `platform` can occur in
older documentation and identifiers, but do not represent separate current products.

See the [platform-context skill](../capabilities/skills.md) for the agent workflow and
the [capability matrix](../capabilities/) for supported context operations.
