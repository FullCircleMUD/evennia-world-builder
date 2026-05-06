# Library Commands

evennia-world-builder ships admin commands that auto-install into any consumer game that adds `evennia_world_builder` to `INSTALLED_APPS`. The consumer does not import or wire these manually.

## Convention

- **`wb_` prefix** on every command name. Namespaces cleanly so a stray short command name (`build`, `load`) cannot accidentally invoke library work.
- **`cmd:superuser()` lock**. Library commands operate on the world database; only the actual superuser may invoke. Not just Developer permission — `superuser()` is stricter.
- **`AccountCmdSet` auto-install**. Commands are added to `AccountCmdSet` in `apps.py`'s `ready()`. `AccountCmdSet` is available OOC and merges with `CharacterCmdSet` on puppet, so library commands work in both contexts with a single patch.
- **AppConfig.ready() + `evennia._init()` wrap**. The patch happens after Evennia's lazy-attribute exports are populated; mirrors the pattern in evennia-shards. See `apps.py` for details. Idempotent — wrap-flag and patch-flag prevent double-installation.

## Configuration the consumer supplies

- **`INSTALLED_APPS`** must include `"evennia_world_builder"`. Without this, AppConfig.ready() never runs and no commands are installed.
- **`WORLDBUILDER_READER`** (optional) — dotted path to a Reader class. Defaults to `"evennia_world_builder.readers.github.GitHubReader"`.
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

**Validation gating** (see [validation-gating.md](validation-gating.md) for the full model): `definitions.yaml` carries a `repo-ci-pre-validation` flag (default `false`). When false, `wb_build` pre-validates the *whole* repo before every build. When true, it skips the whole-repo walk and trusts the consumer's external CI gate. The `--force-validate` flag forces a whole-repo validation regardless of the setting (per-invocation paranoid override).

**Usage:**

- `wb_build all` — build everything in the manifest.
- `wb_build <level>=<value> [<level>=<value> ...]` — scoped build matching the levels declared in the consumer's `definitions.yaml`.
- Append `--force-validate` to any of the above to force a whole-repo pre-validation pass for that invocation regardless of the `repo-ci-pre-validation` setting.

**Bare `wb_build` does nothing.** The explicit `all` keyword is required for a full-world build. This is a deliberate guard rail against accidental rebuilds — a stray Enter on `wb_build` should not start tearing the world apart.

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

## See also

- [discovery-and-loading.md](discovery-and-loading.md) — the underlying Finder + Loader pipeline.
- [reader-api.md](reader-api.md) — the Reader contract and dispatch via `WORLDBUILDER_READER`.
- [validator.md](validator.md) — the checks that run before the Builder is invoked.
- [builder.md](builder.md) — what the Builder does per entity (typeclass / aliases / tags / locks / attributes), and the cleanup-on-rebuild model.
- [cli.md](cli.md) — the standalone `wb-validate` counterpart that runs the same pipeline outside Evennia.
