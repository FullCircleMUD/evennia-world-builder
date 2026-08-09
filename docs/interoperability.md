# Interoperability

This library against every sibling library in `libraries/`. What it does that can constrain, or be
constrained by, a sibling: it reads world YAML through a pluggable reader, dispatches its whole build
pipeline **off the reactor thread**, and creates `ObjectDB` rows — rooms, exits and their contents —
via `create_object`. It creates no persistent scripts.

## evennia-mob-spawner

**No coupling in code.** Neither library imports the other, though both consume `evennia-yaml-reader`
and both deploy content into the same world.

**Content-level dependency, in one direction.** mob-spawner locates candidate rooms by tag, and those
rooms are typically the ones this library builds from YAML. The tags are ordinary Evennia tags, so the
coupling runs through the game database rather than either API — but rules deployed against rooms that
do not yet exist, or that were built without the expected tag, find nothing to spawn into. Deploy order
matters: rooms before rules. The requirement is mob-spawner's and is documented in
[its `interoperability.md`](../../evennia-mob-spawner/docs/interoperability.md).

## evennia-shards

**Optional integration.** `commands.py` imports `preserve_tenant_context` behind a `try`, falling back
to an identity function when shards is absent, so both co-installed and standalone deployments are
supported. See [shards-compatibility.md](shards-compatibility.md) for the mechanism.

`wb_build` defers its entire pipeline to a worker thread, and shards keeps the active shard in
thread-local storage — so without that wrap every row the build creates would land with
`shard_id=NULL`. The wrap must stay at the dispatch site: `preserve_tenant_context` captures the tenant
eagerly at wrap time, not when the wrapped callable runs.

**The first declared level must be named `shard`.** When co-installed with shards, the first entry in
`definitions.yaml`'s `levels:` list must be `shard`, and its value is the `SHARD_ID` of the shard the
content under it belongs to. Level names are otherwise consumer-chosen, so this is the one naming rule
the pairing imposes — it is what lets `wb_build` tell which shard a scope belongs to.

The mandate is enforced, not merely documented: once `definitions.yaml` is parsed, `wb_build` refuses
outright if `shard` is not the first declared level. Nothing else catches that — a consumer who
co-installs shards but keeps their own level names has queries that validate cleanly against their own
declarations.

`wb_build` then requires the query to *start* with `shard=`, and compares its value against
`get_shard_id()`, refusing when they differ — so content can only be built from the process that owns
it. The router runs unscoped and its `SHARD_ID` is mandated to be `"router"`, so this rejects
`wb_build` on the router — where a build would otherwise create rooms carrying no shard stamp, rows no
shard can see — without needing a role check.

**`wb_build all` is refused when shards is installed.** The empty query means build-everything, which
spans every shard's files and so can only ever be partly correct on a single process. Rather than
silently narrowing the scope, the command refuses and tells the operator to build one shard at a time.

**Monolith counts as a non-sharded install.** The gate is *shards installed **and** role is not
`monolith`* — not merely that the import succeeded. Under `monolith` there is no shard context and
`get_shard_id()` returns `None`, so both the shard-match check and the `wb_build all` refusal are
skipped and the library behaves exactly as it does standalone.

These rules mirror mob-spawner's `ms_load`, which faces the same exposure from the same direction; see
[its `interoperability.md`](../../evennia-mob-spawner/docs/interoperability.md).

The `ScriptDB` constraint that applies to mob-spawner does not apply here: this library creates no
persistent scripts, so it has nothing that ticks once per process.

## evennia-targeting

**No coupling.** Neither library imports the other, and they share no data. Targeting resolves in-room
search terms against objects already in play; it plays no part in building them.

## evennia-world-builder

This library.

## evennia-yaml-reader

**Hard dependency.** Imported unconditionally — `Reader` in `builder.py`, `definitions.py` and
`finder.py`, `LocalReader` in `cli.py`, `ReaderError` in `commands.py`. The reader is the only path by
which this library touches YAML; it does no file I/O of its own, which is what lets the same pipeline
run against a local checkout and a remote repo.
