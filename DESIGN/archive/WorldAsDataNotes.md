# World-as-Data: Notes

> Pre-design capture of the intent behind moving FCM world content to declarative YAML in a private repo, deployed by an idempotent loader. **What** and **why**, not **how** — implementation problems are deferred.
>
> When this firms into a real design, it moves to `design/` or `ops/` and this file retires.

---

## The change being proposed

Move FCM world content from Python builders in the public game repo to declarative YAML in a **private content repo**. The game (in the public repo) carries the engine — typeclasses, helpers, loader, schema. The content repo carries only data. Pushes to the content repo are CI-validated. A superuser command in the running game pulls the validated content from GitHub and applies it idempotently, at any chosen scope, asynchronously enough that unaffected zones stay responsive.

---

## Why this matters — three independent justifications

Each strong enough on its own; together they make the case overdetermined.

### 1. Visibility / content confidentiality

Today's zone builders sit in `src/game/world/game_world/zones/*.py` in a public GitHub repo. Anyone can clone and read every room description, exit, NPC placement, secret area, and lore detail before discovering them in-game. For a game whose value depends on discovery and immersion, this is a real problem.

The fix has to be **format change + repo split together**. Python builders can't move to a private repo cleanly because they import engine modules — the entanglement makes the boundary fragile. Declarative YAML has no imports; it can live in any repo and is consumed rather than executed.

### 2. Operational integrity

Current state: deployment is via superuser running scripts (`@py` or commands wrapping the same scripts). Not idempotent. Not proven for partial-scope redeploy.

Target state: a single command can deploy any granularity — district, zone, whole world — and produce the same end state regardless of starting state. Stable identity across redeploys so external references (quests, player bookmarks, saved coordinates) survive.

Most of the operational architecture already exists in [design/WORLD_DEPLOYMENT.md](design/WORLD_DEPLOYMENT.md): the three-tier model, the composite-triple identity, the `find_room` resolver, the per-district clean+rebuild discipline, the operator workflow with broadcast-and-evacuate. The gap is purely that builders are still Python.

### 3. Authoring readability (scattered locality)

In current Python builders, information about a single room is scattered across the script — creation in one cluster, exits in another, tags in a loop at the end, attributes batched separately. Understanding "what is the inn" requires scrolling through five sections. Each batching loop saves the writer twenty seconds and costs every future reader several minutes per visit.

YAML can't really be scattered — there's nowhere for a room's data to go except under that room's node. The format itself forces locality. This is the same insight that drives SQL over hand-written queries, React over jQuery, Terraform over shell scripts: declarative formats win because related information stays together and order-of-operations becomes the runtime's problem, not the author's.

A corollary worth pricing in: an LLM editing one YAML room only needs to see that room's node and the schema. An LLM editing one Python room needs to understand the surrounding loops, imports, helper signatures, and implicit ordering. As FCM's content authoring becomes increasingly AI-assisted, this is a multiplier on every future content push.

---

## What FCM already has — don't reinvent

[design/WORLD_DEPLOYMENT.md](design/WORLD_DEPLOYMENT.md) carries most of the operational thinking already:

- Three-tier model: world base / zones / districts
- Composite-triple identity `(zone_tag, district_tag, room.key)` as the cross-rebuild handle
- `find_room()` resolver
- Per-district clean+rebuild with player evacuation
- District manifest registering builder + evac target
- Operator-driven workflow (broadcast → wait → command)
- Spawn rules already declarative (JSON, hot-reload, self-healing)
- Sharding interaction considered

The deployment design is largely aspirational vs current reality, but the design itself is sound and the world-as-data work should build on it, not replace it.

---

## Design intent — what's been decided

These are the strategic choices that have converged:

**Repo separation.** Engine in public game repo. Content in private content repo. Boundary is the schema.

**GitHub as source of truth.** Push to content repo `main` triggers CI validation. Green CI = deployable. Skip any intermediate "deploy to Railway storage" step — the game pulls directly from GitHub when the operator triggers an apply.

**Operator-triggered apply.** A superuser command in the game pulls the latest validated content and applies it. Automation stops at "validated and merged"; the actual world mutation stays operator-gated. (Higher automation tiers are possible later but not the starting point.)

**Scope as a parameter.** The apply command takes a scope: district, zone, multi-scope, or all. Same command, same code path, different scope. District is the natural smallest unit because it's already the redeploy unit in the existing design.

**Idempotent against any starting state.** Clean+rebuild of the affected scope, mirroring the existing design. Same YAML applied twice produces the same end state. Reconcile-style (Terraform diffing) is explicitly out of scope — it's an order of magnitude more work and the operator-evacuation model means in-room state preservation isn't needed.

**Pre-flight validation, no partial apply.** All validation runs before any DB mutation. The loader resolves cross-references, checks every referenced typeclass and helper exists in the running engine, verifies required attrs, and computes the full operation plan first. If any check fails, the apply refuses with a complete list of reasons and the world stays untouched. Never partial state. This is a defense-in-depth pass on top of CI validation — CI catches yesterday's truth, the pre-flight catches drift between when the YAML was validated and when it's being applied. Same discipline as Terraform's plan phase: the operator gets either a clean apply or a complete refusal, not "I deleted seventeen rooms and then died." Validation tiers (hard error / warning that `--force` can override / informational diff) are a design-time concern.

