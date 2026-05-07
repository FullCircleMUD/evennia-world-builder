# Builder

The architectural design of the Builder — the principles it embodies and the trade-offs that shape it. For per-method behaviour, code-level detail, signatures, and test cross-links, see the implementation reference at [src/evennia_world_builder/builder.md](../src/evennia_world_builder/builder.md).

## Role in the pipeline

```
Reader → Definitions → Finder → Loader → Validator → Builder
                                                     ▲
                                                  (this doc)
```

The Builder is the **only component in the pipeline that mutates the consumer's database**. By the time `build()` runs, the Validator has guaranteed shape; the Builder trusts those guarantees rather than re-checking them.

## Architectural principles

1. **Clean-then-rebuild, not diff-then-reconcile.** Per CLAUDE.md principle 5 and the [deployment-identity contract](deployment-identity.md), every `build()` call sweeps prior deployments of the affected files and recreates from current YAML. The same YAML applied N times produces the same end state. Reconcile-style state-tracking is explicitly out of scope.

2. **File is the atomic deployment unit.** Cleanup is a single tag-sweep per source file — all entities authored in a file (top-level rooms + their nested `contents:` and `exits:` items alike) share one `wb_deployment_file` value. Authors choose deployment granularity by chunking YAML into files; there is no finer-grained partial clean within a file.

3. **Typeclass declares defaults; YAML overrides per-instance.** `create_object` triggers the typeclass's `at_object_creation()` hook, then the Builder's `_apply_*` calls run after — overwriting any default with the same key. This is the load-bearing contract for both content authors and typeclass authors: the typeclass is where defaults belong; YAML is where instance-specific overrides belong.

4. **Single-pass for non-exits, two-pass for exits.** Cross-references resolve in-pass via an in-memory map populated as each object is created. The Loader's depth-first pre-order guarantees a `contents:`-nested child's parent is always already built (single-pass suffices). Exits get their own pass after all non-exits, because bidirectional connections (room A → room B and room B → room A) make pure ordering insufficient — one direction is always a forward ref. The two-pass model is the minimum complexity needed for that case, applied surgically rather than universally. Topological sort was considered and rejected as overkill.

5. **Cross-file refs fall through to the DB.** The Builder's in-memory map only knows about objects built in the current invocation. For cross-file refs to entities already in the DB from a previous build, `_resolve_cross_ref` falls through to a tag-search query against `wb_deployment_file` + `wb_deployment_id`. Hits are cached back into the in-memory map. This means operators can rebuild a single file (`wb_build zone=millholm room=bakery`) without rebuilding everything that file references — the durable identity tags ARE the cross-file lookup mechanism.

6. **No partial state.** Per CLAUDE.md principle 4, any failure (cleanup query, deletion, create, attribute apply, tag apply, unresolved cross-ref) refuses with a typed `BuilderError` before the next entity is touched. The operator gets either a clean apply or a complete refusal — never half a build.

## File-level metadata

The Builder accepts `file_metadata: dict | None` at construction (the `LoadResult.file_metadata` from the Loader). Currently this is plumbed through to support the upcoming pass 3 (dependency restore — step 6e), where file-level `incoming_exits:` registries get walked after the main build pass. The build loop today (passes 1+2) does not consult `file_metadata`.

## What's deferred and why

- **Same-file forward-ref refusal at validate time.** Today the Builder refuses forward refs at create time; Validator Tier 4 sees them as valid (`seen_ids` is fully built before Tier 4 runs). A future predicate could refuse them at validate time so the failure surfaces alongside other findings, before any DB mutation. Deferred — the current refusal is loud enough.

- **Strict attribute validation.** The `strict-attributes` setting in `definitions.yaml` is scaffolded today but refuses to parse `true`. When implemented, it would reject YAML attributes whose key isn't declared on the entity's typeclass, catching typos that today silently create junk attributes. Deferred until the typeclass-introspection cost-benefit is clearer.

- **Cross-file rebuild dependency reconciliation.** When file B references entities in file A, rebuilding A invalidates B's exits to A (the DB references go stale). Today operators must rebuild both; future tooling could detect and follow incoming cross-refs. Out of scope — the failure mode is loud at runtime and easy to fix manually.

## See also

- [src/evennia_world_builder/builder.md](../src/evennia_world_builder/builder.md) — implementation reference (signatures, algorithms, test cross-links).
- [deployment-identity.md](deployment-identity.md) — the `(deployment_file, deployment_id)` identity scheme that anchors cleanup, cross-refs, and the Builder's in-build map.
- [validator.md](validator.md) — what the Validator has guaranteed by the time the Builder runs.
