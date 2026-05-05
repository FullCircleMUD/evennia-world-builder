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

    Concrete subclasses implement read() to fetch a single YAML
    document from their backing source and return a ReaderResult.
    Construction kwargs are reader-specific and supplied by the
    consumer.

    On failure, read() must raise one of the typed errors from
    world_builder.errors so callers can handle each class
    semantically.
    """

    # Names of keyword arguments accepted by __init__. Subclasses override.
    # Consumers may introspect this to discover what kwargs a reader needs
    # without reading docstrings.
    required_kwargs: tuple[str, ...] = ()

    def read(self) -> ReaderResult:
        raise NotImplementedError
