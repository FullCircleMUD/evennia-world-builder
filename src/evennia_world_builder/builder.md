# builder — creates Evennia objects from validated LoadedEntities

Documentation for [builder.py](builder.py). The source file is intentionally code-only; everything explanatory lives here.

The Builder is the **only component in the pipeline that mutates the consumer's database**. By the time `Builder.build()` runs the Validator has guaranteed every entity's mandatory fields are present and well-shaped, every declared typeclass actually resolves (under `evennia_runtime=True`), and every cross-ref resolves in the build set (under `resolve_cross_refs=True`). The Builder trusts those guarantees and skips re-checking shape.

> **Note:** there is also a higher-level design document at [docs/builder.md](../../docs/builder.md) covering the big-picture rationale and trade-offs. This file is the implementation reference.

---

## Module-level constants

```python
_TAG_CATEGORY_DEPLOYMENT_FILE = "wb_deployment_file"
_TAG_CATEGORY_DEPLOYMENT_ID = "wb_deployment_id"
_WB_AT_POST_BUILD_ATTR = "wb_at_post_build"
```

The two tag categories are the **deployment-identity pair** the Builder writes onto every created object — see [docs/deployment-identity.md](../../docs/deployment-identity.md) for the load-bearing identity contract. These two values are the durable handle by which:

- Cleanup finds existing objects to delete on rebuild (`search_tag(key=path, category="wb_deployment_file")`).
- Cross-file cross-references (spike 4) will look up parents already in the DB from a previous invocation.

**Synchronisation requirement:** the `wb_*` prefix is reserved by the library and the Validator's `_check_tags_no_reserved_category` predicate refuses any author tag in that namespace. Adding a new `wb_*` category here without a corresponding update to the validator's reserved-prefix check would let an author tag collide with a Builder-set tag silently.

`_WB_AT_POST_BUILD_ATTR` names the consumer-typeclass method the Builder duck-type-invokes at the end of `_build_one` (see `_invoke_post_build_hook` below). Hook design: [docs/post-build-hook.md](../../docs/post-build-hook.md).

---

## `Builder`

### Signature

```python
class Builder:
    def __init__(
        self, definitions: Definitions, *,
        file_metadata: dict | None = None,
        reader: Reader | None = None,
    ): ...
    def build(self, entities: list) -> list: ...

    # Public attributes populated during build():
    deleted_count: int          # objects swept by cleanup-on-rebuild
    _built_by_id: dict          # (deployment_file, deployment_id) → Evennia obj
```

### Purpose

Take a list of validated `LoadedEntity` records and apply them to the Evennia database, idempotently. The same YAML applied N times produces the same end state.

### Idempotency model — clean-then-rebuild

Per CLAUDE.md principle 5: the system is **clean-then-rebuild**, not diff-then-reconcile. At the start of every `build()`:

1. Collect the unique source-file set from the entities being built.
2. Sweep every existing Evennia object tagged with `wb_deployment_file=<file>` for any file in that set and delete it.
3. Create fresh objects from the YAML.

The result: applying the same YAML twice produces the same end state. Object dbrefs increment (Evennia never reuses), but the *count* of objects tagged with each `deployment_file` stays at exactly the YAML's declared count.

This handles all three edit shapes uniformly: entities added, entities removed, entities changed. No reconciliation, no diffing — the file's full state replaces whatever was there.

`obj.delete()` safely relocates contents (including any player standing in the room) to the home location before the row is removed, so operators get a clean message rather than a crash.

### `__init__(definitions, *, file_metadata=None, reader=None)`

```python
def __init__(
    self, definitions: Definitions, *,
    file_metadata: dict | None = None,
    reader: Reader | None = None,
):
    self.definitions = definitions
    self.deleted_count: int = 0
    self._built_by_id: dict = {}
    self.file_metadata: dict = dict(file_metadata or {})
    self._reader: Reader | None = reader
```

`definitions` is stored for future use (level vocabulary may inform placement decisions like building exits at zone boundaries); current logic doesn't consult it.

