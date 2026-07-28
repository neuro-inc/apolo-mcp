---
name: apolo-resource-management
description: Inspect and safely manage Apolo storage, disks, images, buckets/blobs, secrets, and service accounts with explicit context, bounded transfers, protected credential sources/sinks, policy gates, and exact ownership. Use for platform resource inventory, metadata, creation, transfer, deletion, or cleanup.
---

# Apolo resource management

1. Resolve and display cluster/organization/project with `$apolo-platform-user-context`.
   Fully qualify short resource references and reject cross-context targets.
2. Prefer metadata and bounded text operations. Cap every list, wait, log, byte count,
   and duration; return a truthful truncation marker. Never move binary objects,
   container layers, or large directory trees through model context.
3. Use deterministic local CLI/scripts for bulk storage, image, or bucket transfer.
   Restrict local paths to the configured workspace root and set explicit size,
   duration, overwrite, and write-approval controls.
4. Require explicit user approval and `managed` or `full` policy for every write. Mark
   exact deletes destructive; reject project roots, ambiguous names, and unbounded
   recursion.
5. Preflight the protected ledger before creation and append the exact returned type,
   ID, context, and operation immediately afterward. Automatic cleanup requires an
   exact ledger match and never a naming convention.
6. List secrets as metadata only. Create them only from a named local environment
   variable, protected file, or approved secret source read internally. Never accept,
   fetch into, return, or log a secret value.
7. On service-account creation, preflight an exact Apolo-secret or protected `0600`
   file sink. Atomically write the one-time token directly to that sink and return only
   account metadata and destination. Never return a token even on failure.
8. Treat signed bucket URLs as temporary credentials. Require explicit scope/expiry,
   write them only to a new protected `0600` workspace file, and return only sink
   metadata. Do not create or return bucket credential material to the model.

When a safe public SDK contract is absent, state the precise CLI fallback or
future-scoped limitation instead of inventing a tool.
