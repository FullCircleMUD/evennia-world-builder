# Library Commands

evennia-world-builder ships admin commands that auto-install into any consumer game that adds `evennia_world_builder` to `INSTALLED_APPS`. The consumer does not import or wire these manually.

## Convention

- **`wb_` prefix** on every command name. Namespaces cleanly so a stray short command name (`build`, `load`) cannot accidentally invoke library work.
- **`cmd:superuser()` lock**. Library commands operate on the world database; only the actual superuser may invoke. Not just Developer permission — `superuser()` is stricter.
- **`AccountCmdSet` auto-install**. Commands are added to `AccountCmdSet` in `apps.py`'s `ready()`. `AccountCmdSet` is available OOC and merges with `CharacterCmdSet` on puppet, so library commands work in both contexts with a single patch.
- **AppConfig.ready() + `evennia._init()` wrap**. The patch happens after Evennia's lazy-attribute exports are populated; mirrors the pattern in evennia-shards. See `apps.py` for details. Idempotent — wrap-flag and patch-flag prevent double-installation.

## Configuration the consumer supplies

- **`INSTALLED_APPS`** must include `"evennia_world_builder"`. Without this, AppConfig.ready() never runs and no commands are installed.
- **`WORLDBUILDER_READER`** (optional) — dotted path to a Reader class. Defaults to `"evennia_yaml_reader.github.GitHubReader"`.
- **`WORLDBUILDER_READER_KWARGS`** — dict of kwargs the chosen Reader's `__init__` requires. The library forwards them verbatim, so the consumer fully controls reader-specific configuration. Example for `GitHubReader`:

  ```python
  WORLDBUILDER_READER_KWARGS = {
      "repo": "FullCircleMUD/world-content",
      "ref": "main",
      "pat": "...",  # PAT — local: secret_settings.py override; production: env var
  }
  ```

## Current commands

### `wb_build`

Build world content from the configured manifest source. Drives the full pipeline: `Definitions → Finder → Loader → Validator → Builder`. On a clean validator pass the Builder runs and:

- **cleans up** prior deployments of the source files in this build's scope (Evennia objects tagged `wb_deployment_file=<file>` are deleted before fresh ones are created — see [builder.md](builder.md) and [deployment-identity.md](deployment-identity.md));
- **creates one Evennia object per entity** with all standard per-object dimensions applied: `typeclass`, `name`, `location`, `description`, `aliases`, `tags` (author tags plus the auto-set `wb_deployment_file` / `wb_deployment_id` pair), `locks`, `attributes` (YAML overriding any typeclass defaults).

On any validation finding the command surfaces every message via `caller.msg()` and refuses to call the Builder — same complete-refusal semantics as the standalone CLI.

**Validation scope.** Every invocation loads and validates the whole repo before building, then narrows to the requested scope. There is no gated mode and no flag to skip it: cross-file references and repo-wide identity uniqueness are only checkable at full scope. If the walk becomes a bottleneck the fix belongs in the Reader (a bulk fetch), not in validating less.

**Usage:**

- `wb_build all` — build everything in the manifest.
- `wb_build <level>=<value> [<level>=<value> ...]` — scoped build matching the levels declared in the consumer's `definitions.yaml`.

The command takes no flags; any `--token` is refused rather than ignored, so a typo can't look like it took effect.

**Bare `wb_build` does nothing.** The explicit `all` keyword is required for a full-world build. This is a deliberate guard rail against accidental rebuilds — a stray Enter on `wb_build` should not start tearing the world apart.

**Co-installed with `evennia-shards`** (and the role is not `monolith`), three further refusals apply — see [interoperability.md](interoperability.md):

- `wb_build all` is refused; build one shard at a time.
- The query must start with `shard=`.
- The named shard must be the one this process is running as.

**Failure modes:**

- No scope specified → refusal with a usage hint.
- Bad token format (no `=`, empty key/value) → error.
- Reader misconfiguration → error from `get_configured_reader()`.
- `definitions.yaml` missing or malformed → error from `Definitions.from_reader()`.
- Query key not in declared levels, or skips a level → `DefinitionsError` (caught at validation, before any walk).
- Query value not found in the manifest → `FinderQueryError` (caught during the walk).
- Index missing in a folder, or pointing at a non-existent file → `LoaderError` subtype.
- Validator findings (e.g. missing required field, malformed shape, unresolvable typeclass, duplicate `deployment_id`) → every message echoed, then `wb_build` refuses without invoking the Builder.
- Builder failure (typeclass instantiation error, tag/lock/attribute application exception, cleanup deletion failure) → wrapped in `BuilderError`, surfaced via `caller.msg`, no further entities processed.

The command echoes each pipeline stage (pre-validation reason, validator findings, cleanup count, created objects) for visual verification. The output shape is a debugging aid; not part of any contract.

#### Async execution

`wb_build` defers the entire pipeline to a Twisted worker thread via `evennia.utils.utils.run_async`. **Long deployments do not block the Evennia reactor** — players continue interacting with the game while the build runs.

Flow:

1. **Reactor thread.** `func()` parses args (immediate feedback for malformed input), echoes the invocation back as `"wb_build <args> : running async (gameplay continues)…"` so the operator sees exactly which scope they kicked off, and hands the pipeline off via `run_async(self._run_pipeline, …, at_return=…, at_err=…)`.
2. **Worker thread.** `_run_pipeline` runs the full Reader → Definitions → Finder → Loader → Validator → Builder chain. Operator-facing output is collected into a list of strings (no `caller.msg()` calls — caller pipes are reactor-only). Pipeline-level errors append to the messages list and return normally; only unexpected exceptions bubble out for `at_err`.
3. **Reactor thread (callback).** `_on_async_return` flushes every message via `self.caller.msg()`. `_on_async_err` handles unexpected pipeline exceptions with a one-line summary; the traceback stays in the server log.

Side effects driven by Evennia's own event pipeline (e.g. "brick oven arrives to Limbo from Goldencrust Bakery" during cleanup-on-rebuild) appear in real time as the build progresses. Operator-emitted messages from the pipeline batch at the end of the build via the success callback.

**Caveat — concurrent player actions during the build.** If a player is in a room being deleted by the cleanup pass, `obj.delete()` relocates them to home before the row is removed. This was always the case; the async wrap doesn't change the per-object behaviour, just removes the global reactor pause that previously masked it.

## See also

- [discovery-and-loading.md](discovery-and-loading.md) — the underlying Finder + Loader pipeline.
- [reader-api.md](reader-api.md) — the Reader contract and dispatch via `WORLDBUILDER_READER`.
- [validator.md](validator.md) — the checks that run before the Builder is invoked.
- [builder.md](builder.md) — what the Builder does per entity (typeclass / aliases / tags / locks / attributes), and the cleanup-on-rebuild model.
- [cli.md](cli.md) — the standalone `wb-validate` counterpart that runs the same pipeline outside Evennia.
