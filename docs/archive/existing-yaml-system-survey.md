# YAML World-Building Mechanism — Survey of an Existing System

*Surveyed 2026-05-05.*

## Overview

The surveyed system is the world-content layer of an Evennia-based MUD. It treats YAML as the authoring substrate for zones, rooms, exits, NPCs, items, vendor stock profiles, loot tables, and equipment "kits." A separate import pipeline turns those YAML files into live Evennia `ObjectDB` records at runtime. There is also a separate spatial editor (a Flask launcher fronting a Godot canvas) that reads/writes the same YAML world, and an LLM-assisted generator pipeline that synthesizes prose room descriptions and NPC content. The whole arrangement is tagged for re-import: every spawned object is marked with a `world_sync` tag and a `zone:<id>` tag so a zone wipe-and-reload remains scoped.

## Schema & file layout

The repo splits world data across two directory trees:

- `world_data/` — content libraries (definitions only, no spatial placement)
  - `items/{weapons,armor,ammunition,consumables,containers,…}/*.yaml` plus `schema_item.yaml`
  - `npcs/{hostile,neutral,vendors}/*.yaml` plus `schema_npc.yaml`
  - `vendor_profiles/*.yaml`, `kit_templates/*.yaml`, `loot/*.yaml`, each with its own `schema_*.yaml`
- `worlddata/zones/*.yaml` — one file per zone, containing rooms, exits, and placement records
- `manifests/*.yaml` — small "area manifest" files that name the source PNG map and a generation profile, used by the map ingestion / generator path
- `world/builder/zones/zone_registry_v1.json` and `world/builder/templates/template_registry_v1.json` — JSON registries the builder UI uses for zone listings and reusable templates

A library item is a flat document. Anonymized:

```yaml
id: example_sword
name: Example Sword
category: weapon
weapon_class: medium_edge
tags: [martial, example_faction]
level_band: { min: 1, max: 8 }
value: 24
weight: 2.7
equipment: { slot: weapon, attack: 4, defense: 0 }
description:
  short: an example sword
  long: An example sword rests here in a serviceable scabbard.
meta: { source: vendor_specialization, imported_at: '2026-04-21' }
```

A zone YAML is a single document containing the full graph. Anonymized fragment:

```yaml
schema_version: v1
zone_id: example_zone
name: Example Zone
generation_context:
  setting_type: city
  era_feel: late-medieval
  voice: "Gritty, pragmatic. Present tense."
rooms:
  - id: ZONE_178_132
    typeclass: typeclasses.rooms_extended.ExtendedDireRoom
    desc: ''
    stateful_descs: {}
    details: {}
    ambient: { rate: 0, messages: [] }
    environment: city
    tags: { structure: hallway, specific_function: tavern, named_feature: hearth, condition: worn, custom: [riverside] }
    map: { x: -263, y: -430, layer: 0 }
    exits:
      south: { target: ZONE_178_154, typeclass: typeclasses.exits.Exit, speed: '', travel_time: 0 }
      east:  { target: ZONE_192_132, typeclass: typeclasses.exits.Exit, speed: '', travel_time: 0 }
    npcs: []
    items: []
placements:
  npcs: []
  items: []
```

Zone files are partitioned **per-zone, single-document** — one zone produced files of ~7000 lines for ~470 rooms, indicating the format scales by data volume not by elegance.

## Build pipeline / loader

Authoring → live world goes through `world/worlddata/services/import_zone_service.py`, fronted by an admin command `@zone export | load <zone_id> [--dry] [YES]` (`commands/cmd_zone.py`). The flow is:

