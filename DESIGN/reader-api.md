# Reader API

The library's first contract: a `Reader` is anything that fetches a single YAML document from a backing source and returns a `ReaderResult`. Concrete subclasses determine the source. The library ships `GitHubReader` as the first implementation; future readers (other platforms, consumer-built) plug in via the `WORLDBUILDER_READER` setting.

## Decisions

- **Strategy pattern, not ABC.** `Reader` is a base class with `read()` raising `NotImplementedError`. Duck-typed; mirrors evennia-shards convention.
- **Settings-based dispatch.** `WORLDBUILDER_READER` (dotted path, default `"world_builder.GitHubReader"`) resolved by `get_reader_class()`. Consumer-extensible without library changes (principle #3).
- **Kwargs are consumer-side.** Library does not dictate setting names per platform. The consumer constructs the reader with whatever kwargs that reader requires.
- **`ReaderResult` dataclass** holds both raw bytes and parsed value. Preserves observability for diagnostics; future failure modes (e.g. partial parse) can surface raw bytes alongside the failure.
- **Typed exceptions.** `ReaderError` base + `ReaderAuthError` / `ReaderNotFoundError` / `ReaderNetworkError` / `ReaderParseError` subtypes. Consumer can catch each class semantically. UTF-8 decode failures fold into `ReaderParseError` (degenerate parse case).
- **No `get_reader(**kwargs)` helper.** The library factory returns the class only; construction stays consumer-side because kwargs are reader-specific.
- **Sub-package for readers.** `readers/base.py` holds the `Reader` contract; `readers/github.py` holds `GitHubReader`. Other library modules (`config.py`, `errors.py`) stay at the top level. The sub-package establishes the extension namespace early so future readers (other platforms, consumer-built) have an obvious home.
- **Discoverability via `required_kwargs`.** Each Reader subclass declares a `required_kwargs` class attribute (a tuple of strings) listing the keyword arguments its `__init__` accepts. Consumers can introspect via `ReaderClass.required_kwargs` rather than reading docstrings. Default on the base `Reader` is `()`.

## Settles spike-1 deferred question

The spike's "library/consumer boundary for fetch+auth" is now resolved:

- **Library owns:** fetch, parse, error mapping, dispatch.
- **Consumer owns:** credential storage, reader construction (kwargs), settings naming.

## Test approach

Unit tests in `src/world_builder/tests.py` mock `urllib.request.urlopen` via `unittest.mock.patch`. No new test dependencies. Five `GitHubReaderTest` cases cover the happy path (raw + parsed return, URL/header construction) and the four error paths. Three `GetReaderClassTest` cases cover the dispatch (default returns `GitHubReader`; `@override_settings` returns custom; bad dotted path raises). Plus the existing two smoke tests = 11 tests total.
