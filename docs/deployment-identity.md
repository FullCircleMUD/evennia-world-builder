# Deployment Identity

The load-bearing identity scheme for everything evennia-world-builder creates in the consumer's database. Every downstream subsystem — Validator, Builder, cleanup, cross-references, partial deploys — operates in terms of this contract.

## Principle

**Every Evennia object evennia-world-builder creates is identified by a single globally unique `entity_id`.** It is the handle the deployment system uses to find an object on rebuild, the handle authors use to point one entity at another, and the handle consumer game code resolves at runtime.

**Every YAML file that declares entities carries a `file_id`.** It is the handle cleanup uses to scope a redeploy.

Both are UUID-4, author-supplied in YAML, generated once and never edited afterwards.

Neither identifier is derived from a path or a filename. That is the point of the scheme:

- **Renaming or moving a file** does not change what its objects are, so a rebuild after a rename cleans up the objects the file created last time instead of orphaning them.
- **Moving an entity between files** does not change its identity, so nothing that refers to it needs editing.

Neither operation preserves the underlying Evennia object — a rebuild deletes and recreates, so dbrefs change. What survives is identity, and therefore every reference expressed in terms of it.

## File is the atomic deployment unit

A redeploy of a file cleans every object carrying that file's `file_id` and recreates from the current YAML — including any entities the author has *removed* from the file since the last deploy (orphan cleanup falls out for free).

Authors choose deployment granularity by choosing how to chunk YAML into files: one room per file = redeploy a single room; thirty rooms in `forest.yaml` = redeploy the whole forest at once. There is no finer-grained partial clean within a file.

## The two values

### `file_id`

A UUID-4, declared as the first key of the file, above `entities:`. Mandatory on every file that declares entities.

Index files (`index.yaml`) and `definitions.yaml` do not carry one — they declare no entities, so there is nothing for the tag to identify.

### `entity_id`

A UUID-4, author-supplied, mandatory on every entity. Unique across the whole content repo, not just within its file. Top-level and nested entities share the one namespace.

## YAML shape

A leaf YAML file is a top-level mapping with a `file_id` and an `entities:` key holding a list of entity mappings. Each entity carries its own `entity_id`:

```yaml
file_id: d69ba00c-dc5e-43fe-a0ae-347997753f76
entities:
  - entity_id: e48629d9-dcba-41a0-90d1-4c1080171044
    name: Forest Path
    description: Pines crowd the trail.
  - entity_id: bab596f3-aa25-4ec0-a916-cf472efece06
    name: Forest Clearing
    description: Sunlight breaks through.
```

File-level keys (`incoming_exits:`, `links:`) sit alongside `entities:` in the same top-level mapping. A file that is not a top-level mapping with an `entities:` list is rejected at load. A single source file may produce multiple `LoadedEntity` records.

## Cross-references

Every reference is a bare scalar — the target's `entity_id`, naming no file:

```yaml
location: e48629d9-dcba-41a0-90d1-4c1080171044
```

One shape for all five reference sites: `location:`, `destination:`, `home:`, `incoming_exits:` entries, and `links:` `entity` / `points_to`. A reference that names no file is what makes an entity movable between files without editing anything that points at it, and one shape means one validator predicate rather than a per-construct family of them.

`file_id` never appears in a reference. Its job is to tag objects and scope cleanup.

For nested entities (children of a `contents:` block) the Loader synthesises the parent reference automatically at flatten time. Authors only declare cross-refs explicitly on top-level entities; refusing author-written `location:` on nested entities is a Tier 1 validator predicate (`_check_no_author_location_on_nested`).

### Loader synthesis

When the Loader walks a `contents:` block, for each nested mapping:

1. Records `had_author_location = "location" in mapping` against the *original* YAML (before any modification).
2. Synthesises a `location` pointing at the *immediate* parent, overwriting any author-written value.
3. Emits the entity as a `LoadedEntity` with `is_nested=True` and the recorded `had_author_location`.

The validator refuses entities with `is_nested=True and had_author_location=True` so the Loader's overwrite is never silent. Nesting recurses arbitrarily — the synthesis at each level points at the immediate parent, so a gem inside a backpack inside a room places the gem in the backpack (not the room).

## The `entity_id` → path map

References name no file, so one mechanism needs a path anyway: Builder pass 3 fetches a registered entity's canonical file when that entity is missing from both the in-build cache and the DB.

