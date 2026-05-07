# SPDX-License-Identifier: BSD-3-Clause
# Documentation: see base.md (co-located).
from dataclasses import dataclass


@dataclass(frozen=True)
class ReaderResult:
    raw_bytes: bytes
    parsed: object


class Reader:
    # `path` is per-read, NOT a construction kwarg.
    required_kwargs: tuple[str, ...] = ()

    def read(self, path: str) -> ReaderResult:
        raise NotImplementedError
