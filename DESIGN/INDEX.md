# DESIGN Index

Map of all design documents in this directory, organised by category. Add new documents here when they land — un-indexed documents are invisible.

## Process and discipline

- **[documentation-structure.md](documentation-structure.md)** — what goes in CLAUDE.md vs README.md vs DESIGN/, conventions for new design documents.
- **[progress.md](progress.md)** — running log of milestones with links to evidence.

## Architecture and design

- **[spike-1-load-from-github.md](spike-1-load-from-github.md)** — first PoC: validate that an Evennia superuser command can fetch and parse YAML from a private GitHub repo. Establishes the auth/fetch foundation for all subsequent spikes.
- **[reader-api.md](reader-api.md)** — pointer to the Reader contract (now in [evennia-yaml-reader](https://github.com/FullCircleMUD/evennia-yaml-reader)) plus what stays in world-builder: settings-based dispatch via `WORLDBUILDER_READER` / `WORLDBUILDER_READER_KWARGS`, the `get_reader_class()` / `get_configured_reader()` helpers, and the convenience re-exports.
- **[discovery-and-loading.md](discovery-and-loading.md)** — Finder and Loader: how the library navigates a content repo's manifest tree (definitions.yaml + per-folder index.yaml) and assembles all matching content for downstream processing. Settles spike 1's deferred "indexing convention" question.
- **[deployment-identity.md](deployment-identity.md)** — load-bearing identity scheme: `(deployment_file, deployment_id)` composite, file as atomic deployment unit, cross-reference syntax, cleanup model. Anchor for Validator, Builder, and partial deploys.
- **[validator.md](validator.md)** — predicate-tier architecture (stateless / stateful / Evennia-runtime / cross-repo), top-level-vs-nested scope split, complete-refusal semantics, currently shipped checks, and how to add new ones.
- **[builder.md](builder.md)** — what the Builder does per entity (typeclass / aliases / tags / locks / attributes / description / location), the cleanup-on-rebuild model, and the auto-set `wb_deployment_file` / `wb_deployment_id` tag pair.
- **[links.md](links.md)** — generic file-level `links:` block for cross-entity attribute references (e.g. door pairs sharing `other_side`). Covers YAML shape, validator tier mapping, the Builder's pass 4 placement, and the cross-file "declare in both files" convention.
- **[post-build-hook.md](post-build-hook.md)** — `wb_at_post_build`, the per-entity duck-typed hook the Builder invokes after every YAML attribute/tag/lock/alias has been applied. Consumer typeclasses define it when they need to derive state from YAML-supplied values rather than typeclass defaults. Mirrors `evennia-mob-spawner`'s `ms_at_post_spawn`.
- **[validation-gating.md](validation-gating.md)** — when `wb_build` pre-validates the whole repo vs trusts the consumer's CI gate. Four-way decision matrix (`repo-ci-pre-validation` setting × `--force-validate` flag), three-tier cross-reference correctness model, why the library never verifies gating itself.
- **[cli.md](cli.md)** — standalone console-script CLIs (`wb-validate`); `--reader` dispatch, exit-code semantics, and how this tier coexists with the in-game commands.
- **[library-commands.md](library-commands.md)** — conventions for library-shipped admin commands (`wb_` prefix, superuser lock, AppConfig auto-install into AccountCmdSet) and current commands (`wb_build`).
- **[runtime-lookups.md](runtime-lookups.md)** — `api` module helpers for consumer game code (`wb_lookup_dbref`, `wb_lookup_object`): translating the stable `(deployment_file, deployment_id)` pair to an Evennia dbref or object at runtime, indexed two-tag query, naming convention.
- **[logging.md](logging.md)** — dedicated `world-builder.log` co-located with Evennia logs, via `evennia.utils.logger.log_file()`. `wb_log` shim with ISO-timestamp + level format; silent no-op outside Evennia (CLI path). Hardcoded filename, no rotation/structured logging/Python `logging` integration.

## Archive

Historical context, not authoritative. Material in `archive/` is preserved per the "don't delete; supersede" principle.

- **[archive/WorldAsDataNotes.md](archive/WorldAsDataNotes.md)** — original brainstorm captured during FCM-side discussions that led to creating this library.
- **[archive/evennia-prior-art-survey.md](archive/evennia-prior-art-survey.md)** — survey (2026-05-05) of existing YAML-based world authoring in the Evennia ecosystem. Finding: no widely-adopted layer exists.
- **[archive/existing-yaml-system-survey.md](archive/existing-yaml-system-survey.md)** — survey (2026-05-05) of an existing YAML-based world system, used as design input.
