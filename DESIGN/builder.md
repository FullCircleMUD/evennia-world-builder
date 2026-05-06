# Builder

The Builder turns a list of validated `LoadedEntity` records into Evennia objects, idempotently. It's the only component in the pipeline that mutates the consumer's database.

```
Reader → Definitions → Finder → Loader → Validator → Builder
                                                     ▲
                                                  (this doc)
```

By the time `Builder.build()` runs, the validator has guaranteed every entity's mandatory fields are present and well-shaped, and (under `evennia_runtime=True`) that every declared typeclass actually resolves. The Builder trusts those guarantees and skips re-checking shape.

## Cleanup-on-rebuild

Per the deployment-identity contract (see [deployment-identity.md](deployment-identity.md)) and CLAUDE.md principle 5, the system is **clean-then-rebuild**, not diff-then-reconcile.

```python
def build(self, entities):
    file_paths = {e.path for e in entities}   # source files in this build
    self._cleanup(file_paths)                 # delete prior deployments
    for entity in entities:
        # create + apply per-object dimensions (see below)
        ...
```

`_cleanup` calls `evennia.utils.search.search_tag(key=path, category="wb_deployment_file")` once per source file in the build set, deleting every existing object that came from that file. **All entities from a file share the same `wb_deployment_file` value** — top-level rooms and their nested `contents:` items alike — so a single file-level sweep covers the whole tree. Evennia's `obj.delete()` safely relocates contents (including any player standing in the room) to the home location before the row is removed — operators get a clean message, not a crash.

The number of deleted objects lands on `Builder.deleted_count` so callers (`wb_build` command) can surface the cleanup tally to the operator.

The result: applying the same YAML twice produces the same end state. Object ids increment (Evennia never reuses), but the *count* of objects tagged with each `deployment_file` stays at exactly the YAML's declared count.

## Per-entity construction

For each entity, in order, the Builder runs:

```
create_object  →  _apply_aliases  →  _apply_locks  →  _apply_attributes  →  _apply_tags
```

### `create_object` (the create call)

```python
location = self._resolve_location(content["location"], entity.path)
obj = create_object(
    typeclass=content["typeclass"],
    key=content["name"],
    location=location,
    attributes=[("desc", content.get("description", ""))],
)
self._built_by_id[(entity.path, content["deployment_id"])] = obj
```

`create_object` triggers the typeclass's `at_object_creation()` hook, which can set its own default attributes (e.g. `self.db.room_type = "bakery"`). Any subsequent `_apply_*` call below overwrites those defaults if the YAML declares the same key.

#### Location resolution and the in-build map

`content["location"]` is one of two shapes (the validator guarantees this):

- `None` — orphan placement.
- `{deployment_file: str, deployment_id: int}` — a cross-reference at the entity that contains this one.

The Builder maintains `self._built_by_id: dict[(path, deployment_id), obj]`, populated as each entity is created. `_resolve_location` is a one-line dict lookup against the cross-ref dict's `(deployment_file, deployment_id)` tuple — same identity scheme the Loader uses to synthesise the dict in the first place, so the keys match by construction.

```python
def _resolve_location(self, loc_ref, entity_path):
    if loc_ref is None:
        return None
    key = (loc_ref["deployment_file"], loc_ref["deployment_id"])
    try:
        return self._built_by_id[key]
    except KeyError:
        raise BuilderError(f"{entity_path}: location refers to {key} ...")
```

The map is reset at the start of every `build()` call and discarded when it returns; the durable identity is the `wb_deployment_file` / `wb_deployment_id` tag pair the Builder writes onto every object.

#### Single-pass + ordering contract

Resolution is single-pass: the parent must be in `_built_by_id` by the time the child needs it. Two ordering guarantees make this work:

- **Same-file nested entities**: the Loader emits depth-first pre-order (parent before its children), so a child's location cross-ref always resolves to a just-built parent in the same `build()` call.
- **Cross-file refs (spike 4 future)**: file-level builds run in `index.yaml` order, so authors put referenced files before referencing files. Until spike 4 lands a DB tag-search fallback, cross-file refs to entities NOT being built in this invocation will refuse with `BuilderError`.