1. `_load_zone_yaml` reads `worlddata/zones/<zone_id>.yaml` with `yaml.safe_load`.
2. `_validate_required_keys` asserts top-level `zone_id`, `rooms`, `placements`.
3. `_normalize_room_specs` enforces unique room IDs, validates exits against an `ALLOWED_DIRECTIONS` set, resolves every exit target to a known room, auto-generates reverse exits, and warns on overlaps and compressed map spread.
4. `_normalize_placements` validates room/parent references and detects container cycles.
5. NPC and item registries are loaded from `world_data/` by `server/systems/{npc_loader,item_loader}.py` (also pure YAML readers with allowlisted keys); per-room `npcs:`/`items:` lists are flattened into `placements` by `server/systems/zone_runtime_spawn.py`.
6. Each placement is resolved to either an Evennia prototype (via `evennia.prototypes.spawner.search_prototype`) or a fallback typeclass.
7. If `dry_run`, the loader returns counts and warnings only.
8. Otherwise `_delete_existing_zone` wipes objects tagged `world_sync` + `zone:<id>` and rebuilds rooms, exits, and placements via `evennia.utils.create.create_object`/`spawn`. A `_warm_load_zone` variant performs in-place upsert by `world_id` instead of full wipe, with stale-room warnings.
9. The map cache is invalidated through `world.weather.invalidate_zone_caches`.

The `manifests/*.yaml` + `maps/*.png` path is a separate generation pipeline that produces a zone YAML from a hand-drawn map plus a generation profile (`yaml_graph`, `dr_city`); zones in `worlddata/zones/` are the canonical post-generation product.

## What's covered

YAML-driven: rooms, exits (cardinal + special), per-room ambient messages, stateful descriptions, room "details" (look-at keywords), room state tags, weather environment, room tags (structure/specific_function/named_feature/condition/custom), map coordinates, NPC definitions, item definitions, container nesting, vendor profiles, vendor stock generation rules, loot tables, kit templates, zone-level generation context for the LLM.

Still in Python: combat math, AI ticks, scripts/behaviours, weather rules, profession systems, justice/guard logic. Behaviour types are referenced by string keys (e.g., `behavior: { aggressive, roam, assist }`) which the typeclass interprets — there is no embedded scripting in YAML.

## Hot reload & idempotency

Two reload modes coexist:

- **Wipe-and-rebuild** (`load_zone(..., preserve_existing=False)`) — all objects with `tags.zone:<id>` deleted, then recreated. Players are not skipped explicitly except for `has_account` filtering.
- **Warm load** (`preserve_existing=True`) — looks up existing rooms by `db.world_id`, mutates fields in place, creates only missing rooms/exits, deletes stale exits, but **preserves rooms no longer in YAML** (reported as a warning rather than removed). Runtime NPCs and items are always wiped and re-spawned.

There is no manifest/diff intermediate that records the previous state — re-apply uses live DB tags as the source of truth for what to delete. A separate `diff_history_service.py` exists in `world/builder/` for the editor's undo/audit trail (capped at ~5 MB), but it is not used by the import pipeline.

## Validation

Validation is hand-coded, not schema-driven. Three layers:

- The library `schema_*.yaml` files are documentation, not executable schemas — the loaders implement allowlist/value-set checks in Python (e.g., `ITEM_WEAPON_CLASSES`, `NPC_ALLOWED_TOP_LEVEL_KEYS`).
- `world/builder/schemas/zone_schema_v1.py` provides programmatic validators (`validate_zone_registry`, `normalize_zone_id` regex) used by the builder service path.
- The import pipeline raises `ValueError` on missing keys, unknown direction names, unresolved exit/parent IDs, container cycles, and zone_id mismatch between filename and content. Soft issues become `warnings` collected and surfaced in the `@zone load` summary.

There is no JSON Schema, Pydantic, or `voluptuous` use anywhere in the loader path.

## Cross-references & IDs

Everything is keyed by string IDs:

- Rooms: `id` like `ZONE_178_132` (zone prefix + map coords) or `CRO_450_100`. Stored on the live object as `db.world_id`.
- Exits: refer to destination rooms by `target:` string; resolved at load time against the per-zone `rooms_by_id` map. Forward references are fine because resolution is a two-pass build.
- NPCs/items: definition IDs from `world_data/`. Placements reference them by the same ID. Container nesting uses a `parent:` referring to another placement's `id`.
- A `spawn_key` is synthesized (`room::parent::index::id`) for each placement so the loader can build a placement→item map for parent resolution.

