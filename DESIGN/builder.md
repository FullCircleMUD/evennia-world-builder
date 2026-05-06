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

`_cleanup` calls `evennia.utils.search.search_tag(key=path, category="wb_deployment_file")` once per source file in the build set, deleting every existing object that came from that file. Evennia's `obj.delete()` safely relocates contents (including any player standing in the room) to the home location before the row is removed — operators get a clean message, not a crash.

The number of deleted objects lands on `Builder.deleted_count` so callers (`wb_build` command) can surface the cleanup tally to the operator.

The result: applying the same YAML twice produces the same end state. Object ids increment (Evennia never reuses), but the *count* of objects tagged with each `deployment_file` stays at exactly the YAML's declared count.

## Per-entity construction

For each entity, in order, the Builder runs:

```
create_object  →  _apply_aliases  →  _apply_locks  →  _apply_attributes  →  _apply_tags
```

### `create_object` (the create call)

```python
obj = create_object(
    typeclass=content["typeclass"],
    key=content["name"],
    location=content["location"],   # currently always None — see deployment-identity.md
    attributes=[("desc", content.get("description", ""))],
)
```

`create_object` triggers the typeclass's `at_object_creation()` hook, which can set its own default attributes (e.g. `self.db.room_type = "bakery"`). Any subsequent `_apply_*` call below overwrites those defaults if the YAML declares the same key.

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

- **Recursion** into `exits:` and `contents:` (spike 2). Currently the Builder treats every entity as a top-level entity; nested entity blocks declared in YAML are present in `LoadedEntity.content` but not yet walked.
- **Cross-reference resolution** for exit `destination:` and content placement under another file's parent (spike 4). The validator's Tier 4 lands here too — see [validator.md](validator.md).
- **Strict attribute validation** — typeclass-introspection check that rejects YAML attributes whose key isn't declared on the entity's typeclass. The `strict-attributes` setting in `definitions.yaml` is scaffolded today but refuses to parse `true` until the validator-side feature lands.
