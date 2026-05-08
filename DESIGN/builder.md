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

4. **Two-pass entity creation, then per-file file-level passes.** Cross-references resolve in-pass via an in-memory map populated as each object is created. The Loader's depth-first pre-order guarantees a `contents:`-nested child's parent is always already built. Exits get their own pass after all non-exits, because bidirectional connections (room A → room B and room B → room A) make pure ordering insufficient. Pass 3 then walks each file's `incoming_exits:` to restore cascade-deleted dependencies, and pass 4 walks each file's `links:` to assign cross-entity attribute references (see [links.md](links.md)). All entity creation is contained in passes 1+2; passes 3 and 4 only touch file-level metadata. Topological sort was considered for the entity passes and rejected as overkill.

5. **Cross-file refs fall through to the DB.** The Builder's in-memory map only knows about objects built in the current invocation. For cross-file refs to entities already in the DB from a previous build, `_resolve_cross_ref` falls through to a tag-search query against `wb_deployment_file` + `wb_deployment_id`. Hits are cached back into the in-memory map. This means operators can rebuild a single file (`wb_build zone=millholm room=bakery`) without rebuilding everything that file references — the durable identity tags ARE the cross-file lookup mechanism.

6. **No partial state.** Per CLAUDE.md principle 4, any failure (cleanup query, deletion, create, attribute apply, tag apply, unresolved cross-ref) refuses with a typed `BuilderError` before the next entity is touched. The operator gets either a clean apply or a complete refusal — never half a build.

## File-level metadata + passes 3 and 4

The Builder accepts `file_metadata: dict | None` and an optional `reader: Reader | None` at construction (the `LoadResult.file_metadata` from the Loader and the configured Reader). Two file-level passes run after passes 1+2.

### Pass 3 — incoming_exits dependency restore

For each file path in the build set: walk its `incoming_exits:` list (a file-level YAML key registering exits that live in other files but terminate at this file's rooms).

- For each `(deployment_file, deployment_id)` ref:
  1. **In-build map hit** (target was built in pass 2 because its canonical file is also in scope) → skip.
  2. **DB tag-search hit** (target exists from a previous build, hasn't been cascade-deleted) → cache the result into `_built_by_id`, skip.
  3. **Both miss** (cascade-deleted in cleanup, or never built) → fetch the canonical file via the Reader, run it through `Loader._flatten_top_level` (so location synthesis applies to nested entities), find the entity by deployment_id, build it through the same `_build_one` helper passes 1/2 use.

The fetched entity gets tagged with its **canonical** `wb_deployment_file` (e.g., `inn.yaml` for the inn's south exit), so a future rebuild of that canonical file cleans it up correctly via the standard tag-sweep cleanup model.

If pass 3 needs to fetch a missing dep but no `reader` was configured at construction, it raises `BuilderError`. Callers that don't need pass 3 (no `incoming_exits:` anywhere) can omit the reader.

### Pass 4 — links assignment

For each file path in the build set: walk its `links:` list (a file-level YAML key declaring cross-entity attribute references — see [links.md](links.md) for the design rationale and YAML shape). For each link entry:

- Resolve the link's `entity` and `points_to` cross-refs via the same `_resolve_cross_ref` helper passes 1/2 use (cache → DB tag-search → BuilderError).
- Call `entity_obj.attributes.add(attribute, points_to_obj, category=category)`.

By the time pass 4 runs, `_built_by_id` holds every own-build entity, every cross-ref the build resolved through DB fallback, and every pass-3 incoming_exits restoration — so most link resolutions hit the cache. Cross-file links to entities in unrelated files DB-fall-through identically to how `location:` and `destination:` do today.

`links:` is a generic primitive that doesn't bake in any consumer game concepts (no door type, no pair sugar) — each link is a single directed assignment. Reciprocal pairs are two link entries. See [links.md](links.md) for use cases and the cross-file "declare in both files" convention.

## What's deferred and why

- **Same-file forward-ref refusal at validate time.** Today the Builder refuses forward refs at create time; Validator Tier 4 sees them as valid (`seen_ids` is fully built before Tier 4 runs). A future predicate could refuse them at validate time so the failure surfaces alongside other findings, before any DB mutation. Deferred — the current refusal is loud enough.

- **Strict attribute validation.** The `strict-attributes` setting in `definitions.yaml` is scaffolded today but refuses to parse `true`. When implemented, it would reject YAML attributes whose key isn't declared on the entity's typeclass, catching typos that today silently create junk attributes. Deferred until the typeclass-introspection cost-benefit is clearer.

- **Cross-file rebuild dependency reconciliation.** When file B references entities in file A, rebuilding A invalidates B's exits to A (the DB references go stale). Today operators must rebuild both; future tooling could detect and follow incoming cross-refs. Out of scope — the failure mode is loud at runtime and easy to fix manually.

## See also

- [src/evennia_world_builder/builder.md](../src/evennia_world_builder/builder.md) — implementation reference (signatures, algorithms, test cross-links).
- [deployment-identity.md](deployment-identity.md) — the `(deployment_file, deployment_id)` identity scheme that anchors cleanup, cross-refs, and the Builder's in-build map.
- [validator.md](validator.md) — what the Validator has guaranteed by the time the Builder runs.