`file_metadata` is the per-file metadata dict from `LoadResult.file_metadata` — file-level keys extracted by the Loader. Currently consumed: `incoming_exits:` (walked by pass 3 for cross-file dependency restore) and `links:` (walked by pass 4 for cross-entity attribute references; see [docs/links.md](../../docs/links.md)).

`reader` is the configured Reader, used by pass 3 to fetch canonical files when an `incoming_exits:` target is missing from both `_built_by_id` and the DB. Optional — Builder constructed without a reader can still build entities; pass 3 raises `BuilderError` only if it actually needs to fetch a missing dep.

`deleted_count` and `_built_by_id` are reset at the start of every `build()` call — they're per-build state, not per-instance state. The Builder instance is reusable across multiple `build()` invocations.

### `build(entities) -> list`

```python
def build(self, entities: list) -> list
```

The single public method. Returns the list of created Evennia objects on success; raises `BuilderError` on any failure.

#### Algorithm

1. Reset `deleted_count = 0` and `_built_by_id = {}` (per-build state).
2. Lazy-import `evennia.utils.create.create_object`.
3. **Cleanup pass:** call `_cleanup(file_paths)` against the unique set of source files in the entity list (see `_cleanup` below).
4. **Pass 1+2 partition:** split the entity list into `non_exits` (entities without a `destination:` field) and `exits` (entities with one). Iterate `non_exits + exits` so every non-exit is built before any exit — this guarantees an exit's destination cross-ref always resolves to a non-exit room that's already in `_built_by_id`. Within each pass, entity order is preserved (depth-first pre-order from the Loader for nested entities; YAML order for top-level entities).
5. For each entity in the partitioned order, call `_build_one(entity, create_object)` (see helper below for the per-entity steps).
6. **Pass 3 (dependency restore):** call `_run_pass_3(file_paths_in_scope, create_object)`. Walks `file_metadata[path]["incoming_exits"]` for each file in scope; for each registered ref that's missing from both `_built_by_id` and the DB tag-search, fetches the canonical file via the Reader and builds the missing entity through the same `_build_one` helper.
7. **Pass 4 (links):** call `_run_pass_4(file_paths_in_scope)`. Walks `file_metadata[path]["links"]` for each file in scope; for each entry, resolves `entity` and `points_to` via `_resolve_cross_ref` (cache → DB) and dispatches on the `attribute` field — bare names (no `[` or `]`) go through `entity_obj.attributes.add(attribute, points_to_obj, category=category)`; subscript paths (either bracket present, e.g. `'destinations["foo"]["bar"]'`) go through `_assign_via_subscript_path` which parses via `ast.parse`, walks the existing top-level attribute and subscript chain, assigns the resolved object at the leaf, and re-saves. Pass 4 sees the fully warmed `_built_by_id` (own builds + DB-resolved cross-refs + pass-3 restorations) so most resolutions are cache hits. See [docs/links.md](../../docs/links.md).
8. Return the combined list of created objects (passes 1+2 + any pass-3 restorations). Pass 4 mutates already-built objects and does not return new ones.

#### Two-pass dispatch

The partition is determined by `"destination" in content` — the same signal the Validator uses to decide an entity is an exit. The two-pass model is required (not just preferred) because:

- **Bidirectional exits force forward refs.** Bakery → Inn AND Inn → Bakery. Whichever direction is built first has a destination ref pointing at a not-yet-built room.
- **Ordering alone can't solve it.** No YAML ordering of the non-exit rooms can satisfy both directions simultaneously.
- **Two-pass is sufficient.** If all non-exit rooms are built first (pass 1), every exit's destination is guaranteed to resolve in pass 2 — destinations are always rooms (or, theoretically, other exits, but the partition handles that case too: exits-pointing-at-exits resolve in YAML order during pass 2).

#### Per-entity construction order

```
_resolve_cross_ref(location) → [_resolve_cross_ref(destination)] → [translate home: → home= or nohome=True] → create_object → _apply_aliases → _apply_locks → _apply_attributes → _apply_tags → _invoke_post_build_hook
```

`create_object` triggers the typeclass's `at_object_creation()` hook, which can set its own default attributes (e.g. `self.db.room_type = "bakery"`). Any subsequent `_apply_*` call overwrites those defaults if the YAML declares the same key. **Contract: typeclass declares defaults; YAML overrides per-instance.**

