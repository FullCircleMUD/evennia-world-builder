# SPDX-License-Identifier: BSD-3-Clause
"""Tests for evennia-world-builder.

Verifies the package is importable, the Reader contract is honoured by
GitHubReader against a mocked urllib, settings-based dispatch resolves
correctly, and the manifest discovery + loading pipeline (Definitions,
Finder, Loader) operates against synthetic in-memory fixtures.
"""
import os
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

import evennia_world_builder
from evennia_world_builder import (
    Definitions,
    DefinitionsError,
    Finder,
    FinderManifestError,
    FinderQueryError,
    FoundLocation,
    GitHubReader,
    LoadedEntity,
    Loader,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    LocalReader,
    ReaderAuthError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
    ReaderResult,
    Validator,
    ValidatorError,
    get_reader_class,
)


class FakeReader:
    """Used by GetReaderClassTest to verify dispatch via @override_settings.

    Defined at module scope so it is importable as
    ``evennia_world_builder.tests.FakeReader``. Distinct from FixtureReader below.
    """


class FixtureReader:
    """An in-memory Reader for tests of Finder and Loader.

    Maps path → parsed-data; raises ReaderNotFoundError for unknown paths.
    Built at module scope so individual tests can construct one inline
    with a synthetic content tree.
    """

    def __init__(self, files: dict):
        self.files = files

    def read(self, path: str) -> ReaderResult:
        if path not in self.files:
            raise ReaderNotFoundError(f"FixtureReader: path {path!r} not in fixtures")
        data = self.files[path]
        return ReaderResult(raw_bytes=repr(data).encode(), parsed=data)


# Synthetic manifest used by both Finder and Loader tests. Mirrors the
# scaffolded layout in evennia-world-builder-test-yaml so behaviour is the same
# in unit tests and against the live private repo.
SCAFFOLD = {
    "definitions.yaml": {"levels": ["zone", "room"]},
    "index.yaml": {"entries": [
        {"name": "millholm", "kind": "folder"},
        {"name": "aethenveil", "kind": "file"},
    ]},
    "millholm/index.yaml": {"entries": [
        {"name": "inn", "kind": "file"},
        {"name": "bakery", "kind": "file"},
    ]},
    "aethenveil.yaml": {
        "deployment_id": 1,
        "typeclass": "evennia.objects.objects.DefaultRoom",
        "name": "Sanctum",
        "description": "A circular chamber.",
    },
    "millholm/inn.yaml": {
        "deployment_id": 1,
        "typeclass": "evennia.objects.objects.DefaultRoom",
        "name": "The Crooked Lantern",
        "description": "Warmly lit.",
    },
    "millholm/bakery.yaml": {
        "deployment_id": 2,
        "typeclass": "evennia.objects.objects.DefaultRoom",
        "name": "Goldencrust",
        "description": "Smells of bread.",
    },
}


class SmokeTest(TestCase):
    """End-to-end install + runner sanity check."""

    def test_package_importable(self):
        self.assertTrue(hasattr(evennia_world_builder, "__version__"))

    def test_version_is_string(self):
        self.assertIsInstance(evennia_world_builder.__version__, str)


class CmdWBBuildSmokeTest(TestCase):
    """Smoke check that the library-shipped admin command is importable
    and has the expected metadata. Auto-installation into CharacterCmdSet
    is exercised by live smoke testing in the demo gamedir."""

    def test_command_importable(self):
        from evennia_world_builder.commands import CmdWBBuild

        self.assertEqual(CmdWBBuild.key, "wb_build")
        self.assertEqual(CmdWBBuild.locks, "cmd:superuser()")
        # Evennia normalises help_category to lowercase.
        self.assertEqual(CmdWBBuild.help_category.lower(), "world builder")


