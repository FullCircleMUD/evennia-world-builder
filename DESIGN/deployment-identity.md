# Deployment Identity

The load-bearing identity scheme for everything world-builder creates in the consumer's database. Every downstream subsystem — Validator, Builder, cleanup, cross-references, partial deploys — operates in terms of this contract.

## Principle

**Every Evennia object world-builder creates is identified by a composite of two values: `(deployment_file, deployment_id)`.** This pair is globally unique across the whole world. It is the handle the deployment system uses to find an object on rebuild, the handle authors use to point one entity at another, and the handle cleanup uses to scope a redeploy.

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

## Cross-references

Cross-references between entities use the composite identity. The author specifies only what differs from the current file's scope:

```yaml
# Same-file: deployment_file inferred as "this file"
destination: { deployment_id: 34 }

# Cross-file: deployment_file explicit
destination: { deployment_file: millholm/bakery.yaml, deployment_id: 1 }
```

The amount of qualification scales with how far the reference points: same-file refs stay terse; cross-file refs name the target file. This makes "the file is the atomic unit" visible in the syntax — refs that stay in the unit you're editing don't need to name it.

## Cleanup model

Redeploying a file is a one-tag sweep:

1. Delete every existing object tagged with the file's `deployment_file` value.
2. Create fresh from the current YAML; tag each new object with `deployment_file` and `deployment_id`.

This handles all three edit shapes uniformly: entities added, entities removed, entities changed. No reconciliation, no diffing — the file's full state replaces whatever was there.

## Validator's role

The Validator (see [validator.md](validator.md)) enforces the contract. Currently shipped:

- **Every leaf entity declares `deployment_id`** — mandatory, non-negative integer, `bool` rejected even though it's an `int` subclass.
- **No duplicate `deployment_id`s within a file** — flagged as the per-file `{deployment_file: {ids}}` index is built incrementally during the per-entity pass.

Pending (lands when cross-reference YAML shape is settled):

- **Cross-reference resolution** — for any `(deployment_file, deployment_id)` reference, the target file exists and the referenced entity exists in that file.

Validator scope is per-file for the uniqueness check, plus the `{deployment_file: {ids}}` index for cross-ref resolution. Memory footprint stays small even for large repos: one file's content at a time + an integer-set index.

## Out of scope (deferred)

- **Level-named tags** (e.g. `zone=millholm` as separate Evennia tags). Useful for partial-scope queries ("all rooms in millholm"), but not required for identity. Can be layered on later as additional tags alongside `deployment_file`.
- **Partial cleans within a file.** Authors who want a finer redeploy granularity split the file.
- **Cross-repo references.** Single content-repo per build for v0.
- **Migration tooling for folder reorgs.** Handle when the case lands.
