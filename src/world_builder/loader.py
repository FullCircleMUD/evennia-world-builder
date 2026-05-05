# SPDX-License-Identifier: BSD-3-Clause
"""Loader — recursively reads all leaf content under a FoundLocation.

Returns a flat list of LoadedEntity records. See
DESIGN/discovery-and-loading.md for the design.

Index ordering is execution ordering: the Loader walks each index in
declared order, recursing into folders depth-first. Consumers control
ordering of operations by ordering entries in their indexes.
"""
from dataclasses import dataclass

from .definitions import Definitions
from .errors import (
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
class LoadedEntity:
    """A single leaf content file's parsed contents and hierarchical position.

    Attributes:
        location: Full hierarchical position ({level_name: value} dict).
        content:  Parsed YAML body (yaml.safe_load output).
        path:     Source file path, for diagnostic messages.
    """

    location: dict
    content: object
    path: str


class Loader:
    """Recursively reads all leaf content under a FoundLocation.

    Construction:
        reader:      configured Reader.
        definitions: parsed Definitions (provides level vocabulary).
    """

    def __init__(self, reader: Reader, definitions: Definitions):
        self.reader = reader
        self.definitions = definitions

    def load(self, found: FoundLocation) -> list:
        """From the given entry point, return all leaf entities below it."""
        return self._load(found)

    def _load(self, found: FoundLocation) -> list:
        if found.kind == _KIND_FILE:
            try:
                result = self.reader.read(found.path)
            except ReaderNotFoundError as e:
                raise LoaderMissingEntryError(
                    f"Index pointed at file {found.path!r} but it was not found at source"
                ) from e
            return [LoadedEntity(
                location=dict(found.location),
                content=result.parsed,
                path=found.path,
            )]

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