**Same-file forward refs are refused.** If a top-level entity authors `location: {deployment_file: <self>, deployment_id: <X>}` pointing at another top-level entity that comes later in the file, single-pass build fails — the author has to reorder. (Future enhancement: validator-side check that catches this at validate time, before any DB mutation.)

### `_apply_aliases`

Iterates `content["aliases"]` (list of strings) and calls `obj.aliases.add(alias)` for each. No-op when absent or empty.

### `_apply_locks`

If `content["locks"]` is present, calls `obj.locks.add(lockstring)` once. Evennia's lock system parses the semicolon-joined `<lock>:<func()>` clauses and updates each named lock; locks not mentioned in the YAML keep their typeclass defaults — partial-update behaviour.

### `_apply_attributes`

Iterates `content["attributes"]` (list of `{key, value, category?}` records) and calls `obj.attributes.add(key, value, category=category)` for each. Three things to note:

- **YAML wins over typeclass defaults.** Because this runs after `create_object`, a YAML attribute with the same key as one set in `at_object_creation` (or backed by an `AttributeProperty` descriptor) overrides the default. The contract: typeclass declares defaults; YAML overrides per-instance.
- **Value can be any YAML type.** Strings, ints, floats, bools, null, lists, nested dicts — Evennia's attribute store handles arbitrary serialisable Python values. The validator does no type check on `value`.
- **Category is optional.** When omitted, the attribute uses Evennia's default (uncategorised) attribute category.

### `_apply_tags`

Two passes:

1. **Author tags.** Each entry in `content["tags"]` is normalised to `(key, category)`:
   - Shorthand string → `(string, None)` (default category).
   - Dict form `{key, category?}` → `(key, category)`.
   Each pair is applied via `obj.tags.add(key, category=category)`.
2. **Auto-set deployment pair.** Always appended:
   - `obj.tags.add(entity.path, category="wb_deployment_file")`
   - `obj.tags.add(str(deployment_id), category="wb_deployment_id")`

The `wb_*` category prefix is reserved for library-controlled tags; the validator's `_check_tags_no_reserved_category` predicate rejects any author tag using a `wb_*` category, so the auto-set pair can't collide.

## Failure handling

Any exception from `create_object`, `obj.aliases.add`, `obj.locks.add`, `obj.attributes.add`, `obj.tags.add`, the cleanup `search_tag`, or `obj.delete` is wrapped in `BuilderError` with a contextual message naming the offending entity path and (where applicable) the field that failed. `wb_build` catches `BuilderError` and surfaces it via `caller.msg`, then refuses without continuing.

## Out of scope (deferred)

- **Two-pass build for exits** (spike 4 step 5). Today the Builder is single-pass — fine for `contents:`-style placements where the Loader's depth-first pre-order guarantees the parent is already built. Exits need pass 1 to build all non-exits first, then pass 2 to build exits with their destinations resolved against `_built_by_id`. The Loader already flattens `exits:` blocks (spike 4 step 1) and the Validator already enforces destination correctness (spike 4 steps 2–4); only the Builder dispatch is missing.
- **Cross-file location/destination refs** (spike 4 step 5). The Builder's `_resolve_cross_ref` only reads `_built_by_id` — entities built in this invocation. A target in another file already in the DB from a previous invocation will need a tag-search fallback (`evennia.utils.search.search_tag(key=path, category="wb_deployment_file")` filtered to the right `deployment_id`).
- **Same-file forward-ref refusal at validate time** (separate predicate). Validator Tier 4 catches *unresolved* refs but accepts forward refs (since `seen_ids` is fully built before Tier 4 runs); the Builder still refuses them at create time. A small future enhancement would refuse same-file forward refs at validate time so the failure surfaces alongside other findings, before any DB mutation.
- **Strict attribute validation** — typeclass-introspection check that rejects YAML attributes whose key isn't declared on the entity's typeclass. The `strict-attributes` setting in `definitions.yaml` is scaffolded today but refuses to parse `true` until the validator-side feature lands.
