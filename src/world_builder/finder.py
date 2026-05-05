# SPDX-License-Identifier: BSD-3-Clause
"""Finder — walks the manifest hierarchy following an operator query.

Returns a FoundLocation that the Loader uses as its entry point.
See DESIGN/discovery-and-loading.md for the design.
"""
from dataclasses import dataclass

from .definitions import Definitions
from .errors import FinderManifestError, FinderQueryError, ReaderNotFoundError
from .readers.base import Reader


_INDEX_FILENAME = "index.yaml"
_KIND_FOLDER = "folder"
_KIND_FILE = "file"
_VALID_KINDS = (_KIND_FOLDER, _KIND_FILE)


@dataclass(frozen=True)
class FoundLocation:
    """Entry point for the Loader — the location matching an operator query.

    Attributes:
        path:     Path within the source. "" at root; "foo" for a folder; "foo.yaml" for a file.
        kind:     "folder" or "file".
        location: Accumulated {level_name: value} pairs at this point.
    """

    path: str
    kind: str
    location: dict


class Finder:
    """Walks the manifest tree following an operator query.

    Construction:
        reader:      a configured Reader (for fetching index.yaml files).
        definitions: parsed Definitions (provides the levels vocabulary).

    Usage:
        finder = Finder(reader, definitions)
        found = finder.find({"zone": "millholm"})
    """

    def __init__(self, reader: Reader, definitions: Definitions):
        self.reader = reader
        self.definitions = definitions

    def find(self, query: dict | None = None) -> FoundLocation:
        """Walk the manifest to find the entry point for ``query``.

        ``query`` is a dict of {level_name: value}. Keys must be a contiguous
        prefix of self.definitions.levels (skipping a level is an error).
        An empty dict (or None) returns the root location.
        """
        query = dict(query or {})
        levels = self.definitions.levels

        # Validate query keys are declared
        for key in query:
            if key not in levels:
                raise FinderQueryError(
                    f"Query key {key!r} is not in declared levels {list(levels)}"
                )

        # Validate query forms a contiguous prefix (no skipping)
        seen_missing = False
        for level in levels:
            if level not in query:
                seen_missing = True
            elif seen_missing:
                raise FinderQueryError(
                    f"Query must form a contiguous prefix of levels {list(levels)}; "
                    f"got {query} (cannot skip an intermediate level)"
                )

        # Walk
        location: dict = {}
        path = ""
        for level in levels:
            if level not in query:
                # Query exhausted — return current location as a folder
                return FoundLocation(path=path, kind=_KIND_FOLDER, location=location)

            target_name = query[level]
            index_path = f"{path}/{_INDEX_FILENAME}" if path else _INDEX_FILENAME
            entries = self._read_index_entries(index_path)

            entry = next((e for e in entries if e.get("name") == target_name), None)
            if entry is None:
                raise FinderQueryError(
                    f"{level}={target_name!r} not found at {index_path!r}"
                )

            kind = entry.get("kind")
            if kind not in _VALID_KINDS:
                raise FinderManifestError(
                    f"{index_path}: entry {target_name!r} has invalid kind {kind!r}"
                )

            location = {**location, level: target_name}
            if kind == _KIND_FILE:
                file_path = f"{path}/{target_name}.yaml" if path else f"{target_name}.yaml"
                return FoundLocation(path=file_path, kind=_KIND_FILE, location=location)

            # kind == folder: descend
            path = f"{path}/{target_name}" if path else target_name

        # All levels matched and last entry was a folder
        return FoundLocation(path=path, kind=_KIND_FOLDER, location=location)

    def _read_index_entries(self, path: str) -> list:
        try:
            result = self.reader.read(path)
        except ReaderNotFoundError as e:
            raise FinderManifestError(f"Index not found at {path!r}") from e

        data = result.parsed
        if not isinstance(data, dict) or "entries" not in data:
            raise FinderManifestError(f"{path}: missing 'entries' field")
        entries = data["entries"]
        if not isinstance(entries, list):
            raise FinderManifestError(f"{path}: 'entries' must be a list")
        return entries
