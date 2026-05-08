# Validator

The Validator runs every check the library performs against loaded entities, gathers all findings, and either passes the entities through unchanged or refuses with a complete list of reasons. One Validator class, two invocation paths: in-game `wb_build` and standalone `wb-validate` both construct a Validator and call `validate(entities)`. Adding a check lands once and fires in both paths.

## Pipeline position

```
Reader → Definitions → Finder → Loader → Validator → Builder
                                          ▲
                                       (this doc)
```

Validator consumes `LoadedEntity` objects from the Loader. It mutates nothing in the Evennia DB; the Builder is the only writer. Any check that needs Builder state to fire belongs in the Builder, not here.

## Predicate-tier architecture

Checks are grouped by the *situation* they apply to. The validator runs the tiers appropriate to its caller — controlled by a couple of orthogonal switches, not by environment-detection heuristics inside the predicates themselves.

### Tier 1 — Stateless predicates (always run)

Pure functions: `(entity) -> finding | None`. Each one inspects a single semantic concern and returns `None` (pass) or a finding string (fail). Registered in the `PER_ENTITY_PREDICATES` class tuple. Run on every entity in every Validator invocation regardless of caller.

```python
def _check_deployment_id_well_formed(entity) -> str | None:
    ...
    return None  # or "{path}: missing required field 'deployment_id'"
```

Adding a new stateless check: write the function, append to the tuple. Predicates are pure, so they're trivially testable in isolation and order-independent.

### Tier 2 — Stateful per-file checks (always run)

Methods on Validator that read and update accumulating state — currently `self.seen_ids: dict[str, set[int]]`, the per-file id index. Used for checks that need to know about other entities loaded in the *same call* (duplicate detection; eventually backward cross-reference resolution).

Stateful checks **only run on entities that pass every stateless predicate** for that entity. This keeps the index from being polluted with garbage (e.g. a non-integer `deployment_id` never enters a `set[int]`).

### Tier 3 — Evennia-runtime predicates (caller opt-in)

Stateless predicates that need the consumer's typeclasses + Evennia/Django runtime to be importable. Registered in `EVENNIA_ONLY_PREDICATES`. Run only when the caller passes `evennia_runtime=True` to `Validator.__init__`.

```python
def _check_typeclass_resolvable(entity) -> str | None:
    # imports the dotted typeclass path; flags ImportError / AttributeError
    ...
```

`wb-validate` (CLI) leaves `evennia_runtime` at its default `False` — the consumer's gamedir is generally not on `sys.path` in CI, so attempting these checks would either crash or false-positive.

`wb_build` (Evennia command) passes `evennia_runtime=True` — the gamedir is fully loaded by definition; runtime checks can fire and refuse before the Builder is invoked.

### Tier 4 — Cross-ref resolution (caller opt-in)

Cross-reference resolution against the full `seen_ids` index — for both `location:` and `destination:` dicts. Runs only when the caller passes `resolve_cross_refs=True` to `Validator.__init__`, asserting they've supplied whole-repo entities. Production callers (`wb_build` whole-repo pre-validation, `wb-validate` CLI) opt in; tests default off so narrow-scope predicate tests don't flap on dangling refs they don't care about.

## Two orthogonal switches

The validator's behaviour is set by two constructor flags:

| Switch | Set by | Controls |
|---|---|---|
| `evennia_runtime` | caller (`wb_build` → True; `wb-validate` → False) | Tier 3 predicates |
| `resolve_cross_refs` | caller (whole-repo callers → True; tests/scope-only callers → False) | Tier 4 phase |
| `file_metadata` | caller (orchestrators pass `LoadResult.file_metadata`; tests can construct narrowly-scoped dicts) | File-level Tier 1 shape checks (`incoming_exits:`, `links:`) + Tier 4 walk over both for cross-ref resolution |

No environment detection inside predicates. No try/except heuristics that conflate "wrong env" with "wrong path." The caller asserts what tier of check it can support; the validator runs the appropriate set.