No numeric VNUMs and no UUID generation in the data files; the string IDs are the contract.

## Templating & inheritance

There is no YAML-level inheritance, anchors, or `!include`. Reuse is achieved by:

- **Vendor profiles** — a vendor YAML names a `vendor_profile_id`; the profile YAML defines weighted `allowed_weapon_classes`, `level_band`, and `required_tags` that drive procedural stock generation against the item library.
- **Kit templates** — group items into themed equipment loadouts with `required_slots`, `optional_slots`, `tier_bias`, and `theme_tags` for matching against the library.
- **Loot tables** — separate YAML referenced by `loot_table:` on hostile NPCs.
- **Builder template registry** (`template_registry_v1.json`) — used by the spatial editor for drag-from-library reuse (currently empty in the repo).

This is essentially "indirection by ID" rather than true template inheritance — there is no "guard base class" that twelve mob YAMLs `extends:`.

## Evennia prototype integration

The loader uses Evennia prototypes when present (`spawn(prototype_key)`) but falls back gracefully: `_resolve_spawn_blueprint` calls `search_prototype` and, if missing, downgrades to a generic typeclass with a warning. So the YAML pipeline is independent of the Evennia prototype registry but happily uses it when a placement names one. Typeclass paths are first-class fields in the YAML (`typeclass: typeclasses.rooms_extended.ExtendedDireRoom`) and are resolved by string.

## Behaviour & scripts

Behaviour is attached by:

- Naming a typeclass per-room or per-exit, allowing slow exits, extended rooms, etc.
- A small fixed set of NPC behaviour booleans (`aggressive`, `roam`, `assist`); richer behaviour like guard assist and threat tracking is purely Python.
- Ambient/weather hooks driven by `ambient.rate`, `ambient.messages`, and the `environment` tag — interpreted by the room typeclass at load time.
- Room state tags written into Evennia's `tags` system under category `room_state`, plus per-state description attributes (`desc_<state>`), enabling stateful_descs to appear when conditions tag the room.

There is no embedded DSL or Lua-style trigger scripting.

## Tooling

- `startWeb.bat` — wraps `evennia start`/`stop` with port 4001 detection.
- `startBuilder.bat` → `tools/builder_launcher/launcher.py` — a Flask app on `127.0.0.1:7777`, CORS-locked to the Evennia web port. It launches a Godot project as the spatial editor surface, backed by services under `world/builder/services/` (room_service, exit_service, instance_service, placement_service, spawn_service, undo_service, diff_history_service, map_importer/exporter, plus an LLM client for description synthesis).
- One-shot CLIs (`tools/generate_*`, `tools/import_*`) feed YAML or pull legacy data.
- An LLM pipeline (system prompts in `world/builder/templates/room_description_*_prompt.txt`, governed by `zone_engineering_guidelines.md`) generates room and NPC prose. The generator splits a "planner / generator / critic" three-pass model.
- `diretest.py` — scenario runner used to smoke-test loaded zones.

## Strengths

- **Single-document zone files** make a zone trivially reviewable, diffable, and movable; the whole graph fits in one Git change.
- **Explicit ID-based cross-referencing with two-pass resolution** is robust to authoring order and produces clear, human-readable errors.
- **Tag-scoped wipe (`world_sync` + `zone:<id>`)** means re-apply is bounded; live players and unrelated content survive a reload.
- **Both wipe and warm-load paths** exist with the same code, giving cheap iteration during authoring and clean rebuilds for releases.
- **Prototype-aware but not prototype-dependent** — falls back gracefully, so the system is portable across Evennia configurations.
- **Library / placement separation** (`world_data/` definitions vs. `worlddata/` zones) cleanly distinguishes "what exists in the world" from "where it is."
- **Auto-reverse exits and validation warnings** catch a major class of authoring bugs before runtime.
- **Generation context is data**, not code — the LLM pipeline reads `voice`, `mood`, `era_feel` from the zone YAML, keeping content pipeline parameters versioned with the world.

