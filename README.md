# evennia-world-builder

Declarative YAML-driven world authoring for [Evennia](https://www.evennia.com/).

World content (rooms, exits, fixtures, descriptions) is expressed as data; an idempotent loader applies it to the running game's database via Evennia's typeclass system.

## Status

**Feature-complete.** Single-entity build, `contents:` recursion, `exits:` recursion, same-file and cross-file cross-references, cross-file rebuild-dependency restoration via `incoming_exits:`, and cross-entity attribute references via `links:` all working end-to-end. The canonical YAML file shape ("shape 3" — top-level mapping with `entities:` key) lets authors mix multiple rooms with file-level metadata in one file. Surgical rebuilds (`wb_build zone=X room=Y`) keep cross-file exits alive automatically — operators can rebuild a single file without rebuilding everything that references it. A `wb_at_post_build` typeclass hook lets consumers derive state after the library's apply pipeline finishes. 409 tests green, live-verified on a two-process sharded deployment. See [docs/progress.md](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/docs/progress.md) for the running milestone log.

## What's working today

The library's pipeline reads → validates → builds YAML world content into Evennia, idempotently:

- Fetch YAML from a configured source (GitHub or local filesystem) via the `Reader` abstraction.
- Walk the per-folder `index.yaml` manifest tree to find entities matching an operator query.
- Every leaf YAML file uses one canonical shape — a top-level mapping with an `entities:` key whose value is a list of entity mappings. File-level keys (`incoming_exits:` for cross-file dependency restoration, `links:` for cross-entity attribute references, future extensions) live alongside `entities:`.
- **Flatten `contents:` and `exits:` blocks** into individual `LoadedEntity` records; nested entities inherit their parent's source path and get a Loader-synthesised `location:` cross-ref pointing at the parent. Recursion is depth-unlimited (chest in room contains key contains gem).
- **Loader returns a `LoadResult(entities, file_metadata)`** — the entity list plus a `{file_path → {file-level keys}}` dict that downstream consumers (Validator, Builder) read for file-level concerns.
- Validate every entity through a predicate-tier pipeline (mandatory fields, shape, typeclass-resolvability, per-file `deployment_id` uniqueness, reserved tag categories, `location:` either null or `{deployment_file, deployment_id}` cross-ref, `destination:` shape + presence consistent with whether the typeclass inherits from `DefaultExit`, optional `home:` either null or cross-ref), gathering all findings before any DB mutation. A Tier 4 deferred phase resolves every cross-ref (entity-level location/destination/home, file-level `incoming_exits:`, and file-level `links:` entity/points_to) against the per-file `seen_ids` index, catching dangling refs and id/file typos at validate time.
- **Four-pass build:** non-exits in pass 1, exits in pass 2 (with destinations resolved against the just-built rooms), pass 3 walks each file's `incoming_exits:` registry and restores any cross-file dependent exits that were missing, then pass 4 walks each file's `links:` and applies cross-entity attribute references (e.g. door pairs sharing `other_side`) — see [docs/links.md](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/docs/links.md).
- **Cross-file refs resolve via DB tag-search fallback** when the target isn't in the current build set. Operators can rebuild a single file (`wb_build zone=millholm room=bakery`) and exits/locations pointing into other files still resolve as long as the target file has been built at some point.
- **`incoming_exits:` registration** at the file level lets a file declare cross-file dependents that should be kept alive on isolated rebuilds. When a registered target's file isn't in scope and the target was cascade-deleted by cleanup, pass 3 fetches the canonical file and rebuilds the target — tagged with its true home file so future cleanups handle it correctly.
- **`links:` declaration** at the file level expresses cross-entity attribute references (e.g. paired bidirectional doors sharing `other_side`, teleporters' targets, NPC-master/apprentice). Each link is a single directed assignment; reciprocal pairs are two granular entries. Pass 4 fires after the cache is fully warm, so most resolutions are cache hits.
- Build each Evennia object: typeclass + key + location + (destination, for exits) + (home, optional — null translates to `nohome=True`, cross-ref dict resolves to a target object) + description + aliases + locks + attributes (with YAML overriding typeclass defaults) + author tags + the auto-set `wb_deployment_file` / `wb_deployment_id` identity pair.
- Clean up prior deployments of the same source files before recreating, so re-applying the same YAML produces a stable end state — including all nested entities, since they share their parent's `wb_deployment_file` tag.

Two surfaces:

- **`wb_build`** — in-game admin command (auto-installed into `AccountCmdSet`, superuser-only). Runs the full pipeline including the Builder. **Defers the pipeline to a Twisted worker thread** via `evennia.utils.utils.run_async`, so long deployments don't block the reactor — players continue playing while a build runs.
- **`wb-validate`** — standalone console-script CLI for CI / pre-commit / local iteration. Runs everything up to but excluding the Builder.

## Compatibility with `evennia-shards`

The library is **shards-compatible but does not require shards**. If [`evennia-shards`](https://github.com/FullCircleMUD/evennia-shards) is installed alongside, `wb_build` automatically carries the active multi-tenant context across the `run_async` thread spawn — rooms built in the worker get stamped with the running process's `shard_id` and become correctly scoped under the auto-filter. If shards isn't installed, the library falls back to an identity passthrough at import time and behaves identically to a non-sharded deployment. No configuration needed either way; the integration is a try-import in `commands.py` using shards' `preserve_tenant_context` helper.

Co-installed, the pairing also requires `shard` as the first declared level and confines `wb_build` to the shard it is running as. See [docs/interoperability.md](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/docs/interoperability.md).

## Is this for me?

The library is Evennia-flavored and primarily intended for use on **[FullCircleMUD](https://github.com/FullCircleMUD/game)**, but is consumer-game-agnostic by design: it does not bake in assumptions about specific typeclasses, zones, or game systems. If you are building world content on Evennia and would like to author it as YAML rather than as imperative Python builders, this library aims to be useful to you.

## Install

```
pip install evennia-world-builder
```

Editable install for development against a checkout:

```
git clone https://github.com/FullCircleMUD/evennia-world-builder.git
cd evennia-world-builder
python -m venv venv
# Activate the venv (platform-specific)
pip install -e .
python runtests.py
```

Then in your gamedir's `settings.py`:

```python
INSTALLED_APPS = list(INSTALLED_APPS) + ["evennia_world_builder"]
WORLDBUILDER_READER_KWARGS = {
    "repo": "your-org/your-content-repo",
    "ref": "main",
    "pat": "...",  # GitHub PAT
}
```

…and `wb_build` is available in-game on the next restart.

## Learn more

- **[CLAUDE.md](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/CLAUDE.md)** — load-bearing principles and orientation for working in the repository.
- **[docs/INDEX.md](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/docs/INDEX.md)** — index of design documents.
- **[docs/archive/WorldAsDataNotes.md](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/docs/archive/WorldAsDataNotes.md)** — the original brainstorm that led to creating this library.

## License

BSD 3-Clause. See [LICENSE](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/LICENSE).
