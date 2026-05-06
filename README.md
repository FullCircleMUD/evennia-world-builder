# evennia-world-builder

Declarative YAML-driven world authoring for [Evennia](https://www.evennia.com/).

World content (rooms, exits, fixtures, descriptions) is expressed as data; an idempotent loader applies it to the running game's database via Evennia's typeclass system.

## Status

**Active development** — single-entity build pipeline working end-to-end. Recursion into nested entities (`exits:`, `contents:`) and cross-reference resolution are upcoming spikes. See [DESIGN/progress.md](DESIGN/progress.md) for the running milestone log.

## What's working today

For a single-entity YAML file, the library's pipeline reads → validates → builds the entity into Evennia, idempotently:

- Fetch YAML from a configured source (GitHub or local filesystem) via the `Reader` abstraction.
- Walk the per-folder `index.yaml` manifest tree to find entities matching an operator query.
- Validate every entity through a predicate-tier pipeline (mandatory fields, shape, typeclass-resolvability, per-file `deployment_id` uniqueness, reserved tag categories), gathering all findings before any DB mutation.
- Build the Evennia object: typeclass + key + location + description + aliases + locks + attributes (with YAML overriding typeclass defaults) + author tags + the auto-set `wb_deployment_file` / `wb_deployment_id` identity pair.
- Clean up prior deployments of the same source files before recreating, so re-applying the same YAML produces a stable end state.

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
