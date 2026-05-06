# Discovery and Loading

How evennia-world-builder finds the YAML files relevant to a build command and reads them into memory for downstream processing. Two roles, tightly coupled by a shared manifest convention but cleanly separated by responsibility:

- **Finder** — walks the manifest hierarchy following an operator query, returning the entry-point location.
- **Loader** — from that entry point, recursively reads every leaf content file via the Reader. Returns a flat list of `LoadedEntity` records.

The Validator and Builder consume the Loader's output and are documented separately when designed.

## Pipeline context

```
operator: build zone=millholm room=bakery
            │
            ▼
        ┌────────┐    FoundLocation
query → │ Finder │ ─────────► (path, kind, location_so_far)
        └────────┘
            │
            ▼
        ┌────────┐    list[LoadedEntity]
        │ Loader │ ─────────► [(location, content, path), ...]
        └────────┘
            │
            ▼  validator + builder (out of scope for this doc)
```

Both Finder and Loader use the consumer-supplied Reader for file I/O. They are library-fixed (not pluggable like Reader is) — they encode the manifest convention itself.

## Manifest convention

Strict, with no flexibility:

- **Exactly one `definitions.yaml`** at the root of the content tree. Declares the hierarchy.
- **Exactly one `index.yaml`** per folder. Lists the children of that level.
- **Path inference**: `kind: folder` named `foo` ⇒ folder at `foo/` with `foo/index.yaml` inside. `kind: file` named `foo` ⇒ file at `foo.yaml`.

Orphan YAML files (files not listed in any index) are silently ignored. **The indexes are the source of truth for what is and isn't part of the world.**

### definitions.yaml

```yaml
levels: [zone, room]    # consumer-defined, ordered, hierarchical
```

`levels` is currently the only key. Other declarations may land here as the system grows.

### index.yaml

```yaml
entries:
  - name: millholm
    kind: folder
  - name: aethenveil
    kind: file
```

Per entry:
- **`name`** — consumer-chosen identifier at this level. Becomes the value of `levels[depth]` in a `LoadedEntity`'s `location` dict.
- **`kind`** — `folder` or `file`. Per-entry: a level can mix kinds (one zone might be a folder; another might be a single file).

## Finder

### Algorithm

1. Read `definitions.yaml` once to get `levels`.
2. Validate query: keys must form a contiguous prefix of `levels`. Skipping levels is an error; trailing levels may be omitted.
3. Walk: starting at the root, for each level present in the query (in declared order), read the current index, find the entry whose `name` matches the query value, and descend (or stop if `kind == file`).
4. Return `FoundLocation(path, kind, accumulated_location)`.

### API

```python
@dataclass(frozen=True)
class FoundLocation:
    path: str                  # "" at root; "foo" for a folder; "foo.yaml" for a file
    kind: str                  # "folder" or "file"
    location: dict[str, str]   # accumulated level_name=value pairs at this point

class Finder:
    def __init__(self, reader: Reader, definitions: Definitions): ...
    def find(self, query: dict[str, str] | None = None) -> FoundLocation: ...
```

### Errors

- `FinderError(Exception)` — base
- `FinderManifestError(FinderError)` — `definitions.yaml` or an `index.yaml` malformed or missing
- `FinderQueryError(FinderError)` — query key not in `levels`, query not a contiguous prefix, or value not found at level

## Loader

### Algorithm

Depth-first traversal driven by indexes:

- If location is a **file**: read it via Reader; return one-element list.
- If location is a **folder**: read its `index.yaml`; for each entry, recurse with the child's `FoundLocation` (path + kind constructed via path inference; `location` dict extended with `{levels[depth]: entry.name}`); concatenate results.

Indexes are navigation only — never content. The Loader walks indexes; it never reads files not listed in an index.

### API

```python
@dataclass(frozen=True)
class LoadedEntity:
    location: dict[str, str]   # full hierarchical position
    content: dict              # parsed YAML body (yaml.safe_load output)
    path: str                  # source file path, for diagnostic messages

class Loader:
    def __init__(self, reader: Reader, definitions: Definitions): ...
    def load(self, found: FoundLocation) -> list[LoadedEntity]: ...
```

### Errors

- `LoaderError(Exception)` — base
- `LoaderMissingIndexError(LoaderError)` — folder lacks `index.yaml`
- `LoaderMissingEntryError(LoaderError)` — index points at a file or folder that doesn't exist
- Reader's `ReaderError` subtypes propagate unchanged for HTTP / parse failures

## Definitions

Parsed contents of `definitions.yaml` — read once per command invocation, then handed to Finder, Loader, and any downstream component that needs the level vocabulary.

```python
@dataclass(frozen=True)
class Definitions:
    levels: tuple = ()           # consumer-declared level names; empty tuple = flat world

    @classmethod
    def from_reader(cls, reader: Reader, path: str = "definitions.yaml") -> "Definitions": ...

    @classmethod
    def from_dict(cls, data, *, source_path: str = "<dict>") -> "Definitions": ...
```

`from_dict()` is the primary parsing path; `from_reader()` is sugar that fetches via the Reader then dispatches. As more declarations land in `definitions.yaml` over time, they become typed fields here.

Errors:

- `DefinitionsError(Exception)` — `definitions.yaml` malformed (not a mapping; `levels` not a list; entries not strings).

## Construction (consumer-side)

Mirrors the Reader pattern — the consumer constructs everything explicitly. `Definitions` is read once, up-front, before parsing the operator's command (so the level vocabulary is available to validate the operator's args):

```python
reader = get_reader_class()(**reader_kwargs)
defs = Definitions.from_reader(reader)        # one read, once, up-front

# Now defs.levels is available to validate the operator's parsed args.
finder = Finder(reader, defs)
loader = Loader(reader, defs)

found = finder.find({"zone": "millholm", "room": "bakery"})
entities = loader.load(found)
```

## Index ordering = execution ordering

The Loader walks each index in declared order, recursing depth-first into folders. **Consumers control creation-order dependencies by ordering entries in their indexes.** No separate dependency-graph mechanism is needed in v0; if room A must be created before room B, list A above B in the relevant index.

## Examples (against the test repo)

The test scaffold at `evennia-world-builder-test-yaml/` has:

```
definitions.yaml         # levels: [zone, room]
index.yaml               # millholm (folder), aethenveil (file)
millholm/
  index.yaml             # inn (file), bakery (file)
  inn.yaml
  bakery.yaml
aethenveil.yaml
```

| Command | Finder | Loader (index order) |
|---|---|---|
| `build` | root, kind=folder | inn, bakery, aethenveil |
| `build zone=millholm` | `millholm`, kind=folder | inn, bakery |
| `build zone=aethenveil` | `aethenveil.yaml`, kind=file | aethenveil |
| `build zone=millholm room=bakery` | `millholm/bakery.yaml`, kind=file | bakery |
| `build zone=nonexistent` | — | `FinderQueryError` |
| `build zone=millholm room=nonexistent` | — | `FinderQueryError` |

## Out of scope (deferred)

- **Cross-cutting tags** beyond the hierarchy (e.g. `tier: starter`). Defer; levels alone cover the build-scope case.
- **Multi-value queries** (`zone=A OR zone=B`). Defer; single value per level is enough for v0.
- **Caching the manifest** across commands. On-demand re-read per command, matching the Reader pattern.
- **Tree-structured definitions** (per-subtree level overrides). Defer; one global level set is sufficient.
