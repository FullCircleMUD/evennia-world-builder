# Links

A generic mechanism for setting an attribute on one entity to a reference to another entity, after both have been built. Motivating use case: **bidirectional linked doors** (each `ExitDoor` holds an `other_side` attribute pointing at its partner so open/close/lock state can mirror), but the primitive itself owns no game concepts — any consumer attribute that needs to point at another entity uses the same mechanism.

## Why this exists

Existing cross-reference fields (`location:` and `destination:`) are special-cased in the Builder because they map to native Evennia fields. The Builder reads them inside `_build_one`, calls `_resolve_cross_ref`, and passes the resolved object to `create_object`. That path covers parent/child and exit-target shapes — not arbitrary consumer attributes whose **value** is another entity.

Consumer typeclasses regularly need such pointers (a door's partner, a teleporter's target, an NPC's master, an item's owner). Currently there's no way to express them in YAML — the `attributes:` block treats values as opaque primitives (strings, numbers, dicts).

`links:` is the smallest extension that closes that gap.

## Architectural principles

1. **The library does not own game concepts.** Doors, teleporters, NPCs are consumer-defined. `links:` is a generic "set this attribute on this entity to point at that entity" primitive. No `door`/`pair` types in the library.

2. **Granular, not symmetric.** Each link is a single directed assignment. Reciprocal pairs are two link entries. Some legitimate links are not reciprocal (a teleporter's target, an apprentice's master) — the primitive handles both shapes cleanly without a "pair" sugar.

3. **Reuse the existing cross-ref resolver.** A link's `entity` and `points_to` are `(deployment_file, deployment_id)` refs, resolved through the Builder's existing `_resolve_cross_ref` (cache → DB). Same machinery as `location:` and `destination:`.

4. **No partial state.** Unresolvable `entity` or `points_to` raises `BuilderError` before the next link is touched. Operator gets either a clean apply or a complete refusal.

5. **Self-contained file convention for cross-file pairs.** Each file's `links:` block fully describes what should be set when that file is built. For paired cross-file links (each side in a different file), authors declare **both** directional links in **both** files. DRY violation, but it makes each file an independently rebuildable restoration unit. (See "Cross-file convention" below.)

## YAML shape

`links:` is a file-level YAML key, sibling of `entities:` and `incoming_exits:`. Loader places it in `LoadResult.file_metadata[path]["links"]` alongside `incoming_exits`.

```yaml
entities:
  - deployment_id: 1
    typeclass: typeclasses.terrain.exits.exit_door.ExitDoor
    # ... door A definition ...
  - deployment_id: 2
    typeclass: typeclasses.terrain.exits.exit_door.ExitDoor
    # ... door B definition ...

links:
  - entity:    { deployment_file: shard0/foo.yaml, deployment_id: 1 }
    attribute: other_side
    points_to: { deployment_file: shard0/foo.yaml, deployment_id: 2 }
  - entity:    { deployment_file: shard0/foo.yaml, deployment_id: 2 }
    attribute: other_side
    points_to: { deployment_file: shard0/foo.yaml, deployment_id: 1 }
```

### Fields

| Field        | Type    | Required | Meaning |
|---            |---       |---        |---       |
| `entity`     | dict    | yes      | The entity whose attribute is being set. Always a `(deployment_file, deployment_id)` cross-ref dict, even for same-file refs. |
| `attribute`  | string  | yes      | The attribute key to set on `entity`. |
| `points_to`  | dict    | yes      | The entity that becomes the attribute's value. Same `(deployment_file, deployment_id)` shape. |
| `category`   | string  | no       | Optional Evennia attribute category. Mirrors the existing `attributes:` block's optional `category` field. Defaults to `null` (uncategorised). |

### Semantics

After resolution, the Builder runs:

```python
entity_obj.attributes.add(attribute, points_to_obj, category=category)
```

That's it. Identical to the assignment a consumer would write by hand if connecting entities post-build.

### Self-references

`entity == points_to` is allowed. The library does not refuse it. If the consumer's typeclass has a legitimate use for self-referential pointers, the YAML supports it.

## Validator tier mapping

`links:` validation slots into the existing predicate-tier architecture (see [validator.md](validator.md)):

- **Tier 1 (stateless, always run).** Each link entry has `entity`, `attribute`, `points_to` of correct types; `entity` and `points_to` are well-formed cross-ref dicts; `attribute` is a non-empty string; `category` if present is a string. Findings list bad shape with the file path and the link's index in the list.

- **Tier 2 (stateful per-file).** None — links don't introduce per-file accumulating state beyond what `seen_ids` already tracks.

- **Tier 3 (Evennia-runtime).** None — link assignment doesn't need typeclass introspection. The library does **not** check that `attribute` is declared on the target typeclass. Per the design principle "library does not own game concepts," that's the consumer's responsibility (and would be picked up automatically by a future `strict-attributes:` implementation, since both regular `attributes:` and link assignments end up in the same write path).

- **Tier 4 (cross-ref resolution).** When `resolve_cross_refs=True`, both `entity` and `points_to` must resolve against the full `seen_ids` index. Same finding format as exits: "{file}: link[{i}] {entity|points_to} cross-ref to (deployment_file=..., deployment_id=...) does not resolve."

### What the validator does *not* check

Per the design philosophy: **structural correctness, not logical correctness.**

