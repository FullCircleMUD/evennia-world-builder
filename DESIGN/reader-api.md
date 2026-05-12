# Reader API

The Reader contract has moved.

`Reader`, `ReaderResult`, `GitHubReader`, `LocalReader`, and the typed exception hierarchy (`ReaderError` + `ReaderAuthError` / `ReaderNotFoundError` / `ReaderNetworkError` / `ReaderParseError`) now live in the sibling library [evennia-yaml-reader](https://github.com/FullCircleMUD/evennia-yaml-reader). World-builder depends on it and re-exports the classes from its own top-level package for consumer convenience — `from evennia_world_builder import GitHubReader` continues to work unchanged.

For the contract itself, see:

- **[evennia-yaml-reader's DESIGN/reader-api.md](https://github.com/FullCircleMUD/evennia-yaml-reader/blob/main/DESIGN/reader-api.md)** — the architectural decisions behind the contract.
- **[evennia-yaml-reader's base.md](https://github.com/FullCircleMUD/evennia-yaml-reader/blob/main/src/evennia_yaml_reader/base.md)** — the co-located reference for `Reader` and `ReaderResult`.

## What stays in world-builder

The Reader is the *transport* layer for fetching YAML; world-builder owns the *dispatch* layer that selects which Reader implementation to use at runtime:

- **`WORLDBUILDER_READER`** (optional setting) — dotted path to the Reader class. Defaults to `"evennia_yaml_reader.github.GitHubReader"`.
- **`WORLDBUILDER_READER_KWARGS`** (dict setting) — forwarded verbatim to the resolved Reader class's `__init__`. Reader-specific (`repo`/`ref`/`pat` for GitHub; `root` for local).
- **`get_reader_class()`** — resolves the dotted-path setting to a class.
- **`get_configured_reader()`** — resolves *and* instantiates with the configured kwargs.

Both helpers live in `evennia_world_builder.config` and stay world-builder-specific because the settings names (`WORLDBUILDER_READER*`) are world-builder's vocabulary. Sibling libraries (`evennia-mob-spawner`, etc.) that also use the Reader define their own settings names and their own dispatch helpers — the Reader library doesn't dictate either.

## Provenance

The Reader was first proven inside this library; an extraction once a second consumer ([evennia-mob-spawner](https://github.com/FullCircleMUD/evennia-mob-spawner)) and the prospect of further declarative-content libraries (quests, recipes) tipped the duplicate-vs-extract math toward extraction. See [progress.md](progress.md) for the milestone log.