**Stable identity across rebuilds.** External references (quest waypoints, player bookmarks, anything that outlives a redeploy) must continue to resolve. The composite triple already provides this for tag-based lookups. Whether dbrefs themselves should also be stable is an open question (see below) but the tag-based handle is the contract.

**Async execution.** The apply runs in a way that doesn't freeze unaffected zones. Other players continue to play normally during a Millholm rebuild. This is a hard requirement, not a nice-to-have.

**Engine knows nothing about specific content.** Adding a new zone is a content-repo change only; engine code stays untouched. Adding a new typeclass is an engine change; content references it by name.

**Loader is generic; typeclasses are extensible.** The loader doesn't know about specific typeclasses. They are referenced by dotted-path string and resolved at apply time. Attributes flow through as opaque key-value payloads — the loader knows how to dispatch and apply, not what attrs *mean*. Per-typeclass validation (which attrs are required, which values are valid) is delegated to the typeclass itself, ideally via a hook the loader can call during pre-flight. Adding a new typeclass to the engine doesn't require any loader change. Same principle for exit helpers, fixtures, doors, traps, tag categories — all looked up by name through registries the engine populates, not hardcoded lists in the loader. This is what makes the loader extractable as an internal library later, and what keeps the engine and the loader independently evolvable in the meantime.

---

## Open questions — to be resolved when design begins

These are the real design problems and they shouldn't be answered now. Listing them so they aren't forgotten.

**Schema shape.** FCM rooms are typeclass-rich (`RoomBank`, `RoomCrafting`, `RoomHarvestMoon`, etc.) with detailed `details` dicts and custom fixtures. The schema has to express all of this cleanly. Includes: how typeclass-specific attrs are validated, how fixtures are declared, how cross-zone references are written.

**Exit declaration.** Per-room (and auto-generate the reverse), per-room on both sides (and validate they match), or top-level pairs. Each preserves locality on one axis and breaks it on another. Connected to the bidirectional-helper retirement question.

**Shared state on exit pairs.** Doors with shared lock state, traps with shared armed state. Either match by key (implicit, footgun-prone) or declare as top-level resources that exits reference (explicit, cleaner).

**Asymmetric and intentional one-way exits.** Schema needs to distinguish "I forgot the reverse" from "this is deliberately one-way." Lint enforcement is the long-term value of having had `connect_bidirectional_exit` in the first place.

**Stable dbref vs stable composite-triple.** Composite triple is the contract. But if the loader can also reuse the same dbref when recreating a room (same row PK on insert), every system holding a stored dbref also stays valid for free. Worth investigating whether Evennia/Postgres allow this cleanly.

**Connector rooms between districts.** Districts have rooms that are referenced by neighbouring districts. Rebuilding one district can leave another's exits pointing at the wrong place. Solved either by dynamic exit resolution via `find_room` on every traversal, by auto-expanding the rebuild scope to include affected neighbours, or by stable dbref reuse (above). All three are viable.

**What else moves to the content repo.** Zone YAMLs definitely. Spawn JSON probably (already declarative). Prototypes (weapons, armor, consumables)? Quests? NPC dialogue trees? Each is a separate decision.

**Procedural dungeon coexistence.** Procedurally-generated content doesn't fit declarative authoring. Schema needs a way for a district to declare "this is procedural" so the loader skips it.

**Helpers audit.** Many helpers in `utils/` exist because the imperative format made certain things hard. Each is a candidate to either survive (still solving a real engine problem), become loader logic (validation/dispatch/defaults), or retire (problem disappeared). `connect_bidirectional_*` is the obvious retirement candidate, but the audit should be comprehensive.

**Authoring tooling.** Linting, schema validation in editor (LSP/JSON Schema), preview/dry-run mode, diff visualisation, AI-assisted authoring affordances. None of these are needed for v0 but the schema choices should leave room for them.

**Migration path.** District-by-district translation of existing Python builders. LLM-assisted is plausible. Old and new must coexist during transition. Sequencing and acceptance criteria need their own design.

**Skip-unchanged optimization.** Whether the loader should detect objects that already exist with identical values and skip them rather than delete-and-recreate. Two flavours: per-object hash stored as an attribute for fast comparison, or YAML-fragment hashing per room. Not needed for v0 (clean+rebuild is correct and fast enough at district scale) but worth knowing it's possible.

---

## Related references

- [design/WORLD_DEPLOYMENT.md](design/WORLD_DEPLOYMENT.md) — existing operational design (mostly aspirational vs current reality)
- [design/ROOM_ARCHITECTURE.md](design/ROOM_ARCHITECTURE.md) — what rooms actually contain today
- [design/EXIT_ARCHITECTURE.md](design/EXIT_ARCHITECTURE.md) — exit types and helper conventions
- [src/game/CLAUDE.md](src/game/CLAUDE.md) — service encapsulation patterns; reactor/threading rules
- Sibling exploration in FCM on state-aware description markup — overlaps with this design and would belong in the schema eventually.
- The inspiration source's loader (`worlddata/services/import_zone_service.py`) — reference implementation, simpler than FCM would need.
