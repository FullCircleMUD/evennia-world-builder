# Compatibility with `evennia-shards`

The library is **compatible with [`evennia-shards`](https://github.com/FullCircleMUD/evennia-shards) but does not require it**. The integration is a single optional import in [`commands.py`](../src/evennia_world_builder/commands.py) and a one-line wrap around the `run_async` dispatch.

## What the integration does

`wb_build` defers its pipeline to a Twisted worker thread via `evennia.utils.utils.run_async`. Under a multi-tenant `shards` deployment, the worker thread spawns with a fresh `threading.local` — the active tenant set on the reactor thread does not carry across. Without intervention, any `ObjectDB` row built inside the worker would land `shard_id=NULL`: the shards library's auto-stamp condition requires a tenant to be set, and the worker thread has none.

`wb_build` resolves this by wrapping its pipeline callable with `preserve_tenant_context` from the shards library:

```python
try:
    from evennia_shards import preserve_tenant_context
except ImportError:
    def preserve_tenant_context(fn):
        return fn

# ...

run_async(
    preserve_tenant_context(self._run_pipeline), query, flags,
    at_return=self._on_async_return,
    at_err=self._on_async_err,
)
```

`preserve_tenant_context` captures the reactor thread's active tenant at wrap time and re-applies it inside the worker on entry — rooms built by `wb_build` get correctly stamped with whichever shard the process is running as.

## What happens without shards

If `evennia-shards` is not installed, the top-of-module `try` import raises `ImportError` and the fallback identity function takes its place. `preserve_tenant_context(fn)` then returns `fn` unchanged. `wb_build` runs identically to a non-sharded deployment.

There is no configuration to set, no settings flag to flip, no `INSTALLED_APPS` change required. The integration is structural — the optional import does its thing at module load time, and the wrap is a no-op in the non-sharded case.

## What's guaranteed

- **shards installed + configured** (e.g. `SHARDS_ROLE=shard, SHARD_ID=shard0`): rooms built by `wb_build` land `shard_id="shard0"`. Cross-shard `@tel` into them works. Auto-filter behaviour is correct.
- **shards installed + monolith mode**: shards' `apps.py` returns early in monolith, so no tenant context is ever set. `preserve_tenant_context` captures `None`, the wrapped callable runs unscoped (same as if no shards were installed). No-op effectively.
- **shards not installed**: the optional-import fallback applies. World-builder behaves exactly as the standalone library — no DB-level partitioning, no shard stamping.

## What's not in scope

The library does not currently validate that a `wb_build shard=<id>` invocation matches the running process's actual `shard_id`. An operator running `wb_build shard=shard1` while connected to a shard0 process would get rooms stamped `shard0` (the running process's identity). A future enhancement — gated on `_HAS_SHARDS` — could compare the operator's intent against `get_shard_id()` and refuse mismatched invocations. Deferred until a real misuse case appears.

## Testing the integration

Run `wb_build zone=<some_zone>` from a shard process, then inspect the resulting rooms' `shard_id` field via shards' `@shard_check <room>` admin command. The value should match the process's configured `SHARD_ID`. From the standalone library's test environment (no shards installed), the integration's identity-fallback path is exercised — the 390-test suite passes both ways.
