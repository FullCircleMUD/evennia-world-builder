# Declarative World-Authoring for Evennia — Prior Art Survey

*Surveyed 2026-05-05.*

The landscape is **sparse**. Evennia has imperative or text-format builders (batch files, ASCII maps, Python-dict prototypes), but **no widely-adopted YAML-driven world-authoring layer** exists. Community projects either stay in Python or fall back to ASCII grids and CircleMUD imports.

## Official Evennia contribs

- **[Prototypes / Spawner](https://www.evennia.com/docs/latest/Components/Prototypes.html)** — Python-dict object templates loaded from `PROTOTYPE_MODULES` or DB Scripts. Supports inheritance, tags, locks, attributes. Closest thing to data-driven in core. Limitation: objects only, not rooms/exits/zones/spawn rules.
- **[Batch Code Processor](https://www.evennia.com/docs/latest/Components/Batch-Code-Processor.html)** — executes `.py` blueprint files. Maximally flexible but imperative Python with full DB access, superuser-only.
- **[Batch Command Processor](https://www.evennia.com/docs/latest/Components/Batch-Command-Processor.html) / `.ev` files** (e.g. [tutorial_world/build.ev](https://github.com/evennia/evennia/blob/main/evennia/contrib/tutorials/tutorial_world/build.ev)) — newline-separated lists of in-game build commands (`@dig`, `@create`, …). Text-driven but a script of imperative commands, not declarative.
- **[Mapbuilder contrib](https://www.evennia.com/docs/latest/Contribs/Contrib-Mapbuilder.html)** (`evennia.contrib.grid.mapbuilder`) — ASCII-art map + Python legend dict of trigger→builder-function. Two-pass parser handles forward exit references.
- **[XYZGrid contrib](https://www.evennia.com/docs/latest/Contribs/Contrib-XYZGrid.html)** — most ambitious official option: ASCII map strings + Python legend dicts on a coordinate grid with auto-generated exits, Dijkstra pathfinding, z-level transitions, prototype hookup. Still Python-dict legends, tightly coupled to coordinate grids.
- **Wilderness, ingame_map_display, extended_room, simpledoor, slow_exit** — adjacent grid contribs but not authoring tools.

All actively maintained inside the main Evennia repo.

## Third-party Evennia projects with data-file world authoring

- **[elixx/area_reader](https://github.com/elixx/area_reader)** — fork of `ctoth/area_reader`. Parses **ROM / CircleMUD / SMAUG / MERC** `.are` files into Evennia via `spawnRooms / spawnMobs / spawnObjects`. Requires custom `LegacyRoom/Exit/Object` typeclasses. ~2 stars, last activity March 2022, **effectively unmaintained**.
- **[MorquinDevlar's world import/export scripts (#3712)](https://github.com/evennia/evennia/discussions/3712)** — in development since Jan 2025, last update March 2025. Initially JSON, **pivoted to Python-dict files for performance**. Handles rooms/exits/objects/NPCs/zones/locks/aliases/tags with backups. Designed for clean-DB transfer, not edit-in-place. Not YAML; explicitly moved away from human-readable formats.
- **[evennia/ainneve](https://github.com/evennia/ainneve)** — canonical full example game (~83 stars). Builds via Python prototypes + batch scripts; no YAML.
- **Griatch/evscaperoom** — escape-room game; built imperatively in Python typeclasses.

GitHub searches for `evennia yaml`, `evennia toml`, `evennia world builder` returned no notable repos beyond the above.

## Community discussion

- [Google Groups: "Using CircleMUD's data for Evennia"](https://groups.google.com/g/evennia/c/ZLVphIBguqk) — points at `area_reader`.
- Evennia [Links page](https://www.evennia.com/docs/latest/Links.html) lists `area_reader` as the only legacy-import tool.
- [Discussion #3666: Organizing your Evennia Game](https://github.com/evennia/evennia/discussions/3666) — prototypes-vs-batchcode, no YAML proposals.
- No substantive Reddit/Discord threads advocating YAML world definitions; community center of gravity is Python prototypes + XYZGrid.

## Related prior art

- **DikuMUD/CircleMUD/ROM `.wld/.mob/.obj/.zon`** — historical declarative-world format `area_reader` targets; column-positional, hard to author by hand but battle-tested.
- **tmc2** — not found as an Evennia tool; CircleMUD→Evennia conversion path goes through `area_reader`.
- **Evennia OLC** ([prototypes OLC](https://www.evennia.com/docs/latest/Components/Prototypes.html)) — in-game *interactive* editor for the same Python-dict prototype shape, so any external YAML format ideally round-trips with what OLC saves to the DB.

## Gaps a YAML world-builder could fill

Nothing in the ecosystem provides **a single declarative format covering rooms + exits + zones + NPC/mob spawn rules + object placement + per-zone reset/tick behaviour with hot-reload semantics**. Each existing option covers a slice:

| Tool | Covers | Format | Live-reload |
|------|--------|--------|-------------|
| Prototypes | Objects only | Python dict | No (DB-backed) |
| XYZGrid | Rooms/exits | ASCII + Python legend | Manual rebuild |
| Batchcode | Everything | Imperative Python | No |
| area_reader | Everything | CircleMUD `.are` (stale) | No |
| MorquinDevlar (#3712) | Everything | Python-dict (was JSON) | DB→DB transfer only |

A YAML-first authoring layer with **schema validation**, **idempotent re-apply against a live DB** (explicit non-goal of #3712), **zone-scoped redeploy**, and clean interop with the existing prototype system would be **genuinely novel territory** rather than reinvention.

## Sources

- [Evennia Prototypes / Spawner](https://www.evennia.com/docs/latest/Components/Prototypes.html)
- [Batch Code Processor](https://www.evennia.com/docs/latest/Components/Batch-Code-Processor.html)
- [Batch Command Processor](https://www.evennia.com/docs/latest/Components/Batch-Command-Processor.html)
- [tutorial_world/build.ev](https://github.com/evennia/evennia/blob/main/evennia/contrib/tutorials/tutorial_world/build.ev)
- [Mapbuilder contrib](https://www.evennia.com/docs/latest/Contribs/Contrib-Mapbuilder.html)
- [XYZGrid contrib](https://www.evennia.com/docs/latest/Contribs/Contrib-XYZGrid.html)
- [Contribs overview](https://www.evennia.com/docs/latest/Contribs/Contribs-Overview.html)
- [elixx/area_reader](https://github.com/elixx/area_reader)
- [Discussion #3712 — World import/export](https://github.com/evennia/evennia/discussions/3712)
- [Discussion #3666 — Organizing your Evennia Game](https://github.com/evennia/evennia/discussions/3666)
- [evennia/ainneve](https://github.com/evennia/ainneve)
- [Google Groups: CircleMUD data for Evennia](https://groups.google.com/g/evennia/c/ZLVphIBguqk)
- [Evennia Links page](https://www.evennia.com/docs/latest/Links.html)
