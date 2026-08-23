# SPDX-License-Identifier: BSD-3-Clause
"""Exception types raised by world-builder.

The Reader-related exceptions (``ReaderError`` and its four subtypes) live in
the ``evennia-yaml-reader`` library — world-builder re-exports them from its
top-level ``__init__`` for consumer convenience but they are imported there,
not declared here.
"""


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
    carrying the same ``entity_id``. A
    successful no-match returns ``None`` rather than raising.
    """
