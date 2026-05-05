# SPDX-License-Identifier: BSD-3-Clause
"""GitHubReader — fetches a YAML file from a (private or public) GitHub repo.

Uses the GitHub Contents API with ``Accept: application/vnd.github.raw``
to get the raw file body in one hop. Authenticates via Bearer token
(works for both classic and fine-grained PATs).
"""
import urllib.error
import urllib.request

import yaml

from ..errors import (
    ReaderAuthError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
)
from .base import Reader, ReaderResult


_USER_AGENT = "world-builder"
_GITHUB_API_VERSION = "2022-11-28"
_REQUEST_TIMEOUT_SECONDS = 10


class GitHubReader(Reader):
    """Reader for the GitHub Contents API.

    Construction kwargs (all required, all keyword-only):
        repo: owner/name slug (e.g. ``"FullCircleMUD/world-builder-test-yaml"``).
        path: path within the repo (e.g. ``"hello.yaml"``).
        ref:  branch, tag, or commit SHA.
        pat:  GitHub Personal Access Token with read access to the repo.

    Raises (from read()):
        ReaderAuthError:     HTTP 401 — PAT rejected.
        ReaderNotFoundError: HTTP 404 — repo, ref, or path not found.
        ReaderNetworkError:  HTTP non-401/404 or network failure.
        ReaderParseError:    YAML or UTF-8 decode failure.
    """

    required_kwargs = ("repo", "path", "ref", "pat")

    def __init__(self, *, repo, path, ref, pat):
        self.repo = repo
        self.path = path
        self.ref = ref
        self.pat = pat

    def read(self) -> ReaderResult:
        url = (
            f"https://api.github.com/repos/{self.repo}"
            f"/contents/{self.path}?ref={self.ref}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.pat}",
                "Accept": "application/vnd.github.raw",
                "X-GitHub-Api-Version": _GITHUB_API_VERSION,
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=_REQUEST_TIMEOUT_SECONDS
            ) as response:
                raw_bytes = response.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ReaderAuthError(
                    f"GitHub auth failed (401) for {self.repo}@{self.ref}:{self.path}"
                ) from e
            if e.code == 404:
                raise ReaderNotFoundError(
                    f"Not found (404) for {self.repo}@{self.ref}:{self.path}"
                ) from e
            raise ReaderNetworkError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ReaderNetworkError(f"Network error: {e.reason}") from e

        try:
            parsed = yaml.safe_load(raw_bytes)
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            raise ReaderParseError(str(e)) from e

        return ReaderResult(raw_bytes=raw_bytes, parsed=parsed)
