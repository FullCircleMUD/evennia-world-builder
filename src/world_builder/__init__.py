# SPDX-License-Identifier: BSD-3-Clause
"""world-builder — declarative YAML-driven world authoring for Evennia."""

from .config import get_reader_class
from .definitions import Definitions
from .errors import (
    DefinitionsError,
    FinderError,
    FinderManifestError,
    FinderQueryError,
    LoaderError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ReaderAuthError,
    ReaderError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
)
from .finder import Finder, FoundLocation
from .loader import LoadedEntity, Loader
from .readers import GitHubReader, Reader, ReaderResult

__version__ = "0.0.1"

__all__ = [
    "__version__",
    "Reader",
    "ReaderResult",
    "GitHubReader",
    "ReaderError",
    "ReaderAuthError",
    "ReaderNotFoundError",
    "ReaderNetworkError",
    "ReaderParseError",
    "get_reader_class",
    "Definitions",
    "DefinitionsError",
    "Finder",
    "FoundLocation",
    "FinderError",
    "FinderManifestError",
    "FinderQueryError",
    "Loader",
    "LoadedEntity",
    "LoaderError",
    "LoaderMissingIndexError",
    "LoaderMissingEntryError",
]
