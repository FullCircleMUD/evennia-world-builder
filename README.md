# world-builder

Declarative YAML-driven world authoring for [Evennia](https://www.evennia.com/).

World content (rooms, exits, fixtures, descriptions) is expressed as data; an idempotent loader applies it to the running game's database via Evennia's typeclass system.

## Status

**Pre-implementation — repo is in early scaffolding.** No package code has landed yet. See [DESIGN/progress.md](DESIGN/progress.md) for the running milestone log.

## Is this for me?

The library is Evennia-flavored and primarily intended for use on **[FullCircleMUD](https://github.com/FullCircleMUD/game)**, but is consumer-game-agnostic by design: it does not bake in assumptions about specific typeclasses, zones, or game systems. If you are building world content on Evennia and would like to author it as YAML rather than as imperative Python builders, this library aims to be useful to you.

## Install

Not yet installable — the package is pre-implementation.

## Learn more

- **[CLAUDE.md](CLAUDE.md)** — load-bearing principles and orientation for working in the repository.
- **[DESIGN/INDEX.md](DESIGN/INDEX.md)** — index of design documents.
- **[DESIGN/archive/WorldAsDataNotes.md](DESIGN/archive/WorldAsDataNotes.md)** — the original brainstorm that led to creating this library.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
