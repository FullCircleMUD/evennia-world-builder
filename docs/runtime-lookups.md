# Runtime Lookups

The library exposes a small `api` module of helpers that consumer **game code** — commands, scripts, typeclass methods — calls at runtime to resolve a stable `entity_id` to an Evennia object or dbref.

This is a second public surface alongside the in-game admin commands (`wb_build`, see [library-commands.md](library-commands.md)) and the build-time pipeline classes (Builder/Validator/Loader/Finder). Those run during a build; the helpers here run during normal gameplay.

## Motivation

Builder-authored objects are identified by their `entity_id` (see [deployment-identity.md](deployment-identity.md)). It is **constant across redeploys**: a `wb_build` cleans up and recreates the underlying Evennia objects, so dbrefs change, but the id stays the same. It also survives the entity being moved to a different file.

Consumer game code that needs to refer to a specific library-built object — "the bakery counter", "the shrine altar", "the room the quest NPC retreats to at night" — should resolve the id at runtime rather than hard-coding a dbref. The hard-coded dbref goes stale on the next `wb_build`; the id survives.

## Naming convention

These functions carry the `wb_` prefix — same convention as the in-game admin commands (`wb_build`) and the standalone CLI (`wb_validate`). The rule that produces this: **symbols invoked at a flat call site without their namespace get prefixed; symbols accessed through their namespace don't.**

- `Builder`, `Loader`, `Finder`, etc. are unprefixed because they're called as `Builder(...)` — the PascalCase already signals "type from the library" and they're constructed inside the pipeline, not sprinkled through consumer code.
- `wb_build` and `wb_validate` are prefixed because players type the bare command name and shell users invoke the bare CLI name — the prefix is what tells them (and stops them colliding with) consumer-side equivalents.
- `wb_lookup_dbref` / `wb_lookup_object` follow the same logic: they'll appear scattered through consumer game code (commands, scripts, typeclass methods), and at every call site the package namespace is gone — only the bare function name remains. The prefix announces "library-provided" the same way `wb_build` does.

## Surface

Imported from the package root:

```python
from evennia_world_builder import wb_lookup_dbref, wb_lookup_object
```

### `wb_lookup_dbref(entity_id) -> str | None`

Returns the matching object's dbref as Evennia's `#<id>` string (drop-in for `search()` calls, command handlers, `obj.dbref` comparisons), or `None` if no object matches.

**No typeclass instantiation.** Goes straight to `ObjectDB` via the Django ORM and reads back the integer primary key only — no `at_init` hook fires, no idmapper cache entry warms. Use this when the dbref is all you need.

### `wb_lookup_object(entity_id) -> Object | None`

Returns the live typeclass instance, or `None` if no object matches. Use this when you actually need to operate on the object (read attributes, call methods, etc.) rather than just identify it.

## Contract

- **Returns `None` on no match.** A missing object at runtime is a normal case (the build may not have run yet, the file may be redeployed in pieces, the author may have removed the entity). Callers check for `None` rather than wrapping in try/except.
- **Raises `ApiError` on multiple matches.** Should be unreachable if the Builder's cleanup-on-rebuild invariant holds — an `entity_id` is globally unique, and the Validator refuses a repo that declares one twice. If it fires, cleanup integrity has broken and the operator needs to know loudly.
- **The argument mirrors the YAML.** `entity_id` is the value the author declared on the entity.
- **No Evennia bootstrap done for you.** These run inside Evennia; if you invoke them from a context where Evennia isn't initialised (a standalone script, a fixture loader before `django.setup()`), the lazy imports will fail. Same constraint as every other call into Evennia.

## Implementation

Both functions share the same indexed query implemented in the private `_query_object_ids` helper: a single join on `ObjectDB`'s `db_tags` M2M selecting the `wb_entity_id` tag.

The query filters on `db_key__iexact` and `db_category__iexact` (both indexed columns on the `Tag` model), plus `db_tagtype__isnull=True` (excludes alias/permission tags) and `db_model__iexact="objectdb"` (scopes the join to object-side tags). This mirrors what Evennia's own `get_by_tag` does for normal tags.

### Complexity

**O(log n) on the Tag-table size.** One indexed B-tree seek. Independent of how many entities the file declares — a 1-room file and a 500-room file cost the same query.

`Builder._lookup_in_db` reaches the same result through Evennia's `search_tag`, which inflates typeclasses; the runtime lookups skip that cost.

### Why not reuse `Builder._lookup_in_db`?

- Different lifecycle. Builder is a one-shot apply-time pipeline; runtime helpers may be called per-tick from game code.
- Builder carries build-time state (`_built_by_id`, `file_metadata`, `_reader`) that's irrelevant for a runtime lookup.
- `wb_lookup_dbref`'s "no instantiation" goal needs a different implementation path (`values_list("id", flat=True)`) than Builder's `search_tag`-based fetch.

The two paths could converge if Builder's lookup were ported to the same indexed query, but that's a separate refactor — `_lookup_in_db` is called rarely (once per cross-file ref during a build) so the gain is small.

## Out of scope (deferred)

- **Bulk lookups** (`wb_lookup_dbrefs(file_id)` → every dbref from one file). Would let consumers enumerate "every room in this zone"-style queries. Mechanism is straightforward — query `wb_file_id` instead — but no concrete consumer need yet.
- **Reverse lookup** (object → `entity_id`). The data lives on `obj.tags` already; ergonomics question is whether a helper is worth shipping.
- **Caching layer.** The query is fast enough that adding a cache would just introduce invalidation problems on rebuild. Reconsider if profiling ever shows it dominating a hot path.
- **`AccountDB` / `ScriptDB` variants.** The library only creates objects today. If `Account` or `Script` creation ever lands, these helpers grow siblings on the appropriate model.

## See also

- [deployment-identity.md](deployment-identity.md) — the identity scheme these helpers resolve against.
- [builder.md](builder.md) — the tag-write side of the same scheme (Builder's `_apply_tags` sets the two reserved categories).
- [library-commands.md](library-commands.md) — the other public surface aimed at running Evennia (admin commands, not game code).