`_invoke_post_build_hook` is the final per-entity step: a duck-typed, opt-in call to `obj.wb_at_post_build()` if the typeclass defines it. By the time this fires, every `_apply_*` has run, so consumer typeclasses that need to derive state from the YAML-supplied values (rather than the defaults Evennia's `at_object_creation` saw) get a documented seam. Hook design and rationale: [docs/post-build-hook.md](../../docs/post-build-hook.md).

#### Field expectations

The Validator's Tier 1 predicates have already guaranteed:

- `content["name"]` — non-empty string. Becomes the Evennia object's `key`.
- `content["typeclass"]` — non-empty string. Selects the typeclass; Tier 3 has verified resolvability when `evennia_runtime=True`.
- `content["location"]` — `null` (orphan) or strict `{deployment_file, deployment_id}` cross-ref dict.
- `content["deployment_id"]` — non-negative integer. Used both for the in-build map key and the `wb_deployment_id` tag.

Optional fields:

- `content["description"]` — string, written to `db.desc` via the `attributes` kwarg of `create_object` (default `""`).
- `content["destination"]` — strict `{deployment_file, deployment_id}` cross-ref dict. Presence makes the entity an exit (built in pass 2). Validator Tier 1 has guaranteed shape; Tier 3 has guaranteed it's consistent with the typeclass (DefaultExit-derived ⇒ required; otherwise ⇒ forbidden); Tier 4 (when run) has guaranteed the ref resolves in `seen_ids`.
- `content["home"]` — `null`, or strict `{deployment_file, deployment_id}` cross-ref dict, or absent. Translates to a create_object kwarg per Evennia semantics: absent ⇒ no kwarg passed (defaults to `settings.DEFAULT_HOME`); `null` ⇒ `nohome=True` (object's `home` becomes `None`); cross-ref dict ⇒ `home=<resolved obj>`. The null translation is non-obvious — passing `home=None` directly to create_object falls through to `settings.DEFAULT_HOME` because the manager-level check is truthy-only (see [evennia/objects/manager.py:683-688](https://github.com/evennia/evennia/blob/main/evennia/objects/manager.py)). Validator Tier 1 has guaranteed shape; Tier 4 (when run) has guaranteed any well-shaped ref resolves in `seen_ids`.
- `content["aliases"]` — list of non-empty strings.
- `content["locks"]` — non-empty lockstring.
- `content["attributes"]` — list of `{key, value, category?}` records.
- `content["tags"]` — list of strings or `{key, category?}` dicts.

#### Failure handling

Each step is wrapped in a typed `try/except` that re-raises as `BuilderError` with a contextual message naming the offending entity path and (where applicable) the field that failed. The wrapped exception chain (`from e`) preserves the underlying Evennia exception for debugging.

`_resolve_cross_ref` already raises `BuilderError` directly (its message has more specific context); the build loop re-raises that as-is rather than wrapping it again.

`wb_build` catches `BuilderError` and surfaces it via `caller.msg`, then refuses without continuing — partial state is never returned.

### `_resolve_cross_ref(ref, entity_path, field_name)`

```python
def _resolve_cross_ref(self, ref, entity_path: str, field_name: str)
```

Turn a `content["location"]` or `content["destination"]` value into the corresponding argument for `create_object`. Single helper for both fields — the only difference between location and destination resolution is the value the caller passes; the lookup logic is identical.

- **`None`** ⇒ `None` (orphan placement; `location:` only — destination null is refused at validate time).
- **Cross-ref dict `{deployment_file, deployment_id}`** ⇒ a two-step lookup:
  1. **In-build map first.** Try `self._built_by_id[(deployment_file, deployment_id)]`. Hits when the target was built earlier in this same `build()` call.
  2. **DB fallback** if step 1 misses. Call `_lookup_in_db(deployment_file, deployment_id)` to find an existing object in the database tagged with that identity pair.
  3. If the DB lookup also misses, raise `BuilderError` naming the field, the `(deployment_file, deployment_id)` pair, and the likely causes.

  **DB hits are cached back into `_built_by_id`** before returning, so subsequent refs to the same target in this build pass don't re-query — one DB query per cross-file target, regardless of how many entities reference it.

The Validator's `_check_location_well_formed` and `_check_destination_well_formed` have already guaranteed the value is `null` or a well-shaped cross-ref dict, so this method trusts shape.

#### Cross-file refs

Cross-file refs to entities *not* built in this invocation (i.e. another file's content already in the DB from a previous build) resolve via the DB fallback in step 2 above. Operators can rebuild a single file (`wb_build zone=millholm room=bakery`) and exits/locations pointing into other files still resolve, as long as the target file has been built at some point.

#### Same-file forward refs

A top-level entity declaring `location:` to point at another top-level entity later in the same file would miss the lookup (the parent hasn't been built yet at this point in the iteration). The Builder refuses with `BuilderError` — the author has to reorder. Validator Tier 4 sees forward refs as valid (`seen_ids` is fully built before Tier 4 runs); the Builder's create-time refusal is the load-bearing distinction between "ref is correct in the abstract" and "ref can be used at this point in the build."

Note that this restriction does not apply to **destinations on exits**: the two-pass build (see `build()` above) builds every non-exit before any exit, so destination cross-refs always resolve as long as the target is in the build set, regardless of YAML order.

### `_lookup_in_db(deployment_file, deployment_id)`

```python
def _lookup_in_db(self, deployment_file: str, deployment_id: int)
```

Find an existing Evennia object tagged with the given `(deployment_file, deployment_id)` identity pair. The DB-side counterpart to the in-build `_built_by_id` map — used by `_resolve_cross_ref` to resolve cross-file refs to entities already in the database from a previous build invocation.

#### Algorithm

1. Lazy-import `evennia.utils.search.search_tag`.
2. Query `search_tag(key=deployment_file, category="wb_deployment_file")` → list of all objects from that file.
3. Filter by `wb_deployment_id` tag matching `str(deployment_id)`.
4. Return the matching object, `None` (no match), or raise `BuilderError` (multiple matches).

#### Why filter client-side instead of intersecting two `search_tag` calls

Filtering candidates from a single file-level query is cheaper than two `search_tag` calls + an intersection. The number of objects per file is typically small (the file's declared entity count) so the in-Python filter is fast.

#### Multiple matches

A pair of `(deployment_file, deployment_id)` is contractually unique across the whole world (per the [deployment-identity contract](../../docs/deployment-identity.md)). If `_lookup_in_db` finds more than one match, that's a cleanup-on-rebuild integrity failure — a previous build's orphan tagged objects weren't cleaned up. The method raises `BuilderError` rather than silently picking one, so the operator gets clear evidence of the corruption.

### `_cleanup(file_paths)`

```python
def _cleanup(self, file_paths) -> None
```

Delete every existing Evennia object tagged with any of the source files in the current build set. Called once at the start of `build()`.

#### Algorithm

For each `path` in `file_paths`:

1. `search_tag(key=path, category="wb_deployment_file")` → list of existing Evennia objects from prior deployments of that file.
2. For each found object: `obj.delete()` (Evennia handles relocation of any contents to home location automatically).
3. Increment `self.deleted_count` per deletion.

#### Why one tag-search per file (not one per entity)

A file's full state replaces whatever was there. Sweeping by file means: entities added since last build land fresh; entities removed since last build are deleted; entities changed are recreated. **Per-entity lookup would also need a "find what's here that shouldn't be" pass for orphan removal — file-level sweep gets that for free.**

#### Failure handling

Both `search_tag` and `obj.delete()` failures are wrapped as `BuilderError` with context (file path, dbref where applicable). A failure in cleanup aborts the whole build before any new object is created, so the "no partial state" invariant holds even when cleanup itself fails.

### `_apply_aliases(obj, entity)`

```python
def _apply_aliases(self, obj, entity: LoadedEntity) -> None
```

Iterate `content["aliases"]` (list of strings) and call `obj.aliases.add(alias)` for each. No-op when the field is absent or empty. The Validator's `_check_aliases_field_shape` predicate has already guaranteed the field is a list of non-empty strings if present.

### `_apply_locks(obj, entity)`

```python
def _apply_locks(self, obj, entity: LoadedEntity) -> None
```

If `content["locks"]` is present, calls `obj.locks.add(lockstring)` once. Evennia's lock system parses the semicolon-joined `<lock>:<func()>` clauses and adds/updates each named lock; locks not mentioned in the YAML keep their typeclass defaults — **partial-update behaviour, not replace-all-locks**.

The Validator's `_check_locks_field_shape` predicate has guaranteed the field is a non-empty string if present.

### `_apply_attributes(obj, entity)`

```python
def _apply_attributes(self, obj, entity: LoadedEntity) -> None
```

Iterate `content["attributes"]` (list of `{key, value, category?}` records) and call `obj.attributes.add(key, value, category=category)` for each.

Three things to note:

- **YAML wins over typeclass defaults.** Because this method runs after `create_object`, a YAML attribute with the same key as one set in `at_object_creation` (or backed by an `AttributeProperty` descriptor) overrides the default. Contract: typeclass declares defaults; YAML overrides per-instance.
- **Value can be any YAML type.** Strings, ints, floats, bools, null, lists, nested dicts — Evennia's attribute store handles arbitrary serialisable Python values. The Validator does no type check on `value`.
- **Category is optional.** When omitted, the attribute uses Evennia's default (uncategorised) attribute category.

### `_apply_tags(obj, entity)`

```python
def _apply_tags(self, obj, entity: LoadedEntity) -> None
```

Two passes:

1. **Author tags.** Each entry in `content["tags"]` is normalised to `(key, category)` via `_normalise_tag` (see below). Each pair is applied via `obj.tags.add(key, category=category)`.
2. **Auto-set deployment pair.** Always appended:
   - `obj.tags.add(entity.path, category="wb_deployment_file")`
   - `obj.tags.add(str(deployment_id), category="wb_deployment_id")`

The `wb_*` category prefix is reserved for library-controlled tags; the Validator's `_check_tags_no_reserved_category` predicate rejects any author tag using a `wb_*` category, so the auto-set pair can't collide. The author-tags-first ordering keeps the auto-set pair as the last word about identity.

### `_invoke_post_build_hook(obj, entity)`

```python
def _invoke_post_build_hook(self, obj, entity: LoadedEntity) -> None
```

Final step of `_build_one`. Duck-type-invokes `obj.wb_at_post_build()` if the typeclass defines that method; absent or non-callable, silent no-op.

```python
hook = getattr(obj, _WB_AT_POST_BUILD_ATTR, None)
if not callable(hook):
    return
try:
    hook()
except Exception as e:
    wb_log(f"{type(obj).__name__}.{_WB_AT_POST_BUILD_ATTR}() raised ...", level="ERROR")
```

Three properties to note:

- **Duck-typed and opt-in.** Consumer typeclasses that need post-apply derivation declare the method; everything else continues to work unchanged. No base class, no protocol. Preserves CLAUDE.md principle 1 — "the library does not own game concepts."
- **Fires per-entity in passes 1+2, not after pass 3+4.** Symmetric with `evennia-mob-spawner`'s `ms_at_post_spawn`. Consumer typeclasses that needed to read link-resolved attributes (assigned in pass 4) would not see them through this hook. None of the current consumers need this; if a future case appears, a second hook with post-pass-4 timing is straightforward to add.
- **Exception isolation.** Hook failures are logged via `wb_log` at `ERROR` level and the entity remains built. Consumer hook bugs cannot turn a successful apply into a `BuilderError` abort. This is a deliberate departure from the "no partial state" Builder rule because the alternative — failing the build over a consumer-side bug — would be more brittle.

Full contract, rationale, and comparison to `ms_at_post_spawn`: [docs/post-build-hook.md](../../docs/post-build-hook.md).

---

## Module function

### `_normalise_tag(tag)`

```python
def _normalise_tag(tag) -> tuple[str, str | None]
```

Turn a YAML tag entry into `(key, category)`:

- **Shorthand string** ⇒ `(string, None)` — Evennia's default category.
- **Dict form `{key, category?}`** ⇒ `(tag["key"], tag.get("category"))`.

The Validator's `_check_tags_field_shape` predicate has already rejected anything else by the time we reach this code, so this function trusts shape.

A free function (not a method) because it doesn't depend on Builder state — keeping it module-scoped makes it independently testable and reusable if other components ever need the same normalisation.

---

## Tests

Tests use `unittest.mock.patch` against `evennia.utils.create.create_object` and `evennia.utils.search.search_tag` — the Builder is exercised without a live Evennia DB, focusing on the orchestration logic (what it decides to pass to `create_object`) rather than the DB write itself.

### Cleanup + create + per-entity-construction (spike 1, 2)

| Test | Covers | Location |
|---|---|---|
| `BuilderTest.test_orphan_passes_none_as_location` | Orphan entity (location=None) → `create_object(location=None)` | [tests.py:2545](tests.py#L2545) |
| `BuilderTest.test_cross_ref_resolves_to_parent_obj` | Cross-ref location resolves to the parent's just-built mock object | [tests.py:2553](tests.py#L2553) |
| `BuilderTest.test_unresolved_cross_ref_raises` | Unresolved cross-ref raises `BuilderError` with "does not resolve" wording | [tests.py:2580](tests.py#L2580) |
| `BuilderTest.test_unresolved_cross_ref_error_names_field` | Error message identifies the field (`'location'`) carrying the unresolved ref | [tests.py:2596](tests.py#L2596) |
| `BuilderTest.test_built_by_id_populated_after_build` | `_built_by_id` map populated correctly with multi-entity, multi-file builds | [tests.py:2611](tests.py#L2611) |
| `BuilderTest.test_built_by_id_resets_between_builds` | Map resets between consecutive `build()` calls | [tests.py:2625](tests.py#L2625) |
| `BuilderTest.test_deeply_nested_each_child_uses_immediate_parent` | Depth-3 nesting: each child placed in its *immediate* parent (gem in backpack in room) | [tests.py:2636](tests.py#L2636) |
| `BuilderTest.test_same_file_forward_ref_refused` | Same-file forward `location:` ref raises `BuilderError` (author must reorder) | [tests.py:2667](tests.py#L2667) |

### Two-pass dispatch + destination resolution (spike 4 step 5b)

| Test | Covers | Location |
|---|---|---|
| `BuilderTest.test_non_exit_does_not_pass_destination_kwarg` | Non-exit entities call `create_object` without a `destination=` kwarg | [tests.py:2687](tests.py#L2687) |
| `BuilderTest.test_exit_passes_destination_to_create_object` | Exit entity → `create_object` called with both `location=` and `destination=` resolved to parent objects | [tests.py:2697](tests.py#L2697) |
| `BuilderTest.test_exits_built_after_non_exits_regardless_of_yaml_order` | Two-pass dispatch: non-exits always built before exits, even when YAML order interleaves | [tests.py:2728](tests.py#L2728) |
| `BuilderTest.test_exit_destination_resolves_via_built_by_id` | End-to-end bakery + inn + bakery→inn exit; destination resolves to inn built in pass 1 | [tests.py:2760](tests.py#L2760) |
| `BuilderTest.test_unresolved_destination_error_names_destination_field` | Unresolved destination → error names `'destination'` (not `'location'`) | [tests.py:2790](tests.py#L2790) |
| `BuilderTest.test_two_exits_pointing_at_each_other_resolve` | Bidirectional exits (north/south between two rooms) — both resolve in pass 2 | [tests.py:2944](tests.py#L2944) |

### DB tag-search fallback for cross-file refs (spike 4 step 5c)

| Test | Covers | Location |
|---|---|---|
| `BuilderTest.test_lookup_in_db_returns_match` | `_lookup_in_db` returns the single matching tagged object | [tests.py:2820](tests.py#L2820) |
| `BuilderTest.test_lookup_in_db_returns_none_when_no_file_match` | No objects from that file → returns `None` | [tests.py:2828](tests.py#L2828) |
| `BuilderTest.test_lookup_in_db_returns_none_when_no_id_match` | File has objects but none with the requested deployment_id → returns `None` | [tests.py:2833](tests.py#L2833) |
| `BuilderTest.test_lookup_in_db_filters_by_deployment_id` | Multiple file matches, deployment_id filter picks the right one | [tests.py:2843](tests.py#L2843) |
| `BuilderTest.test_lookup_in_db_raises_on_multiple_matches` | Cleanup integrity failure (two objects with same identity pair) → `BuilderError` | [tests.py:2853](tests.py#L2853) |
| `BuilderTest.test_cross_ref_falls_through_to_db_on_in_build_miss` | In-build miss → DB fallback hit → `create_object(location=db_obj)` | [tests.py:2868](tests.py#L2868) |
| `BuilderTest.test_db_fallback_caches_back_into_built_by_id` | Two refs to same DB-only target → one DB query (cache-back) | [tests.py:2894](tests.py#L2894) |
| `BuilderTest.test_unresolved_when_db_misses_too` | In-build miss + DB miss → `BuilderError` with "neither built nor present in the DB" wording | [tests.py:2926](tests.py#L2926) |

### Pass 4 — links assignment

`_run_pass_4(file_paths_in_scope)` walks each in-scope file's `links:` and applies each entry via `_apply_one_link`, which resolves both `entity` and `points_to` through the same `_resolve_cross_ref` helper passes 1/2 use, then dispatches on the `attribute` field: bare names (no `[` or `]`) call `entity_obj.attributes.add(attribute, points_to_obj, category=category)`; subscript paths (either bracket present, e.g. `'destinations["foo"]["bar"]'`) call `_assign_via_subscript_path` which parses the path with `ast.parse`, walks the leading attribute and subscript chain, assigns at the leaf, and re-saves the top-level attribute. Triggering on either bracket (rather than just `[`) means defensively-malformed inputs like `'foo]'` route to the path branch and fail loudly via the parser instead of being silently set as garbage attribute names. See [docs/links.md](../../docs/links.md) for the design rationale and the path-syntax preconditions (top-level attribute must already exist; `category:` not allowed with path form).

| Test | Covers | Location |
|---|---|---|
| `BuilderPass4LinksTest.test_no_links_section_is_noop` | File without a `links:` key — pass 4 walks but does nothing | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_same_file_pair_links_both_assigned` | Two-link reciprocal pair both fire; each entity's `attributes.add` called once with the partner mock | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_link_with_category_applied` | Optional `category:` propagates through to `attributes.add(category=...)` | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_self_link_assigns` | `entity == points_to` accepted; assignment fires with the same object on both sides | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_unresolved_entity_raises` | Unresolved `entity` raises `BuilderError` naming `links[i].entity` | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_unresolved_points_to_raises` | Unresolved `points_to` raises `BuilderError` naming `links[i].points_to` | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_links_in_out_of_scope_files_skipped` | Links declared in a file not in build scope do not fire | [tests.py](tests.py) |
| `BuilderPass4LinksTest.test_link_after_pass3_can_resolve_restored_entity` | Pass 4 runs after pass 3 — link to entity in scope resolves via cache | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_assigns_into_nested_dict` | Path-form attribute walks an existing dict-of-dicts and assigns the resolved cross-ref at the leaf, leaving sibling literals untouched | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_bare_attribute_still_works` | Regression: bare-name attribute still goes through `attributes.add(...)` unchanged | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_with_category_refused` | Combining path syntax with `category:` raises `BuilderError` (category only applies to bare names) | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_missing_top_level_attribute_refused` | Path-form refuses with `BuilderError` if the leading top-level attribute doesn't exist on the entity | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_bad_navigation_refused` | Mid-walk navigation failure (key/index missing) raises `BuilderError` with "cannot navigate" wording | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_malformed_syntax_refused` | Malformed Python syntax (e.g. unclosed bracket) raises `BuilderError` with "not valid Python" wording | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_close_bracket_only_refused` | Defence-in-depth: an attribute string with `]` but no `[` (e.g. `'dict(thing]'`) routes to the path branch and refuses with "not valid Python" wording, instead of being silently set as a garbage attribute name | [tests.py](tests.py) |
| `BuilderPass4LinksSubscriptPathTest.test_subscript_path_with_int_index_into_list` | Integer subscripts (e.g. `routes[0]["to"]`) are distinguished from string subscripts and walk into list-shaped placeholders | [tests.py](tests.py) |
