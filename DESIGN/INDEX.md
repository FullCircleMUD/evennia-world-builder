# DESIGN Index

Map of all design documents in this directory, organised by category. Add new documents here when they land — un-indexed documents are invisible.

## Process and discipline

- **[documentation-structure.md](documentation-structure.md)** — what goes in CLAUDE.md vs README.md vs DESIGN/, conventions for new design documents.
- **[progress.md](progress.md)** — running log of milestones with links to evidence.

## Architecture and design

- **[spike-1-load-from-github.md](spike-1-load-from-github.md)** — first PoC: validate that an Evennia superuser command can fetch and parse YAML from a private GitHub repo. Establishes the auth/fetch foundation for all subsequent spikes.
- **[reader-api.md](reader-api.md)** — the library's Reader contract: settings-based dispatch, GitHubReader as first concrete implementation, typed exceptions, `required_kwargs` for discoverability. Settles spike 1's "library/consumer fetch boundary" question.
- **[discovery-and-loading.md](discovery-and-loading.md)** — Finder and Loader: how the library navigates a content repo's manifest tree (definitions.yaml + per-folder index.yaml) and assembles all matching content for downstream processing. Settles spike 1's deferred "indexing convention" question.

## Archive

Historical context, not authoritative. Material in `archive/` is preserved per the "don't delete; supersede" principle.

- **[archive/WorldAsDataNotes.md](archive/WorldAsDataNotes.md)** — original brainstorm captured during FCM-side discussions that led to creating this library.
- **[archive/evennia-prior-art-survey.md](archive/evennia-prior-art-survey.md)** — survey (2026-05-05) of existing YAML-based world authoring in the Evennia ecosystem. Finding: no widely-adopted layer exists.
- **[archive/existing-yaml-system-survey.md](archive/existing-yaml-system-survey.md)** — survey (2026-05-05) of an existing YAML-based world system, used as design input.
