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
