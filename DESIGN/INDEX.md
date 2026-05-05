# DESIGN Index

Map of all design documents in this directory, organised by category. Add new documents here when they land — un-indexed documents are invisible.

## Process and discipline

- **[documentation-structure.md](documentation-structure.md)** — what goes in CLAUDE.md vs README.md vs DESIGN/, conventions for new design documents.
- **[progress.md](progress.md)** — running log of milestones with links to evidence.

## Architecture and design

- **[spike-1-load-from-github.md](spike-1-load-from-github.md)** — first PoC: validate that an Evennia superuser command can fetch and parse YAML from a private GitHub repo. Establishes the auth/fetch foundation for all subsequent spikes.
- **[reader-api.md](reader-api.md)** — the library's Reader contract: settings-based dispatch, GitHubReader as first concrete implementation, typed exceptions, `required_kwargs` for discoverability. Settles spike 1's "library/consumer fetch boundary" question.
- **[discovery-and-loading.md](discovery-and-loading.md)** — Finder and Loader: how the library navigates a content repo's manifest tree (definitions.yaml + per-folder index.yaml) and assembles all matching content for downstream processing. Settles spike 1's deferred "indexing convention" question.
- **[deployment-identity.md](deployment-identity.md)** — load-bearing identity scheme: `(deployment_file, deployment_id)` composite, file as atomic deployment unit, cross-reference syntax, cleanup model. Anchor for Validator, Builder, and partial deploys.
- **[validator.md](validator.md)** — two-tier check architecture (stateless predicates + stateful per-file index), complete-refusal semantics, currently shipped checks, and how to add new ones.
- **[cli.md](cli.md)** — standalone console-script CLIs (`wb-validate`); `--reader` dispatch, exit-code semantics, and how this tier coexists with the in-game commands.
- **[library-commands.md](library-commands.md)** — conventions for library-shipped admin commands (`wb_` prefix, superuser lock, AppConfig auto-install into AccountCmdSet) and current commands (`wb_build`).

## Archive

Historical context, not authoritative. Material in `archive/` is preserved per the "don't delete; supersede" principle.

- **[archive/WorldAsDataNotes.md](archive/WorldAsDataNotes.md)** — original brainstorm captured during FCM-side discussions that led to creating this library.
- **[archive/evennia-prior-art-survey.md](archive/evennia-prior-art-survey.md)** — survey (2026-05-05) of existing YAML-based world authoring in the Evennia ecosystem. Finding: no widely-adopted layer exists.
- **[archive/existing-yaml-system-survey.md](archive/existing-yaml-system-survey.md)** — survey (2026-05-05) of an existing YAML-based world system, used as design input.