class GitHubReaderTest(TestCase):
    """Verify GitHubReader.read() against a mocked urllib."""

    KWARGS = {
        "repo": "owner/repo",
        "ref": "main",
        "pat": "ghp_test",
    }
    PATH = "file.yaml"

    def _response_with_payload(self, payload: bytes) -> MagicMock:
        """Build a context-manager-protocol-supporting mock for urlopen()."""
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = payload
        return mock_response

    def test_required_kwargs_declared(self):
        self.assertEqual(
            GitHubReader.required_kwargs, ("repo", "ref", "pat")
        )

    @patch("evennia_world_builder.readers.github.urllib.request.urlopen")
    def test_happy_path_returns_raw_and_parsed(self, mock_urlopen):
        mock_urlopen.return_value = self._response_with_payload(b"key: value\n")
        result = GitHubReader(**self.KWARGS).read(self.PATH)
        self.assertIsInstance(result, ReaderResult)
        self.assertEqual(result.raw_bytes, b"key: value\n")
        self.assertEqual(result.parsed, {"key": "value"})

    @patch("evennia_world_builder.readers.github.urllib.request.urlopen")
    def test_request_url_and_headers(self, mock_urlopen):
        mock_urlopen.return_value = self._response_with_payload(b"x: 1\n")
        GitHubReader(**self.KWARGS).read(self.PATH)
        request = mock_urlopen.call_args[0][0]
        self.assertIn("/repos/owner/repo/contents/file.yaml", request.full_url)
        self.assertIn("ref=main", request.full_url)
        # urllib.request.Request normalises header keys via .capitalize();
        # compare via a lowercased view to stay robust against that.
        headers_lower = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers_lower["authorization"], "Bearer ghp_test")
        self.assertEqual(headers_lower["accept"], "application/vnd.github.raw")
        self.assertEqual(headers_lower["x-github-api-version"], "2022-11-28")
        self.assertEqual(headers_lower["user-agent"], "evennia-world-builder")

    @patch("evennia_world_builder.readers.github.urllib.request.urlopen")
    def test_401_raises_auth_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None
        )
        with self.assertRaises(ReaderAuthError):
            GitHubReader(**self.KWARGS).read(self.PATH)

    @patch("evennia_world_builder.readers.github.urllib.request.urlopen")
    def test_404_raises_not_found_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 404, "Not Found", {}, None
        )
        with self.assertRaises(ReaderNotFoundError):
            GitHubReader(**self.KWARGS).read(self.PATH)

    @patch("evennia_world_builder.readers.github.urllib.request.urlopen")
    def test_url_error_raises_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("nodename nor servname")
        with self.assertRaises(ReaderNetworkError):
            GitHubReader(**self.KWARGS).read(self.PATH)

    @patch("evennia_world_builder.readers.github.urllib.request.urlopen")
    def test_bad_yaml_raises_parse_error(self, mock_urlopen):
        mock_urlopen.return_value = self._response_with_payload(b":::not yaml\n: : :")
        with self.assertRaises(ReaderParseError):
            GitHubReader(**self.KWARGS).read(self.PATH)


