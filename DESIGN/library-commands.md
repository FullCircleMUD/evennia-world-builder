# Library Commands

world-builder ships admin commands that auto-install into any consumer game that adds `world_builder` to `INSTALLED_APPS`. The consumer does not import or wire these manually.

## Convention

- **`wb_` prefix** on every command name. Namespaces cleanly so a stray short command name (`build`, `load`) cannot accidentally invoke library work.
- **`cmd:superuser()` lock**. Library commands operate on the world database; only the actual superuser may invoke. Not just Developer permission — `superuser()` is stricter.
- **`AccountCmdSet` auto-install**. Commands are added to `AccountCmdSet` in `apps.py`'s `ready()`. `AccountCmdSet` is available OOC and merges with `CharacterCmdSet` on puppet, so library commands work in both contexts with a single patch.
- **AppConfig.ready() + `evennia._init()` wrap**. The patch happens after Evennia's lazy-attribute exports are populated; mirrors the pattern in evennia-shards. See `apps.py` for details. Idempotent — wrap-flag and patch-flag prevent double-installation.

## Configuration the consumer supplies

- **`INSTALLED_APPS`** must include `"world_builder"`. Without this, AppConfig.ready() never runs and no commands are installed.
- **`WORLDBUILDER_READER`** (optional) — dotted path to a Reader class. Defaults to `"world_builder.readers.github.GitHubReader"`.
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

Build world content from the configured manifest source. Drives the full discovery + loading pipeline (`Definitions → Finder → Loader`); the Validator and Builder phases are not yet implemented.

**Usage:**

- `wb_build all` — build everything in the manifest.
- `wb_build <level>=<value> [<level>=<value> ...]` — scoped build matching the levels declared in the consumer's `definitions.yaml`.

**Bare `wb_build` does nothing.** The explicit `all` keyword is required for a full-world build. This is a deliberate guard rail against accidental rebuilds — a stray Enter on `wb_build` should not start tearing the world apart.

**Failure modes:**

- No scope specified → refusal with a usage hint.
- Bad token format (no `=`, empty key/value) → error.
- Reader misconfiguration → error from `get_configured_reader()`.
- `definitions.yaml` missing or malformed → error from `Definitions.from_reader()`.
- Query key not in declared levels, or skips a level → `DefinitionsError` (caught at validation, before any walk).
- Query value not found in the manifest → `FinderQueryError` (caught during the walk).
- Index missing in a folder, or pointing at a non-existent file → `LoaderError` subtype.

For the iterative-build phases this command currently echoes the pipeline stages and dumps the loaded entities for visual verification. The output shape is a debugging aid; not part of any contract.

## See also

- [discovery-and-loading.md](discovery-and-loading.md) — the underlying Finder + Loader pipeline.
- [reader-api.md](reader-api.md) — the Reader contract and dispatch via `WORLDBUILDER_READER`.
