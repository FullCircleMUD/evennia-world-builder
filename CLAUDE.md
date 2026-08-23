# CLAUDE.md

> **Project-wide working rules and cross-repo context live in the FCM umbrella repo's `CLAUDE.md`**,
> loaded automatically when you work from the umbrella root. If you opened this repo directly instead
> of via the umbrella, relaunch from the umbrella root for the full context. This file holds only this
> repo's specific instructions.

Instructions for Claude (and other LLM agents) working in this repository.

## What this project is

`evennia-world-builder` is a library that adds declarative, YAML-driven world authoring to [Evennia](https://www.evennia.com/). World content (rooms, exits, fixtures, descriptions) is expressed as data; an idempotent loader applies it to the running game's database via Evennia's typeclass system. Tagline: **"Your Evennia world, in YAML."**

The library is Evennia-flavored and primarily intended for use on FullCircleMUD, but is FCM-agnostic by design: nothing in the library knows about FCM-specific zones, typeclasses, or game systems.

For the big-picture overview, read [README.md](README.md).
For the design wiki, read [docs/INDEX.md](docs/INDEX.md).

## Project status

For the current state of the project — milestones reached, what's pending — see [docs/progress.md](docs/progress.md), the running log of milestones with links to evidence.

The design substrate is the archived [docs/archive/WorldAsDataNotes.md](docs/archive/WorldAsDataNotes.md), a brainstorm artifact from the FCM-side discussions that led to creating this library. It is *not* authoritative; current decisions belong in focused DESIGN documents indexed in [docs/INDEX.md](docs/INDEX.md).

## Where to read first

For any non-trivial task, start by reading in this order:

1. [README.md](README.md) — what the project is, status, quick start.
2. [docs/INDEX.md](docs/INDEX.md) — map of all design docs.
3. [docs/archive/WorldAsDataNotes.md](docs/archive/WorldAsDataNotes.md) — *archived* brainstorm captured during FCM-side discussions that led to creating this library. Useful historical context; not authoritative.

## Load-bearing architectural principles

These are the principles every implementation decision must respect. Getting them wrong is expensive to undo.

1. **The library does not own game concepts.** Rooms, exits, fixtures, NPCs, typeclasses, tags, items belong to the consumer game. The library provides infrastructure: YAML parsing, schema validation, cross-reference resolution, operation planning, idempotent execution against Evennia's typeclass system. When tempted to add a game concept, ask whether it's actually game-specific and should stay in the consumer.
2. **No FCM-specific assumptions.** This library was created in service of FullCircleMUD (FCM). Anything FCM-specific creeping into the library is a code smell. Zone names, district conventions, economy concepts, NFT references, FCM typeclass names — all stay in FCM. Default to "consumer concern" when uncertain.
3. **Loader is consumer-extensible.** The library does not bake in knowledge of specific typeclasses, exit helpers, fixtures, or other consumer-defined types. Adding a new type to the consumer game must not require changes to the library. The exact extension mechanism — registry, dotted-path resolution, hook protocol, or other — is a design decision to be made during PoC and captured in docs/ once chosen.
4. **Pre-flight validation, no partial apply.** All validation runs before any DB mutation. The loader resolves cross-references, checks every referenced typeclass and helper exists in the running engine, verifies required attrs, and computes the full operation plan first. If any check fails, the apply refuses with a complete list of reasons and no DB state is touched. Never partial state. Same discipline as Terraform's plan phase — the operator gets either a clean apply or a complete refusal.
5. **Idempotent against any starting state (clean + rebuild).** Apply scope is cleaned then rebuilt from YAML. Same YAML applied twice produces the same end state. Reconcile-style (Terraform diffing) is explicitly out of scope. The library's job is to converge to declared state by tearing down and recreating; in-flight state preservation is a consumer concern (handled via evacuation by the consumer).
6. **Synthetic content first.** Build the library against synthetic test fixtures the library owns, exhaustively, before any consumer-game integration. Real consumer content surfaces edge cases synthetic fixtures didn't reach; when it does, pause integration, capture the case as a new synthetic fixture, fix against it, resume. Fixtures stay forever as regression coverage.

## Out of scope

Scope boundaries are decided as concrete questions arise, by applying the principles above. The library's surface area will be drawn deliberately as actual design needs surface, with each scope decision captured in docs/ when it is made.

Areas where scope questions are likely to need explicit decisions (TBD when they arrive):

- Where the library / consumer boundary lies for operator commands and admin surface
- Library involvement (if any) in player evacuation and in-flight state during apply
- Library involvement (if any) in CI / deployment tooling for consumer content repos

## Working conventions

- **Editing design docs.** Update or add design documents whenever an architectural decision is made or refined. Capture the *why*, not just the *what*. Index new docs in [docs/INDEX.md](docs/INDEX.md).
- **Don't put implementation detail in this file or README.** Link out to docs/ instead. Keep CLAUDE.md and README.md stable; let docs/ churn.
- **License.** BSD 3-Clause. Source files carry an SPDX header on the first line (`# SPDX-License-Identifier: BSD-3-Clause`).

## Documentation discipline (load-bearing)

Design documents in `docs/` must reflect decisions **actually discussed and agreed on with the project owner**. They are not a place to forward-design the system from first principles or extrapolate "reasonable defaults" from a starting point.

**Rules:**

1. **Only capture what was discussed and agreed.** If the conversation establishes a principle (e.g. "the library mandates a gateway helper, everything else is consumer choice"), do not extrapolate it into specifics that were not raised (e.g. a numbered adoption checklist, a decision tree, specific API shapes, naming conventions).
2. **Flag open questions explicitly.** Where a topic has been raised but not resolved, write `[TBD — needs discussion: <what is open>]` in the doc. Future sessions then pick the topic up deliberately rather than inheriting unagreed assumptions.
3. **Distinguish archived material from in-conversation decisions.** Material in `docs/archive/` (e.g. [WorldAsDataNotes.md](docs/archive/WorldAsDataNotes.md)) is preserved historical context, not authoritative. Restating archived content in new docs is acceptable when it provides necessary context, but mark it as such (e.g. *"Per the archived brainstorm: ..."*) rather than presenting it as a decision freshly made or as canonical project intent.
4. **Smaller is better.** A doc that captures three discussed points faithfully is more useful than one that captures three discussed points plus seven invented ones. Resist the urge to fill out sections "for completeness."

If a session catches itself writing content that goes beyond what was discussed, stop and either remove the extrapolation or convert it to a `[TBD]` marker. Documentation that puts unagreed decisions in the project's mouth is worse than documentation that has gaps.

## Repository layout

```
evennia-world-builder/
├── CLAUDE.md                  # this file
├── README.md
├── LICENSE                    # BSD 3-Clause
├── pyproject.toml
├── runtests.py                # standalone test runner (no consumer gamedir needed)
├── docs/                    # design wiki (humans + LLMs)
├── src/
│   └── evennia_world_builder/         # library code (src layout)
│       ├── __init__.py
│       └── tests.py           # unit tests (run via runtests.py)
├── tests/                     # standalone test settings (test_settings.py, urls.py)
└── examples/                  # reserved for future synthetic content / demo gamedirs
```

## Tools and environment

- Python 3.10+ (pinned via `pyproject.toml`).
- Evennia is a runtime dependency (`pip install evennia`).
- Tests use Django's test runner via `runtests.py` (no consumer gamedir required).
- YAML parsing: PyYAML (`yaml.safe_load`). Schema validation: hand-written predicates in `validator.py` rather than a schema library — see [docs/validator.md](docs/validator.md) for the rationale.
