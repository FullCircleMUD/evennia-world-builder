# SPDX-License-Identifier: BSD-3-Clause
"""Loader — recursively reads all leaf content under a FoundLocation.

Returns a flat list of LoadedEntity records. See
DESIGN/discovery-and-loading.md for the design.

Index ordering is execution ordering: the Loader walks each index in
declared order, recursing into folders depth-first. Consumers control
ordering of operations by ordering entries in their indexes.
"""
from dataclasses import dataclass, field

from .definitions import Definitions
from .errors import (
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ReaderNotFoundError,
)
from .finder import FoundLocation
from .readers.base import Reader


_INDEX_FILENAME = "index.yaml"
_KIND_FOLDER = "folder"
_KIND_FILE = "file"
_VALID_KINDS = (_KIND_FOLDER, _KIND_FILE)


@dataclass(frozen=True)
class LoadResult:
    """Output of ``Loader.load()``.

    Carries both the flat list of ``LoadedEntity`` records and a
    per-file metadata dict for any file-level keys found in the
    loaded files (currently ``incoming_exits:``; the structure is
    open for future file-level keys).

    Attributes:
        entities: Flat depth-first pre-order list of ``LoadedEntity``
                  records, exactly as before — the entity-level data
                  consumed by Validator and Builder.
        file_metadata: ``{file_path: {key: value, ...}}``. A file
                  appears in this dict only if it declared at least one
                  file-level key besides ``entities:``. The library
                  doesn't curate the keys; consumers (Validator,
                  Builder) look up the keys they care about.
    """

    entities: list
    file_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedEntity:
    """A single leaf content file's parsed contents and hierarchical position.

    Attributes:
        location:  Full hierarchical position ({level_name: value} dict).
        content:   Parsed YAML body (yaml.safe_load output). For an entity
                   loaded from a `contents:` block, the parent's `contents`
                   key has already been popped by the Loader so the body
                   doesn't carry duplicate child data; for nested entities
                   the Loader has also overwritten ``content["location"]``
                   with a cross-ref dict pointing at the parent (see
                   ``had_author_location`` for whether the author had
                   written a ``location:`` of their own).
        path:      Source file path, for diagnostic messages.
        is_nested: True iff this entity was authored inside another
                   entity's `contents:` block. Top-level entities (the
                   roots of a YAML file) are False. Nested entities share
                   their parent's ``path`` and ``location`` — the file is
                   the atomic deployment unit, and a `contents:` block
                   doesn't change that.
        had_author_location:
                   True iff the author wrote a ``location:`` key on this
                   entity's YAML mapping (whether ``null``, a cross-ref
                   dict, or anything else). Recorded *before* the Loader
                   synthesises ``location:`` on nested entities so the
                   validator can later refuse author-written ``location:``
                   on a nested entity (where placement is implicit in the
                   YAML structure). On top-level entities the flag just
                   reflects what the YAML had; the validator's existing
                   ``_check_location_well_formed`` already enforces that
                   top-level entities declare ``location:``.
    """

    location: dict
    content: object
    path: str
    is_nested: bool = False
    had_author_location: bool = False


