# Post-build hook

A per-entity, duck-typed, opt-in hook the Builder invokes on each created object after every YAML attribute, tag, lock, and alias has been applied. Consumer typeclasses define `wb_at_post_build()` if they need to derive state from the YAML-supplied values; everything else continues to work unchanged.

## Why this exists

[builder.md](builder.md) principle 3 — *"Typeclass declares defaults; YAML overrides per-instance"* — is implemented by calling `create_object` first and applying the YAML's `attributes:`, `tags:`, `locks:`, `aliases:` afterwards. This ordering is the load-bearing contract content authors and typeclass authors both rely on.

It also means Evennia's standard `at_object_post_creation` hook fires **before** the Builder applies the YAML values. Evennia's hook contract is "fires after the `attributes=` kwarg has been applied" — and from Evennia's point of view that's exactly what it does. But the Builder passes only `desc` via that kwarg and writes every other YAML attribute afterwards, so by the time `at_object_post_creation` fires, the YAML-supplied values are still in the typeclass-default state.

This is fine for typeclasses whose `at_object_post_creation` doesn't read attributes that the YAML will override. It is **not** fine for typeclasses that compute derived state from those attributes — they'd cache the defaults and never see the YAML.

`wb_at_post_build` is the documented seam for that case. The Builder calls it at the end of each `_build_one` pass, after every `_apply_*` has run.

## Contract

```python
class MyRoomTypeclass(BaseRoom):
    def wb_at_post_build(self):
        # All YAML attributes/tags/locks/aliases are in place on self.
        # Free to read self.foo (or self.db.foo) and derive state from it.
        ...
```

- **Signature.** No arguments. The object knows itself.
- **Timing.** Fires once per built entity, at the end of `_build_one`, after `_apply_aliases` / `_apply_locks` / `_apply_attributes` / `_apply_tags` have all completed for that entity. Inside passes 1 (non-exits) and 2 (exits) — not after passes 3 (incoming_exits) or 4 (links).
- **Opt-in.** Typeclasses without the method get a silent no-op. The library does not require a base class, mixin, or protocol declaration — just `def wb_at_post_build(self): ...` if you need it.
- **Exception isolation.** A raising hook is logged via `wb_log` at `ERROR` level. The entity remains built, the build pass continues, no `BuilderError` is raised. Consumer hook bugs cannot turn a successful apply into a "no partial state" abort.
- **No YAML schema involvement.** The hook is a typeclass method, not a YAML field. Per-build customisation is *behaviour*, not authored data.

## Comparison to `evennia-mob-spawner`'s `ms_at_post_spawn`

`evennia-mob-spawner` faces the same lifecycle gap — `create_object` followed by per-rule attribute application — and resolved it via decision #23 in that library's architecture log: a duck-typed, opt-in, single-method hook on the spawned typeclass.

| | `ms_at_post_spawn` | `wb_at_post_build` |
|---|---|---|
| Library | evennia-mob-spawner | evennia-world-builder |
| Fires per | spawned mob | built entity (any object the Builder creates) |
| Invocation site | `MobSpawnerScript._spawn_one` after `attrs:` apply | `Builder._build_one` after `_apply_tags` |
| Opt-in mechanism | `getattr(mob, "ms_at_post_spawn", None)` | `getattr(obj, "wb_at_post_build", None)` |
| Arguments | none | none |
| Exception handling | caught, logged via `ms_log` | caught, logged via `wb_log` |
| YAML schema impact | none (method-on-typeclass, not a YAML field) | same |

The shape is intentionally identical. Operators and typeclass authors learning one library's pattern recognise the other.

## What's deliberately not included

These are out of scope for this hook. Each one would be a separate design conversation if a real consumer case arises.

- **No post-pass-3-4 timing variant.** A hook that fires after `links:` cross-entity attribute references are assigned (pass 4) would let a typeclass derive state from link-resolved values. No current consumer needs this. If one appears, the answer is a *second* hook (e.g. `wb_at_post_links`), not moving this one's timing.
- **No library-declared base class.** The library does not own game concepts (CLAUDE.md principle 1). Forcing a base class to expose the hook would couple consumers to a library type for no gain over duck typing.
- **No YAML field for ad-hoc per-build callbacks.** Mob-spawner explicitly considered and rejected a YAML `post_spawn_hook:` field in favour of the method-on-typeclass protocol; world-builder follows. Per-build customisation is behaviour and belongs in Python, not data.
- **No `wb_build`-level "whole build finished" hook.** Plausible (e.g. for cross-entity invariants), but speculative. Defer until concrete.
