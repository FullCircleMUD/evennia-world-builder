# readers/base — Reader contract

Documentation for [base.py](base.py). The source file is intentionally code-only; everything explanatory lives here.

This module defines the **contract** every world-builder source reader satisfies: a configured connection to some content backend (GitHub, S3, local filesystem, …) that fetches a single YAML document at a given path and returns its raw bytes plus the parsed structure. The base classes carry no behaviour beyond the contract — concrete readers (`github.py`, `local.py`) implement the actual I/O.

---

## Consumes

| What | Used for |
|---|---|
| `dataclasses.dataclass` (stdlib) | `ReaderResult`'s frozen-dataclass declaration |

The base contract deliberately depends on nothing else in the library — it sits at the bottom of the dependency graph so concrete readers and downstream pipeline components (Finder, Loader, Validator, Builder) can all import it without cycles.

The typed exception hierarchy referenced in the contract (`ReaderAuthError`, `ReaderNotFoundError`, …) lives in [`errors.py`](../errors.py) and is **not imported here** — it's documented as part of the contract that subclasses must honour, not consumed by the base classes.

## Consumed by

| Consumer | What it uses |
|---|---|
| [readers/\_\_init\_\_.py](__init__.py) | Re-exports `Reader`, `ReaderResult` from the package |
| [readers/github.py](github.py) | `from .base import Reader, ReaderResult` — `GitHubReader(Reader)` subclass |
| [readers/local.py](local.py) | `from .base import Reader, ReaderResult` — `LocalReader(Reader)` subclass |
| [definitions.py](../definitions.py) | `Reader` as type hint on `Definitions.from_reader(reader: Reader, …)` |
| [finder.py](../finder.py) | `Reader` as type hint on `Finder.__init__(reader: Reader, …)` |
| [loader.py](../loader.py) | `Reader` as type hint on `Loader.__init__(reader: Reader, …)` |
| [\_\_init\_\_.py](../__init__.py) (top-level) | Re-exports `Reader`, `ReaderResult` as part of the public API |

---

## `ReaderResult`

### Signature

```python
@dataclass(frozen=True)
class ReaderResult:
    raw_bytes: bytes
    parsed: object
```

### Purpose

The return value of every successful `Reader.read()` call. Carries both the unparsed bytes and the parsed YAML structure so callers can choose whichever they need without re-fetching:

- **`raw_bytes`** — the fetch result before any decoding. Used for hashing, logging, change-detection, or "show me exactly what the server sent" debugging surfaces. Bytes (not str) so non-UTF-8 content surfaces as a parse error rather than a silent decode artefact.
- **`parsed`** — `yaml.safe_load` output: a dict, list, scalar, or `None` (empty document). Typed as `object` because the contract doesn't constrain YAML's outer shape — that's the consumer's job.

### Design notes

- **Frozen.** A reader's result is an observation of a moment in time at the source; mutating it after return would conflate "what we read" with "what we did with it." Downstream code that needs to transform the parsed structure copies it.
- **No path / no source identifier on the result.** The caller already knows what they asked for; cluttering the result with that information makes it harder to compare results across readers or sources.
- **No error variant.** `read()` returns `ReaderResult` on success and raises a typed exception on failure (see `Reader.read` below). Forcing every caller to handle a result-or-error union for the unhappy path adds noise; raising lets each caller decide how much to catch.

### Tests

