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

### Tier 4 — Cross-repo checks (controlled by what entities are passed)

Currently planned, not yet shipped. Cross-reference resolution against the full `seen_ids` index requires that the validator was given the *whole repo's* entities in the call. That's a property of *what* the caller passed in (an empty-query `Loader.load(finder.find())` result), not a separate switch — see [validation-gating.md](validation-gating.md) for when this happens.

## Two orthogonal switches

The validator's behaviour is set by exactly two things:

| Switch | Set by | Controls |
|---|---|---|
| `evennia_runtime` (constructor arg) | caller (`wb_build` → True; `wb-validate` → False) | Tier 3 predicates |
| Whether entities are whole-repo or scoped | what `loader.load(...)` was called with | Tier 4 checks |

No environment detection inside predicates. No try/except heuristics that conflate "wrong env" with "wrong path." The caller asserts what tier of check it can support; the validator runs the appropriate set.

### Behaviour matrix

|  | `wb-validate` (CI) | `wb-validate` (local dev) | `wb_build` |
|---|---|---|---|
| Tier 1 (stateless) | always | always | always |
| Tier 2 (stateful per-file) | always | always | always |
| Tier 3 (Evennia-runtime) | skipped | skipped (no gamedir loaded) | runs |
| Tier 4 (cross-repo) | runs (whole-repo loaded) | runs (whole-repo loaded) | runs only on `wb_build all` or `--force-validate` |

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
| 1 — Stateless | `_check_no_author_location_on_nested` | nested entity (`is_nested=True`) had a `location:` key in the original YAML — the Loader synthesises one and silently overwriting author intent would be a "fails loudly" violation |
| 1 — Stateless | `_check_description_field_shape` | `description` (optional) present but not a string |
| 1 — Stateless | `_check_aliases_field_shape` | `aliases` (optional) not a list, or items not non-empty strings |
| 1 — Stateless | `_check_locks_field_shape` | `locks` (optional) not a non-empty string |
| 1 — Stateless | `_check_attributes_field_shape` | `attributes` (optional) not a list, item not a mapping, missing/empty/non-string `key`, missing `value`, or non-string `category` |
| 1 — Stateless | `_check_tags_field_shape` | `tags` not a list, items not string-or-mapping, dict missing/empty `key`, non-string `category` |
| 1 — Stateless | `_check_tags_no_reserved_category` | author tag uses category in the reserved `wb_*` namespace |
| 2 — Stateful | `_check_and_record_unique_id` | `deployment_id` already declared in the same file (top-level + nested share one namespace) |
| 3 — Evennia-runtime | `_check_typeclass_resolvable` | typeclass dotted path can't be imported, or class missing on the loaded module |

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

Cross-reference resolution happens on two levels, by two different actors:

- **Same-file backward refs are resolved by the Builder in-pass** via its `_built_by_id` map (see [builder.md](builder.md)). The Loader emits depth-first pre-order so a nested entity's parent is always already built when its location dict is resolved. No validator-side work needed here — the Builder fails loudly with `BuilderError` if the lookup misses.
- **Same-file forward refs and cross-file refs** are still pending. A future Tier 4 phase against the per-file `seen_ids` index will refuse same-file forward refs at validate time (so the failure surfaces alongside other findings, before any DB mutation); cross-file refs will get a corresponding cross-repo index in spike 4 along with the Builder's tag-search fallback for parents already in the DB from a previous invocation.

## Out of scope (deferred)

- **Severity levels.** v0 has only "error". Warnings (e.g. "this entity has no name field") land if/when there's a meaningful distinction; right now anything worth flagging is worth refusing for.
- **Per-file message grouping.** Findings include the file path inline; explicit grouping (`forest.yaml: 3 issues`) lands if operator output gets noisy at scale.
- **Schema-driven validation.** Hand-written predicates are clearer than a YAML schema for the small number of fields we currently care about. Revisit if predicates start duplicating each other.
