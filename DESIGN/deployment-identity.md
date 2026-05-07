# Deployment Identity

The load-bearing identity scheme for everything evennia-world-builder creates in the consumer's database. Every downstream subsystem — Validator, Builder, cleanup, cross-references, partial deploys — operates in terms of this contract.

## Principle

**Every Evennia object evennia-world-builder creates is identified by a composite of two values: `(deployment_file, deployment_id)`.** This pair is globally unique across the whole world. It is the handle the deployment system uses to find an object on rebuild, the handle authors use to point one entity at another, and the handle cleanup uses to scope a redeploy.

`deployment_file` is set automatically by the Builder from the source path. `deployment_id` is set manually by the author in YAML, mandatory on every entity, an integer unique within its file.

## File is the atomic deployment unit

A redeploy of `millholm/forest.yaml` cleans every object tagged with that file value and recreates from the current YAML — including any entities the author has *removed* from the file since the last deploy (orphan cleanup falls out for free).

Authors choose deployment granularity by choosing how to chunk YAML into files: one room per file = redeploy a single room; thirty rooms in `forest.yaml` = redeploy the whole forest at once. There is no finer-grained partial clean within a file.

## The two values

### `deployment_file`

The full path from the content-repo root, exactly as the Reader sees it. Examples:

- `aethenveil.yaml`
- `millholm/forest.yaml`
- `millholm/town/bakery.yaml`

Set automatically by the Builder; never appears in YAML content. The full path makes it collision-free across folder structures: `millholm/forest.yaml` and `aethenveil/forest.yaml` are distinct strings, and `millholm/forest.yaml` (file) and `millholm/forest/town.yaml` (file under folder) are also distinct.

Reorganising folders changes `deployment_file` for affected entities and will require migration tooling. Treat folder layout as a stable structural choice, not something to refactor casually.

### `deployment_id`

A non-negative integer, author-supplied in YAML, mandatory on every entity. Unique within its file. Numbers reset per file — each file is its own 1..N namespace, independent of every other file. Authors keep a short, manageable list per file rather than a global registry.

## YAML shape

A leaf YAML file may declare one entity (top-level mapping) or many (top-level list of mappings). Both shapes carry `deployment_id` per entity:

```yaml
# Single entity:
deployment_id: 1
name: The Bakery
description: Smells of bread.
```

```yaml
# Multiple entities in one file:
- deployment_id: 1
  name: Forest Path
  description: Pines crowd the trail.
- deployment_id: 2
  name: Forest Clearing
  description: Sunlight breaks through.
```

The library normalises both shapes — a top-level mapping is treated as a one-element list. A single source file may therefore produce multiple `LoadedEntity` records.

## File-level `incoming_exits:` registry

Each YAML file may declare a file-level `incoming_exits:` list — references to exits that *terminate at* one of this file's rooms but live (canonically) in another file's `exits:` block. The library treats these as **registrations**, not declarations: when this file is rebuilt and a registered target is missing from the database, the Builder's pass 3 fetches the target's canonical file via the Reader and rebuilds it.

The field lives at file level (sibling of `entities:` in the canonical YAML shape), extracted by the Loader into `LoadResult.file_metadata[file_path]["incoming_exits"]`. Authors typically declare it at the bottom of a file:

```yaml
entities:
  - { ... room A ... }
  - { ... room B ... }

incoming_exits:
  - { deployment_file: zones/inn.yaml, deployment_id: 2 }
```

The library is type-agnostic about what's registered — the field is named for its dominant case (cross-file exits) but the mechanism only checks "does this `(deployment_file, deployment_id)` exist; if not, fetch and rebuild it from its canonical file."

**End-to-end behaviour:**

- Validator's Tier 1 file-level shape check refuses malformed `incoming_exits:` lists (must be a list of strict cross-ref dicts).
- Validator's Tier 4 cross-ref resolution (when run on whole-repo entities) verifies every registered ref points at an entity that exists somewhere in the repo.
- Builder's pass 3 (after passes 1+2) walks each registered ref. In-build map and DB tag-search lookups handle the common cases ("already built in pass 2" or "still alive in DB"); when both miss (typical scenario: cascade-deleted by the just-finished cleanup of this file), Pass 3 fetches the canonical file via the Reader, runs it through `Loader._flatten_top_level`, finds the entity by deployment_id, and builds it through the same per-entity logic passes 1/2 use. The rebuilt entity gets tagged with its **canonical** `wb_deployment_file` so a future rebuild of THAT file cleans it up correctly.

