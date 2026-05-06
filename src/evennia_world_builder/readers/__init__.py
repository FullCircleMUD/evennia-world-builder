# SPDX-License-Identifier: BSD-3-Clause
"""Reader implementations for world-builder.

The base `Reader` contract lives in ``base``. Concrete readers live
alongside it (one file per source platform, e.g. ``github.py``,
``local.py``).
"""

from .base import Reader, ReaderResult
from .github import GitHubReader
from .local import LocalReader

__all__ = ["Reader", "ReaderResult", "GitHubReader", "LocalReader"]
