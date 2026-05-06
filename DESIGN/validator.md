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

| Tier | Scope | Name | Failure |
|---|---|---|---|
| 1 — Stateless | every entity | `_check_deployment_id_well_formed` | field missing, not an integer (rejects `bool`), or negative |
| 1 — Stateless | every entity | `_check_name_well_formed` | field missing, not a string, or empty/whitespace |
| 1 — Stateless | every entity | `_check_typeclass_well_formed` | field missing, not a string, or empty/whitespace |
| 1 — Stateless | every entity | `_check_description_field_shape` | `description` (optional) present but not a string |
| 1 — Stateless | every entity | `_check_aliases_field_shape` | `aliases` (optional) not a list, or items not non-empty strings |
| 1 — Stateless | every entity | `_check_locks_field_shape` | `locks` (optional) not a non-empty string |
| 1 — Stateless | every entity | `_check_attributes_field_shape` | `attributes` (optional) not a list, item not a mapping, missing/empty/non-string `key`, missing `value`, or non-string `category` |
| 1 — Stateless | every entity | `_check_tags_field_shape` | `tags` not a list, items not string-or-mapping, dict missing/empty `key`, non-string `category` |
| 1 — Stateless | every entity | `_check_tags_no_reserved_category` | author tag uses category in the reserved `wb_*` namespace |
| 1 — Stateless | **top-level only** | `_check_location_well_formed` | field missing, or non-null (cross-ref dict support deferred to spike 4) |
| 2 — Stateful | every entity | `_check_and_record_unique_id` | `deployment_id` already declared in the same file |
| 3 — Evennia-runtime | every entity | `_check_typeclass_resolvable` | typeclass dotted path can't be imported, or class missing on the loaded module |

### Top-level vs nested scope

Some predicates run on every entity (top-level *and* nested-inside-`contents:`/`exits:`); others apply only to top-level entities, where YAML structure doesn't itself declare position.

`_check_location_well_formed` is the first of the latter kind. A nested entity's location is implicit in the YAML structure that nests it, so requiring `location:` on those would force redundant declarations; conversely, a top-level entity has no structural signal, so explicit `location:` is what disambiguates orphan rooms from accidentally-orphaned objects the author meant to place somewhere.

The Validator stores these in two tuples:

- `PER_ENTITY_PREDICATES` — runs on every entity.
- `TOP_LEVEL_PREDICATES` — runs only on top-level entities. Today every entity is top-level (recursion lands in spike 2), so the loop runs both uniformly; once nested entities exist, the validator's iteration will distinguish via a `LoadedEntity` flag and skip `TOP_LEVEL_PREDICATES` for nested ones.

When recursion lands, a sibling predicate (refusing `location:` *on* nested entities) joins the picture, since author-set location on a nested entity would conflict with the implicit nesting declaration.

### Mandatory fields

Every entity must declare:

- `deployment_id` — non-negative integer
- `name` — non-empty string
- `typeclass` — non-empty string
- `location` — explicit (currently `null` only) — *top-level entities only*

No defaults, no fallbacks. The Builder relies on the validator's guarantees to skip its own shape checks.

## Cross-reference resolution (Tier 4, planned)

Cross-references between entities (e.g. `destination: { deployment_id: 34 }`) need a deferred-check phase that runs **after** the per-entity loop, against the fully-built `seen_ids` index. Backward refs resolve in-pass; forward refs (target processed later) defer to that post-loop phase.

The check itself is straightforward — given a `(deployment_file, deployment_id)` pair, is it in the index? — but the YAML shape of cross-refs (which fields carry them, how same-file defaults work) is settled in [deployment-identity.md](deployment-identity.md) and lands when the Builder grows exit/contents support.

## Out of scope (deferred)

- **Severity levels.** v0 has only "error". Warnings (e.g. "this entity has no name field") land if/when there's a meaningful distinction; right now anything worth flagging is worth refusing for.
- **Per-file message grouping.** Findings include the file path inline; explicit grouping (`forest.yaml: 3 issues`) lands if operator output gets noisy at scale.
- **Schema-driven validation.** Hand-written predicates are clearer than a YAML schema for the small number of fields we currently care about. Revisit if predicates start duplicating each other.
