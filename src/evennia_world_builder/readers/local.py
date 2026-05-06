# SPDX-License-Identifier: BSD-3-Clause
"""LocalReader — reads YAML files from a local directory tree.

Counterpart to GitHubReader for the case where the YAML repo is checked
out on disk: local development, CI validation, or any consumer that
prefers to fetch content from the filesystem rather than over HTTP.

Construction kwargs:
    root: filesystem path to the directory that contains
          ``definitions.yaml`` (i.e. the repo root, not a subfolder).

The path supplied per-read is interpreted relative to ``root``. Reads
that resolve outside ``root`` are rejected with ReaderNotFoundError —
path-traversal guard, not a feature.
"""
from pathlib import Path

import yaml

from ..errors import (
    ReaderAuthError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
)
from .base import Reader, ReaderResult


class LocalReader(Reader):
    """Reader for a local directory tree.

    Construction kwargs (all required, all keyword-only):
        root: directory containing ``definitions.yaml``.

    Raises (from read()):
        ReaderNotFoundError: file does not exist, or path resolves
                             outside ``root``.
        ReaderAuthError:     filesystem permission denied.
        ReaderNetworkError:  other OS-level read failure.
        ReaderParseError:    YAML or UTF-8 decode failure.
    """

    required_kwargs = ("root",)

    def __init__(self, *, root):
        self.root = Path(root).resolve()

    def read(self, path: str) -> ReaderResult:
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as e:
            raise ReaderNotFoundError(
                f"Path {path!r} resolves outside reader root {self.root}"
            ) from e

        try:
            raw_bytes = target.read_bytes()
        except FileNotFoundError as e:
            raise ReaderNotFoundError(
                f"Not found: {target} (root={self.root}, path={path!r})"
            ) from e
        except PermissionError as e:
            raise ReaderAuthError(f"Permission denied: {target}") from e
        except OSError as e:
            raise ReaderNetworkError(f"OS error reading {target}: {e}") from e

        try:
            parsed = yaml.safe_load(raw_bytes)
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            raise ReaderParseError(str(e)) from e

        return ReaderResult(raw_bytes=raw_bytes, parsed=parsed)
