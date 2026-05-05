# Reader API

The library's first contract: a `Reader` is a configured connection to a content source (a GitHub repo, an S3 bucket, a filesystem root). Construction kwargs are reader-specific (auth, source identity); `path` is supplied per-read as the query against that source. `read(path)` returns a `ReaderResult` with the raw bytes and parsed YAML. Concrete subclasses determine the source. Future readers plug in via the `WORLDBUILDER_READER` setting without library changes.

## Shipped readers

| Class | Source | Construction kwargs | Use case |
|---|---|---|---|
| `GitHubReader` | GitHub Contents API | `repo`, `ref`, `pat` | Production / canonical content fetch |
| `LocalReader` | Local filesystem tree | `root` | Dev iteration, CI/pre-commit, standalone `wb-validate` |

Both use the same downstream pipeline. A developer iterates locally with `LocalReader` against a checkout; the same content goes through `GitHubReader` once committed. No code path changes — only the configured reader.

## Decisions

- **Strategy pattern, not ABC.** `Reader` is a base class with `read()` raising `NotImplementedError`. Duck-typed; mirrors evennia-shards convention.
- **Settings-based dispatch.** `WORLDBUILDER_READER` (dotted path, default `"world_builder.GitHubReader"`) resolved by `get_reader_class()`. Consumer-extensible without library changes (principle #3).
- **Kwargs are consumer-side.** Library does not dictate setting names per platform. The consumer constructs the reader with whatever kwargs that reader requires.
- **`ReaderResult` dataclass** holds both raw bytes and parsed value. Preserves observability for diagnostics; future failure modes (e.g. partial parse) can surface raw bytes alongside the failure.
- **Typed exceptions.** `ReaderError` base + `ReaderAuthError` / `ReaderNotFoundError` / `ReaderNetworkError` / `ReaderParseError` subtypes. Consumer can catch each class semantically. UTF-8 decode failures fold into `ReaderParseError` (degenerate parse case).
- **No `get_reader(**kwargs)` helper.** The library factory returns the class only; construction stays consumer-side because kwargs are reader-specific.
- **Sub-package for readers.** `readers/base.py` holds the `Reader` contract; `readers/github.py` holds `GitHubReader`. Other library modules (`config.py`, `errors.py`) stay at the top level. The sub-package establishes the extension namespace early so future readers (other platforms, consumer-built) have an obvious home.
- **Discoverability via `required_kwargs`.** Each Reader subclass declares a `required_kwargs` class attribute (a tuple of strings) listing the keyword arguments its `__init__` accepts. Consumers can introspect via `ReaderClass.required_kwargs` rather than reading docstrings. Default on the base `Reader` is `()`. Note: `path` is NOT a construction kwarg — it is supplied per-read.
- **`path` is a per-read parameter, not a construction kwarg.** A Reader instance is reusable across many `read(path)` calls against the same source. Required for Finder and Loader, which read many files (definitions.yaml, multiple index.yaml files, content files) from one configured Reader.

## Settles spike-1 deferred question

The spike's "library/consumer boundary for fetch+auth" is now resolved:

- **Library owns:** fetch, parse, error mapping, dispatch.
- **Consumer owns:** credential storage, reader construction (kwargs), settings naming.

## Test approach

Unit tests in `src/world_builder/tests.py`:

- `GitHubReaderTest` — mocks `urllib.request.urlopen` via `unittest.mock.patch`. Covers happy path (raw + parsed return, URL/header construction), four error paths (401, 404, network, bad YAML), and `required_kwargs` declaration.
- `LocalReaderTest` — uses `tempfile.TemporaryDirectory` for real-filesystem fixtures. Covers happy path (single + nested paths), missing-file + bad-YAML error paths, path-traversal guard (escape attempts via `..` resolve outside `root` and are rejected as not-found), and string-vs-Path root acceptance.
- `GetReaderClassTest` — covers dispatch (default returns `GitHubReader`; `@override_settings` returns custom; bad dotted path raises).
