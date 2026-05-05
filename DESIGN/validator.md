# Validator

The Validator runs every check the library performs against loaded entities, gathers all findings, and either passes the entities through unchanged or refuses with a complete list of reasons. One Validator class, two invocation paths: in-game `wb_build` and standalone `wb-validate` both construct a Validator and call `validate(entities)`. Adding a check lands once and fires in both paths.

## Pipeline position

```
Reader → Definitions → Finder → Loader → Validator → Builder
                                          ▲
                                       (this doc)
```

Validator consumes `LoadedEntity` objects from the Loader. It mutates nothing in the Evennia DB; the Builder is the only writer. Any check that needs Builder state to fire belongs in the Builder, not here.

## Two-tier check architecture

Two kinds of check fire per entity in a single pass:

### Stateless predicates

Pure functions: `(entity) -> finding | None`. Each one inspects a single semantic concern and returns `None` (pass) or a finding string (fail). Registered in the `PER_ENTITY_PREDICATES` class tuple.

```python
def _check_deployment_id_well_formed(entity) -> str | None:
    ...
    return None  # or "{path}: missing required field 'deployment_id'"
```

Adding a new stateless check: write the function, append to the tuple. Predicates are pure, so they're trivially testable in isolation and order-independent.

### Stateful checks

Methods on Validator that read and update accumulating state — currently `self.seen_ids: dict[str, set[int]]`, the per-file id index. Used for checks that need to know about other entities (duplicate detection, eventually backward cross-reference resolution).

Stateful checks **only run on entities that pass every stateless predicate** for that entity. This keeps the index from being polluted with garbage (e.g. a non-integer `deployment_id` never enters a `set[int]`).

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
| Stateless | `_check_deployment_id_well_formed` | field missing, not an integer (rejects `bool`), or negative |
| Stateful | `_check_and_record_unique_id` | `deployment_id` already declared in the same file |

## Cross-reference resolution (next tier)

Cross-references between entities (e.g. `destination: { deployment_id: 34 }`) need a third tier: a deferred-check phase that runs **after** the per-entity loop, against the fully-built `seen_ids` index. Backward refs resolve in-pass; forward refs (target processed later) defer to that post-loop phase.

The check itself is straightforward — given a `(deployment_file, deployment_id)` pair, is it in the index? — but the YAML shape of cross-refs (which fields carry them, how same-file defaults work) is settled in [deployment-identity.md](deployment-identity.md) and lands when the Builder grows exit/contents support.

## Out of scope (deferred)

- **Severity levels.** v0 has only "error". Warnings (e.g. "this entity has no name field") land if/when there's a meaningful distinction; right now anything worth flagging is worth refusing for.
- **Per-file message grouping.** Findings include the file path inline; explicit grouping (`forest.yaml: 3 issues`) lands if operator output gets noisy at scale.
- **Schema-driven validation.** Hand-written predicates are clearer than a YAML schema for the small number of fields we currently care about. Revisit if predicates start duplicating each other.