class Loader:
    """Recursively reads all leaf content under a FoundLocation.

    Construction:
        reader:      configured Reader.
        definitions: parsed Definitions (provides level vocabulary).
    """

    def __init__(self, reader: Reader, definitions: Definitions):
        self.reader = reader
        self.definitions = definitions
        # Per-load accumulator, reset at the start of every load() call.
        # Populated by _flatten_top_level whenever a leaf file declares
        # any file-level keys besides ``entities:``.
        self._file_metadata: dict = {}

    def load(self, found: FoundLocation) -> LoadResult:
        """From the given entry point, return loaded entities + file metadata."""
        self._file_metadata = {}
        entities = self._load(found)
        return LoadResult(
            entities=entities,
            file_metadata=dict(self._file_metadata),
        )

    def _load(self, found: FoundLocation) -> list:
        if found.kind == _KIND_FILE:
            try:
                result = self.reader.read(found.path)
            except ReaderNotFoundError as e:
                raise LoaderMissingEntryError(
                    f"Index pointed at file {found.path!r} but it was not found at source"
                ) from e
            # A leaf YAML file is either one mapping (one entity) or a list
            # of mappings (many entities). Either way each top-level mapping
            # may carry a `contents:` block that nests further entities;
            # _flatten turns the whole tree into a flat depth-first
            # pre-order list so the parent always precedes its children.
            return self._flatten_top_level(
                parsed=result.parsed,
                path=found.path,
                location=dict(found.location),
            )

        # folder — read its index, recurse over entries in order
        index_path = f"{found.path}/{_INDEX_FILENAME}" if found.path else _INDEX_FILENAME
        try:
            index_result = self.reader.read(index_path)
        except ReaderNotFoundError as e:
            raise LoaderMissingIndexError(
                f"Folder {found.path!r} has no index.yaml"
            ) from e

        entries = self._validate_entries(index_result.parsed, index_path)
        depth = len(found.location)
        levels = self.definitions.levels

        result = []
        for entry in entries:
            name = entry["name"]
            kind = entry["kind"]
            child_location = dict(found.location)
            if depth < len(levels):
                child_location[levels[depth]] = name

            if kind == _KIND_FILE:
                child_path = f"{found.path}/{name}.yaml" if found.path else f"{name}.yaml"
            else:
                child_path = f"{found.path}/{name}" if found.path else name

            child_found = FoundLocation(
                path=child_path,
                kind=kind,
                location=child_location,
            )
            result.extend(self._load(child_found))

        return result

    def _flatten_top_level(self, parsed, path: str, location: dict) -> list:
        """Turn a leaf file's parsed YAML into a flat list of LoadedEntities.

        The library standardises on a single file shape: a top-level
        mapping with an ``entities:`` key whose value is a list of
        entity mappings. This method enforces that shape at load time.

        ```yaml
        entities:
          - { ... entity 1 ... }
          - { ... entity 2 ... }
        # (file-level keys like `incoming_exits:` will live alongside
        # `entities:` once they're shipped.)
        ```

        Within each entity in the list, a ``contents:`` and/or ``exits:``
        block may nest further entities; ``_flatten`` walks each subtree
        depth-first pre-order so the parent always precedes its children
        in the returned list.

        Anything else at the top level — a bare list, a mapping without
        ``entities:``, a non-mapping value — is rejected with
        ``LoaderInvalidShapeError`` so authors get a clear refusal at
        load time.
        """
        if not isinstance(parsed, dict) or "entities" not in parsed:
            raise LoaderInvalidShapeError(
                f"{path!r}: file must be a top-level mapping with an "
                f"'entities:' key whose value is a list of entity mappings"
            )

        entity_list = parsed["entities"]
        if not isinstance(entity_list, list):
            raise LoaderInvalidShapeError(
                f"{path!r}: 'entities:' must be a list, "
                f"got {type(entity_list).__name__}"
            )

        # File-level keys: everything in the top-level mapping except
        # `entities:`. Library doesn't curate the keys here; consumers
        # (Validator, Builder) look up the ones they care about. A file
        # only appears in self._file_metadata if it declared at least
        # one such key — clean by-default.
        file_meta = {k: v for k, v in parsed.items() if k != "entities"}
        if file_meta:
            self._file_metadata[path] = file_meta

        results = []
        for item in entity_list:
            results.extend(
                self._flatten(item, path, location, is_nested=False, parent_deployment_id=None),
            )
        return results

    def _flatten(
        self, mapping, path: str, location: dict, is_nested: bool,
        parent_deployment_id,
    ) -> list:
        """Walk one entity and its ``contents:`` / ``exits:`` subtree depth-first pre-order.

        Pops both child-block keys (``contents`` and ``exits``) from a
        shallow copy of the mapping so the emitted LoadedEntity's
        ``content`` doesn't carry duplicate child data, then recurses
        into each child mapping with ``is_nested=True`` and the same
        ``path`` / ``location`` (a child block doesn't cross the file
        boundary). Both blocks are flattened identically — the
        ``exits:`` vs ``contents:`` distinction is author-organizational
        and carries no semantic weight downstream; what makes an entity
        an exit is its typeclass + ``destination:`` field, not the YAML
        block name.

        For nested entities the Loader records ``had_author_location``
        from the *original* mapping before any modification, then
        unconditionally **synthesises** ``content["location"]`` as
        ``{deployment_file: path, deployment_id: parent_deployment_id}``.
        The synthesised dict overwrites whatever the author wrote (the
        validator refuses the author's value via the recorded flag).
        Top-level entities are not synthesised — their ``location:`` is
        the author's responsibility, and ``_check_location_well_formed``
        enforces it.

        Order: when both blocks are present, ``contents:`` children flatten
        first, then ``exits:`` children. Order within each block is
        preserved. The Builder's two-pass model orders the *creation* of
        exit objects after non-exit objects regardless, so flatten order
        between the two blocks isn't load-bearing.

        Defensive about malformed input — non-dict mappings emerge as a
        single LoadedEntity carrying the raw value (validator's
        field-shape predicates refuse them); a non-list ``contents`` /
        ``exits`` value is silently ignored here; a non-mapping child
        within either block is skipped.
        """
        if not isinstance(mapping, dict):
            return [LoadedEntity(
                location=dict(location),
                content=mapping,
                path=path,
                is_nested=is_nested,
                had_author_location=False,
            )]

        had_author_location = "location" in mapping

        body = dict(mapping)
        contents_children = body.pop("contents", None)
        exits_children = body.pop("exits", None)

        if is_nested:
            body["location"] = {
                "deployment_file": path,
                "deployment_id": parent_deployment_id,
            }

        results = [LoadedEntity(
            location=dict(location),
            content=body,
            path=path,
            is_nested=is_nested,
            had_author_location=had_author_location,
        )]

        my_deployment_id = mapping.get("deployment_id")
        for children in (contents_children, exits_children):
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, dict):
                    continue
                results.extend(self._flatten(
                    child, path, location,
                    is_nested=True,
                    parent_deployment_id=my_deployment_id,
                ))

        return results

    def _validate_entries(self, parsed, path: str) -> list:
        if not isinstance(parsed, dict) or "entries" not in parsed:
            raise LoaderMissingIndexError(f"{path}: missing 'entries' field")
        entries = parsed["entries"]
        if not isinstance(entries, list):
            raise LoaderMissingIndexError(f"{path}: 'entries' must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or "name" not in entry or "kind" not in entry:
                raise LoaderMissingIndexError(f"{path}: malformed entry {entry!r}")
            if entry["kind"] not in _VALID_KINDS:
                raise LoaderMissingIndexError(
                    f"{path}: entry {entry['name']!r} has invalid kind {entry['kind']!r}"
                )
        return entries