class LocalReaderTest(TestCase):
    """Verify LocalReader.read() against real temp-directory fixtures."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, path: str, content: bytes) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def test_required_kwargs_declared(self):
        self.assertEqual(LocalReader.required_kwargs, ("root",))

    def test_happy_path_returns_raw_and_parsed(self):
        self._write("hello.yaml", b"key: value\n")
        result = LocalReader(root=self.root).read("hello.yaml")
        self.assertIsInstance(result, ReaderResult)
        self.assertEqual(result.raw_bytes, b"key: value\n")
        self.assertEqual(result.parsed, {"key": "value"})

    def test_nested_path_resolves(self):
        self._write("millholm/bakery.yaml", b"deployment_id: 1\nname: B\n")
        result = LocalReader(root=self.root).read("millholm/bakery.yaml")
        self.assertEqual(result.parsed, {"deployment_id": 1, "name": "B"})

    def test_missing_file_raises_not_found(self):
        with self.assertRaises(ReaderNotFoundError):
            LocalReader(root=self.root).read("ghost.yaml")

    def test_bad_yaml_raises_parse_error(self):
        self._write("bad.yaml", b":::not yaml\n: : :")
        with self.assertRaises(ReaderParseError):
            LocalReader(root=self.root).read("bad.yaml")

    def test_path_traversal_blocked(self):
        # An "escape" path resolves outside root and must be rejected
        # rather than reading whatever happens to be on disk above root.
        with self.assertRaises(ReaderNotFoundError):
            LocalReader(root=self.root).read("../../etc/passwd")

    def test_root_accepts_string_path(self):
        # Many consumers will pass a settings string, not a Path.
        self._write("hello.yaml", b"k: v\n")
        result = LocalReader(root=str(self.root)).read("hello.yaml")
        self.assertEqual(result.parsed, {"k": "v"})


class GetReaderClassTest(TestCase):
    """Verify settings-based dispatch via WORLDBUILDER_READER."""

    def test_default_returns_github_reader(self):
        self.assertIs(get_reader_class(), GitHubReader)

    @override_settings(WORLDBUILDER_READER="evennia_world_builder.tests.FakeReader")
    def test_override_via_settings(self):
        self.assertIs(get_reader_class(), FakeReader)

    @override_settings(WORLDBUILDER_READER="evennia_world_builder.does_not_exist.Nope")
    def test_bad_dotted_path_raises(self):
        with self.assertRaises((ImportError, AttributeError)):
            get_reader_class()


class DefinitionsTest(TestCase):
    """Verify Definitions parsing."""

    def test_default_empty_levels(self):
        self.assertEqual(Definitions().levels, ())

    def test_from_dict_with_levels(self):
        d = Definitions.from_dict({"levels": ["zone", "room"]})
        self.assertEqual(d.levels, ("zone", "room"))

    def test_from_dict_with_none(self):
        self.assertEqual(Definitions.from_dict(None).levels, ())

    def test_from_dict_with_no_levels_key(self):
        self.assertEqual(Definitions.from_dict({}).levels, ())

    def test_from_dict_levels_must_be_list(self):
        with self.assertRaises(DefinitionsError):
            Definitions.from_dict({"levels": "zone,room"})

    def test_from_dict_levels_entries_must_be_strings(self):
        with self.assertRaises(DefinitionsError):
            Definitions.from_dict({"levels": ["zone", 42]})

    def test_from_dict_must_be_mapping(self):
        with self.assertRaises(DefinitionsError):
            Definitions.from_dict("not a dict")

    def test_from_reader(self):
        reader = FixtureReader({"definitions.yaml": {"levels": ["zone", "room"]}})
        d = Definitions.from_reader(reader)
        self.assertEqual(d.levels, ("zone", "room"))

    def test_repo_ci_pre_validation_defaults_false(self):
        self.assertFalse(Definitions.from_dict({"levels": ["zone"]}).repo_ci_pre_validation)

    def test_repo_ci_pre_validation_explicit_true(self):
        d = Definitions.from_dict({
            "levels": ["zone"],
            "repo-ci-pre-validation": True,
        })
        self.assertTrue(d.repo_ci_pre_validation)

    def test_repo_ci_pre_validation_explicit_false(self):
        d = Definitions.from_dict({
            "levels": ["zone"],
            "repo-ci-pre-validation": False,
        })
        self.assertFalse(d.repo_ci_pre_validation)

    def test_repo_ci_pre_validation_must_be_bool(self):
        with self.assertRaises(DefinitionsError):
            Definitions.from_dict({
                "levels": ["zone"],
                "repo-ci-pre-validation": "true",  # string, not bool
            })

    def test_validate_query_empty_is_valid(self):
        Definitions(levels=("zone", "room")).validate_query({})

    def test_validate_query_full_prefix_is_valid(self):
        Definitions(levels=("zone", "room")).validate_query(
            {"zone": "x", "room": "y"}
        )

    def test_validate_query_partial_prefix_is_valid(self):
        Definitions(levels=("zone", "room")).validate_query({"zone": "x"})

    def test_validate_query_unknown_key_raises(self):
        with self.assertRaises(DefinitionsError):
            Definitions(levels=("zone", "room")).validate_query({"area": "x"})

    def test_validate_query_skipped_level_raises(self):
        # levels=[zone, area, room]; can't query {zone, room} (area skipped)
        with self.assertRaises(DefinitionsError):
            Definitions(levels=("zone", "area", "room")).validate_query(
                {"zone": "x", "room": "y"}
            )

    def test_validate_query_against_empty_levels_rejects_any_keys(self):
        with self.assertRaises(DefinitionsError):
            Definitions(levels=()).validate_query({"zone": "x"})


class ParseArgsTest(TestCase):
    """Verify the wb_build argument parser (kv pairs + flags + 'all')."""

    def _parse(self, s):
        from evennia_world_builder.commands import _parse_args
        return _parse_args(s)

    def test_all_token_returns_empty_query(self):
        query, flags = self._parse("all")
        self.assertEqual(query, {})
        self.assertEqual(flags, set())

    def test_single_pair(self):
        query, flags = self._parse("zone=millholm")
        self.assertEqual(query, {"zone": "millholm"})
        self.assertEqual(flags, set())

    def test_multiple_pairs(self):
        query, flags = self._parse("zone=millholm room=bakery")
        self.assertEqual(query, {"zone": "millholm", "room": "bakery"})
        self.assertEqual(flags, set())

    def test_extra_whitespace_tolerated(self):
        query, _ = self._parse("  zone=millholm   room=bakery  ")
        self.assertEqual(query, {"zone": "millholm", "room": "bakery"})

    def test_force_validate_flag_with_kv(self):
        query, flags = self._parse("zone=millholm --force-validate")
        self.assertEqual(query, {"zone": "millholm"})
        self.assertEqual(flags, {"force-validate"})

    def test_force_validate_flag_with_all(self):
        query, flags = self._parse("all --force-validate")
        self.assertEqual(query, {})
        self.assertEqual(flags, {"force-validate"})

    def test_flag_position_does_not_matter(self):
        query, flags = self._parse("--force-validate zone=millholm")
        self.assertEqual(query, {"zone": "millholm"})
        self.assertEqual(flags, {"force-validate"})

    def test_empty_input_raises_no_scope(self):
        # Empty / whitespace-only input has no positional tokens — no scope.
        with self.assertRaises(ValueError):
            self._parse("")
        with self.assertRaises(ValueError):
            self._parse("   ")

    def test_flags_only_raises_no_scope(self):
        # A flag is not a scope; require 'all' or a level=value pair.
        with self.assertRaises(ValueError):
            self._parse("--force-validate")

    def test_token_without_equals_raises(self):
        with self.assertRaises(ValueError):
            self._parse("zone")

    def test_empty_value_raises(self):
        with self.assertRaises(ValueError):
            self._parse("zone=")

    def test_empty_key_raises(self):
        with self.assertRaises(ValueError):
            self._parse("=millholm")


class FilterByQueryTest(TestCase):
    """Verify _filter_by_query reduces a whole-repo entity list to a scope."""

    def _entities(self):
        return [
            LoadedEntity(location={"zone": "millholm", "room": "inn"},
                         content={"deployment_id": 1}, path="millholm/inn.yaml"),
            LoadedEntity(location={"zone": "millholm", "room": "bakery"},
                         content={"deployment_id": 1}, path="millholm/bakery.yaml"),
            LoadedEntity(location={"zone": "aethenveil"},
                         content={"deployment_id": 1}, path="aethenveil.yaml"),
        ]

    def test_empty_query_returns_all(self):
        from evennia_world_builder.commands import _filter_by_query
        e = self._entities()
        self.assertEqual(_filter_by_query(e, {}), e)

    def test_filter_by_zone_returns_subtree(self):
        from evennia_world_builder.commands import _filter_by_query
        result = _filter_by_query(self._entities(), {"zone": "millholm"})
        self.assertEqual(len(result), 2)
        self.assertEqual({e.path for e in result},
                         {"millholm/inn.yaml", "millholm/bakery.yaml"})

    def test_filter_full_path_returns_single(self):
        from evennia_world_builder.commands import _filter_by_query
        result = _filter_by_query(
            self._entities(), {"zone": "millholm", "room": "bakery"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].path, "millholm/bakery.yaml")

    def test_filter_no_match_returns_empty(self):
        from evennia_world_builder.commands import _filter_by_query
        result = _filter_by_query(self._entities(), {"zone": "ghost"})
        self.assertEqual(result, [])


class FinderTest(TestCase):
    """Verify Finder.find() against a synthetic manifest tree."""

    def _make_finder(self):
        reader = FixtureReader(SCAFFOLD)
        defs = Definitions.from_reader(reader)
        return Finder(reader, defs)

    def test_empty_query_returns_root(self):
        found = self._make_finder().find()
        self.assertEqual(found.path, "")
        self.assertEqual(found.kind, "folder")
        self.assertEqual(found.location, {})

    def test_zone_folder_query_returns_folder_location(self):
        found = self._make_finder().find({"zone": "millholm"})
        self.assertEqual(found.path, "millholm")
        self.assertEqual(found.kind, "folder")
        self.assertEqual(found.location, {"zone": "millholm"})

    def test_zone_file_query_returns_file_location(self):
        found = self._make_finder().find({"zone": "aethenveil"})
        self.assertEqual(found.path, "aethenveil.yaml")
        self.assertEqual(found.kind, "file")
        self.assertEqual(found.location, {"zone": "aethenveil"})

    def test_full_path_query(self):
        found = self._make_finder().find({"zone": "millholm", "room": "bakery"})
        self.assertEqual(found.path, "millholm/bakery.yaml")
        self.assertEqual(found.kind, "file")
        self.assertEqual(found.location, {"zone": "millholm", "room": "bakery"})

    def test_invalid_key_raises(self):
        # Keys-not-in-levels is now DefinitionsError (Definitions owns the
        # query-shape validation; Finder only validates manifest content).
        with self.assertRaises(DefinitionsError):
            self._make_finder().find({"area": "town"})

    def test_skipped_level_raises(self):
        # levels=[zone, room]; can't query just {room: X}
        with self.assertRaises(DefinitionsError):
            self._make_finder().find({"room": "inn"})

    def test_value_not_in_index_raises(self):
        with self.assertRaises(FinderQueryError):
            self._make_finder().find({"zone": "nonexistent"})

    def test_room_not_in_zone_raises(self):
        with self.assertRaises(FinderQueryError):
            self._make_finder().find({"zone": "millholm", "room": "nonexistent"})

    def test_missing_index_raises_manifest_error(self):
        scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            # no index.yaml at root
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        finder = Finder(reader, defs)
        with self.assertRaises(FinderManifestError):
            finder.find({"zone": "x"})


class LoaderTest(TestCase):
    """Verify Loader.load() recursive walk against synthetic fixtures."""

    def _make(self):
        reader = FixtureReader(SCAFFOLD)
        defs = Definitions.from_reader(reader)
        return Finder(reader, defs), Loader(reader, defs)

    def test_load_root_returns_all_in_index_order(self):
        finder, loader = self._make()
        entities = loader.load(finder.find())
        # Index order: millholm (folder, recurses into inn, bakery), then aethenveil
        self.assertEqual(len(entities), 3)
        self.assertEqual(entities[0].location, {"zone": "millholm", "room": "inn"})
        self.assertEqual(entities[0].path, "millholm/inn.yaml")
        self.assertEqual(entities[1].location, {"zone": "millholm", "room": "bakery"})
        self.assertEqual(entities[1].path, "millholm/bakery.yaml")
        self.assertEqual(entities[2].location, {"zone": "aethenveil"})
        self.assertEqual(entities[2].path, "aethenveil.yaml")

    def test_load_zone_folder_returns_subtree(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "millholm"}))
        self.assertEqual(len(entities), 2)
        self.assertEqual(entities[0].path, "millholm/inn.yaml")
        self.assertEqual(entities[1].path, "millholm/bakery.yaml")

    def test_load_zone_file_returns_single(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "aethenveil"}))
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].path, "aethenveil.yaml")
        self.assertEqual(entities[0].location, {"zone": "aethenveil"})

    def test_load_specific_room(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "millholm", "room": "bakery"}))
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].path, "millholm/bakery.yaml")
        self.assertEqual(entities[0].location, {"zone": "millholm", "room": "bakery"})

    def test_loaded_entity_carries_content(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "millholm", "room": "inn"}))
        self.assertEqual(entities[0].content, {
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "The Crooked Lantern",
            "description": "Warmly lit.",
        })

    def test_index_pointing_at_missing_file_raises(self):
        scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            "index.yaml": {"entries": [{"name": "ghost", "kind": "file"}]},
            # no ghost.yaml
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        loader = Loader(reader, defs)
        with self.assertRaises(LoaderMissingEntryError):
            loader.load(FoundLocation(path="", kind="folder", location={}))

    def test_folder_missing_index_raises(self):
        scaffold = {
            "definitions.yaml": {"levels": ["zone", "room"]},
            "index.yaml": {"entries": [{"name": "ghost", "kind": "folder"}]},
            # no ghost/index.yaml
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        loader = Loader(reader, defs)
        with self.assertRaises(LoaderMissingIndexError):
            loader.load(FoundLocation(path="", kind="folder", location={}))


class ValidatorTest(TestCase):
    """Verify Validator's per-entity predicates and per-file id index."""

    def _entity(self, path: str, content) -> LoadedEntity:
        # Inject a default typeclass when the test didn't supply one — Tier 1
        # makes typeclass mandatory now, so otherwise every test would have to
        # repeat it just to keep the unrelated Tier 1 checks from firing.
        if isinstance(content, dict) and "typeclass" not in content:
            content = {**content, "typeclass": "evennia.objects.objects.DefaultRoom"}
        return LoadedEntity(location={}, content=content, path=path)

    def _valid(self, path: str, deployment_id: int) -> LoadedEntity:
        return self._entity(path, {
            "deployment_id": deployment_id,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "x",
        })

    def _validator(self):
        return Validator(Definitions(levels=("zone", "room")))

    # --- baseline -----------------------------------------------------

    def test_messages_starts_empty(self):
        self.assertEqual(self._validator().messages, [])

    def test_seen_ids_starts_empty(self):
        self.assertEqual(self._validator().seen_ids, {})

    def test_validate_empty_list_emits_proof_of_life_only(self):
        v = self._validator()
        v.validate([])
        self.assertEqual(len(v.messages), 1)
        self.assertTrue(v.messages[0].startswith("VALIDATOR: "))
        self.assertEqual(v.errors, [])

    def test_clean_run_returns_entities_unchanged(self):
        v = self._validator()
        entities = [
            self._valid("a.yaml", 1),
            self._valid("b.yaml", 1),
        ]
        self.assertEqual(v.validate(entities), entities)
        self.assertEqual(v.errors, [])

    # --- deployment_id well-formed predicate --------------------------

    def test_missing_deployment_id_raises_and_records(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity("inn.yaml", {"name": "x"})])
        self.assertEqual(len(v.errors), 1)
        self.assertIn("missing required field 'deployment_id'", v.errors[0])
        self.assertIn("inn.yaml", v.errors[0])

    def test_non_integer_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity("a.yaml", {"deployment_id": "five"})])
        self.assertIn("must be an integer", v.errors[0])

    def test_bool_deployment_id_rejected(self):
        # bool is a subclass of int in Python — must not be accepted.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity("a.yaml", {"deployment_id": True})])
        self.assertIn("must be an integer", v.errors[0])

    def test_negative_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity("a.yaml", {"deployment_id": -1})])
        self.assertIn("must be non-negative", v.errors[0])

    def test_zero_deployment_id_accepted(self):
        # Non-negative includes zero by design.
        v = self._validator()
        v.validate([self._valid("a.yaml", 0)])
        self.assertEqual(v.errors, [])

    # --- duplicate-id-within-file stateful check ----------------------

    def test_duplicate_deployment_id_within_file_flagged(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([
                self._valid("forest.yaml", 1),
                self._valid("forest.yaml", 1),
            ])
        self.assertEqual(len(v.errors), 1)
        self.assertIn("duplicate deployment_id=1", v.errors[0])
        self.assertIn("forest.yaml", v.errors[0])

    def test_same_id_in_different_files_is_not_a_duplicate(self):
        v = self._validator()
        v.validate([
            self._valid("forest.yaml", 1),
            self._valid("bakery.yaml", 1),
        ])
        self.assertEqual(v.errors, [])

    def test_seen_ids_index_populated_after_clean_run(self):
        v = self._validator()
        v.validate([
            self._valid("forest.yaml", 1),
            self._valid("forest.yaml", 2),
            self._valid("bakery.yaml", 1),
        ])
        self.assertEqual(v.seen_ids, {
            "forest.yaml": {1, 2},
            "bakery.yaml": {1},
        })

    def test_malformed_entity_skips_stateful_checks(self):
        # An entity that fails the well-formed predicate must NOT be
        # recorded in seen_ids — stateful checks would otherwise operate
        # on bad data (e.g. trying to add a non-integer to the set).
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([
                self._entity("a.yaml", {"deployment_id": "nope"}),
                self._valid("a.yaml", 1),
            ])
        # Only the second entity made it into the index.
        self.assertEqual(v.seen_ids, {"a.yaml": {1}})

    # --- "complete refusal" semantics ---------------------------------

    def test_all_findings_collected_before_raise(self):
        # Two distinct errors across two entities — both must be in
        # messages/errors after the single ValidatorError is raised.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([
                self._entity("a.yaml", {"name": "x"}),       # missing field
                self._entity("b.yaml", {"deployment_id": -1}),  # negative
            ])
        self.assertEqual(len(v.errors), 2)
        self.assertTrue(any("a.yaml" in e and "missing" in e for e in v.errors))
        self.assertTrue(any("b.yaml" in e and "non-negative" in e for e in v.errors))