**The library builds an in-memory `entity_id` → path map as it parses.** Every file is read once; while its entities are being flattened, each `entity_id` is inserted against the file's path. The map is built fresh every run, so a renamed or relocated file is picked up automatically — there is no checked-in artifact to regenerate, no CI step to keep in sync, and nothing an author has to maintain after a rename.

Duplicate detection falls out of the same insert: a second entity claiming an `entity_id` already in the map is a collision, reported rather than silently overwritten. The equivalent `file_id` check works the same way.

**Who builds it.** The Validator. It already constructs an equivalent index during its per-entity pass, and duplicate detection has to be able to refuse the build with the complete list of findings — which is the Validator's discipline, not the Loader's. It returns the map, and the Builder carries it through.

**The map is always repo-complete**, because every build pre-validates the whole repo. There is no scope-only validation mode and no gating setting: a partial map would be complete exactly when pass 3 doesn't need it and partial exactly when it does. Paying for the full walk every time keeps one code path instead of two, and makes repo-wide `entity_id` uniqueness checkable at all.

The cost is one read per leaf file. If that ever becomes the bottleneck, the fix belongs at the transport layer — a bulk fetch in `evennia-yaml-reader`, one request for the whole repo — not in skipping validation.

**Only indexed files are in the map.** The Finder discovers content through `index.yaml`, so an entity in an unindexed file is invisible to it and a reference to one does not resolve. This is intended: unindexed means "not part of the world".

## File-level `incoming_exits:` registry

Each YAML file may declare a file-level `incoming_exits:` list — references to exits that *terminate at* one of this file's rooms but live (canonically) in another file's `exits:` block. The library treats these as **registrations**, not declarations: when this file is rebuilt and a registered target is missing from the database, the Builder's pass 3 fetches the target's canonical file via the Reader and rebuilds it.

The field lives at file level (sibling of `entities:` in the canonical YAML shape), extracted by the Loader into `LoadResult.file_metadata[file_path]["incoming_exits"]`. Authors typically declare it at the bottom of a file.

The library is type-agnostic about what's registered — the field is named for its dominant case (cross-file exits) but the mechanism only checks "does this entity exist; if not, fetch and rebuild it from its canonical file."

**End-to-end behaviour:**

- Validator's Tier 1 file-level shape check refuses malformed `incoming_exits:` lists.
- Validator's Tier 4 cross-ref resolution (when run on whole-repo entities) verifies every registered ref points at an entity that exists somewhere in the repo.
- Builder's pass 3 (after passes 1+2) walks each registered ref. In-build map and DB tag-search lookups handle the common cases ("already built in pass 2" or "still alive in DB"); when both miss (typical scenario: cascade-deleted by the just-finished cleanup of this file), Pass 3 fetches the canonical file via the Reader, runs it through `Loader._flatten_top_level`, finds the entity, and builds it through the same per-entity logic passes 1/2 use. The rebuilt entity gets tagged with its **canonical** file so a future rebuild of THAT file cleans it up correctly.

This restores the operator's ability to rebuild any single file in isolation without hand-tracking which other files reference it. Cross-file rebuild dependencies dissolve.

## File-level `links:` block

Each YAML file may declare a file-level `links:` list — directed assignments that set an attribute on one entity to a reference to another entity, after both have been built. The motivating case is bidirectional door pairs (each `ExitDoor` holding `other_side` pointing at its partner) but the primitive itself owns no game concepts: any consumer attribute whose value is another entity uses the same mechanism. See [links.md](links.md).

The field lives at file level (sibling of `entities:` and `incoming_exits:`), extracted by the Loader into `LoadResult.file_metadata[file_path]["links"]`. Each entry uses the same identity scheme as `location:` / `destination:` / `incoming_exits:`.

**End-to-end behaviour:**

- Validator's Tier 1 file-level shape check refuses malformed `links:` entries (each must have `entity`, `attribute`, `points_to` of correct shape, plus optional `category`).
- Validator's Tier 4 cross-ref resolution (when run on whole-repo entities) verifies every link's `entity` and `points_to` point at entities that exist somewhere in the repo.
- Builder's pass 4 (after passes 1+2 and pass 3) walks each link entry, resolves `entity` and `points_to` via the same `_resolve_cross_ref` helper used for `location:` / `destination:`, and calls `entity_obj.attributes.add(attribute, points_to_obj, category=category)`. Unresolvable refs raise `BuilderError` — no partial state.