| Test | Covers | Location |
|---|---|---|
| `GitHubReaderTest.test_happy_path_returns_raw_and_parsed` | `ReaderResult` shape via the GitHub reader | [tests.py:153](../tests.py#L153) |
| `LocalReaderTest.test_happy_path_returns_raw_and_parsed` | `ReaderResult` shape via the local reader | [tests.py:221](../tests.py#L221) |

The dataclass itself is not tested in isolation — its behaviour is observable only via the readers that produce it, so subclass tests cover it.

---

## `Reader`

### Signature

```python
class Reader:
    required_kwargs: tuple[str, ...] = ()
    def read(self, path: str) -> ReaderResult: ...
```

### Purpose

The abstract base every concrete reader inherits. A `Reader` is a **configured connection**: construction kwargs identify the source (a repo + ref + PAT for GitHub; a directory `root` for local), and `read(path)` is the per-request query against that source.

The split between construction kwargs and per-read `path` is the load-bearing decision. It means:

- A consumer constructs **one** reader per source and reuses it across many reads.
- The library can swap reader implementations at the dispatch layer without re-reading every consumer's settings.
- `path` always means "what to fetch within the source," never "what source to fetch from" — there's no ambiguity at the call site.

The base class itself is purely a contract. Direct instantiation is meaningless; `read()` raises `NotImplementedError`. Concrete subclasses do the actual I/O and convert backend-specific errors to the typed exception hierarchy.

### Algorithm

None at the base level — the contract is what subclasses must satisfy. Each concrete reader's `read()` does:

1. Build a backend-specific request from `path` and the construction kwargs.
2. Fetch the bytes, translating backend errors to the typed exceptions below.
3. `yaml.safe_load` the bytes (translating parse errors to `ReaderParseError`).
4. Return `ReaderResult(raw_bytes, parsed)`.

See [github.py](github.py) and [local.py](local.py) for the two implementations the library ships.

### Class attribute: `required_kwargs`

A tuple of construction-kwarg names the subclass accepts. Subclasses override the empty default with their own tuple, e.g.:

```python
class GitHubReader(Reader):
    required_kwargs = ("repo", "ref", "pat")
```

Two reasons this exists:

- **Discoverability.** Consumers (or tooling around the consumer's settings) can introspect `MyReader.required_kwargs` to know what settings need to be supplied, without parsing a docstring.
- **Future validation.** A dispatch layer could check that the consumer's `WORLDBUILDER_READER_KWARGS` covers the chosen reader's required set before construction, surfacing missing settings before any I/O.

`path` is deliberately **not** a construction kwarg — it's supplied per-read as the query, so listing it in `required_kwargs` would be a category error.

### Method: `read(path: str) -> ReaderResult`

The single operation every reader implements. Fetch one YAML document at `path`, return a `ReaderResult` on success, raise on failure.

### Errors

`read()` must raise one of the typed exceptions from [`evennia_world_builder.errors`](../errors.py) so callers can handle each class semantically:

| Exception | Meaning |
|---|---|
| `ReaderAuthError` | Authentication / authorisation failure (HTTP 401, filesystem permission denied, …). |
| `ReaderNotFoundError` | The path didn't resolve at the source (HTTP 404, file not found, path traversal outside `root`). |
| `ReaderNetworkError` | Generic network / OS-level read failure that isn't auth or not-found. |
| `ReaderParseError` | Bytes fetched OK but the parse step failed (invalid YAML, non-UTF-8, …). |
| `ReaderError` | Base class — used for anything reader-specific that doesn't fit the above. |

Concrete readers must NOT leak backend-specific exception types to callers; translation to the typed hierarchy is part of the contract.

### Design notes

- **No `close()` / context manager.** Readers don't hold resources between calls — every `read()` is independent. If a future reader needs connection pooling, that's the subclass's concern; the contract stays single-call-per-fetch.
- **No retry / caching.** Each is a strictly orthogonal concern that belongs in a wrapper or middleware, not the contract. A reader that retries internally couples policy with mechanism in a way that's hard to override.
- **`path` is `str`, not `Path`.** The contract is platform-neutral and works against backends where filesystem paths aren't meaningful (HTTP, S3 keys). A backend that wants `pathlib.Path` semantics converts at its own boundary.

### Tests

| Test | Covers | Location |
|---|---|---|
| `GitHubReaderTest.test_required_kwargs_declared` | `required_kwargs` override contract via the GitHub reader | [tests.py:147](../tests.py#L147) |
| `LocalReaderTest.test_required_kwargs_declared` | `required_kwargs` override contract via the local reader | [tests.py:218](../tests.py#L218) |
| All [GitHubReaderTest](../tests.py#L131) and [LocalReaderTest](../tests.py#L204) cases | The `read(path) -> ReaderResult` contract end-to-end (happy path + every typed-exception path) | tests.py:131, tests.py:204 |

The base class has no direct tests because it carries no behaviour. The contract is exercised only through subclasses, which is correct — testing `Reader.read()`'s `NotImplementedError` branch would be a tautology.