- Two links in the same file writing the same `(entity, attribute)` to different `points_to` values: **allowed**. Last write wins. The validator does not detect or refuse this. If the operator wants to do something silly, that's their problem; the library's job is to make sure the build doesn't crash.
- Whether the assignment "makes sense" (e.g. setting `other_side` on an entity that isn't a door): **not checked**. The library doesn't know typeclass schemas.
- Cross-file logical conflicts (file A and file B both writing the same `(entity, attribute)` to different `points_to`): **not detected**. Whole-repo pre-validation could in principle catch this, but it's deferred — see "What's deferred."

## Build pass placement

A new **pass 4** runs after pass 3 (incoming_exits dependency restore):

1. **Cleanup** — delete prior deployments tagged with files in scope.
2. **Pass 1+2** — build non-exits, then exits. Cross-refs (`location:`, `destination:`) resolve via the in-build map.
3. **Pass 3** — restore cascaded exits via `incoming_exits:` (existing behaviour).
4. **Pass 4 (new)** — walk `file_metadata[path]["links"]` for every file in scope. For each link:
   - Resolve `entity` via `_resolve_cross_ref` (cache → DB). Must succeed.
   - Resolve `points_to` via `_resolve_cross_ref` (cache → DB). Must succeed.
   - Call `entity_obj.attributes.add(attribute, points_to_obj, category=category)`.

By the end of pass 3, `_built_by_id` is fully warm: own-build entities, DB-resolved cross-refs from pass 1+2, and any incoming-exits restorations. Pass 4 mostly hits the cache. The DB fallback handles the case where a link's target is in some file unrelated to anything pass 3 touched.

### Why after pass 3, not between 2 and 3

If pass 4 ran between passes 2 and 3, links pointing at incoming-exits-restored entities would miss the cache and hit the DB. Putting pass 4 after pass 3 means the warmup is complete and links benefit from any work pass 3 already did.

### Failure mode

Any unresolved cross-ref in a link raises `BuilderError` with the link's source file, its index in the `links:` list, the unresolved field (`entity` or `points_to`), and the cross-ref dict. No partial apply — the build refuses.

## Cross-file convention

The motivating example is paired bidirectional doors. The clean architectural answer is straightforward; the operator-experience question is more interesting.

**The resolver doesn't care.** Both `entity` and `points_to` use `_resolve_cross_ref`, which already handles cross-file targets via the DB tag-search fallback. Authors can declare a link in any file regardless of where its `entity` and `points_to` live.

**The convention.** For a paired cross-file link (e.g. door pair where door A is in file A and door B is in file B), declare **both** directional links in **both** files:

```yaml
# file A
links:
  - { entity: <door A>, attribute: other_side, points_to: <door B> }
  - { entity: <door B>, attribute: other_side, points_to: <door A> }

# file B  (same two entries)
links:
  - { entity: <door A>, attribute: other_side, points_to: <door B> }
  - { entity: <door B>, attribute: other_side, points_to: <door A> }
```

This makes each file independently rebuildable: a partial rebuild of either side restores both directional links to current dbrefs. The cost is (a) DRY violation and (b) on full-repo rebuild each link gets written twice — both harmless. The benefit is no special restoration mechanism in the library and no operator footgun where partial rebuilds leave half-set links.

**Why not a `inbound_links:` registry in the destination file (analogous to `incoming_exits:`)?** Considered. Would let each file declare only its outbound links, with pass 3 reading canonical-file links during cascade restoration. Rejected for v1: more library complexity, more validator state, and "declare in both files" is a five-second authoring task for a fundamentally rare construct (door pairs and similar). If/when cross-file links become common and the duplication becomes painful, revisit.

**Same-file links** are unaffected by all of this — declare once, no convention required.

## What's deferred and why

- **`inbound_links:` restoration registry.** Symmetric to `incoming_exits:` for cross-file links. Would eliminate the "declare in both files" convention. Deferred — see "Cross-file convention" above.

- **Whole-repo cross-file conflict detection.** A whole-repo pre-validation pass could detect "file A and file B both write `(entity, attribute)` with different `points_to`." Today's validator only sees per-file scope plus the global `seen_ids` index; surfacing such conflicts cleanly would mean walking every file's `links:` during whole-repo validation. Deferred until conflicts are observed in real content.

- **Strict-attribute checking.** The `strict-attributes:` setting (currently scaffolded but refuses `true`) would, when implemented, refuse YAML attributes whose key isn't declared on the target typeclass. Will cover both `attributes:` and `links:` automatically — same write path. No links-specific work needed when strict-attributes lands.

- **Self-link semantics enforcement.** Today self-links are accepted and execute. If a consumer typeclass cannot tolerate a self-reference for some specific attribute, that's a typeclass concern; the library does not enforce.

## Implementation notes

- Loader: extend the file-level metadata extraction to include `links:` alongside `incoming_exits:` in `LoadResult.file_metadata[path]`.
- Validator: add stateless predicate(s) for link shape (Tier 1) and extend Tier 4 cross-ref resolution to include `entity` and `points_to`.
- Builder: add `_run_pass_4(file_paths_in_scope)` invoked from `build()` after `_run_pass_3`. Resolves each link's `entity` and `points_to` via `_resolve_cross_ref` and calls `obj.attributes.add(...)`.
- Tests: synthetic fixtures in test-yaml covering at minimum (a) same-file pair (door-style), (b) cross-file one-way (teleporter-style), (c) self-link.

## See also

- [builder.md](builder.md) — pass structure, `_resolve_cross_ref`, cleanup model.
- [validator.md](validator.md) — predicate-tier architecture, scope of each tier.
- [deployment-identity.md](deployment-identity.md) — `(deployment_file, deployment_id)` cross-ref scheme that links share with location/destination.
