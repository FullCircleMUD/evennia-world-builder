# Discovery and Loading

How evennia-world-builder finds the YAML files relevant to a `wb_build` command and reads them into memory for downstream processing. Two roles, tightly coupled by a shared manifest convention but cleanly separated by responsibility:

- **Finder** — walks the manifest hierarchy following an operator query, returning the entry-point location.
- **Loader** — from that entry point, recursively reads every leaf content file via the Reader. Returns a flat list of `LoadedEntity` records.

The Validator and Builder consume the Loader's output and are documented separately when designed.

## Pipeline context

```
operator: wb_build zone=millholm room=bakery
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

Depth-first traversal driven by indexes, then per-file flatten of any `contents:` recursion:

- If location is a **file**: read it via Reader, then **flatten**:
  - A top-level mapping is one entity; a top-level list of mappings is many.
  - For each top-level entity, walk its `contents:` block (if any) depth-first pre-order, emitting each nested mapping as its own `LoadedEntity` with `is_nested=True`. The parent's `contents:` key is popped from the emitted body so downstream consumers don't see duplicate child data.
  - Nested entities inherit the parent's `path` and `location` dict (a `contents:` block doesn't cross the file boundary).
- If location is a **folder**: read its `index.yaml`; for each entry, recurse with the child's `FoundLocation` (path + kind constructed via path inference; `location` dict extended with `{levels[depth]: entry.name}`); concatenate results.

Indexes are navigation only — never content. The Loader walks indexes; it never reads files not listed in an index.

### Location synthesis on nested entities

For each nested mapping, before emitting the LoadedEntity, the Loader:

1. Records `had_author_location = "location" in mapping` against the *original* YAML.
2. Synthesises `mapping["location"] = {deployment_file: <parent.path>, deployment_id: <parent.deployment_id>}`, overwriting whatever the author wrote.

This unifies the `location:` shape across top-level and nested entities (both are now either `null` or a cross-ref dict), letting the validator and Builder treat them uniformly. The validator separately refuses author-written `location:` on a nested entity via `had_author_location` so the overwrite is never silent. See [deployment-identity.md](deployment-identity.md#loader-synthesis) for the synthesis rationale and [validator.md](validator.md) for the predicate.

Defensive about malformed input: a non-list `contents:` value is silently ignored (skip recursion); a non-mapping child within a list is skipped. No errors raised at load time — the validator's existing field-shape predicates catch malformed entity bodies, and authoring tools surface YAML structure problems upstream.

### API

```python
@dataclass(frozen=True)
class LoadedEntity:
    location: dict[str, str]   # full hierarchical position (folder discovery; same for nested)
    content: dict              # parsed YAML body; for nested, `contents` popped + `location` synthesised
    path: str                  # source file path; same for top-level + nested in the same file
    is_nested: bool = False    # True iff loaded from inside a `contents:` block
    had_author_location: bool = False  # True iff the original YAML had a `location:` key

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
| `wb_build all` | root, kind=folder | inn, bakery, aethenveil |
| `wb_build zone=millholm` | `millholm`, kind=folder | inn, bakery |
| `wb_build zone=aethenveil` | `aethenveil.yaml`, kind=file | aethenveil |
| `wb_build zone=millholm room=bakery` | `millholm/bakery.yaml`, kind=file | bakery |
| `wb_build zone=nonexistent` | — | `FinderQueryError` |
| `wb_build zone=millholm room=nonexistent` | — | `FinderQueryError` |

## Out of scope (deferred)

- **Cross-cutting tags** beyond the hierarchy (e.g. `tier: starter`). Defer; levels alone cover the build-scope case.
- **Multi-value queries** (`zone=A OR zone=B`). Defer; single value per level is enough for v0.
- **Caching the manifest** across commands. On-demand re-read per command, matching the Reader pattern.
- **Tree-structured definitions** (per-subtree level overrides). Defer; one global level set is sufficient.
