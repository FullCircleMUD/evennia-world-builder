# SPDX-License-Identifier: BSD-3-Clause
"""Tests for world-builder.

Verifies the package is importable, the Reader contract is honoured by
GitHubReader against a mocked urllib, and settings-based dispatch
resolves the configured reader correctly.
"""
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

import world_builder
from world_builder import (
    GitHubReader,
    ReaderAuthError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
    ReaderResult,
    get_reader_class,
)


class FakeReader:
    """Used by GetReaderClassTest to verify dispatch via @override_settings.

    Defined at module scope so it is importable as
    ``world_builder.tests.FakeReader``.
    """


class SmokeTest(TestCase):
    """End-to-end install + runner sanity check."""

    def test_package_importable(self):
        self.assertTrue(hasattr(world_builder, "__version__"))

    def test_version_is_string(self):
        self.assertIsInstance(world_builder.__version__, str)


class GitHubReaderTest(TestCase):
    """Verify GitHubReader.read() against a mocked urllib."""

    KWARGS = {
        "repo": "owner/repo",
        "path": "file.yaml",
        "ref": "main",
        "pat": "ghp_test",
    }

    def _response_with_payload(self, payload: bytes) -> MagicMock:
        """Build a context-manager-protocol-supporting mock for urlopen()."""
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = payload
        return mock_response

    def test_required_kwargs_declared(self):
        self.assertEqual(
            GitHubReader.required_kwargs, ("repo", "path", "ref", "pat")
        )

    @patch("world_builder.readers.github.urllib.request.urlopen")
    def test_happy_path_returns_raw_and_parsed(self, mock_urlopen):
        mock_urlopen.return_value = self._response_with_payload(b"key: value\n")
        result = GitHubReader(**self.KWARGS).read()
        self.assertIsInstance(result, ReaderResult)
        self.assertEqual(result.raw_bytes, b"key: value\n")
        self.assertEqual(result.parsed, {"key": "value"})

    @patch("world_builder.readers.github.urllib.request.urlopen")
    def test_request_url_and_headers(self, mock_urlopen):
        mock_urlopen.return_value = self._response_with_payload(b"x: 1\n")
        GitHubReader(**self.KWARGS).read()
        request = mock_urlopen.call_args[0][0]
        self.assertIn("/repos/owner/repo/contents/file.yaml", request.full_url)
        self.assertIn("ref=main", request.full_url)
        # urllib.request.Request normalises header keys via .capitalize();
        # compare via a lowercased view to stay robust against that.
        headers_lower = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers_lower["authorization"], "Bearer ghp_test")
        self.assertEqual(headers_lower["accept"], "application/vnd.github.raw")
        self.assertEqual(headers_lower["x-github-api-version"], "2022-11-28")
        self.assertEqual(headers_lower["user-agent"], "world-builder")

    @patch("world_builder.readers.github.urllib.request.urlopen")
    def test_401_raises_auth_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None
        )
        with self.assertRaises(ReaderAuthError):
            GitHubReader(**self.KWARGS).read()

    @patch("world_builder.readers.github.urllib.request.urlopen")
    def test_404_raises_not_found_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        with self.assertRaises(ReaderNotFoundError):
            GitHubReader(**self.KWARGS).read()

    @patch("world_builder.readers.github.urllib.request.urlopen")
    def test_url_error_raises_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("nodename nor servname")
        with self.assertRaises(ReaderNetworkError):
            GitHubReader(**self.KWARGS).read()

    @patch("world_builder.readers.github.urllib.request.urlopen")
    def test_bad_yaml_raises_parse_error(self, mock_urlopen):
        mock_urlopen.return_value = self._response_with_payload(b":::not yaml\n: : :")
        with self.assertRaises(ReaderParseError):
            GitHubReader(**self.KWARGS).read()


class GetReaderClassTest(TestCase):
    """Verify settings-based dispatch via WORLDBUILDER_READER."""

    def test_default_returns_github_reader(self):
        self.assertIs(get_reader_class(), GitHubReader)

    @override_settings(WORLDBUILDER_READER="world_builder.tests.FakeReader")
    def test_override_via_settings(self):
        self.assertIs(get_reader_class(), FakeReader)

    @override_settings(WORLDBUILDER_READER="world_builder.does_not_exist.Nope")
    def test_bad_dotted_path_raises(self):
        with self.assertRaises((ImportError, AttributeError)):
            get_reader_class()