## Weaknesses & limitations

- **No real schema validation.** The `schema_*.yaml` files are docs-as-data; actual checks are hand-rolled key allowlists. A typoed nested key in a library file is silently dropped on load. JSON Schema or Pydantic would catch this.
- **No template/inheritance primitive.** Reuse depends on category indirection (vendor profiles, kit templates), which works for stock but not for "twelve goblin variants share a base." YAML anchors are not used.
- **Verbose, often near-empty zone files.** A 7000-line zone file is mostly defaulted scaffolding (`stateful_descs: {}` etc.) repeated per room. No defaulting, no compression by zone-level defaults.
- **Warm-load is not a true diff.** Stale rooms are warned about but not removed; runtime NPCs/items are wiped wholesale rather than reconciled. There's no manifest comparing previous vs current state.
- **No in-flight player handling.** A wipe relies on the `has_account` filter; players in a deleted room would experience the world disappearing under them. There's no drain/migrate step.
- **Behaviour expressivity is shallow.** Three booleans for NPCs and a typeclass string for rooms is the entire behaviour surface; richer authoring requires writing new Python typeclasses.
- **Two parallel zone concepts** — the `world_data` libraries vs. the `world/builder/zones/zone_registry_v1.json` (which contains different/empty zones from what's actually deployed). The builder world model and the live-game world model are not the same source of truth.
- **Hand-coded normalization is duplicated** across loaders (`_normalize_string_map`, `_normalize_string_list`, ambient parsing) — refactor risk surface.
- **No referential integrity at write-time.** YAML is edited by hand or by the editor without authoring-time validation hooks; problems surface only when `@zone load --dry` runs.
- **Map coords are absolute and zone-prefixed in IDs.** Renaming a zone or rescaling a map cascades through every exit target.

## Expansion possibilities

- **Adopt Pydantic v2 (or JSON Schema) models** generated from the existing `schema_*.yaml` shape; produce richer error messages and IDE autocompletion. Keep the schema files as the source of truth and codegen the validators.
- **Introduce zone-level defaults / inheritance.** A `defaults:` block at the zone level that fills omitted room fields would shrink files an order of magnitude. A `from: base_template` field on items/NPCs (with a small merge function) would express the "12 goblin variants" use case without a new format.
- **Manifest-based diff and reconcile.** Persist a zone manifest (hash + list of `world_id`s) post-load; on next `@zone load`, compute add/update/remove and apply the minimal change set (truly stale rooms removed, players in removed rooms migrated to a fallback). This makes hot-reload safe with players online.
- **Live-zone editor commit path.** The Flask/Godot builder already has services for rooms/exits/placements; pipe its outputs directly to the same `worlddata/zones/<zone>.yaml` and trigger `@zone load --warm` automatically, closing the loop between editor and runtime.
- **Behaviour DSL or component refs.** Allow NPCs to declare `components: [patrol, wares, dialogue_tree]` with parameters, mapping to Python components at load time — keeps YAML data-only while allowing rich AI without per-NPC typeclasses.
- **Per-zone migrations.** A `schema_version:` per zone is already present; pair it with a registry of upgraders that auto-rewrite older zones forward.
- **Weather, calendar, and economy hooks.** The room `environment` and zone `generation_context` are good seams to attach weather profiles, time-of-day overrides, and economy parameters (vendor refresh cadence) directly in YAML rather than per-system Python config.
- **Validate cross-zone references.** Currently each zone is self-contained; supporting `target: other_zone:room_id` for inter-zone exits, validated via the registry, would let large worlds compose cleanly.
- **Partial / district-level reload.** Tag `district:<id>` alongside `zone:<id>` so a single neighbourhood can reload without the whole zone.
- **Dry-run linter as CI.** Run `@zone load --dry` headless against every zone YAML in CI to catch broken references before deploy.
- **Atomic publish via temp-and-swap tags.** Build the new zone under `zone:<id>:staging`, then atomically retag and delete the old set, eliminating the "world flickers during reload" window.
