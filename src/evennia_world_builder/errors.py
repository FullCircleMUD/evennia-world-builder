# SPDX-License-Identifier: BSD-3-Clause
"""Exception types raised by world-builder readers."""


class ReaderError(Exception):
    """Base class for world-builder reader failures."""


class ReaderAuthError(ReaderError):
    """Reader rejected by source authentication (e.g. HTTP 401)."""


class ReaderNotFoundError(ReaderError):
    """Reader target does not exist at source (e.g. HTTP 404)."""


class ReaderNetworkError(ReaderError):
    """Reader could not reach source (DNS, timeout, refused, other HTTP)."""


class ReaderParseError(ReaderError):
    """Source content fetched but could not be parsed (YAML or UTF-8 decode failure)."""


class DefinitionsError(Exception):
    """Raised when definitions.yaml is malformed or violates the schema."""


class FinderError(Exception):
    """Base class for Finder failures."""


class FinderManifestError(FinderError):
    """An index.yaml is malformed or missing where the Finder expected it."""


class FinderQueryError(FinderError):
    """Operator query is invalid: key not in levels, levels skipped, or value not found at level."""


class LoaderError(Exception):
    """Base class for Loader failures."""


class LoaderMissingIndexError(LoaderError):
    """A folder lacks the required index.yaml."""


class LoaderMissingEntryError(LoaderError):
    """An index references a file or folder that does not exist at the source."""


class LoaderInvalidShapeError(LoaderError):
    """A leaf YAML file is not in the required shape — see DESIGN docs.

    The library standardises on a single file shape: a top-level mapping
    with an ``entities:`` key whose value is a list of entity mappings.
    Any other top-level shape (a bare list, a single-entity mapping
    without `entities:`, a non-mapping value) raises this error.
    """


class ValidatorError(Exception):
    """Base class for Validator failures.

    v0 has no concrete subtypes — Validator is a no-op placeholder until
    specific validation needs emerge from Builder work.
    """


class BuilderError(Exception):
    """Base class for Builder failures."""


class ApiError(Exception):
    """Raised by ``api.py`` runtime-lookup helpers on integrity failures.

    Used only for conditions that should be impossible if the Builder's
    cleanup-on-rebuild invariant has held — e.g. multiple objects
    sharing the same ``(deployment_file, deployment_id)`` pair. A
    successful no-match returns ``None`` rather than raising.
    """