### Behaviour matrix

|  | `wb-validate` (CI) | `wb-validate` (local dev) | `wb_build` (CI gate off) | `wb_build` (CI gate on, no force) | `wb_build` (CI gate on + `--force-validate`) |
|---|---|---|---|---|---|
| Loads | whole repo | whole repo | whole repo | scope only | whole repo |
| Tier 1 (stateless) | always | always | always | always | always |
| Tier 2 (stateful per-file) | always | always | always | always | always |
| Tier 3 (Evennia-runtime) | skipped | skipped (no gamedir loaded) | runs | runs | runs |
| Tier 4 (cross-ref resolution) | runs | runs | runs | skipped (trust CI) | runs |

## Complete refusal, not halt-on-first-error

Per CLAUDE.md principle 4: gather every finding from every entity, then either pass cleanly or raise `ValidatorError` once at the end. Operators see the complete list of problems in one run, not a trickle of errors that change as they fix them.

```python
for entity in entities:
    if not self._run_stateless_predicates(entity):
        continue
    self._check_and_record_unique_id(entity)

if self.errors:
    raise ValidatorError(...)
```

Findings flow through `_record_finding()` so messages and errors stay in sync; on error, callers still read `validator.messages` to surface the full list before halting.

## Caller contract

Validator does no I/O. Callers print `validator.messages` after `validate()` returns or raises:

- `wb-validate` CLI prints to stdout, returns exit code 1 on `ValidatorError`.
- `wb_build` Evennia command sends each message via `caller.msg()`, refuses to call Builder.

Same Validator, two output channels.

## Currently shipped checks

| Tier | Name | Failure |
|---|---|---|
| 1 — Stateless | `_check_deployment_id_well_formed` | field missing, not an integer (rejects `bool`), or negative |
| 1 — Stateless | `_check_name_well_formed` | field missing, not a string, or empty/whitespace |
| 1 — Stateless | `_check_typeclass_well_formed` | field missing, not a string, or empty/whitespace |
| 1 — Stateless | `_check_location_well_formed` | field missing; or value is neither `null` nor a strict `{deployment_file: non-empty str, deployment_id: non-negative int}` cross-ref dict (extra keys refused, `bool` excluded from int) |
| 1 — Stateless | `_check_destination_well_formed` | `destination:` (optional) present but not a strict `{deployment_file, deployment_id}` cross-ref dict; same shape rules as `location:` but `null` is also rejected (an exit must point somewhere) |
| 1 — File-level | `_check_file_metadata_shape` (method, dispatches to `_check_incoming_exits_shape` and `_check_links_shape`) | `file_metadata[path]["incoming_exits"]` (optional) not a list; or any list entry not a strict `{deployment_file, deployment_id}` cross-ref dict. `file_metadata[path]["links"]` (optional) not a list; any entry not a `{entity, attribute, points_to[, category]}` dict; bad `entity` / `points_to` cross-ref shape; non-string or empty `attribute` / `category`; unexpected keys. Runs once per file path that declared the registry, not once per entity. Findings name the file path and array index (e.g. `path: links[2]`). See [links.md](links.md). |
| 1 — Stateless | `_check_location_not_null_when_destination_present` | entity declares `destination:` but `location:` is null — an exit must live in a room |
| 1 — Stateless | `_check_no_author_location_on_nested` | nested entity (`is_nested=True`) had a `location:` key in the original YAML — the Loader synthesises one and silently overwriting author intent would be a "fails loudly" violation |
| 1 — Stateless | `_check_description_field_shape` | `description` (optional) present but not a string |
| 1 — Stateless | `_check_aliases_field_shape` | `aliases` (optional) not a list, or items not non-empty strings |
| 1 — Stateless | `_check_locks_field_shape` | `locks` (optional) not a non-empty string |
| 1 — Stateless | `_check_attributes_field_shape` | `attributes` (optional) not a list, item not a mapping, missing/empty/non-string `key`, missing `value`, or non-string `category` |
| 1 — Stateless | `_check_tags_field_shape` | `tags` not a list, items not string-or-mapping, dict missing/empty `key`, non-string `category` |
| 1 — Stateless | `_check_tags_no_reserved_category` | author tag uses category in the reserved `wb_*` namespace |
| 2 — Stateful | `_check_and_record_unique_id` | `deployment_id` already declared in the same file (top-level + nested share one namespace) |
| 3 — Evennia-runtime | `_check_typeclass_resolvable` | typeclass dotted path can't be imported, or class missing on the loaded module |
| 3 — Evennia-runtime | `_check_destination_required_for_exit_typeclass` | typeclass inherits from Evennia's `DefaultExit` but no `destination:` is set |
| 3 — Evennia-runtime | `_check_destination_forbidden_for_non_exit_typeclass` | typeclass does NOT inherit from `DefaultExit` but `destination:` is set |
| 4 — Cross-ref | `_check_cross_refs` (post-loop phase) | any `location:` or `destination:` cross-ref (per-entity), `incoming_exits[N]` cross-ref (file-level), or `links[N].entity` / `links[N].points_to` cross-ref (file-level) `(deployment_file, deployment_id)` not present in the `seen_ids` index built during the per-entity pass. All three categories share the same `_check_one_cross_ref` helper. |

