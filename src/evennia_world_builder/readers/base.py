# SPDX-License-Identifier: BSD-3-Clause
"""Base contract for world-builder source readers.

A Reader fetches a single YAML document from a backing source and
returns a ReaderResult. Concrete subclasses live alongside this module
(see ``github.py`` for the first implementation) and determine the
source-specific kwargs they accept at construction. The library does
not dictate setting names per reader — that is a consumer concern.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReaderResult:
    """Outcome of a successful Reader.read() call.

    Attributes:
        raw_bytes: bytes exactly as fetched from source, pre-decode.
        parsed:    yaml.safe_load output (dict, list, scalar, or None).
    """

    raw_bytes: bytes
    parsed: object


class Reader:
    """Base contract for world-builder source readers.

    A Reader is a configured connection to a content source (a GitHub
    repo, an S3 bucket, a filesystem root). Construction kwargs are
    reader-specific and supplied by the consumer; `path` is supplied
    per-read as the query against that source.

    Concrete subclasses implement read(path) to fetch a single YAML
    document at the given path within the source and return a
    ReaderResult.

    On failure, read() must raise one of the typed errors from
    evennia_world_builder.errors so callers can handle each class
    semantically.
    """

    # Names of construction kwargs accepted by __init__. Subclasses override.
    # Consumers may introspect this to discover what kwargs a reader needs
    # without reading docstrings. Note: `path` is NOT a construction kwarg —
    # it is supplied per-read.
    required_kwargs: tuple[str, ...] = ()

    def read(self, path: str) -> ReaderResult:
        raise NotImplementedError