class ValidatorTypeclassResolvableTest(TestCase):
    """Tier 3 — verify _check_typeclass_resolvable gating + behaviour."""

    def _entity(self, path: str, content) -> LoadedEntity:
        return LoadedEntity(location={}, content=content, path=path)

    def _entity_with_typeclass(self, typeclass) -> LoadedEntity:
        return self._entity("a.yaml", {"deployment_id": 1, "typeclass": typeclass})

    def _defs(self):
        return Definitions(levels=("zone",))

    # --- gating: predicate runs only when evennia_runtime=True --------

    def test_evennia_runtime_defaults_false(self):
        self.assertFalse(Validator(self._defs()).evennia_runtime)

    def test_default_off_skips_typeclass_check(self):
        # Bogus typeclass — would fail Tier 3, but Tier 3 doesn't run.
        v = Validator(self._defs())
        v.validate([self._entity_with_typeclass("nonexistent.module.NopeClass")])
        self.assertEqual(v.errors, [])

    def test_evennia_runtime_true_runs_typeclass_check(self):
        v = Validator(self._defs(), evennia_runtime=True)
        with self.assertRaises(ValidatorError):
            v.validate([self._entity_with_typeclass("nonexistent.module.NopeClass")])
        self.assertTrue(any("could not be imported" in e for e in v.errors))

    # --- per-case behaviour (only relevant under evennia_runtime=True)

    def test_typeclass_no_dot_flagged(self):
        v = Validator(self._defs(), evennia_runtime=True)
        with self.assertRaises(ValidatorError):
            v.validate([self._entity_with_typeclass("NopeClass")])
        self.assertTrue(any("not a dotted path" in e for e in v.errors))

    def test_typeclass_module_loaded_class_missing(self):
        # `os` is reliably importable; `NotARealClass` definitely isn't on it.
        v = Validator(self._defs(), evennia_runtime=True)
        with self.assertRaises(ValidatorError):
            v.validate([self._entity_with_typeclass("os.NotARealClass")])
        msg = " ".join(v.errors)
        self.assertIn("loaded but class", msg)
        self.assertIn("'NotARealClass'", msg)

    def test_typeclass_resolvable_passes(self):
        # `os.PathLike` is a real, importable, public name.
        v = Validator(self._defs(), evennia_runtime=True)
        v.validate([self._entity_with_typeclass("os.PathLike")])
        self.assertEqual(v.errors, [])