All Tier 1 / Tier 2 predicates run on every entity uniformly — top-level and nested alike. The validator's earlier `TOP_LEVEL_PREDICATES` split (location-only-required-on-top-level) was collapsed when the Loader landed `contents:` recursion: since the Loader now synthesises `content["location"]` as a cross-ref dict on every nested entity at flatten time, `_check_location_well_formed` passes uniformly without needing a tier split. `_check_no_author_location_on_nested` gates on the LoadedEntity's `is_nested` flag directly.

### Mandatory fields

Every entity must declare:

- `deployment_id` — non-negative integer, unique within its file
- `name` — non-empty string
- `typeclass` — non-empty string
- `location` — explicit (`null` for orphan, or a `{deployment_file, deployment_id}` cross-ref dict)

For a nested entity the Loader synthesises `location:` automatically (pointing at the parent), so authors only need to declare it on top-level entities — and authoring it on a nested entity is refused.

No defaults, no fallbacks. The Builder relies on the validator's guarantees to skip its own shape checks.

## Cross-reference resolution

Two layers, each owning a different correctness check:

- **Validator Tier 4 — existence.** When `resolve_cross_refs=True`, the validator's post-loop phase walks every entity's `location:` and `destination:` cross-refs and verifies the `(deployment_file, deployment_id)` pair appears in `seen_ids`. Catches "this ref points at nothing in the build set" — dangling refs, deployment_id typos, file-name typos. Forward refs within the same file resolve correctly because `seen_ids` is fully built before Tier 4 runs.
- **Builder runtime — usability.** The Builder's `_resolve_cross_ref` does a single dict lookup against its `_built_by_id` map at create time (see [builder.md](builder.md)). For same-file backward refs this always hits (depth-first pre-order). Same-file forward refs miss and are refused at create time — a separate decision from Tier 4 (which says the ref is valid in the abstract; the Builder says "but it can't be used yet at this point in the build"). Cross-file refs to entities NOT in the current build invocation will fall through to a DB tag-search lookup once spike 4 step 5 lands; until then they refuse with `BuilderError`.

The two layers are independent — Tier 4 catches *correctness* problems before any DB mutation, and the Builder enforces *ordering* problems at create time.

## Out of scope (deferred)

- **Severity levels.** v0 has only "error". Warnings (e.g. "this entity has no name field") land if/when there's a meaningful distinction; right now anything worth flagging is worth refusing for.
- **Per-file message grouping.** Findings include the file path inline; explicit grouping (`forest.yaml: 3 issues`) lands if operator output gets noisy at scale.
- **Schema-driven validation.** Hand-written predicates are clearer than a YAML schema for the small number of fields we currently care about. Revisit if predicates start duplicating each other.