`links:` is a generic primitive: each entry is one directed assignment. Reciprocal pairs (door pairs, married NPCs) become two entries; one-way refs (a teleporter target, an NPC's master) are a single entry. The library does not bake in any "pair" type or symmetry assumption.

For paired cross-file links, authors declare the same link entries in both files (the cross-file convention — see [links.md](links.md)). This makes each file an independently rebuildable restoration unit without library-side inbound-link restoration machinery.

## Per-entity `home:`

Optional per-entity field that controls the Evennia `home` reference set on the new object — the fallback location Evennia uses when an object's location is deleted. Three valid forms:

| YAML | create_object kwarg | Resulting `obj.home` |
|---|---|---|
| field absent | (no `home=`/`nohome=` passed) | `settings.DEFAULT_HOME` (Limbo by convention) |
| `home: null` | `nohome=True` | `None` |
| `home: <reference>` | `home=<resolved_obj>` | that resolved object |

The null translation is non-obvious: passing `home=None` to `create_object` does **not** yield `home=None` — Evennia's `if home:` check is falsy on None and falls through to `settings.DEFAULT_HOME`. The Builder converts YAML null into `nohome=True` instead, which is the only way to get the no-home outcome.

**Use cases.** The motivating case is **fixtures** that should never relocate to Limbo if their location is deleted (a fountain bolted into the market square shouldn't end up in nowhere). For the library's cleanup-on-rebuild model the practical effect is small (tag-sweep deletes co-located fixtures with their location before any home-relocation logic fires). The more load-bearing case is **objects with a meaningful home elsewhere** — NPCs that retreat to a quarters room at night, quest items that auto-return to a giver, characters whose login destination is a particular dwelling. For those, the reference form is the actively-used path.

`home:` references use the same scheme as `location:` and `destination:` and resolve through the same `_resolve_cross_ref` machinery (in-build map → DB tag-search fallback). Same-file forward refs fail at create time with a "does not resolve" error; same-file backward refs and cross-file refs work transparently.

**End-to-end behaviour:**

- Validator's Tier 1 file-level shape check refuses malformed `home:` values (must be absent, `null`, or a well-formed reference).
- Validator's Tier 4 cross-ref resolution (when run on whole-repo entities) verifies any well-formed `home:` reference points at an entity that exists somewhere in the repo. Null homes are skipped at Tier 4 (not malformed, just meaningful).
- Builder's `_build_one` reads `content["home"]` per entity, translates per the table above, and includes the appropriate kwarg in the `create_object` call.

## Cleanup model

Redeploying a file is a one-tag sweep:

1. Delete every existing object tagged with the file's `file_id`.
2. Create fresh from the current YAML; tag each new object with the `file_id` and its own `entity_id`.

This handles all three edit shapes uniformly: entities added, entities removed, entities changed. No reconciliation, no diffing — the file's full state replaces whatever was there.

Because the sweep keys on `file_id` rather than the path, renaming or relocating a file is invisible to cleanup: the rebuild deletes exactly the objects the file created last time.

The two values reach the database as Evennia tags in reserved categories: `wb_file_id` and `wb_entity_id`. The `wb_` prefix is library-owned — the Validator refuses any author tag using it, so an auto-set value can't be shadowed.

## Validator's role

The Validator (see [validator.md](validator.md)) enforces the contract:

- **Every entity declares a well-formed `entity_id`.**
- **Every entity file declares a well-formed `file_id`.**
- **No duplicate `entity_id`s.** Uniqueness is repo-wide, not per-file. Every build validates the whole repo, so the check always has the scope it needs.
- **No duplicate `file_id`s.** The failure this catches is a copied file: a duplicated `file_id` means rebuilding the copy sweeps the original's objects.
- **Reference shape** for `location:`, `destination:`, `home:`, `incoming_exits:`, and `links:` entity/points_to — one predicate, since all five carry the same shape.
- **Reference resolution (Tier 4)** — every reference must name an entity that exists somewhere in the repo. Catches dangling refs at validate time, before any DB mutation.

## Out of scope (deferred)

- **Level-named tags** (e.g. `zone=millholm` as separate Evennia tags). Useful for partial-scope queries ("all rooms in millholm"), but not required for identity. Can be layered on later as additional tags.
- **Partial cleans within a file.** Authors who want a finer redeploy granularity split the file.
- **Cross-repo references.** Single content-repo per build for v0.
