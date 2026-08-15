# SPDX-License-Identifier: BSD-3-Clause
"""evennia-world-builder — declarative YAML-driven world authoring for Evennia."""

from evennia_yaml_reader import (
    GitHubReader,
    LocalReader,
    Reader,
    ReaderAuthError,
    ReaderError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
    ReaderResult,
)

from .api import wb_lookup_dbref, wb_lookup_object
from .builder import Builder
from .config import get_configured_reader, get_reader_class
from .definitions import Definitions
from .errors import (
    ApiError,
    BuilderError,
    DefinitionsError,
    FinderError,
    FinderManifestError,
    FinderQueryError,
    LoaderError,
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ValidatorError,
)
from .finder import Finder, FoundLocation
from .loader import LoadedEntity, Loader, LoadResult
from .validator import Validator

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Reader",
    "ReaderResult",
    "GitHubReader",
    "LocalReader",
    "ReaderError",
    "ReaderAuthError",
    "ReaderNotFoundError",
    "ReaderNetworkError",
    "ReaderParseError",
    "get_reader_class",
    "get_configured_reader",
    "Definitions",
    "DefinitionsError",
    "Finder",
    "FoundLocation",
    "FinderError",
    "FinderManifestError",
    "FinderQueryError",
    "Loader",
    "LoadedEntity",
    "LoadResult",
    "LoaderError",
    "LoaderInvalidShapeError",
    "LoaderMissingIndexError",
    "LoaderMissingEntryError",
    "Validator",
    "ValidatorError",
    "Builder",
    "BuilderError",
    "wb_lookup_dbref",
    "wb_lookup_object",
    "ApiError",
]