This restores the operator's ability to rebuild any single file in isolation without hand-tracking which other files reference it. Cross-file rebuild dependencies dissolve.

## Cross-references

Cross-references between entities use the composite identity. **Both keys are always required** — no same-file inference. Uniform shape simplifies the validator's predicate and the Builder's lookup.

```yaml
location: { deployment_file: millholm/bakery.yaml, deployment_id: 1 }
```

For nested entities (children of a `contents:` block) the Loader synthesises this dict automatically at flatten time, pointing at the immediate parent. Authors only declare cross-refs explicitly on top-level entities; refusing author-written `location:` on nested entities is a Tier 1 validator predicate (`_check_no_author_location_on_nested`).

### Loader synthesis

When the Loader walks a `contents:` block, for each nested mapping:

1. Records `had_author_location = "location" in mapping` against the *original* YAML (before any modification).
2. Synthesises `mapping["location"] = {deployment_file: <parent.path>, deployment_id: <parent.deployment_id>}`, overwriting any author-written value.
3. Emits the entity as a `LoadedEntity` with `is_nested=True` and the recorded `had_author_location`.

The validator refuses entities with `is_nested=True and had_author_location=True` so the Loader's overwrite is never silent. Nesting recurses arbitrarily — the synthesis at each level points at the *immediate* parent, so a gem inside a backpack inside a room places the gem in the backpack (not the room).

## Cleanup model

Redeploying a file is a one-tag sweep:

1. Delete every existing object tagged with the file's `deployment_file` value.
2. Create fresh from the current YAML; tag each new object with `deployment_file` and `deployment_id`.

This handles all three edit shapes uniformly: entities added, entities removed, entities changed. No reconciliation, no diffing — the file's full state replaces whatever was there.

## Validator's role

The Validator (see [validator.md](validator.md)) enforces the contract. Currently shipped:

- **Every entity declares `deployment_id`** — mandatory, non-negative integer, `bool` rejected even though it's an `int` subclass. Top-level and nested entities share one per-file namespace.
- **No duplicate `deployment_id`s within a file** — flagged as the per-file `{deployment_file: {ids}}` index is built incrementally during the per-entity pass.
- **`location:` shape** — accepts `null` or a strict `{deployment_file: non-empty str, deployment_id: non-negative int}` cross-ref dict; refuses extras, missing keys, or wrong types.
- **`destination:` shape** — same strict cross-ref dict as `location:`, but `null` rejected (an exit must point somewhere). Optional at the shape layer; required for exit typeclasses (Tier 3) and forbidden for non-exit typeclasses (Tier 3).
- **`location:` not null when `destination:` present** — an entity that declares destination must live in a room.
- **No author-written `location:` on nested entities** — refuses if the Loader recorded `had_author_location=True` on an `is_nested=True` entity (i.e. the author wrote `location:` and the Loader overwrote it).
- **Cross-ref resolution (Tier 4)** — when the caller passes `resolve_cross_refs=True`, every `location:` and `destination:` cross-ref `(deployment_file, deployment_id)` pair must appear in the `seen_ids` index. Catches dangling refs and id/file typos at validate time, before any DB mutation.

Same-file *backward* cross-ref resolution at build time happens via the Builder's `_built_by_id` map (see [builder.md](builder.md)) — same identity scheme, same tuple key, direct dict lookup. Cross-file refs to entities NOT in the current build invocation will fall through to a DB tag-search lookup once the Builder grows that fallback (spike 4 step 5).

Validator scope is per-file for the uniqueness check, plus the `{deployment_file: {ids}}` index that powers both Tier 2 duplicate detection and Tier 4 cross-ref resolution. Memory footprint stays small even for large repos: one file's content at a time + an integer-set index.

## Out of scope (deferred)

- **Level-named tags** (e.g. `zone=millholm` as separate Evennia tags). Useful for partial-scope queries ("all rooms in millholm"), but not required for identity. Can be layered on later as additional tags alongside `deployment_file`.
- **Partial cleans within a file.** Authors who want a finer redeploy granularity split the file.
- **Cross-repo references.** Single content-repo per build for v0.
- **Migration tooling for folder reorgs.** Handle when the case lands.
