# evennia-world-builder

Declarative YAML-driven world authoring for [Evennia](https://www.evennia.com/).

World content (rooms, exits, fixtures, descriptions) is expressed as data; an idempotent loader applies it to the running game's database via Evennia's typeclass system.

## Status

**Active development.** Single-entity build, `contents:` recursion, `exits:` recursion, same-file and cross-file cross-references all working end-to-end. The canonical YAML file shape ("shape 3" — top-level mapping with `entities:` key) lets authors mix multiple rooms with file-level metadata in one file. Currently building out the file-level `incoming_exits:` registry mechanism for cross-file rebuild-dependency restoration (spike 6). See [DESIGN/progress.md](DESIGN/progress.md) for the running milestone log.

## What's working today

The library's pipeline reads → validates → builds YAML world content into Evennia, idempotently:

- Fetch YAML from a configured source (GitHub or local filesystem) via the `Reader` abstraction.
- Walk the per-folder `index.yaml` manifest tree to find entities matching an operator query.
- Every leaf YAML file uses one canonical shape — a top-level mapping with an `entities:` key whose value is a list of entity mappings. File-level keys (e.g. `incoming_exits:`, future extensions) live alongside `entities:`.
- **Flatten `contents:` and `exits:` blocks** into individual `LoadedEntity` records; nested entities inherit their parent's source path and get a Loader-synthesised `location:` cross-ref pointing at the parent. Recursion is depth-unlimited (chest in room contains key contains gem).
- **Loader returns a `LoadResult(entities, file_metadata)`** — the entity list plus a `{file_path → {file-level keys}}` dict that downstream consumers (Validator, Builder) read for file-level concerns.
- Validate every entity through a predicate-tier pipeline (mandatory fields, shape, typeclass-resolvability, per-file `deployment_id` uniqueness, reserved tag categories, `location:` either null or `{deployment_file, deployment_id}` cross-ref, `destination:` shape + presence consistent with whether the typeclass inherits from `DefaultExit`), gathering all findings before any DB mutation. A Tier 4 deferred phase resolves every cross-ref (entity-level location/destination AND file-level `incoming_exits:` lists) against the per-file `seen_ids` index, catching dangling refs and id/file typos at validate time.
- **Two-pass build** so bidirectional exits resolve correctly — non-exits in pass 1, exits in pass 2 with their destinations resolved against the just-built rooms.
- **Cross-file refs resolve via DB tag-search fallback** when the target isn't in the current build set. Operators can rebuild a single file (`wb_build zone=millholm room=bakery`) and exits/locations pointing into other files still resolve as long as the target file has been built at some point.
- Build each Evennia object: typeclass + key + location + (destination, for exits) + description + aliases + locks + attributes (with YAML overriding typeclass defaults) + author tags + the auto-set `wb_deployment_file` / `wb_deployment_id` identity pair.
- Clean up prior deployments of the same source files before recreating, so re-applying the same YAML produces a stable end state — including all nested entities, since they share their parent's `wb_deployment_file` tag.

Two surfaces:

- **`wb_build`** — in-game admin command (auto-installed into `AccountCmdSet`, superuser-only). Runs the full pipeline including the Builder.
- **`wb-validate`** — standalone console-script CLI for CI / pre-commit / local iteration. Runs everything up to but excluding the Builder.

## Is this for me?

The library is Evennia-flavored and primarily intended for use on **[FullCircleMUD](https://github.com/FullCircleMUD/game)**, but is consumer-game-agnostic by design: it does not bake in assumptions about specific typeclasses, zones, or game systems. If you are building world content on Evennia and would like to author it as YAML rather than as imperative Python builders, this library aims to be useful to you.

## Install

The package isn't on PyPI yet. Install directly from git:

```
pip install git+https://github.com/FullCircleMUD/evennia-world-builder.git@main
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

- **[CLAUDE.md](CLAUDE.md)** — load-bearing principles and orientation for working in the repository.
- **[DESIGN/INDEX.md](DESIGN/INDEX.md)** — index of design documents.
- **[DESIGN/archive/WorldAsDataNotes.md](DESIGN/archive/WorldAsDataNotes.md)** — the original brainstorm that led to creating this library.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