class ValidatorTypeclassWellFormedTest(TestCase):
    """Tier 1 — typeclass is mandatory, must be a non-empty string."""

    def _entity(self, content) -> LoadedEntity:
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_missing_typeclass_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"deployment_id": 1})])
        self.assertTrue(any("missing required field 'typeclass'" in e for e in v.errors))

    def test_non_string_typeclass_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"deployment_id": 1, "typeclass": 42})])
        self.assertTrue(any("'typeclass' must be a string" in e for e in v.errors))

    def test_empty_string_typeclass_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"deployment_id": 1, "typeclass": ""})])
        self.assertTrue(any("must be a non-empty string" in e for e in v.errors))

    def test_whitespace_only_typeclass_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"deployment_id": 1, "typeclass": "   "})])
        self.assertTrue(any("must be a non-empty string" in e for e in v.errors))

    def test_well_formed_typeclass_passes(self):
        v = self._validator()
        v.validate([self._entity({
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
        })])
        self.assertEqual(v.errors, [])


class ValidatorTagsShapeTest(TestCase):
    """Tier 1 — verify _check_tags_field_shape on the tags field."""

    _BASE = {
        "deployment_id": 1,
        "typeclass": "evennia.objects.objects.DefaultRoom",
    }

    def _entity(self, tags) -> LoadedEntity:
        return LoadedEntity(
            location={},
            content={**self._BASE, "tags": tags},
            path="a.yaml",
        )

    def _entity_no_tags(self) -> LoadedEntity:
        return LoadedEntity(
            location={}, content=dict(self._BASE), path="a.yaml"
        )

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_no_tags_field_passes(self):
        v = self._validator()
        v.validate([self._entity_no_tags()])
        self.assertEqual(v.errors, [])

    def test_empty_tag_list_passes(self):
        v = self._validator()
        v.validate([self._entity([])])
        self.assertEqual(v.errors, [])

    def test_string_tags_pass(self):
        v = self._validator()
        v.validate([self._entity(["bakery", "commerce"])])
        self.assertEqual(v.errors, [])

    def test_dict_tags_pass(self):
        v = self._validator()
        v.validate([self._entity([{"key": "indoor", "category": "environment"}])])
        self.assertEqual(v.errors, [])

    def test_dict_tag_without_category_passes(self):
        # Default category is permitted; category is optional in the dict form.
        v = self._validator()
        v.validate([self._entity([{"key": "fixture"}])])
        self.assertEqual(v.errors, [])

    def test_mixed_string_and_dict_pass(self):
        v = self._validator()
        v.validate([self._entity(["bakery", {"key": "indoor", "category": "environment"}])])
        self.assertEqual(v.errors, [])

    def test_tags_not_a_list_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity("bakery")])
        self.assertTrue(any("'tags' must be a list" in e for e in v.errors))

    def test_empty_string_tag_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(["bakery", ""])])
        self.assertTrue(any("non-empty string" in e for e in v.errors))

    def test_int_tag_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([42])])
        self.assertTrue(any("must be a string or a mapping" in e for e in v.errors))

    def test_dict_tag_missing_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([{"category": "environment"}])])
        self.assertTrue(any("must include 'key'" in e for e in v.errors))

    def test_dict_tag_empty_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([{"key": "", "category": "x"}])])
        self.assertTrue(any("'key' must be a non-empty string" in e for e in v.errors))

    def test_dict_tag_non_string_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([{"key": 42}])])
        self.assertTrue(any("'key' must be a non-empty string" in e for e in v.errors))

    def test_dict_tag_non_string_category_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([{"key": "x", "category": 42}])])
        self.assertTrue(any("'category' must be a string" in e for e in v.errors))


class ValidatorTagsReservedCategoryTest(TestCase):
    """Tier 1 — verify _check_tags_no_reserved_category guards wb_ prefix."""

    def _entity(self, tags) -> LoadedEntity:
        return LoadedEntity(
            location={},
            content={
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "tags": tags,
            },
            path="a.yaml",
        )

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_non_reserved_category_passes(self):
        v = self._validator()
        v.validate([self._entity([{"key": "x", "category": "environment"}])])
        self.assertEqual(v.errors, [])

    def test_wb_deployment_file_category_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([
                {"key": "millholm/x.yaml", "category": "wb_deployment_file"}
            ])])
        self.assertTrue(any("reserved for the library" in e for e in v.errors))
        self.assertTrue(any("wb_deployment_file" in e for e in v.errors))

    def test_wb_deployment_id_category_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([
                {"key": "1", "category": "wb_deployment_id"}
            ])])
        self.assertTrue(any("reserved for the library" in e for e in v.errors))

    def test_wb_anything_category_rejected(self):
        # The whole wb_ prefix is reserved, not just the two current categories.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity([
                {"key": "x", "category": "wb_future_thing"}
            ])])
        self.assertTrue(any("reserved for the library" in e for e in v.errors))

    def test_string_tags_unaffected(self):
        # Shorthand tags don't carry a category and can't trip this predicate.
        v = self._validator()
        v.validate([self._entity(["wb_deployment_file"])])  # legal as a tag KEY
        self.assertEqual(v.errors, [])

    def test_default_category_unaffected(self):
        # Dict tag without a category falls back to default — can't be reserved.
        v = self._validator()
        v.validate([self._entity([{"key": "wb_deployment_file"}])])
        self.assertEqual(v.errors, [])
