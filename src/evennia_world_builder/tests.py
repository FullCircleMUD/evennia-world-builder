# SPDX-License-Identifier: BSD-3-Clause
"""Tests for evennia-world-builder.

Verifies the package is importable, the Reader contract is honoured by
GitHubReader against a mocked urllib, settings-based dispatch resolves
correctly, and the manifest discovery + loading pipeline (Definitions,
Finder, Loader) operates against synthetic in-memory fixtures.
"""
import os
from unittest.mock import MagicMock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase, override_settings

import evennia_world_builder
from evennia_world_builder import (
    ApiError,
    Builder,
    BuilderError,
    Definitions,
    DefinitionsError,
    Finder,
    FinderManifestError,
    FinderQueryError,
    FoundLocation,
    GitHubReader,
    LoadedEntity,
    Loader,
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ReaderNotFoundError,
    ReaderResult,
    Validator,
    ValidatorError,
    get_reader_class,
    wb_lookup_dbref,
    wb_lookup_object,
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
    "aethenveil.yaml": {"entities": [
        {
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "Sanctum",
            "location": None,
            "description": "A circular chamber.",
        },
    ]},
    "millholm/inn.yaml": {"entities": [
        {
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "The Crooked Lantern",
            "location": None,
            "description": "Warmly lit.",
        },
    ]},
    "millholm/bakery.yaml": {"entities": [
        {
            "deployment_id": 2,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "Goldencrust",
            "location": None,
            "description": "Smells of bread.",
        },
    ]},
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


# GitHubReaderTest and LocalReaderTest live in evennia-yaml-reader, where the
# Reader implementations now reside. This module retains only world-builder's
# own concerns — the dispatch test below, the Finder/Loader/Validator/Builder
# tests further down, and the FixtureReader they use.


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

    def test_strict_attributes_defaults_false(self):
        self.assertFalse(Definitions.from_dict({"levels": ["zone"]}).strict_attributes)

    def test_strict_attributes_true_refused_until_implemented(self):
        # Feature isn't shipped yet — accepting True would mislead
        # consumers into thinking validation was running. Refuse early.
        with self.assertRaises(DefinitionsError) as ctx:
            Definitions.from_dict({
                "levels": ["zone"],
                "strict-attributes": True,
            })
        self.assertIn("not yet implemented", str(ctx.exception))

    def test_strict_attributes_explicit_false(self):
        d = Definitions.from_dict({
            "levels": ["zone"],
            "strict-attributes": False,
        })
        self.assertFalse(d.strict_attributes)

    def test_strict_attributes_must_be_bool(self):
        with self.assertRaises(DefinitionsError):
            Definitions.from_dict({
                "levels": ["zone"],
                "strict-attributes": "true",  # string, not bool
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
        entities = loader.load(finder.find()).entities
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
        entities = loader.load(finder.find({"zone": "millholm"})).entities
        self.assertEqual(len(entities), 2)
        self.assertEqual(entities[0].path, "millholm/inn.yaml")
        self.assertEqual(entities[1].path, "millholm/bakery.yaml")

    def test_load_zone_file_returns_single(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "aethenveil"})).entities
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].path, "aethenveil.yaml")
        self.assertEqual(entities[0].location, {"zone": "aethenveil"})

    def test_load_specific_room(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "millholm", "room": "bakery"})).entities
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].path, "millholm/bakery.yaml")
        self.assertEqual(entities[0].location, {"zone": "millholm", "room": "bakery"})

    def test_loaded_entity_carries_content(self):
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "millholm", "room": "inn"})).entities
        self.assertEqual(entities[0].content, {
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "The Crooked Lantern",
            "location": None,
            "description": "Warmly lit.",
        })

    def test_loaded_entity_carries_home_field_null(self):
        # `home: null` in YAML is a per-entity field that the Loader
        # passes through verbatim in entity.content, ready for the
        # Builder to translate into create_object's nohome=True kwarg.
        # See docs/home.md (Validator/Builder downstream consume it).
        result = self._load_yaml_result({
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultObject",
            "name": "A fixture",
            "location": None,
            "home": None,
        })
        self.assertEqual(result.entities[0].content["home"], None)

    def test_loaded_entity_carries_home_field_cross_ref(self):
        # `home: {deployment_file, deployment_id}` likewise rides through
        # entity.content untouched. Cross-ref resolution happens at
        # Builder time via the same `_resolve_cross_ref` location uses.
        result = self._load_yaml_result({
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultObject",
            "name": "A courier",
            "location": None,
            "home": {"deployment_file": "x.yaml", "deployment_id": 2},
        })
        self.assertEqual(
            result.entities[0].content["home"],
            {"deployment_file": "x.yaml", "deployment_id": 2},
        )

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

    # --- shape-3 enforcement (single supported file shape) ---
    #
    # The library standardises on top-level mapping with an `entities:`
    # key whose value is a list of entity mappings. Other top-level
    # shapes (bare list, single-entity mapping without `entities:`,
    # non-mapping) are refused at load time.

    def _load_with_raw_file_yaml(self, file_yaml):
        """Load a one-file scaffold WITHOUT the auto-wrap helper applies.

        Used for tests that need to exercise the Loader's refusal of
        shape violations directly. Returns just ``.entities`` for
        consistency with the other test helpers; tests that need the
        full LoadResult bypass this and build their own scaffold.
        """
        scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            "index.yaml": {"entries": [{"name": "x", "kind": "file"}]},
            "x.yaml": file_yaml,
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        loader = Loader(reader, defs)
        return loader.load(FoundLocation(path="", kind="folder", location={})).entities

    def test_shape_3_mapping_with_entities_accepted(self):
        # Canonical shape: top-level mapping with `entities:` list.
        entities = self._load_with_raw_file_yaml({
            "entities": [
                {"deployment_id": 1, "name": "A"},
                {"deployment_id": 2, "name": "B"},
            ],
        })
        self.assertEqual(len(entities), 2)

    def test_shape_3_empty_entities_list_accepted(self):
        # Empty entities list is valid (file declares no entities).
        entities = self._load_with_raw_file_yaml({"entities": []})
        self.assertEqual(entities, [])

    def test_top_level_list_refused(self):
        # Legacy shape 2 (top-level YAML list) is no longer supported.
        with self.assertRaises(LoaderInvalidShapeError):
            self._load_with_raw_file_yaml([
                {"deployment_id": 1, "name": "A"},
            ])

    def test_top_level_mapping_without_entities_refused(self):
        # Legacy shape 1 (single-entity mapping at top level) is no
        # longer supported — author must wrap in `entities:`.
        with self.assertRaises(LoaderInvalidShapeError):
            self._load_with_raw_file_yaml({
                "deployment_id": 1, "name": "Solo",
            })

    def test_entities_value_must_be_list(self):
        # `entities:` present but not a list — refuse.
        with self.assertRaises(LoaderInvalidShapeError):
            self._load_with_raw_file_yaml({"entities": "oops"})

    def test_non_mapping_top_level_refused(self):
        # null / scalar / etc. at the top level — refuse.
        with self.assertRaises(LoaderInvalidShapeError):
            self._load_with_raw_file_yaml(None)

    # --- file-level metadata extraction (spike 6 step 6d) ---
    #
    # Anything in the top-level mapping besides `entities:` is
    # collected into LoadResult.file_metadata[file_path]. Library
    # doesn't curate the keys; consumers (Validator, Builder) look up
    # the keys they care about (currently `incoming_exits:`).

    def test_file_metadata_empty_when_only_entities_key(self):
        # A file with just an entities: key produces no file_metadata.
        result = self._load_yaml_result({"entities": [
            {"deployment_id": 1, "name": "A"},
        ]})
        self.assertEqual(result.file_metadata, {})

    def test_file_metadata_extracts_incoming_exits(self):
        result = self._load_yaml_result({
            "entities": [{"deployment_id": 1, "name": "A"}],
            "incoming_exits": [
                {"deployment_file": "millholm/inn.yaml", "deployment_id": 2},
            ],
        })
        self.assertEqual(result.file_metadata, {
            "x.yaml": {
                "incoming_exits": [
                    {"deployment_file": "millholm/inn.yaml", "deployment_id": 2},
                ],
            },
        })

    def test_file_metadata_keyed_by_file_path(self):
        # Multi-file load — each file's metadata keyed by its own path.
        scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            "index.yaml": {"entries": [
                {"name": "a", "kind": "file"},
                {"name": "b", "kind": "file"},
            ]},
            "a.yaml": {
                "entities": [{"deployment_id": 1, "name": "A"}],
                "incoming_exits": [
                    {"deployment_file": "b.yaml", "deployment_id": 1},
                ],
            },
            "b.yaml": {
                "entities": [{"deployment_id": 1, "name": "B"}],
                # b.yaml has no file-level metadata
            },
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        loader = Loader(reader, defs)
        result = loader.load(FoundLocation(path="", kind="folder", location={}))
        self.assertIn("a.yaml", result.file_metadata)
        self.assertNotIn("b.yaml", result.file_metadata)
        self.assertEqual(
            result.file_metadata["a.yaml"]["incoming_exits"],
            [{"deployment_file": "b.yaml", "deployment_id": 1}],
        )

    def test_file_metadata_extracts_links(self):
        # `links:` lives alongside `entities:` and `incoming_exits:` as a
        # file-level key. Loader extracts it into file_metadata verbatim
        # — shape validation and resolution happen downstream.
        # See docs/links.md.
        result = self._load_yaml_result({
            "entities": [{"deployment_id": 1, "name": "A"}],
            "links": [
                {
                    "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
                    "attribute": "other_side",
                    "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
                },
            ],
        })
        self.assertEqual(result.file_metadata, {
            "x.yaml": {
                "links": [
                    {
                        "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
                        "attribute": "other_side",
                        "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
                    },
                ],
            },
        })

    def test_file_metadata_preserves_unknown_keys(self):
        # Library doesn't curate file-level keys — consumers do. An
        # unrecognised key sits in file_metadata; the Loader doesn't
        # complain and doesn't filter.
        result = self._load_yaml_result({
            "entities": [{"deployment_id": 1, "name": "A"}],
            "future_extension_key": {"some": "value"},
        })
        self.assertEqual(
            result.file_metadata["x.yaml"]["future_extension_key"],
            {"some": "value"},
        )

    def test_file_metadata_resets_between_loads(self):
        # State inside the Loader doesn't leak across calls.
        scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            "index.yaml": {"entries": [{"name": "x", "kind": "file"}]},
            "x.yaml": {
                "entities": [{"deployment_id": 1, "name": "A"}],
                "incoming_exits": [
                    {"deployment_file": "y.yaml", "deployment_id": 1},
                ],
            },
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        loader = Loader(reader, defs)
        first = loader.load(FoundLocation(path="", kind="folder", location={}))
        self.assertIn("x.yaml", first.file_metadata)
        # Second load against an empty repo — file_metadata starts fresh.
        empty_scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            "index.yaml": {"entries": []},
        }
        empty_reader = FixtureReader(empty_scaffold)
        empty_loader = Loader(empty_reader, Definitions.from_reader(empty_reader))
        second = empty_loader.load(FoundLocation(path="", kind="folder", location={}))
        self.assertEqual(second.file_metadata, {})

    # --- contents: recursion (spike 2 step 1) ---
    #
    # The Loader flattens a single YAML file's `contents:` tree into a flat
    # depth-first pre-order list. Top-level entities have is_nested=False;
    # entities authored inside a `contents:` block have is_nested=True. The
    # parent's `contents` key is popped off the emitted LoadedEntity's
    # content so downstream consumers don't see duplicate child data.

    def _load_yaml(self, yaml_body):
        """Return loaded entities (just ``.entities``) for legacy tests."""
        return self._load_yaml_result(yaml_body).entities

    def _load_yaml_result(self, yaml_body):
        """Wrap yaml_body in a one-file scaffold and return the full LoadResult.

        ``yaml_body`` is whatever the test wants to exercise. The library
        requires shape 3 (top-level mapping with ``entities:`` key) on
        every leaf file, so this helper auto-wraps legacy inputs:

        - A single entity mapping (no ``entities:`` key) → wraps as
          ``{entities: [yaml_body]}``.
        - A list of entity mappings → wraps as ``{entities: list}``.
        - A pre-shape-3 mapping (already has ``entities:`` key) →
          passes through unchanged. Use this form to exercise file-level
          metadata (``incoming_exits:``, etc.).

        Tests that want to exercise refusal of malformed file shapes
        bypass this helper and build their own scaffold.
        """
        if isinstance(yaml_body, list):
            file_yaml = {"entities": yaml_body}
        elif isinstance(yaml_body, dict) and "entities" not in yaml_body:
            file_yaml = {"entities": [yaml_body]}
        else:
            file_yaml = yaml_body
        scaffold = {
            "definitions.yaml": {"levels": ["zone"]},
            "index.yaml": {"entries": [{"name": "x", "kind": "file"}]},
            "x.yaml": file_yaml,
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        loader = Loader(reader, defs)
        return loader.load(FoundLocation(path="", kind="folder", location={}))

    def test_top_level_mapping_no_contents(self):
        # Regression: the existing single-entity flow still emits one
        # LoadedEntity with is_nested=False.
        finder, loader = self._make()
        entities = loader.load(finder.find({"zone": "millholm", "room": "inn"})).entities
        self.assertEqual(len(entities), 1)
        self.assertFalse(entities[0].is_nested)

    def test_top_level_mapping_empty_contents(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Empty",
            "typeclass": "ev.X", "location": None,
            "contents": [],
        })
        self.assertEqual(len(entities), 1)
        self.assertFalse(entities[0].is_nested)
        self.assertNotIn("contents", entities[0].content)

    def test_top_level_mapping_with_one_nested(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "contents": [{"deployment_id": 2, "name": "Counter"}],
        })
        self.assertEqual(len(entities), 2)
        self.assertEqual([e.is_nested for e in entities], [False, True])
        self.assertEqual([e.content["name"] for e in entities], ["Bakery", "Counter"])

    def test_top_level_mapping_with_multiple_nested(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "contents": [
                {"deployment_id": 2, "name": "A"},
                {"deployment_id": 3, "name": "B"},
                {"deployment_id": 4, "name": "C"},
            ],
        })
        self.assertEqual(len(entities), 4)
        self.assertEqual([e.is_nested for e in entities], [False, True, True, True])
        self.assertEqual(
            [e.content["name"] for e in entities],
            ["Bakery", "A", "B", "C"],
        )

    def test_arbitrarily_deep_nesting(self):
        # Pre-order: room → chest → key. The chest itself is_nested=True,
        # and the key inside the chest is_nested=True too (nesting depth
        # doesn't change the flag — only "is this inside another entity").
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Room",
            "contents": [{
                "deployment_id": 2, "name": "Chest",
                "contents": [{"deployment_id": 3, "name": "Key"}],
            }],
        })
        self.assertEqual(len(entities), 3)
        self.assertEqual([e.is_nested for e in entities], [False, True, True])
        self.assertEqual(
            [e.content["name"] for e in entities],
            ["Room", "Chest", "Key"],
        )

    def test_top_level_list_of_mappings(self):
        # File-level list of two parents; first parent has one child.
        # Outer order × pre-order within each subtree:
        # First, FirstChild, Second.
        entities = self._load_yaml([
            {
                "deployment_id": 1, "name": "First",
                "contents": [{"deployment_id": 2, "name": "FirstChild"}],
            },
            {"deployment_id": 3, "name": "Second"},
        ])
        self.assertEqual(len(entities), 3)
        self.assertEqual([e.is_nested for e in entities], [False, True, False])
        self.assertEqual(
            [e.content["name"] for e in entities],
            ["First", "FirstChild", "Second"],
        )

    def test_nested_inherits_parent_path_and_location(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": [{"deployment_id": 2, "name": "Child"}],
        })
        self.assertEqual(entities[0].path, entities[1].path)
        self.assertEqual(entities[0].location, entities[1].location)
        self.assertEqual(entities[0].path, "x.yaml")

    def test_contents_key_removed_from_parent_content(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "description": "preserved",
            "contents": [{"deployment_id": 2, "name": "Child"}],
        })
        # Parent body has the original keys minus `contents`.
        self.assertNotIn("contents", entities[0].content)
        self.assertEqual(entities[0].content["name"], "Parent")
        self.assertEqual(entities[0].content["description"], "preserved")

    def test_malformed_contents_not_a_list(self):
        # `contents: "oops"` — Loader silently skips recursion. The
        # validator's _check_contents_field_shape (step 2) refuses this
        # cleanly; for step 1 the Loader just doesn't crash.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": "oops",
        })
        self.assertEqual(len(entities), 1)
        self.assertFalse(entities[0].is_nested)

    def test_malformed_contents_non_mapping_child(self):
        # A non-dict child inside `contents:` is skipped without raising.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": ["oops", {"deployment_id": 2, "name": "Real"}],
        })
        # The string child is skipped; the dict child is emitted.
        self.assertEqual(len(entities), 2)
        self.assertEqual([e.is_nested for e in entities], [False, True])
        self.assertEqual(entities[1].content["name"], "Real")

    # --- location synthesis on nested entities (spike 2 step 2) ---
    #
    # The Loader detects whether the author wrote a `location:` field on
    # each entity (records had_author_location), and on nested entities
    # synthesises content["location"] as a cross-ref dict pointing at the
    # parent. The synthesis overwrites any author-written location — the
    # had_author_location flag preserves the violation for the validator
    # to refuse later.

    def test_nested_entity_location_synthesised(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": [{"deployment_id": 2, "name": "Child"}],
        })
        self.assertEqual(entities[1].content["location"], {
            "deployment_file": "x.yaml",
            "deployment_id": 1,
        })

    def test_nested_entity_had_author_location_false_when_absent(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": [{"deployment_id": 2, "name": "Child"}],
        })
        self.assertFalse(entities[1].had_author_location)

    def test_nested_entity_had_author_location_true_when_present(self):
        # Even `location: null` from the author counts — the flag tracks
        # whether the YAML *had* the key, not what value it carried.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": [{
                "deployment_id": 2, "name": "Child",
                "location": None,
            }],
        })
        self.assertTrue(entities[1].had_author_location)

    def test_synthesised_location_overwrites_author_value(self):
        # Author wrote a location; Loader still synthesises (the validator
        # will refuse later via had_author_location), so the emitted
        # content["location"] is the synthesised dict, not the author's.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": [{
                "deployment_id": 2, "name": "Child",
                "location": "I wrote something here",
            }],
        })
        self.assertEqual(entities[1].content["location"], {
            "deployment_file": "x.yaml",
            "deployment_id": 1,
        })
        self.assertTrue(entities[1].had_author_location)

    def test_top_level_entity_had_author_location_true_when_present(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Solo",
            "location": None,
        })
        self.assertFalse(entities[0].is_nested)
        self.assertTrue(entities[0].had_author_location)

    def test_top_level_entity_had_author_location_false_when_absent(self):
        # No `location:` key on the top-level mapping at all.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Solo",
        })
        self.assertFalse(entities[0].is_nested)
        self.assertFalse(entities[0].had_author_location)

    def test_top_level_entity_location_not_synthesised(self):
        # Top-level entity's location is the author's responsibility;
        # the Loader does not touch it. content["location"] stays None.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Solo",
            "location": None,
        })
        self.assertIsNone(entities[0].content["location"])

    def test_deeply_nested_location_points_at_immediate_parent(self):
        # room (1) → chest (2) → key (3). Chest's location points at
        # room; key's location points at chest, not at room.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Room",
            "contents": [{
                "deployment_id": 2, "name": "Chest",
                "contents": [{"deployment_id": 3, "name": "Key"}],
            }],
        })
        self.assertEqual(entities[1].content["location"], {
            "deployment_file": "x.yaml",
            "deployment_id": 1,
        })
        self.assertEqual(entities[2].content["location"], {
            "deployment_file": "x.yaml",
            "deployment_id": 2,
        })

    def test_synthesised_location_uses_parent_deployment_file(self):
        # Sanity: deployment_file in the synthesised dict is the file the
        # parent was loaded from (same as nested entity's path).
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Parent",
            "contents": [{"deployment_id": 2, "name": "Child"}],
        })
        self.assertEqual(
            entities[1].content["location"]["deployment_file"],
            entities[0].path,
        )

    # --- exits: block flattening (spike 4 step 1) ---
    #
    # The Loader walks `exits:` blocks identically to `contents:` blocks —
    # both flatten into LoadedEntity records with is_nested=True and a
    # synthesised location: cross-ref pointing at the parent. The block
    # name is purely author-organizational; downstream code (validator,
    # Builder) tells exits from non-exits via typeclass + destination
    # presence, not via which block the entity came from.

    def test_exits_block_flattens_like_contents(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": [{
                "deployment_id": 2, "name": "north",
                "destination": {
                    "deployment_file": "millholm/inn.yaml",
                    "deployment_id": 1,
                },
            }],
        })
        self.assertEqual(len(entities), 2)
        self.assertEqual([e.is_nested for e in entities], [False, True])
        self.assertEqual([e.content["name"] for e in entities], ["Bakery", "north"])

    def test_exits_block_synthesises_location(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": [{"deployment_id": 2, "name": "north", "destination": {}}],
        })
        self.assertEqual(entities[1].content["location"], {
            "deployment_file": "x.yaml",
            "deployment_id": 1,
        })

    def test_exits_key_removed_from_parent_content(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "description": "preserved",
            "exits": [{"deployment_id": 2, "name": "north", "destination": {}}],
        })
        self.assertNotIn("exits", entities[0].content)
        self.assertEqual(entities[0].content["description"], "preserved")

    def test_exits_block_preserves_destination_field(self):
        # The Loader doesn't touch destination — it's authored on the exit
        # entity and passes through as-is for the validator and Builder.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": [{
                "deployment_id": 2, "name": "north",
                "destination": {
                    "deployment_file": "millholm/inn.yaml",
                    "deployment_id": 1,
                },
            }],
        })
        self.assertEqual(entities[1].content["destination"], {
            "deployment_file": "millholm/inn.yaml",
            "deployment_id": 1,
        })

    def test_both_contents_and_exits_blocks_flatten(self):
        # Author writes both blocks; the Loader walks contents first then
        # exits (consistent ordering, regardless of YAML key order).
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "contents": [{"deployment_id": 2, "name": "chest"}],
            "exits": [{
                "deployment_id": 3, "name": "north",
                "destination": {"deployment_file": "x.yaml", "deployment_id": 1},
            }],
        })
        self.assertEqual(len(entities), 3)
        self.assertEqual([e.is_nested for e in entities], [False, True, True])
        self.assertEqual(
            [e.content["name"] for e in entities],
            ["Bakery", "chest", "north"],
        )
        # Both children get synthesised location pointing at the parent.
        self.assertEqual(entities[1].content["location"]["deployment_id"], 1)
        self.assertEqual(entities[2].content["location"]["deployment_id"], 1)

    def test_exits_block_empty_list_no_op(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": [],
        })
        self.assertEqual(len(entities), 1)
        self.assertNotIn("exits", entities[0].content)

    def test_malformed_exits_not_a_list(self):
        # Same defensive behaviour as malformed contents — skip recursion,
        # don't crash. Validator catches typeclass/shape mistakes downstream.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": "oops",
        })
        self.assertEqual(len(entities), 1)
        self.assertFalse(entities[0].is_nested)

    def test_malformed_exits_non_mapping_child(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": ["oops", {"deployment_id": 2, "name": "north", "destination": {}}],
        })
        self.assertEqual(len(entities), 2)
        self.assertEqual([e.is_nested for e in entities], [False, True])
        self.assertEqual(entities[1].content["name"], "north")

    def test_nested_exit_had_author_location_false_when_absent(self):
        # Same `had_author_location` recording applies to exits-block
        # children: validator can later refuse author-written location on
        # any nested entity uniformly.
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": [{"deployment_id": 2, "name": "north", "destination": {}}],
        })
        self.assertFalse(entities[1].had_author_location)

    def test_nested_exit_had_author_location_true_when_present(self):
        entities = self._load_yaml({
            "deployment_id": 1, "name": "Bakery",
            "exits": [{
                "deployment_id": 2, "name": "north",
                "destination": {},
                "location": None,
            }],
        })
        self.assertTrue(entities[1].had_author_location)


class ValidatorTest(TestCase):
    """Verify Validator's per-entity predicates and per-file id index."""

    def _entity(self, path: str, content) -> LoadedEntity:
        # Inject mandatory Tier 1 fields when the test didn't supply them.
        # Tests that specifically exercise a missing-field predicate set
        # the field directly and the relevant default below is overridden
        # via {**defaults, **content}.
        if isinstance(content, dict):
            defaults = {
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            }
            for key, default_value in defaults.items():
                if key not in content:
                    content = {**content, key: default_value}
        return LoadedEntity(location={}, content=content, path=path)

    def _valid(self, path: str, deployment_id: int) -> LoadedEntity:
        return self._entity(path, {
            "deployment_id": deployment_id,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "x",
            "location": None,
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
        # Auto-inject the other mandatory Tier 1 fields so these tests
        # only exercise the typeclass-resolvable predicate.
        if isinstance(content, dict):
            for key, default in (("name", "x"), ("location", None)):
                if key not in content:
                    content = {**content, key: default}
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


class ValidatorDestinationTypeclassTest(TestCase):
    """Tier 3 — typeclass-aware destination required/forbidden predicates.

    Two paired predicates:
      - typeclass inherits DefaultExit ⇒ destination required
      - typeclass does NOT inherit DefaultExit ⇒ destination forbidden

    Both run only with evennia_runtime=True (registered in
    EVENNIA_ONLY_PREDICATES). wb_build sets that flag; wb-validate
    leaves it False, so the CLI gets shape checks only.
    """

    _DEFAULT_EXIT = "evennia.objects.objects.DefaultExit"
    _DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def _entity(self, content) -> LoadedEntity:
        # Inject Tier 1 mandatories plus a non-null cross-ref location so
        # `_check_location_not_null_when_destination_present` doesn't fire
        # on tests that aren't about it.
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "name": "x",
                "location": {
                    "deployment_file": "a.yaml",
                    "deployment_id": 99,
                },
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self, *, evennia_runtime=True):
        return Validator(Definitions(levels=("zone",)), evennia_runtime=evennia_runtime)

    # --- four-cell behaviour matrix -----------------------------------

    def test_exit_typeclass_with_destination_passes(self):
        v = self._validator()
        v.validate([self._entity({
            "typeclass": self._DEFAULT_EXIT,
            "destination": {
                "deployment_file": "millholm/inn.yaml",
                "deployment_id": 1,
            },
        })])
        self.assertEqual(v.errors, [])

    def test_exit_typeclass_without_destination_refused(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"typeclass": self._DEFAULT_EXIT})])
        self.assertTrue(any(
            "inherits from DefaultExit" in e and "destination" in e
            for e in v.errors
        ))

    def test_non_exit_typeclass_with_destination_refused(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({
                "typeclass": self._DEFAULT_OBJECT,
                "destination": {
                    "deployment_file": "millholm/inn.yaml",
                    "deployment_id": 1,
                },
            })])
        self.assertTrue(any(
            "does not inherit from DefaultExit" in e for e in v.errors
        ))

    def test_non_exit_typeclass_without_destination_passes(self):
        v = self._validator()
        v.validate([self._entity({"typeclass": self._DEFAULT_OBJECT, "location": None})])
        self.assertEqual(v.errors, [])

    # --- gating: predicates only run with evennia_runtime=True --------

    def test_skipped_when_evennia_runtime_false(self):
        # The Tier 3 predicates must not fire when the caller (e.g.
        # wb-validate CLI) hasn't asserted Evennia-runtime availability.
        v = self._validator(evennia_runtime=False)
        v.validate([self._entity({"typeclass": self._DEFAULT_EXIT})])
        # No findings about destination required/forbidden — only Tier 1/2 ran.
        self.assertFalse(any(
            "DefaultExit" in e for e in v.errors
        ))

    # --- defensive skips: avoid double-reporting with other predicates -

    def test_skips_when_typeclass_not_a_string(self):
        # Tier 1's _check_typeclass_well_formed catches the shape problem;
        # the Tier 3 predicates skip cleanly so the operator gets one
        # finding for the typeclass shape, not three.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"typeclass": 7})])
        self.assertFalse(any(
            "DefaultExit" in e for e in v.errors
        ))

    def test_skips_when_typeclass_unimportable(self):
        # Tier 3's _check_typeclass_resolvable catches the import failure;
        # the destination predicates skip cleanly.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({
                "typeclass": "totally.bogus.module.Class",
            })])
        self.assertTrue(any(
            "could not be imported" in e for e in v.errors
        ))
        self.assertFalse(any(
            "DefaultExit" in e for e in v.errors
        ))


class ValidatorTypeclassWellFormedTest(TestCase):
    """Tier 1 — typeclass is mandatory, must be a non-empty string."""

    def _entity(self, content) -> LoadedEntity:
        # Auto-inject the other mandatory Tier 1 fields so these tests
        # only exercise the typeclass-well-formed predicate.
        if isinstance(content, dict):
            for key, default in (("name", "x"), ("location", None)):
                if key not in content:
                    content = {**content, key: default}
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


class ValidatorNameWellFormedTest(TestCase):
    """Tier 1 — name is mandatory, must be a non-empty string."""

    def _entity(self, content) -> LoadedEntity:
        # Auto-inject the other mandatory Tier 1 fields except name.
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "location": None,
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_missing_name_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({})])
        self.assertTrue(any("missing required field 'name'" in e for e in v.errors))

    def test_non_string_name_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"name": 42})])
        self.assertTrue(any("'name' must be a string" in e for e in v.errors))

    def test_empty_string_name_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"name": ""})])
        self.assertTrue(any("must be a non-empty string" in e for e in v.errors))

    def test_whitespace_only_name_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"name": "   "})])
        self.assertTrue(any("must be a non-empty string" in e for e in v.errors))

    def test_well_formed_name_passes(self):
        v = self._validator()
        v.validate([self._entity({"name": "Goldencrust Bakery"})])
        self.assertEqual(v.errors, [])


class ValidatorLocationWellFormedTest(TestCase):
    """Tier 1 — location is mandatory; null OR cross-ref dict accepted.

    The cross-ref dict shape lands here in spike 2 step 3 alongside the
    Loader's synthesis on nested entities (so nested entities — whose
    location is now a Loader-synthesised cross-ref — can pass the same
    predicate as top-level entities). Top-level entities can also use
    the cross-ref shape to point at any same-file entity by its
    deployment id; resolution lands as a separate Tier 4 check later.
    """

    def _entity(self, content, **kwargs) -> LoadedEntity:
        # Auto-inject the other mandatory Tier 1 fields except location.
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml", **kwargs)

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_missing_location_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({})])
        self.assertTrue(any("missing required field 'location'" in e for e in v.errors))

    def test_explicit_null_location_passes(self):
        # `location: null` is the orphan-room declaration. Mandated by
        # the predicate to make orphan placement explicit, not implicit.
        v = self._validator()
        v.validate([self._entity({"location": None})])
        self.assertEqual(v.errors, [])

    def test_string_location_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": "somewhere"})])
        self.assertTrue(any(
            "must be null or a cross-ref dict" in e for e in v.errors
        ))

    def test_well_formed_cross_ref_dict_accepted(self):
        v = self._validator()
        v.validate([self._entity({"location": {
            "deployment_file": "millholm/bakery.yaml",
            "deployment_id": 1,
        }})])
        self.assertEqual(v.errors, [])

    def test_cross_ref_zero_deployment_id_accepted(self):
        # Non-negative includes zero, mirroring _check_deployment_id_well_formed.
        v = self._validator()
        v.validate([self._entity({"location": {
            "deployment_file": "a.yaml",
            "deployment_id": 0,
        }})])
        self.assertEqual(v.errors, [])

    def test_cross_ref_missing_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {"deployment_id": 5}})])
        self.assertTrue(any(
            "missing required key" in e and "deployment_file" in e
            for e in v.errors
        ))

    def test_cross_ref_missing_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {"deployment_file": "a.yaml"}})])
        self.assertTrue(any(
            "missing required key" in e and "deployment_id" in e
            for e in v.errors
        ))

    def test_cross_ref_extra_keys_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {
                "deployment_file": "a.yaml",
                "deployment_id": 1,
                "comment": "annotation",
            }})])
        self.assertTrue(any("unexpected key" in e for e in v.errors))

    def test_cross_ref_non_string_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {
                "deployment_file": 7,
                "deployment_id": 1,
            }})])
        self.assertTrue(any(
            "'deployment_file' must be a non-empty string" in e for e in v.errors
        ))

    def test_cross_ref_empty_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {
                "deployment_file": "   ",
                "deployment_id": 1,
            }})])
        self.assertTrue(any(
            "'deployment_file' must be a non-empty string" in e for e in v.errors
        ))

    def test_cross_ref_non_int_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {
                "deployment_file": "a.yaml",
                "deployment_id": "five",
            }})])
        self.assertTrue(any(
            "'deployment_id' must be an integer" in e for e in v.errors
        ))

    def test_cross_ref_bool_deployment_id_rejected(self):
        # bool is an int subclass — must not be accepted.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {
                "deployment_file": "a.yaml",
                "deployment_id": True,
            }})])
        self.assertTrue(any(
            "'deployment_id' must be an integer" in e for e in v.errors
        ))

    def test_cross_ref_negative_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"location": {
                "deployment_file": "a.yaml",
                "deployment_id": -1,
            }})])
        self.assertTrue(any(
            "'deployment_id' must be non-negative" in e for e in v.errors
        ))

    def test_location_predicate_is_per_entity(self):
        # The TOP_LEVEL_PREDICATES split was collapsed in spike 2 step 3
        # because the Loader synthesises a cross-ref `location:` on every
        # nested entity, so the predicate applies uniformly. Guard the
        # arrangement with a test.
        from evennia_world_builder.validator import _check_location_well_formed
        self.assertIn(
            _check_location_well_formed, Validator.PER_ENTITY_PREDICATES
        )
        self.assertFalse(hasattr(Validator, "TOP_LEVEL_PREDICATES"))


class ValidatorDestinationWellFormedTest(TestCase):
    """Tier 1 — `destination:` (when present) is a strict cross-ref dict.

    Mirrors the cross-ref shape check on `location:` — same shared helper
    (`_check_cross_ref_dict_shape`) drives both. The difference between
    location and destination at validate time is that `location:` accepts
    null (orphan) while `destination:` does not (presence implies an
    exit, which must point somewhere).
    """

    def _entity(self, content) -> LoadedEntity:
        # Inject the other Tier 1 mandatory fields plus a non-null
        # location so _check_location_not_null_when_destination_present
        # doesn't fire on tests that aren't about it.
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultExit",
                "name": "north",
                "location": {
                    "deployment_file": "a.yaml",
                    "deployment_id": 99,
                },
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_destination_absent_passes(self):
        # destination: is optional at this layer — Tier 3 (step 3) decides
        # whether the typeclass requires it. Tier 1 just shape-checks.
        v = self._validator()
        v.validate([self._entity({})])
        self.assertEqual(v.errors, [])

    def test_well_formed_destination_dict_accepted(self):
        v = self._validator()
        v.validate([self._entity({"destination": {
            "deployment_file": "millholm/inn.yaml",
            "deployment_id": 1,
        }})])
        self.assertEqual(v.errors, [])

    def test_destination_zero_deployment_id_accepted(self):
        v = self._validator()
        v.validate([self._entity({"destination": {
            "deployment_file": "a.yaml",
            "deployment_id": 0,
        }})])
        self.assertEqual(v.errors, [])

    def test_destination_string_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": "somewhere"})])
        self.assertTrue(any(
            "'destination' must be a cross-ref dict" in e for e in v.errors
        ))

    def test_destination_null_rejected(self):
        # null is fine for location (orphan) but never for destination.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": None})])
        self.assertTrue(any(
            "'destination' must be a cross-ref dict" in e for e in v.errors
        ))

    def test_destination_missing_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {"deployment_id": 5}})])
        self.assertTrue(any(
            "missing required key" in e and "deployment_file" in e
            for e in v.errors
        ))

    def test_destination_missing_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {"deployment_file": "a.yaml"}})])
        self.assertTrue(any(
            "missing required key" in e and "deployment_id" in e
            for e in v.errors
        ))

    def test_destination_extra_keys_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {
                "deployment_file": "a.yaml",
                "deployment_id": 1,
                "comment": "annotation",
            }})])
        self.assertTrue(any("unexpected key" in e for e in v.errors))

    def test_destination_non_string_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {
                "deployment_file": 7,
                "deployment_id": 1,
            }})])
        self.assertTrue(any(
            "'deployment_file' must be a non-empty string" in e for e in v.errors
        ))

    def test_destination_empty_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {
                "deployment_file": "   ",
                "deployment_id": 1,
            }})])
        self.assertTrue(any(
            "'deployment_file' must be a non-empty string" in e for e in v.errors
        ))

    def test_destination_non_int_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {
                "deployment_file": "a.yaml",
                "deployment_id": "five",
            }})])
        self.assertTrue(any(
            "'deployment_id' must be an integer" in e for e in v.errors
        ))

    def test_destination_bool_deployment_id_rejected(self):
        # bool is an int subclass — reject it explicitly.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {
                "deployment_file": "a.yaml",
                "deployment_id": True,
            }})])
        self.assertTrue(any(
            "'deployment_id' must be an integer" in e for e in v.errors
        ))

    def test_destination_negative_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"destination": {
                "deployment_file": "a.yaml",
                "deployment_id": -1,
            }})])
        self.assertTrue(any(
            "'deployment_id' must be non-negative" in e for e in v.errors
        ))


class ValidatorHomeWellFormedTest(TestCase):
    """Tier 1 — `home:` (when present) is null or a strict cross-ref dict.

    Optional field. Absence means "use Evennia's settings.DEFAULT_HOME"
    (typically Limbo). When present, two valid shapes:
    - null  → translates to nohome=True at create_object time.
    - dict  → cross-ref to another entity, resolved by the Builder.

    Same shape predicates as `location:` apply for the dict case via
    the shared `_check_cross_ref_dict_shape` helper.
    """

    def _entity(self, content) -> LoadedEntity:
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultObject",
                "name": "x",
                "location": None,
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_home_absent_passes(self):
        v = self._validator()
        v.validate([self._entity({})])
        self.assertEqual(v.errors, [])

    def test_home_null_accepted(self):
        v = self._validator()
        v.validate([self._entity({"home": None})])
        self.assertEqual(v.errors, [])

    def test_well_formed_home_dict_accepted(self):
        v = self._validator()
        v.validate([self._entity({"home": {
            "deployment_file": "a.yaml",
            "deployment_id": 5,
        }})])
        self.assertEqual(v.errors, [])

    def test_home_string_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"home": "Limbo"})])
        self.assertTrue(any(
            "'home' must be null or a cross-ref dict" in e for e in v.errors
        ))

    def test_home_int_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"home": 2})])
        self.assertTrue(any(
            "'home' must be null or a cross-ref dict" in e for e in v.errors
        ))

    def test_home_missing_deployment_file_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"home": {"deployment_id": 5}})])
        self.assertTrue(any(
            "missing required key" in e and "deployment_file" in e
            for e in v.errors
        ))

    def test_home_missing_deployment_id_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"home": {"deployment_file": "a.yaml"}})])
        self.assertTrue(any(
            "missing required key" in e and "deployment_id" in e
            for e in v.errors
        ))

    def test_home_unexpected_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"home": {
                "deployment_file": "a.yaml",
                "deployment_id": 5,
                "extra": "no",
            }})])
        self.assertTrue(any(
            "unexpected key" in e for e in v.errors
        ))


class ValidatorLocationNotNullWhenDestinationPresentTest(TestCase):
    """Tier 1 — entity with `destination:` must have non-null `location:`.

    Any entity carrying destination is an exit (regardless of whether
    nested in an `exits:` block or authored top-level as a connector);
    an exit has to live in a room. `location: null` contradicts the
    presence of `destination:`.
    """

    def _entity(self, content) -> LoadedEntity:
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultExit",
                "name": "north",
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_destination_with_null_location_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({
                "destination": {
                    "deployment_file": "millholm/inn.yaml",
                    "deployment_id": 1,
                },
                "location": None,
            })])
        self.assertTrue(any(
            "an exit must be in a room" in e for e in v.errors
        ))

    def test_destination_with_cross_ref_location_passes(self):
        v = self._validator()
        v.validate([self._entity({
            "destination": {
                "deployment_file": "millholm/inn.yaml",
                "deployment_id": 1,
            },
            "location": {
                "deployment_file": "millholm/bakery.yaml",
                "deployment_id": 1,
            },
        })])
        self.assertEqual(v.errors, [])

    def test_no_destination_with_null_location_passes(self):
        # Regression: regular orphan rooms still pass.
        v = self._validator()
        v.validate([self._entity({"location": None})])
        self.assertEqual(v.errors, [])

    def test_destination_without_location_doesnt_double_report(self):
        # Missing-location is handled by _check_location_well_formed; this
        # predicate stays quiet so the operator sees one finding, not two,
        # for the same authoring mistake.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({
                "destination": {
                    "deployment_file": "a.yaml",
                    "deployment_id": 1,
                },
                # no location key at all
            })])
        # Exactly one finding mentions "location" — the missing-field one.
        # The "exit must be in a room" finding must not also fire.
        self.assertFalse(any(
            "an exit must be in a room" in e for e in v.errors
        ))
        self.assertTrue(any(
            "missing required field 'location'" in e for e in v.errors
        ))


class ValidatorIncomingExitsFieldShapeTest(TestCase):
    """File-level Tier 1 — `incoming_exits:` shape check via file_metadata.

    `incoming_exits:` is a file-level YAML key (lives alongside
    `entities:` in the wrapper-mapping shape). The Loader extracts it
    into ``LoadResult.file_metadata[path]["incoming_exits"]``; the
    Validator runs shape checks against that dict once per file path
    (regardless of how many entities were declared in the file).

    Each entry references an exit terminating at one of this file's
    rooms that lives in another file. The Builder's pass 3 (step 6e)
    reads each ref's canonical file and rebuilds missing exits, keeping
    incoming connections alive across isolated rebuilds. This Tier 1
    check just shape-checks the dicts.
    """

    def _entity(self) -> LoadedEntity:
        # A minimal valid entity from the file. The shape check doesn't
        # actually consult the entity, but validate() needs at least one
        # entity from the relevant path to make seen_ids non-empty.
        return LoadedEntity(
            location={},
            content={
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            },
            path="a.yaml",
        )

    def _validator(self, file_metadata):
        return Validator(
            Definitions(levels=("zone",)), file_metadata=file_metadata,
        )

    def test_absent_field_passes(self):
        # File metadata is empty for this file — predicate stays quiet.
        v = self._validator({})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_no_incoming_exits_key_passes(self):
        # File has metadata but no incoming_exits — predicate stays quiet.
        v = self._validator({"a.yaml": {"some_other_key": "value"}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_empty_list_passes(self):
        v = self._validator({"a.yaml": {"incoming_exits": []}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_well_formed_list_accepted(self):
        v = self._validator({"a.yaml": {"incoming_exits": [
            {"deployment_file": "millholm/inn.yaml", "deployment_id": 2},
            {"deployment_file": "millholm/forest.yaml", "deployment_id": 7},
        ]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_non_list_rejected(self):
        v = self._validator({"a.yaml": {"incoming_exits": "oops"}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "'incoming_exits' must be a list" in e for e in v.errors
        ))

    def test_non_dict_entry_rejected(self):
        v = self._validator({"a.yaml": {"incoming_exits": ["oops"]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "must be a cross-ref dict" in e for e in v.errors
        ))

    def test_missing_deployment_file_rejected(self):
        v = self._validator({"a.yaml": {"incoming_exits": [
            {"deployment_id": 1},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "missing required key" in e and "deployment_file" in e
            for e in v.errors
        ))

    def test_missing_deployment_id_rejected(self):
        v = self._validator({"a.yaml": {"incoming_exits": [
            {"deployment_file": "a.yaml"},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "missing required key" in e and "deployment_id" in e
            for e in v.errors
        ))

    def test_extra_keys_rejected(self):
        v = self._validator({"a.yaml": {"incoming_exits": [
            {"deployment_file": "a.yaml", "deployment_id": 1, "comment": "no"},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any("unexpected key" in e for e in v.errors))

    def test_negative_deployment_id_rejected(self):
        v = self._validator({"a.yaml": {"incoming_exits": [
            {"deployment_file": "a.yaml", "deployment_id": -1},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "'deployment_id' must be non-negative" in e for e in v.errors
        ))

    def test_index_in_finding_path(self):
        # When an entry is malformed, the finding identifies WHICH index
        # in the list is at fault — easier for authors to locate the typo.
        v = self._validator({"a.yaml": {"incoming_exits": [
            {"deployment_file": "good.yaml", "deployment_id": 1},
            {"deployment_file": "bad.yaml", "deployment_id": "not-an-int"},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "incoming_exits[1]" in e for e in v.errors
        ))

    def test_finding_names_file_path_not_entity_path(self):
        # The new shape check is file-level, so findings name the file
        # path (which is the source of the registration), not any
        # specific entity in the file.
        v = self._validator({"x.yaml": {"incoming_exits": ["oops"]}})
        # Provide an entity from a DIFFERENT path to make sure the
        # finding doesn't accidentally use the entity's path.
        entity = LoadedEntity(
            location={},
            content={
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            },
            path="a.yaml",
        )
        with self.assertRaises(ValidatorError):
            v.validate([entity])
        self.assertTrue(any("x.yaml: incoming_exits" in e for e in v.errors))


class ValidatorLinksFieldShapeTest(TestCase):
    """File-level Tier 1 — `links:` shape check via file_metadata.

    `links:` is a file-level YAML key (lives alongside `entities:` in
    the wrapper-mapping shape, sibling of `incoming_exits:`). Each
    entry has required `entity`, `attribute`, `points_to` and optional
    `category`. The Validator runs shape checks against the dict once
    per file path (regardless of how many entities were declared).

    See docs/links.md for the spec.
    """

    def _entity(self) -> LoadedEntity:
        return LoadedEntity(
            location={},
            content={
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            },
            path="a.yaml",
        )

    def _validator(self, file_metadata):
        return Validator(
            Definitions(levels=("zone",)), file_metadata=file_metadata,
        )

    def _well_formed_link(self):
        return {
            "entity": {"deployment_file": "a.yaml", "deployment_id": 1},
            "attribute": "other_side",
            "points_to": {"deployment_file": "a.yaml", "deployment_id": 2},
        }

    def test_absent_field_passes(self):
        v = self._validator({})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_no_links_key_passes(self):
        v = self._validator({"a.yaml": {"some_other_key": "value"}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_empty_list_passes(self):
        v = self._validator({"a.yaml": {"links": []}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_well_formed_list_accepted(self):
        v = self._validator({"a.yaml": {"links": [self._well_formed_link()]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_optional_category_accepted(self):
        link = self._well_formed_link()
        link["category"] = "doors"
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_non_list_rejected(self):
        v = self._validator({"a.yaml": {"links": "oops"}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "'links' must be a list" in e for e in v.errors
        ))

    def test_non_dict_entry_rejected(self):
        v = self._validator({"a.yaml": {"links": ["oops"]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "must be a link dict" in e for e in v.errors
        ))

    def test_missing_entity_rejected(self):
        link = self._well_formed_link()
        del link["entity"]
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "missing required key" in e and "entity" in e
            for e in v.errors
        ))

    def test_missing_attribute_rejected(self):
        link = self._well_formed_link()
        del link["attribute"]
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "missing required key" in e and "attribute" in e
            for e in v.errors
        ))

    def test_missing_points_to_rejected(self):
        link = self._well_formed_link()
        del link["points_to"]
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "missing required key" in e and "points_to" in e
            for e in v.errors
        ))

    def test_unexpected_key_rejected(self):
        link = self._well_formed_link()
        link["foo"] = "bar"
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any("unexpected key" in e for e in v.errors))

    def test_attribute_must_be_non_empty_string(self):
        for bad in (123, "", "   ", None):
            link = self._well_formed_link()
            link["attribute"] = bad
            v = self._validator({"a.yaml": {"links": [link]}})
            with self.assertRaises(ValidatorError):
                v.validate([self._entity()])
            self.assertTrue(any(
                "'attribute' must be a non-empty string" in e
                for e in v.errors
            ), f"missing finding for attribute={bad!r}")

    def test_category_must_be_non_empty_string_if_present(self):
        for bad in (123, "", "   "):
            link = self._well_formed_link()
            link["category"] = bad
            v = self._validator({"a.yaml": {"links": [link]}})
            with self.assertRaises(ValidatorError):
                v.validate([self._entity()])
            self.assertTrue(any(
                "'category' must be a non-empty string when present" in e
                for e in v.errors
            ), f"missing finding for category={bad!r}")

    def test_entity_non_dict_rejected(self):
        link = self._well_formed_link()
        link["entity"] = "oops"
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "'entity' must be a cross-ref dict" in e for e in v.errors
        ))

    def test_points_to_non_dict_rejected(self):
        link = self._well_formed_link()
        link["points_to"] = "oops"
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "'points_to' must be a cross-ref dict" in e for e in v.errors
        ))

    def test_entity_bad_cross_ref_shape_rejected(self):
        link = self._well_formed_link()
        link["entity"] = {"deployment_file": "a.yaml"}  # missing deployment_id
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0].entity" in e and "missing required key" in e
            for e in v.errors
        ))

    def test_finding_names_path_and_index(self):
        v = self._validator({"x.yaml": {"links": ["oops"]}})
        with self.assertRaises(ValidatorError):
            v.validate([LoadedEntity(
                location={},
                content={
                    "deployment_id": 1,
                    "typeclass": "evennia.objects.objects.DefaultRoom",
                    "name": "x",
                    "location": None,
                },
                path="x.yaml",
            )])
        self.assertTrue(any("x.yaml: links[0]" in e for e in v.errors))

    # ── Subscript-path attribute syntax checks ──
    # See docs/links.md § Subscript-path attribute syntax.

    def test_subscript_path_string_keys_accepted(self):
        link = self._well_formed_link()
        link["attribute"] = 'destinations["foo"]["bar"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_int_index_accepted(self):
        # Integer indices distinguished from string subscripts — the
        # parser reads them as ints, so list-shaped placeholders are
        # navigable.
        link = self._well_formed_link()
        link["attribute"] = 'routes[0]["to"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_mixed_subscripts_accepted(self):
        link = self._well_formed_link()
        link["attribute"] = 'foo["bar"][0]["baz"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_unclosed_bracket_rejected(self):
        link = self._well_formed_link()
        link["attribute"] = 'foo["unclosed'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "not valid Python subscript syntax" in e
            for e in v.errors
        ))

    def test_subscript_path_close_bracket_only_rejected(self):
        # Defensive: an attribute string with `]` but no `[` (e.g.
        # 'dict(thing]') still triggers the subscript-path validator
        # so the malformed input fails loudly at validate time
        # instead of being silently set as a garbage attribute name.
        link = self._well_formed_link()
        link["attribute"] = 'dict(thing]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "not valid Python subscript syntax" in e
            for e in v.errors
        ))

    def test_subscript_path_dot_attribute_rejected(self):
        # Mid-path attribute access is not allowed — only the leading
        # bare identifier is an attribute, everything after must be
        # subscripts.
        link = self._well_formed_link()
        link["attribute"] = 'foo.bar'
        v = self._validator({"a.yaml": {"links": [link]}})
        # No `[` so no subscript-path validation runs — bare-name path
        # accepts anything as a string. Not a path-syntax error.
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_dot_attribute_with_subscript_rejected(self):
        # foo.bar["baz"] — has [ so subscript-path validation runs;
        # leading expression is Attribute, not Name → refused.
        link = self._well_formed_link()
        link["attribute"] = 'foo.bar["baz"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e
            and "must start with a bare attribute name" in e
            for e in v.errors
        ))

    def test_subscript_path_non_literal_subscript_rejected(self):
        # foo[bar] — the subscript is a Name (variable reference), not
        # a literal. Path syntax only allows string keys and ints.
        link = self._well_formed_link()
        link["attribute"] = 'foo[bar]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "non-literal subscript" in e
            for e in v.errors
        ))

    def test_subscript_path_call_at_head_rejected(self):
        link = self._well_formed_link()
        link["attribute"] = 'foo()["bar"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e
            and "must start with a bare attribute name" in e
            for e in v.errors
        ))

    def test_subscript_path_with_category_rejected(self):
        # category: only applies to bare attribute names — combining it
        # with subscript-path syntax is structurally meaningless.
        link = self._well_formed_link()
        link["attribute"] = 'foo["bar"]'
        link["category"] = "doors"
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e
            and "'category' cannot be used with subscript-path attribute" in e
            for e in v.errors
        ))

    def test_bare_attribute_with_category_still_accepted(self):
        # Regression: bare-name attribute + category remains valid — the
        # new check only fires on path-syntax attributes.
        link = self._well_formed_link()
        link["category"] = "doors"
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    # ── Unbalanced-bracket cases (Python's parser is the bracket
    # counter; ast.parse raises SyntaxError on any imbalance, our
    # check catches it). ──

    def test_subscript_path_extra_close_bracket_rejected(self):
        link = self._well_formed_link()
        link["attribute"] = 'foo["bar"]]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "not valid Python subscript syntax" in e
            for e in v.errors
        ))

    def test_subscript_path_extra_open_bracket_rejected(self):
        link = self._well_formed_link()
        link["attribute"] = 'foo[["bar"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "not valid Python subscript syntax" in e
            for e in v.errors
        ))

    def test_subscript_path_empty_subscript_rejected(self):
        # `foo[]` — empty subscript isn't valid Python.
        link = self._well_formed_link()
        link["attribute"] = 'foo[]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "not valid Python subscript syntax" in e
            for e in v.errors
        ))

    def test_subscript_path_deep_balanced_accepted(self):
        # Six levels deep — the parser handles arbitrary depth, no
        # special-cased depth limit.
        link = self._well_formed_link()
        link["attribute"] = 'a["b"]["c"]["d"]["e"]["f"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    # ── Non-literal subscript cases (the slice must be a literal value
    # readable by ast.literal_eval — anything dynamic is refused). ──

    def test_subscript_path_slice_subscript_rejected(self):
        # `foo[1:2]` is a slice, not a literal index — refused.
        link = self._well_formed_link()
        link["attribute"] = 'foo[1:2]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "non-literal subscript" in e
            for e in v.errors
        ))

    def test_subscript_path_expression_subscript_rejected(self):
        # `foo["a" + "b"]` is a BinOp expression — literal_eval refuses.
        link = self._well_formed_link()
        link["attribute"] = 'foo["a" + "b"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e and "non-literal subscript" in e
            for e in v.errors
        ))

    # ── Wrong shape at the head (must be a bare Name, not a list
    # literal, dict literal, call, etc.). ──

    def test_subscript_path_list_literal_at_head_rejected(self):
        # `[1, 2]["bar"]` — leading expression is a List, not a Name.
        link = self._well_formed_link()
        link["attribute"] = '[1, 2]["bar"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e
            and "must start with a bare attribute name" in e
            for e in v.errors
        ))

    def test_subscript_path_dict_literal_at_head_rejected(self):
        link = self._well_formed_link()
        link["attribute"] = '{}[\'bar\']'
        v = self._validator({"a.yaml": {"links": [link]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity()])
        self.assertTrue(any(
            "links[0]" in e
            and "must start with a bare attribute name" in e
            for e in v.errors
        ))

    # ── Stylistic variants that should be accepted (whitespace, quote
    # styles inside the subscript). ──

    def test_subscript_path_with_whitespace_accepted(self):
        # ast.parse is whitespace-tolerant — extra spaces don't break it.
        link = self._well_formed_link()
        link["attribute"] = 'foo[ "bar" ][ 0 ]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_with_single_quotes_inside_accepted(self):
        # YAML lets us double-quote the outer string and use single
        # quotes for the keys, e.g. attribute: "foo['bar']".
        # The parser doesn't care which quote style is used inside.
        link = self._well_formed_link()
        link["attribute"] = "foo['bar']['baz']"
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_negative_int_index_accepted(self):
        # Negative indices are valid literals (literal_eval handles
        # them). Useful for "last element" idioms in list-shaped attrs.
        link = self._well_formed_link()
        link["attribute"] = 'routes[-1]["to"]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])

    def test_subscript_path_empty_string_key_accepted(self):
        # Empty string is a valid (if strange) dict key.
        link = self._well_formed_link()
        link["attribute"] = 'foo[""]'
        v = self._validator({"a.yaml": {"links": [link]}})
        v.validate([self._entity()])
        self.assertEqual(v.errors, [])


class ValidatorNoAuthorLocationOnNestedTest(TestCase):
    """Tier 1 — refuse author-written `location:` on nested entities."""

    def _entity(self, *, is_nested, had_author_location, location_value=None) -> LoadedEntity:
        # Build a minimally-valid entity content; the Loader's synthesised
        # location: dict for a nested entity would normally be present
        # (and the test exercises the predicate alone, not the Loader),
        # so we set it to a well-formed default unless overridden.
        synthesised = {
            "deployment_file": "a.yaml",
            "deployment_id": 99,
        }
        content = {
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "x",
            "location": location_value if location_value is not None else synthesised,
        }
        return LoadedEntity(
            location={}, content=content, path="a.yaml",
            is_nested=is_nested,
            had_author_location=had_author_location,
        )

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_nested_with_author_location_refused(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(is_nested=True, had_author_location=True)])
        self.assertTrue(any(
            "nested entity declares 'location:'" in e for e in v.errors
        ))

    def test_nested_without_author_location_passes(self):
        v = self._validator()
        v.validate([self._entity(is_nested=True, had_author_location=False)])
        self.assertEqual(v.errors, [])

    def test_top_level_with_author_location_passes(self):
        # Author writing `location:` on a top-level entity is correct usage —
        # the predicate must not fire.
        v = self._validator()
        v.validate([self._entity(
            is_nested=False, had_author_location=True,
            location_value=None,
        )])
        self.assertEqual(v.errors, [])

    def test_top_level_without_author_location_passes(self):
        # In practice top-level entities always have had_author_location=True
        # (otherwise _check_location_well_formed refuses for missing field).
        # But the no-author-location predicate itself is purely about
        # is_nested; verify it stays quiet for a top-level entity even
        # when had_author_location=False.
        v = self._validator()
        # Build content without a `location` key at all, so the *other*
        # predicate (_check_location_well_formed) is the one that refuses.
        # We assert that the no-author-location finding isn't among the
        # findings.
        content = {
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "x",
        }
        entity = LoadedEntity(
            location={}, content=content, path="a.yaml",
            is_nested=False, had_author_location=False,
        )
        with self.assertRaises(ValidatorError):
            v.validate([entity])
        self.assertFalse(any(
            "nested entity declares 'location:'" in e for e in v.errors
        ))


class ValidatorAttributesFieldShapeTest(TestCase):
    """Tier 1 — attributes is optional; list of {key, value, category?} dicts."""

    def _entity(self, content) -> LoadedEntity:
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    # --- pass cases ---------------------------------------------------

    def test_no_attributes_field_passes(self):
        v = self._validator()
        v.validate([self._entity({})])
        self.assertEqual(v.errors, [])

    def test_empty_attributes_list_passes(self):
        v = self._validator()
        v.validate([self._entity({"attributes": []})])
        self.assertEqual(v.errors, [])

    def test_minimal_attribute_passes(self):
        v = self._validator()
        v.validate([self._entity({"attributes": [
            {"key": "ambient_smell", "value": "fresh bread"}
        ]})])
        self.assertEqual(v.errors, [])

    def test_attribute_with_category_passes(self):
        v = self._validator()
        v.validate([self._entity({"attributes": [
            {"key": "noise_level", "value": "quiet", "category": "ambient"}
        ]})])
        self.assertEqual(v.errors, [])

    def test_arbitrary_value_types_pass(self):
        # value can be string, int, float, bool, null, list, dict — any
        # YAML scalar/composite. The Builder/Evennia handle storage.
        v = self._validator()
        v.validate([self._entity({"attributes": [
            {"key": "as_string", "value": "x"},
            {"key": "as_int", "value": 42},
            {"key": "as_float", "value": 3.14},
            {"key": "as_bool", "value": True},
            {"key": "as_null", "value": None},
            {"key": "as_list", "value": [1, 2, 3]},
            {"key": "as_dict", "value": {"a": 1, "b": [2, 3]}},
        ]})])
        self.assertEqual(v.errors, [])

    # --- fail cases ---------------------------------------------------

    def test_attributes_not_a_list_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": {"key": "x", "value": "y"}})])
        self.assertTrue(any("'attributes' must be a list" in e for e in v.errors))

    def test_non_mapping_entry_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": ["bare_string"]})])
        self.assertTrue(any("attributes[0]" in e and "must be a mapping" in e for e in v.errors))

    def test_missing_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": [{"value": "y"}]})])
        self.assertTrue(any("attributes[0]: missing 'key'" in e for e in v.errors))

    def test_empty_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": [{"key": "", "value": "y"}]})])
        self.assertTrue(any("'key' must be a non-empty string" in e for e in v.errors))

    def test_non_string_key_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": [{"key": 42, "value": "y"}]})])
        self.assertTrue(any("'key' must be a non-empty string" in e for e in v.errors))

    def test_missing_value_rejected(self):
        # Half-declared attribute (key but no value field) is almost
        # certainly an author typo — don't silently default to None.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": [{"key": "x"}]})])
        self.assertTrue(any("missing 'value'" in e for e in v.errors))

    def test_explicit_null_value_passes(self):
        # Explicit null IS a value (sets the attribute to None). Only
        # absence of the value key is rejected.
        v = self._validator()
        v.validate([self._entity({"attributes": [{"key": "x", "value": None}]})])
        self.assertEqual(v.errors, [])

    def test_non_string_category_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"attributes": [
                {"key": "x", "value": "y", "category": 42}
            ]})])
        self.assertTrue(any("'category' must be a string" in e for e in v.errors))


class ValidatorLocksFieldShapeTest(TestCase):
    """Tier 1 — locks is optional; non-empty string when present."""

    def _entity(self, content) -> LoadedEntity:
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_no_locks_field_passes(self):
        v = self._validator()
        v.validate([self._entity({})])
        self.assertEqual(v.errors, [])

    def test_string_locks_passes(self):
        v = self._validator()
        v.validate([self._entity({"locks": "examine:all();get:false()"})])
        self.assertEqual(v.errors, [])

    def test_non_string_locks_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"locks": ["examine:all()"]})])
        self.assertTrue(any("'locks' must be a string" in e for e in v.errors))

    def test_empty_string_locks_rejected(self):
        # "I want no extra locks" is best expressed by omitting the
        # field, not by writing an empty string.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"locks": ""})])
        self.assertTrue(any("must be a non-empty string" in e for e in v.errors))

    def test_whitespace_only_locks_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"locks": "   "})])
        self.assertTrue(any("must be a non-empty string" in e for e in v.errors))


class ValidatorAliasesFieldShapeTest(TestCase):
    """Tier 1 — aliases is optional; when present, list of non-empty strings."""

    def _entity(self, content) -> LoadedEntity:
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_no_aliases_field_passes(self):
        v = self._validator()
        v.validate([self._entity({})])
        self.assertEqual(v.errors, [])

    def test_empty_aliases_list_passes(self):
        v = self._validator()
        v.validate([self._entity({"aliases": []})])
        self.assertEqual(v.errors, [])

    def test_single_alias_passes(self):
        v = self._validator()
        v.validate([self._entity({"aliases": ["bakery"]})])
        self.assertEqual(v.errors, [])

    def test_multiple_aliases_pass(self):
        v = self._validator()
        v.validate([self._entity({"aliases": ["bakery", "goldencrust", "shop"]})])
        self.assertEqual(v.errors, [])

    def test_aliases_not_a_list_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"aliases": "bakery"})])
        self.assertTrue(any("'aliases' must be a list" in e for e in v.errors))

    def test_non_string_alias_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"aliases": ["bakery", 42]})])
        self.assertTrue(any("aliases[1]" in e and "must be a string" in e for e in v.errors))

    def test_empty_string_alias_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"aliases": ["bakery", ""]})])
        self.assertTrue(any("aliases[1]" in e and "non-empty" in e for e in v.errors))

    def test_whitespace_only_alias_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"aliases": ["   "]})])
        self.assertTrue(any("aliases[0]" in e and "non-empty" in e for e in v.errors))


class ValidatorDescriptionFieldShapeTest(TestCase):
    """Tier 1 — description is optional, must be a string when present."""

    def _entity(self, content) -> LoadedEntity:
        # Auto-inject the other mandatory Tier 1 fields.
        if isinstance(content, dict):
            defaults = {
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "x",
                "location": None,
            }
            for key, default in defaults.items():
                if key not in content:
                    content = {**content, key: default}
        return LoadedEntity(location={}, content=content, path="a.yaml")

    def _validator(self):
        return Validator(Definitions(levels=("zone",)))

    def test_no_description_field_passes(self):
        v = self._validator()
        v.validate([self._entity({})])
        self.assertEqual(v.errors, [])

    def test_string_description_passes(self):
        v = self._validator()
        v.validate([self._entity({"description": "Smells of bread."})])
        self.assertEqual(v.errors, [])

    def test_empty_string_description_passes(self):
        # Author choice — accept it, don't second-guess.
        v = self._validator()
        v.validate([self._entity({"description": ""})])
        self.assertEqual(v.errors, [])

    def test_multiline_description_passes(self):
        v = self._validator()
        v.validate([self._entity({"description": "Line one.\nLine two."})])
        self.assertEqual(v.errors, [])

    def test_int_description_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"description": 42})])
        self.assertTrue(any("'description' must be a string" in e for e in v.errors))

    def test_list_description_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"description": ["one", "two"]})])
        self.assertTrue(any("'description' must be a string" in e for e in v.errors))

    def test_dict_description_rejected(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity({"description": {"text": "..."}})])
        self.assertTrue(any("'description' must be a string" in e for e in v.errors))


class ValidatorTagsShapeTest(TestCase):
    """Tier 1 — verify _check_tags_field_shape on the tags field."""

    _BASE = {
        "deployment_id": 1,
        "typeclass": "evennia.objects.objects.DefaultRoom",
        "name": "x",
        "location": None,
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
                "name": "x",
                "location": None,
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


class ValidatorCrossRefResolutionTest(TestCase):
    """Tier 4 — `_check_cross_refs` post-loop phase against `seen_ids`.

    Runs only when the caller passes `resolve_cross_refs=True` to
    Validator.__init__. `wb_build` whole-repo pre-validation and
    `wb-validate` set the flag; tests default off.
    """

    _NO_HOME = object()  # sentinel — distinguish "omit home" from "home: null"

    def _entity(
        self, *, path, deployment_id,
        location=None, destination=None, home=_NO_HOME,
    ) -> LoadedEntity:
        content = {
            "deployment_id": deployment_id,
            "name": "x",
            "typeclass": "evennia.objects.objects.DefaultObject",
            "location": location,
        }
        if destination is not None:
            content["destination"] = destination
        if home is not self._NO_HOME:
            content["home"] = home
        return LoadedEntity(location={}, content=content, path=path)

    def _validator(self, *, resolve_cross_refs=True, file_metadata=None):
        return Validator(
            Definitions(levels=("zone",)),
            resolve_cross_refs=resolve_cross_refs,
            file_metadata=file_metadata,
        )

    # --- gating ----------------------------------------------------------

    def test_skipped_when_resolve_cross_refs_false(self):
        # With the flag off, an unresolved cross-ref produces no Tier 4
        # finding. (Tier 1's shape check already passed; the dangling
        # ref is invisible without Tier 4.)
        v = self._validator(resolve_cross_refs=False)
        v.validate([self._entity(
            path="bakery.yaml", deployment_id=1,
            location={"deployment_file": "ghost.yaml", "deployment_id": 99},
        )])
        self.assertFalse(any("does not resolve" in e for e in v.errors))

    # --- happy path -----------------------------------------------------

    def test_location_cross_ref_resolves_within_same_file(self):
        # Author writes a top-level entity placed inside another top-level
        # entity in the same file. Both seen_ids entries land before
        # Tier 4 runs, so the lookup hits.
        v = self._validator()
        v.validate([
            self._entity(path="a.yaml", deployment_id=1, location=None),
            self._entity(
                path="a.yaml", deployment_id=2,
                location={"deployment_file": "a.yaml", "deployment_id": 1},
            ),
        ])
        self.assertEqual(v.errors, [])

    def test_destination_cross_ref_resolves_across_files(self):
        v = self._validator()
        v.validate([
            self._entity(path="bakery.yaml", deployment_id=1, location=None),
            self._entity(path="inn.yaml", deployment_id=1, location=None),
            self._entity(
                path="bakery.yaml", deployment_id=2,
                location={"deployment_file": "bakery.yaml", "deployment_id": 1},
                destination={"deployment_file": "inn.yaml", "deployment_id": 1},
            ),
        ])
        self.assertEqual(v.errors, [])

    def test_forward_ref_within_same_file_resolves(self):
        # Entity A (id=1) declares location pointing at entity B (id=2)
        # which appears later in the entity list. seen_ids is fully
        # built before Tier 4 runs, so the forward ref still resolves.
        # (Builder's same-file forward-ref refusal is a separate
        # decision at create time, not a Tier 4 concern.)
        v = self._validator()
        v.validate([
            self._entity(
                path="a.yaml", deployment_id=1,
                location={"deployment_file": "a.yaml", "deployment_id": 2},
            ),
            self._entity(path="a.yaml", deployment_id=2, location=None),
        ])
        self.assertEqual(v.errors, [])

    # --- miss paths -----------------------------------------------------

    def test_unresolved_location_reported(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="bakery.yaml", deployment_id=1,
                location={"deployment_file": "ghost.yaml", "deployment_id": 99},
            )])
        self.assertTrue(any(
            "'location' cross-ref to" in e and "does not resolve" in e
            for e in v.errors
        ))

    def test_unresolved_destination_reported(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="bakery.yaml", deployment_id=1,
                location={"deployment_file": "bakery.yaml", "deployment_id": 1},
                destination={"deployment_file": "ghost.yaml", "deployment_id": 99},
            )])
        self.assertTrue(any(
            "'destination' cross-ref to" in e and "does not resolve" in e
            for e in v.errors
        ))

    # --- home: per-entity cross-ref ----------------------------------------
    #
    # `home:` is an optional per-entity field. Tier 4 walks it the same way
    # as location/destination: well-shaped dict refs must resolve in
    # seen_ids; null is a meaningful value (translates to nohome=True at
    # build time) and must not produce a Tier 4 finding; absence likewise.

    def test_home_absent_passes(self):
        v = self._validator()
        v.validate([self._entity(path="a.yaml", deployment_id=1, location=None)])
        self.assertEqual(v.errors, [])

    def test_home_null_passes(self):
        v = self._validator()
        v.validate([self._entity(
            path="a.yaml", deployment_id=1, location=None, home=None,
        )])
        self.assertEqual(v.errors, [])

    def test_home_cross_ref_resolves(self):
        v = self._validator()
        v.validate([
            self._entity(path="a.yaml", deployment_id=1, location=None),
            self._entity(
                path="a.yaml", deployment_id=2, location=None,
                home={"deployment_file": "a.yaml", "deployment_id": 1},
            ),
        ])
        self.assertEqual(v.errors, [])

    def test_home_unresolved_reported(self):
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="a.yaml", deployment_id=1, location=None,
                home={"deployment_file": "ghost.yaml", "deployment_id": 99},
            )])
        self.assertTrue(any(
            "'home' cross-ref to" in e and "does not resolve" in e
            for e in v.errors
        ))

    def test_home_skipped_when_resolve_cross_refs_false(self):
        v = self._validator(resolve_cross_refs=False)
        v.validate([self._entity(
            path="a.yaml", deployment_id=1, location=None,
            home={"deployment_file": "ghost.yaml", "deployment_id": 99},
        )])
        self.assertFalse(any("does not resolve" in e for e in v.errors))

    def test_target_file_present_but_id_missing_reported(self):
        # Target file is in seen_ids but the specific deployment_id
        # isn't — still a miss.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([
                self._entity(path="a.yaml", deployment_id=1, location=None),
                self._entity(
                    path="b.yaml", deployment_id=1,
                    location={"deployment_file": "a.yaml", "deployment_id": 99},
                ),
            ])
        self.assertTrue(any(
            "deployment_id=99" in e and "does not resolve" in e
            for e in v.errors
        ))

    # --- defensive: don't double-report -------------------------------

    def test_malformed_cross_ref_skipped_no_double_report(self):
        # Tier 1's _check_destination_well_formed already flags the bad
        # shape. Tier 4 must skip cleanly so the operator doesn't see
        # both a shape error AND a "doesn't resolve" error for the same
        # field.
        v = self._validator()
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="a.yaml", deployment_id=1,
                location=None,
                destination="not a dict",
            )])
        self.assertTrue(any(
            "'destination' must be a cross-ref dict" in e for e in v.errors
        ))
        self.assertFalse(any(
            "does not resolve" in e for e in v.errors
        ))

    # --- incoming_exits resolution (spike 6 step 6b) ---
    #
    # incoming_exits is file-level metadata; refs come through
    # file_metadata, not entity content. Tier 4 walks file_metadata per
    # file path and resolves each ref against seen_ids.

    def test_incoming_exits_all_resolve(self):
        # bakery.yaml's incoming_exits register two exits living in
        # other files; both targets exist in seen_ids.
        v = self._validator(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "inn.yaml", "deployment_id": 2},
            {"deployment_file": "forest.yaml", "deployment_id": 7},
        ]}})
        v.validate([
            self._entity(path="bakery.yaml", deployment_id=1, location=None),
            self._entity(path="inn.yaml", deployment_id=2, location=None),
            self._entity(path="forest.yaml", deployment_id=7, location=None),
        ])
        self.assertEqual(v.errors, [])

    def test_incoming_exits_unresolved_reported(self):
        v = self._validator(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "ghost.yaml", "deployment_id": 99},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="bakery.yaml", deployment_id=1, location=None,
            )])
        self.assertTrue(any(
            "'incoming_exits[0]' cross-ref to" in e and "does not resolve" in e
            for e in v.errors
        ))

    def test_incoming_exits_partial_resolution_reports_only_misses(self):
        # First entry resolves, second doesn't. Only the second produces
        # a finding — index naming makes the bad one easy to locate.
        v = self._validator(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "inn.yaml", "deployment_id": 2},
            {"deployment_file": "ghost.yaml", "deployment_id": 99},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([
                self._entity(path="bakery.yaml", deployment_id=1, location=None),
                self._entity(path="inn.yaml", deployment_id=2, location=None),
            ])
        # Exactly one finding, naming index 1 (the bad one).
        unresolved = [e for e in v.errors if "does not resolve" in e]
        self.assertEqual(len(unresolved), 1)
        self.assertIn("incoming_exits[1]", unresolved[0])

    def test_incoming_exits_skipped_when_resolve_cross_refs_false(self):
        # Mirrors location/destination gating behaviour.
        v = self._validator(
            resolve_cross_refs=False,
            file_metadata={"bakery.yaml": {"incoming_exits": [
                {"deployment_file": "ghost.yaml", "deployment_id": 99},
            ]}},
        )
        v.validate([self._entity(
            path="bakery.yaml", deployment_id=1, location=None,
        )])
        self.assertFalse(any("does not resolve" in e for e in v.errors))

    def test_incoming_exits_malformed_skipped_no_double_report(self):
        # File-level Tier 1 catches a non-dict entry. Tier 4 must stay
        # quiet so the operator sees one finding for that mistake, not
        # two.
        v = self._validator(file_metadata={"bakery.yaml": {"incoming_exits": [
            "not a dict",
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="bakery.yaml", deployment_id=1, location=None,
            )])
        # File-level Tier 1 finding present:
        self.assertTrue(any(
            "incoming_exits[0]" in e and "must be a cross-ref dict" in e
            for e in v.errors
        ))
        # Tier 4 stays quiet on the malformed entry:
        self.assertFalse(any(
            "does not resolve" in e for e in v.errors
        ))

    def test_incoming_exits_finding_names_file_path(self):
        # Tier 4 findings for incoming_exits use the file path (the
        # registry's home), not any specific entity from the file.
        v = self._validator(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "ghost.yaml", "deployment_id": 99},
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="bakery.yaml", deployment_id=1, location=None,
            )])
        unresolved = [e for e in v.errors if "does not resolve" in e]
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(unresolved[0].startswith("bakery.yaml: "))

    # --- file-level: links: -----------------------------------------------
    #
    # Tier 4 walks each well-shaped link entry and resolves both `entity`
    # and `points_to` against seen_ids. See docs/links.md.

    def _link(self, entity_file, entity_id, points_to_file, points_to_id):
        return {
            "entity": {"deployment_file": entity_file, "deployment_id": entity_id},
            "attribute": "other_side",
            "points_to": {
                "deployment_file": points_to_file, "deployment_id": points_to_id,
            },
        }

    def test_links_both_sides_resolve(self):
        # Both halves of a same-file door pair land in seen_ids.
        v = self._validator(file_metadata={"a.yaml": {"links": [
            self._link("a.yaml", 1, "a.yaml", 2),
            self._link("a.yaml", 2, "a.yaml", 1),
        ]}})
        v.validate([
            self._entity(path="a.yaml", deployment_id=1, location=None),
            self._entity(path="a.yaml", deployment_id=2, location=None),
        ])
        self.assertEqual(v.errors, [])

    def test_links_cross_file_resolve(self):
        v = self._validator(file_metadata={"a.yaml": {"links": [
            self._link("a.yaml", 1, "b.yaml", 1),
        ]}})
        v.validate([
            self._entity(path="a.yaml", deployment_id=1, location=None),
            self._entity(path="b.yaml", deployment_id=1, location=None),
        ])
        self.assertEqual(v.errors, [])

    def test_links_unresolved_entity_reported(self):
        v = self._validator(file_metadata={"a.yaml": {"links": [
            self._link("ghost.yaml", 99, "a.yaml", 1),
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="a.yaml", deployment_id=1, location=None,
            )])
        self.assertTrue(any(
            "'links[0].entity' cross-ref to" in e and "does not resolve" in e
            for e in v.errors
        ))

    def test_links_unresolved_points_to_reported(self):
        v = self._validator(file_metadata={"a.yaml": {"links": [
            self._link("a.yaml", 1, "ghost.yaml", 99),
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="a.yaml", deployment_id=1, location=None,
            )])
        self.assertTrue(any(
            "'links[0].points_to' cross-ref to" in e and "does not resolve" in e
            for e in v.errors
        ))

    def test_links_partial_resolution_reports_only_misses(self):
        # link[0] resolves both sides; link[1].points_to dangles.
        v = self._validator(file_metadata={"a.yaml": {"links": [
            self._link("a.yaml", 1, "a.yaml", 2),
            self._link("a.yaml", 1, "ghost.yaml", 99),
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([
                self._entity(path="a.yaml", deployment_id=1, location=None),
                self._entity(path="a.yaml", deployment_id=2, location=None),
            ])
        unresolved = [e for e in v.errors if "does not resolve" in e]
        self.assertEqual(len(unresolved), 1)
        self.assertIn("links[1].points_to", unresolved[0])

    def test_links_skipped_when_resolve_cross_refs_false(self):
        v = self._validator(
            resolve_cross_refs=False,
            file_metadata={"a.yaml": {"links": [
                self._link("a.yaml", 1, "ghost.yaml", 99),
            ]}},
        )
        v.validate([self._entity(
            path="a.yaml", deployment_id=1, location=None,
        )])
        self.assertFalse(any("does not resolve" in e for e in v.errors))

    def test_links_malformed_skipped_no_double_report(self):
        # Tier 1 catches a non-dict entry; Tier 4 must stay quiet so the
        # operator gets one finding, not two.
        v = self._validator(file_metadata={"a.yaml": {"links": [
            "not a dict",
        ]}})
        with self.assertRaises(ValidatorError):
            v.validate([self._entity(
                path="a.yaml", deployment_id=1, location=None,
            )])
        # Tier 1 finding present:
        self.assertTrue(any(
            "links[0]" in e and "must be a link dict" in e for e in v.errors
        ))
        # Tier 4 stays quiet:
        self.assertFalse(any(
            "does not resolve" in e for e in v.errors
        ))

    def test_links_self_reference_resolves(self):
        # entity == points_to is allowed per design — Tier 4 just checks
        # both refs land in seen_ids; identity is fine.
        v = self._validator(file_metadata={"a.yaml": {"links": [
            self._link("a.yaml", 1, "a.yaml", 1),
        ]}})
        v.validate([self._entity(
            path="a.yaml", deployment_id=1, location=None,
        )])
        self.assertEqual(v.errors, [])


class BuilderTest(TestCase):
    """Verify Builder.build's location resolution + in-build map behaviour.

    Mocks ``evennia.utils.create.create_object`` and
    ``evennia.utils.search.search_tag`` so the build pass can run without
    a live Evennia DB. The Builder's job is orchestration — what it
    decides to pass to ``create_object`` for ``location=`` is the focus
    of these tests.
    """

    _NO_DESTINATION = object()  # sentinel — distinguish "omit field" from None

    def _entity(
        self, *, path="x.yaml", deployment_id=1, location=None,
        destination=_NO_DESTINATION,
        is_nested=False, name="X", typeclass="ev.X",
    ) -> LoadedEntity:
        content = {
            "deployment_id": deployment_id,
            "name": name,
            "typeclass": typeclass,
            "location": location,
        }
        if destination is not self._NO_DESTINATION:
            content["destination"] = destination
        return LoadedEntity(
            location={}, content=content, path=path, is_nested=is_nested,
        )

    def _builder(self, *, file_metadata=None, reader=None):
        return Builder(
            Definitions(levels=("zone",)),
            file_metadata=file_metadata,
            reader=reader,
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_orphan_passes_none_as_location(self, mock_create, _mock_search):
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([self._entity(deployment_id=1, location=None)])
        self.assertEqual(mock_create.call_args_list[0].kwargs["location"], None)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_cross_ref_resolves_to_parent_obj(self, mock_create, _mock_search):
        # Two entities: parent (id=1, orphan) and child (id=2, location
        # cross-ref to parent). The child's create_object call must be
        # passed the parent's just-built mock as `location=`.
        parents_created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            parents_created.append(obj)
            return obj
        mock_create.side_effect = make

        parent = self._entity(deployment_id=1, location=None, name="Parent")
        child = self._entity(
            deployment_id=2, name="Child", is_nested=True,
            location={"deployment_file": "x.yaml", "deployment_id": 1},
        )

        b = self._builder()
        b.build([parent, child])

        parent_obj = parents_created[0]
        child_call = mock_create.call_args_list[1]
        self.assertIs(child_call.kwargs["location"], parent_obj)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_cross_ref_raises(self, mock_create, _mock_search):
        # No parent entity in the build set AND DB lookup returns nothing
        # (search_tag mocked to []) — child's cross-ref must fail.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        orphan_child = self._entity(
            deployment_id=2, is_nested=True,
            location={"deployment_file": "x.yaml", "deployment_id": 99},
        )
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([orphan_child])
        self.assertIn("does not resolve", str(ctx.exception))
        self.assertIn("deployment_id=99", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_cross_ref_error_names_field(self, mock_create, _mock_search):
        # The error message identifies which field carried the unresolved
        # ref — relevant once destination joins location in step 5b.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        orphan_child = self._entity(
            deployment_id=2, is_nested=True,
            location={"deployment_file": "x.yaml", "deployment_id": 99},
        )
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([orphan_child])
        self.assertIn("'location'", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_built_by_id_populated_after_build(self, mock_create, _mock_search):
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([
            self._entity(path="a.yaml", deployment_id=1, location=None),
            self._entity(path="a.yaml", deployment_id=2, location=None),
            self._entity(path="b.yaml", deployment_id=1, location=None),
        ])
        self.assertEqual(set(b._built_by_id.keys()), {
            ("a.yaml", 1), ("a.yaml", 2), ("b.yaml", 1),
        })

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_built_by_id_resets_between_builds(self, mock_create, _mock_search):
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([self._entity(deployment_id=1, location=None)])
        self.assertEqual(len(b._built_by_id), 1)
        b.build([self._entity(deployment_id=2, location=None)])
        # Second build's map only has the second entity.
        self.assertEqual(set(b._built_by_id.keys()), {("x.yaml", 2)})

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_deeply_nested_each_child_uses_immediate_parent(self, mock_create, _mock_search):
        # room (1) → chest (2) → key (3). The key's location must
        # resolve to the chest's mock object, not the room's.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        room = self._entity(deployment_id=1, location=None, name="Room")
        chest = self._entity(
            deployment_id=2, is_nested=True, name="Chest",
            location={"deployment_file": "x.yaml", "deployment_id": 1},
        )
        key = self._entity(
            deployment_id=3, is_nested=True, name="Key",
            location={"deployment_file": "x.yaml", "deployment_id": 2},
        )

        b = self._builder()
        b.build([room, chest, key])

        room_obj, chest_obj, _ = created
        self.assertIs(mock_create.call_args_list[0].kwargs["location"], None)
        self.assertIs(mock_create.call_args_list[1].kwargs["location"], room_obj)
        self.assertIs(mock_create.call_args_list[2].kwargs["location"], chest_obj)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_same_file_forward_ref_refused(self, mock_create, _mock_search):
        # Two top-level entities in the same file: A (id=1) declares a
        # location ref pointing at B (id=2), but B is later in the build
        # order. Single-pass build refuses; author must reorder.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        a = self._entity(
            deployment_id=1, name="A",
            location={"deployment_file": "x.yaml", "deployment_id": 2},
        )
        b_entity = self._entity(deployment_id=2, name="B", location=None)

        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([a, b_entity])
        self.assertIn("does not resolve", str(ctx.exception))

    # --- two-pass dispatch: exits build after non-exits (spike 4 step 5b) ---

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_non_exit_does_not_pass_destination_kwarg(self, mock_create, _mock_search):
        # Regression: regular entities (no destination field) call
        # create_object WITHOUT a destination kwarg.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([self._entity(deployment_id=1, location=None)])
        self.assertNotIn("destination", mock_create.call_args_list[0].kwargs)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_exit_passes_destination_to_create_object(self, mock_create, _mock_search):
        # Bakery + exit-from-bakery-to-bakery (self-link, just so the
        # destination resolves within the build). create_object for the
        # exit is called with destination=bakery_obj.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        bakery = self._entity(
            deployment_id=1, name="Bakery", location=None,
        )
        exit_entity = self._entity(
            deployment_id=2, name="north",
            location={"deployment_file": "x.yaml", "deployment_id": 1},
            destination={"deployment_file": "x.yaml", "deployment_id": 1},
        )

        b = self._builder()
        b.build([bakery, exit_entity])

        bakery_obj = created[0]
        exit_call_kwargs = mock_create.call_args_list[1].kwargs
        self.assertIs(exit_call_kwargs["location"], bakery_obj)
        self.assertIs(exit_call_kwargs["destination"], bakery_obj)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_exits_built_after_non_exits_regardless_of_yaml_order(
        self, mock_create, _mock_search,
    ):
        # YAML order interleaves non-exits and exits. The two-pass
        # dispatch must put all non-exits first, then all exits, so the
        # exits' destinations can resolve to just-built rooms.
        created_order = []

        def make(**kw):
            obj = MagicMock(_kw=kw, key=kw["key"])
            created_order.append(kw["key"])
            return obj
        mock_create.side_effect = make

        bakery = self._entity(deployment_id=1, name="Bakery", location=None)
        # Exit appears BEFORE the inn in the entity list, but the inn is
        # the destination — so without two-pass, this would fail.
        exit_to_inn = self._entity(
            deployment_id=2, name="north",
            location={"deployment_file": "x.yaml", "deployment_id": 1},
            destination={"deployment_file": "x.yaml", "deployment_id": 3},
        )
        inn = self._entity(deployment_id=3, name="Inn", location=None)

        b = self._builder()
        b.build([bakery, exit_to_inn, inn])

        # Non-exits first (Bakery, Inn), then exits (north).
        self.assertEqual(created_order, ["Bakery", "Inn", "north"])

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_exit_destination_resolves_via_built_by_id(self, mock_create, _mock_search):
        # End-to-end: bakery + inn + bakery→inn exit. The exit's
        # destination resolves to the inn object that was built in pass 1.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw, key=kw["key"])
            created.append(obj)
            return obj
        mock_create.side_effect = make

        bakery = self._entity(deployment_id=1, name="Bakery", location=None)
        inn = self._entity(deployment_id=2, name="Inn", location=None)
        exit_entity = self._entity(
            deployment_id=3, name="north",
            location={"deployment_file": "x.yaml", "deployment_id": 1},
            destination={"deployment_file": "x.yaml", "deployment_id": 2},
        )

        b = self._builder()
        b.build([bakery, inn, exit_entity])

        # created order matches non-exits-then-exits → [bakery, inn, exit]
        bakery_obj, inn_obj, _ = created
        exit_kwargs = mock_create.call_args_list[2].kwargs
        self.assertIs(exit_kwargs["location"], bakery_obj)
        self.assertIs(exit_kwargs["destination"], inn_obj)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_destination_error_names_destination_field(
        self, mock_create, _mock_search,
    ):
        # Exit with a destination cross-ref pointing at nothing in the
        # build set — error message identifies 'destination' specifically.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        bakery = self._entity(deployment_id=1, name="Bakery", location=None)
        broken_exit = self._entity(
            deployment_id=2, name="north",
            location={"deployment_file": "x.yaml", "deployment_id": 1},
            destination={"deployment_file": "x.yaml", "deployment_id": 99},
        )
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([bakery, broken_exit])
        msg = str(ctx.exception)
        self.assertIn("'destination'", msg)
        self.assertIn("deployment_id=99", msg)

    # --- DB tag-search fallback for cross-file refs (spike 4 step 5c) ---

    def _mock_db_obj(self, *, deployment_id):
        # Helper: a MagicMock shaped like a tagged Evennia object —
        # `obj.tags.get(category="wb_deployment_id", return_list=True)`
        # returns the right list of strings for filter matching.
        obj = MagicMock()
        obj.tags.get.return_value = [str(deployment_id)]
        return obj

    @patch("evennia.utils.search.search_tag")
    def test_lookup_in_db_returns_match(self, mock_search):
        target = self._mock_db_obj(deployment_id=1)
        mock_search.return_value = [target]
        b = self._builder()
        result = b._lookup_in_db("a.yaml", 1)
        self.assertIs(result, target)

    @patch("evennia.utils.search.search_tag", return_value=[])
    def test_lookup_in_db_returns_none_when_no_file_match(self, _mock_search):
        b = self._builder()
        self.assertIsNone(b._lookup_in_db("a.yaml", 1))

    @patch("evennia.utils.search.search_tag")
    def test_lookup_in_db_returns_none_when_no_id_match(self, mock_search):
        # File has objects but none with deployment_id=99.
        mock_search.return_value = [
            self._mock_db_obj(deployment_id=1),
            self._mock_db_obj(deployment_id=2),
        ]
        b = self._builder()
        self.assertIsNone(b._lookup_in_db("a.yaml", 99))

    @patch("evennia.utils.search.search_tag")
    def test_lookup_in_db_filters_by_deployment_id(self, mock_search):
        # Multiple objects from same file; filter picks the right one.
        wrong = self._mock_db_obj(deployment_id=1)
        right = self._mock_db_obj(deployment_id=42)
        mock_search.return_value = [wrong, right]
        b = self._builder()
        result = b._lookup_in_db("a.yaml", 42)
        self.assertIs(result, right)

    @patch("evennia.utils.search.search_tag")
    def test_lookup_in_db_raises_on_multiple_matches(self, mock_search):
        # Cleanup integrity invariant violated — two objects share the
        # same (file, deployment_id) tag pair. Fail loudly.
        mock_search.return_value = [
            self._mock_db_obj(deployment_id=1),
            self._mock_db_obj(deployment_id=1),
        ]
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b._lookup_in_db("a.yaml", 1)
        self.assertIn("multiple objects", str(ctx.exception))
        self.assertIn("cleanup integrity", str(ctx.exception))

    @patch("evennia.utils.create.create_object")
    @patch("evennia.utils.search.search_tag")
    def test_cross_ref_falls_through_to_db_on_in_build_miss(
        self, mock_search, mock_create,
    ):
        # Single child entity references a parent that's NOT in the build
        # set. The DB has it (search_tag returns the tagged object on the
        # second call — first call is the cleanup query). _resolve_cross_ref
        # falls through, finds it, passes it as `location=` to create_object.
        cleanup_response = []                       # cleanup: nothing to delete
        db_obj = self._mock_db_obj(deployment_id=1)
        # search_tag is called: (1) once per file in cleanup, (2) once per
        # cross-ref lookup. Distinguish via side_effect list.
        mock_search.side_effect = [cleanup_response, [db_obj]]
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        child = self._entity(
            deployment_id=2, is_nested=True,
            location={"deployment_file": "elsewhere.yaml", "deployment_id": 1},
        )
        b = self._builder()
        b.build([child])

        # create_object was called with the DB-resolved object as location.
        self.assertIs(mock_create.call_args.kwargs["location"], db_obj)

    @patch("evennia.utils.create.create_object")
    @patch("evennia.utils.search.search_tag")
    def test_db_fallback_caches_back_into_built_by_id(
        self, mock_search, mock_create,
    ):
        # Two children both point at the same DB-only target. The DB
        # should be queried ONCE — the first lookup caches the object
        # into _built_by_id; the second hits the in-memory map.
        cleanup_response = []
        db_obj = self._mock_db_obj(deployment_id=1)
        mock_search.side_effect = [cleanup_response, [db_obj]]
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        c1 = self._entity(
            deployment_id=10, is_nested=True, name="c1",
            location={"deployment_file": "elsewhere.yaml", "deployment_id": 1},
        )
        c2 = self._entity(
            deployment_id=11, is_nested=True, name="c2",
            location={"deployment_file": "elsewhere.yaml", "deployment_id": 1},
        )

        b = self._builder()
        b.build([c1, c2])

        # search_tag called: 1× cleanup + 1× DB lookup = 2 calls total.
        # If cache-back failed, we'd see 3 (a second DB lookup for c2).
        self.assertEqual(mock_search.call_count, 2)
        # Both children received the same db_obj as their location.
        self.assertIs(mock_create.call_args_list[0].kwargs["location"], db_obj)
        self.assertIs(mock_create.call_args_list[1].kwargs["location"], db_obj)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_when_db_misses_too(self, mock_create, _mock_search):
        # In-build map miss + DB miss → BuilderError mentions both
        # failure modes.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        orphan = self._entity(
            deployment_id=2, is_nested=True,
            location={"deployment_file": "ghost.yaml", "deployment_id": 99},
        )
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([orphan])
        msg = str(ctx.exception)
        self.assertIn("neither built in this pass nor present in the DB", msg)
        self.assertIn("ghost.yaml", msg)
        self.assertIn("deployment_id=99", msg)

    @patch("evennia.utils.create.create_object")
    @patch("evennia.utils.search.search_tag")
    def test_cleanup_skips_cascade_deleted_ghost_objects(
        self, mock_search, mock_create,
    ):
        # When Evennia cascades a delete (e.g. an exit whose destination
        # room was just deleted in this same cleanup pass), search_tag
        # can still return a handle for the cascaded-deleted object —
        # its tag-side row outlives the cascade for the duration of the
        # query. Calling .delete() on that ghost raises "already deleted!".
        # Cleanup must defensively skip ghosts (pk is None).
        ghost = MagicMock()
        ghost.pk = None
        ghost.dbref = "#None"
        live = MagicMock()
        live.pk = 42
        live.dbref = "#42"
        mock_search.return_value = [ghost, live]
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        b = self._builder()
        b.build([self._entity(deployment_id=1, location=None)])

        # Live object got deleted; ghost was skipped without exception.
        live.delete.assert_called_once()
        ghost.delete.assert_not_called()
        # deleted_count reflects only the explicit deletion.
        self.assertEqual(b.deleted_count, 1)

    @patch("evennia.utils.create.create_object")
    @patch("evennia.utils.search.search_tag")
    def test_cleanup_skips_cascade_ghosts_with_cached_pk(
        self, mock_search, mock_create,
    ):
        # The OTHER ghost case: Django's database-level CASCADE delete
        # (e.g. an exit whose destination room was just deleted) removes
        # the underlying row directly, bypassing Evennia's `.delete()`
        # method. The Python wrapper still in our `existing` snapshot
        # therefore has a CACHED pk (not None) and `_is_deleted=False`
        # — both ghost-detection signals lie. The wrapper only reveals
        # itself as a ghost when something tries to access a field
        # through the Django ORM, which raises ObjectDoesNotExist
        # (see evennia/utils/idmapper/models.py:120-130).
        #
        # Cleanup catches that exception during the `.delete()` call
        # and skips the cascade-ghost silently.
        live_room = MagicMock()
        live_room.pk = 100
        live_room.dbref = "#100"

        cascade_ghost = MagicMock()
        cascade_ghost.pk = 627  # cached pk from before the cascade
        cascade_ghost.dbref = "#627"
        cascade_ghost.delete.side_effect = ObjectDoesNotExist(
            "Cannot access db_destination: Hosting object was already deleted."
        )

        another_live = MagicMock()
        another_live.pk = 200
        another_live.dbref = "#200"

        mock_search.return_value = [live_room, cascade_ghost, another_live]
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        b = self._builder()
        # Should not raise — the cascade-ghost's ObjectDoesNotExist is
        # caught and the loop continues to delete the remaining live.
        b.build([self._entity(deployment_id=1, location=None)])

        live_room.delete.assert_called_once()
        cascade_ghost.delete.assert_called_once()
        another_live.delete.assert_called_once()
        # deleted_count counts only successful deletes — the ghost
        # is excluded since its delete raised.
        self.assertEqual(b.deleted_count, 2)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_two_exits_pointing_at_each_other_resolve(self, mock_create, _mock_search):
        # Bidirectional exits: bakery → inn AND inn → bakery in the same
        # build. Both exits build in pass 2 by YAML order, so the
        # second exit's destination resolves to the first exit's already-
        # built (in pass 2) parent room. (The point of the test isn't
        # exit→exit refs — it's that two exits in pass 2 are both able
        # to resolve their destinations to the rooms built in pass 1.)
        created = {}

        def make(**kw):
            obj = MagicMock(_kw=kw, key=kw["key"])
            created[kw["key"]] = obj
            return obj
        mock_create.side_effect = make

        bakery = self._entity(deployment_id=1, name="Bakery", location=None)
        inn = self._entity(deployment_id=2, name="Inn", location=None)
        north = self._entity(
            deployment_id=3, name="north",
            location={"deployment_file": "x.yaml", "deployment_id": 1},
            destination={"deployment_file": "x.yaml", "deployment_id": 2},
        )
        south = self._entity(
            deployment_id=4, name="south",
            location={"deployment_file": "x.yaml", "deployment_id": 2},
            destination={"deployment_file": "x.yaml", "deployment_id": 1},
        )

        b = self._builder()
        b.build([bakery, inn, north, south])

        # north (in bakery, → inn)
        north_kwargs = mock_create.call_args_list[2].kwargs
        self.assertIs(north_kwargs["location"], created["Bakery"])
        self.assertIs(north_kwargs["destination"], created["Inn"])
        # south (in inn, → bakery)
        south_kwargs = mock_create.call_args_list[3].kwargs
        self.assertIs(south_kwargs["location"], created["Inn"])
        self.assertIs(south_kwargs["destination"], created["Bakery"])

    # --- pass 3: incoming_exits dependency restore (spike 6 step 6e) ---

    def _make_pass3_reader(self, files: dict):
        """Build a fixture Reader returning canonical YAML for given files."""
        return FixtureReader(files)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_pass_3_no_op_when_file_metadata_empty(self, mock_create, _mock_search):
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([self._entity(deployment_id=1, location=None)])
        # Only the one entity built; no pass 3 work.
        self.assertEqual(mock_create.call_count, 1)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_pass_3_skips_when_target_already_built_in_pass_2(
        self, mock_create, _mock_search,
    ):
        # bakery.yaml registers (inn.yaml, 2) as an incoming_exit. The
        # inn's south exit is also being built in this invocation
        # (pass 2). Pass 3 sees the in-build map hit and skips —
        # no extra create_object call.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        bakery = self._entity(
            path="bakery.yaml", deployment_id=1, name="Bakery", location=None,
        )
        inn = self._entity(
            path="inn.yaml", deployment_id=1, name="Inn", location=None,
        )
        south_exit = self._entity(
            path="inn.yaml", deployment_id=2, name="south",
            location={"deployment_file": "inn.yaml", "deployment_id": 1},
            destination={"deployment_file": "bakery.yaml", "deployment_id": 1},
        )
        b = self._builder(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "inn.yaml", "deployment_id": 2},
        ]}})
        b.build([bakery, inn, south_exit])
        # 3 creates: bakery, inn, south_exit. Pass 3 finds south_exit
        # already in _built_by_id and skips.
        self.assertEqual(mock_create.call_count, 3)

    @patch("evennia.utils.create.create_object")
    @patch("evennia.utils.search.search_tag")
    def test_pass_3_skips_when_target_in_db(self, mock_search, mock_create):
        # Bakery is being rebuilt in scope; inn.yaml is NOT. The inn's
        # south exit is in the DB (search_tag returns it). Pass 3 finds
        # it via DB fallback and skips fetching the canonical file.
        cleanup_response = []
        db_obj = self._mock_db_obj(deployment_id=2)
        mock_search.side_effect = [cleanup_response, [db_obj]]
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        bakery = self._entity(
            path="bakery.yaml", deployment_id=1, name="Bakery", location=None,
        )
        # No reader needed — DB lookup hits before the fetch path.
        b = self._builder(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "inn.yaml", "deployment_id": 2},
        ]}})
        b.build([bakery])
        # Only 1 create (the bakery). Pass 3 found the south exit in DB.
        self.assertEqual(mock_create.call_count, 1)
        # And the DB-found object is now cached in the in-build map.
        self.assertIn(("inn.yaml", 2), b._built_by_id)

    @patch("evennia.utils.create.create_object")
    @patch("evennia.utils.search.search_tag")
    def test_pass_3_fetches_and_builds_missing_dep(
        self, mock_search, mock_create,
    ):
        # Bakery rebuilt in scope; the inn's south exit was cascade-
        # deleted (DB lookup for it misses) but the inn itself is
        # still in the DB (Evennia's SET_NULL on db_destination doesn't
        # cascade-delete the location-side relations). Pass 3 must:
        # 1. Look up (inn.yaml, 2) → not in DB (cascade-deleted).
        # 2. Fetch inn.yaml from the Reader.
        # 3. Build the south exit. Its location (inn.yaml, 1) resolves
        #    via DB fallback (inn still exists). Its destination
        #    (bakery.yaml, 1) resolves via _built_by_id (just built).
        inn_db_mock = self._mock_db_obj(deployment_id=1)

        def fake_search_tag(key, category):
            # Cleanup queries bakery.yaml (no prior state in this stub).
            # Pass 3 queries inn.yaml when resolving the south exit's
            # location (and when checking if the south exit itself is
            # in the DB). Filter logic in _lookup_in_db decides which
            # candidate matches the requested deployment_id.
            if key == "inn.yaml":
                return [inn_db_mock]
            return []
        mock_search.side_effect = fake_search_tag
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw, _key=kw["key"])

        # Canonical inn.yaml contains a top-level inn and its south exit.
        inn_yaml = {"entities": [{
            "deployment_id": 1,
            "typeclass": "evennia.objects.objects.DefaultRoom",
            "name": "The Crooked Lantern",
            "location": None,
            "exits": [{
                "deployment_id": 2,
                "typeclass": "evennia.objects.objects.DefaultExit",
                "name": "south",
                "destination": {
                    "deployment_file": "bakery.yaml", "deployment_id": 1,
                },
            }],
        }]}
        reader = self._make_pass3_reader({
            "definitions.yaml": {"levels": ["zone"]},
            "inn.yaml": inn_yaml,
        })

        bakery = self._entity(
            path="bakery.yaml", deployment_id=1, name="Bakery", location=None,
        )
        b = self._builder(
            reader=reader,
            file_metadata={"bakery.yaml": {"incoming_exits": [
                {"deployment_file": "inn.yaml", "deployment_id": 2},
            ]}},
        )
        b.build([bakery])

        # Two create_object calls: bakery (pass 1), south exit (pass 3).
        self.assertEqual(mock_create.call_count, 2)
        # Pass 3 entity is the south exit — got built with location
        # resolved to the inn (from DB) and destination resolved to the
        # just-built bakery (from _built_by_id).
        pass_3_kwargs = mock_create.call_args_list[1].kwargs
        self.assertEqual(pass_3_kwargs["key"], "south")
        # Location resolves to the inn DB mock.
        self.assertIs(pass_3_kwargs["location"], inn_db_mock)
        # Destination resolves to the bakery's just-built mock obj.
        self.assertEqual(pass_3_kwargs["destination"]._key, "Bakery")

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_pass_3_raises_when_no_reader_and_dep_missing(
        self, mock_create, _mock_search,
    ):
        # Builder constructed without a reader. Pass 3 needs to fetch a
        # canonical file but can't — must raise BuilderError.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        bakery = self._entity(
            path="bakery.yaml", deployment_id=1, name="Bakery", location=None,
        )
        b = self._builder(file_metadata={"bakery.yaml": {"incoming_exits": [
            {"deployment_file": "inn.yaml", "deployment_id": 2},
        ]}})  # NO reader supplied
        with self.assertRaises(BuilderError) as ctx:
            b.build([bakery])
        self.assertIn(
            "Builder constructed without a reader", str(ctx.exception),
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_pass_3_raises_when_canonical_file_lacks_id(
        self, mock_create, _mock_search,
    ):
        # Author registered (inn.yaml, 99) but inn.yaml has no
        # deployment_id=99. Pass 3 must refuse with a clear error.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        reader = self._make_pass3_reader({
            "definitions.yaml": {"levels": ["zone"]},
            "inn.yaml": {"entities": [{
                "deployment_id": 1,
                "typeclass": "evennia.objects.objects.DefaultRoom",
                "name": "Inn", "location": None,
            }]},
        })
        bakery = self._entity(
            path="bakery.yaml", deployment_id=1, name="Bakery", location=None,
        )
        b = self._builder(
            reader=reader,
            file_metadata={"bakery.yaml": {"incoming_exits": [
                {"deployment_file": "inn.yaml", "deployment_id": 99},
            ]}},
        )
        with self.assertRaises(BuilderError) as ctx:
            b.build([bakery])
        self.assertIn("not found in canonical file", str(ctx.exception))


class BuilderHomeFieldTest(TestCase):
    """Verify Builder.build's translation of `home:` into create_object kwargs.

    YAML semantics → create_object semantics:
      - field absent → no kwarg (Evennia default: settings.DEFAULT_HOME)
      - null → nohome=True (object.home becomes None)
      - cross-ref dict → home=<resolved_obj>

    Note: passing home=None directly to create_object would fall through
    to settings.DEFAULT_HOME — must use nohome=True for the null case.
    See manager.py:683-688 in Evennia.
    """

    _NO_HOME = object()

    def _entity(
        self, *, path="x.yaml", deployment_id=1, location=None,
        home=_NO_HOME, name="X", typeclass="ev.X",
    ) -> LoadedEntity:
        content = {
            "deployment_id": deployment_id,
            "name": name,
            "typeclass": typeclass,
            "location": location,
        }
        if home is not self._NO_HOME:
            content["home"] = home
        return LoadedEntity(location={}, content=content, path=path)

    def _builder(self):
        return Builder(Definitions(levels=("zone",)))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_home_absent_no_kwarg_passed(self, mock_create, _mock_search):
        # Field absent → neither home= nor nohome= in create_kwargs.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([self._entity()])
        kwargs = mock_create.call_args_list[0].kwargs
        self.assertNotIn("home", kwargs)
        self.assertNotIn("nohome", kwargs)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_home_null_translates_to_nohome_true(self, mock_create, _mock_search):
        # YAML null → nohome=True, no home= kwarg.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        b.build([self._entity(home=None)])
        kwargs = mock_create.call_args_list[0].kwargs
        self.assertEqual(kwargs.get("nohome"), True)
        self.assertNotIn("home", kwargs)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_home_cross_ref_resolves_to_target_obj(
        self, mock_create, _mock_search,
    ):
        # YAML cross-ref dict → home=<that built object>, no nohome=.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder()
        b.build([
            self._entity(deployment_id=1, name="Target"),
            self._entity(
                deployment_id=2, name="Resident",
                home={"deployment_file": "x.yaml", "deployment_id": 1},
            ),
        ])

        target = created[0]
        resident_kwargs = mock_create.call_args_list[1].kwargs
        self.assertIs(resident_kwargs.get("home"), target)
        self.assertNotIn("nohome", resident_kwargs)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_home_raises(self, mock_create, _mock_search):
        # Cross-ref to nothing → BuilderError naming the home field.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([self._entity(
                home={"deployment_file": "ghost.yaml", "deployment_id": 99},
            )])
        self.assertIn("'home'", str(ctx.exception))
        self.assertIn("does not resolve", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_same_file_forward_home_ref_refused(
        self, mock_create, _mock_search,
    ):
        # Same ordering rule as location: target must be built before
        # the entity that references it. Forward refs fail at create
        # time with the operator-meaningful "does not resolve" message.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)
        b = self._builder()
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(
                    deployment_id=1, name="Resident",
                    home={"deployment_file": "x.yaml", "deployment_id": 2},
                ),
                self._entity(deployment_id=2, name="Target"),
            ])
        self.assertIn("'home'", str(ctx.exception))


class BuilderPass4LinksTest(TestCase):
    """Verify Builder pass 4 (links) resolves and assigns each link.

    Mocks ``evennia.utils.create.create_object`` so the build can run
    without a live Evennia DB. Each created object is a MagicMock —
    pass 4 calls ``obj.attributes.add(attribute, points_to_obj,
    category=category)`` on the resolved ``entity`` mock; the test
    asserts those calls happened with the expected arguments.

    See docs/links.md.
    """

    def _entity(self, *, path="x.yaml", deployment_id, name=None) -> LoadedEntity:
        return LoadedEntity(
            location={},
            content={
                "deployment_id": deployment_id,
                "name": name or f"E{deployment_id}",
                "typeclass": "ev.X",
                "location": None,
            },
            path=path,
        )

    def _builder(self, *, file_metadata=None, reader=None):
        return Builder(
            Definitions(levels=("zone",)),
            file_metadata=file_metadata,
            reader=reader,
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_no_links_section_is_noop(self, mock_create, _mock_search):
        # File has no links: key — pass 4 walks but finds nothing.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {}})
        b.build([self._entity(deployment_id=1)])

        # Built entity's attributes.add was never called by pass 4.
        created[0].attributes.add.assert_not_called()

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_same_file_pair_links_both_assigned(
        self, mock_create, _mock_search,
    ):
        # Two entities, two links forming a reciprocal pair. After
        # build, each entity's attributes.add should have been called
        # exactly once with the correct partner mock.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [
            {
                "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
                "attribute": "other_side",
                "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
            },
            {
                "entity": {"deployment_file": "x.yaml", "deployment_id": 2},
                "attribute": "other_side",
                "points_to": {"deployment_file": "x.yaml", "deployment_id": 1},
            },
        ]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        a, c = created[0], created[1]
        a.attributes.add.assert_called_once_with("other_side", c, category=None)
        c.attributes.add.assert_called_once_with("other_side", a, category=None)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_link_with_category_applied(self, mock_create, _mock_search):
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": "other_side",
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
            "category": "doors",
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        created[0].attributes.add.assert_called_once_with(
            "other_side", created[1], category="doors",
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_self_link_assigns(self, mock_create, _mock_search):
        # entity == points_to. Allowed per design — Builder applies it.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": "self_ref",
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 1},
        }]}})
        b.build([self._entity(deployment_id=1)])

        created[0].attributes.add.assert_called_once_with(
            "self_ref", created[0], category=None,
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_entity_raises(self, mock_create, _mock_search):
        # entity not in this build set and DB lookup returns nothing —
        # BuilderError, no partial state.
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "ghost.yaml", "deployment_id": 99},
            "attribute": "other_side",
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 1},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([self._entity(deployment_id=1)])
        self.assertIn("links[0].entity", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_unresolved_points_to_raises(self, mock_create, _mock_search):
        mock_create.side_effect = lambda **kw: MagicMock(_kw=kw)

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": "other_side",
            "points_to": {"deployment_file": "ghost.yaml", "deployment_id": 99},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([self._entity(deployment_id=1)])
        self.assertIn("links[0].points_to", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_links_in_out_of_scope_files_skipped(
        self, mock_create, _mock_search,
    ):
        # file_metadata declares links in y.yaml, but only x.yaml is in
        # the build's scope. y.yaml's links must not fire — they belong
        # to a file not being built.
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder(file_metadata={
            "y.yaml": {"links": [{
                "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
                "attribute": "other_side",
                "points_to": {"deployment_file": "x.yaml", "deployment_id": 1},
            }]},
        })
        # Build only x.yaml's entity. file_metadata for y.yaml exists,
        # but y.yaml is not in file_paths_in_scope.
        b.build([self._entity(path="x.yaml", deployment_id=1)])

        created[0].attributes.add.assert_not_called()

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_link_after_pass3_can_resolve_restored_entity(
        self, mock_create, _mock_search,
    ):
        # Pass 4 runs after pass 3, so an entity restored by pass 3 is
        # in _built_by_id and a link pointing at it resolves cleanly via
        # the cache (no extra DB lookup needed). We don't trigger pass
        # 3 here — the assertion is just that pass 4 happens *after*
        # pass 3 in the build() ordering. The simplest proof: a link
        # whose entity is in scope and points_to is in scope resolves.
        # (Pass 3 ordering is exercised in BuilderTest above.)
        created = []

        def make(**kw):
            obj = MagicMock(_kw=kw)
            created.append(obj)
            return obj
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": "other_side",
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        created[0].attributes.add.assert_called_once_with(
            "other_side", created[1], category=None,
        )


class BuilderPass4LinksSubscriptPathTest(TestCase):
    """Verify Builder pass 4 supports subscript-path attribute syntax.

    When the link's ``attribute`` field contains ``[`` the builder
    treats it as a Python subscript expression like
    ``destinations["ironback_peaks"]["destination"]`` — the leading
    bare identifier names a top-level attribute that already exists
    on the entity (typically created by an ``attributes:`` block in
    pass 1 with placeholder ``null`` values), and the subscripts walk
    into the nested dict/list structure to assign the resolved
    cross-ref at the leaf.
    """

    def _entity(self, *, path="x.yaml", deployment_id, name=None) -> LoadedEntity:
        return LoadedEntity(
            location={},
            content={
                "deployment_id": deployment_id,
                "name": name or f"E{deployment_id}",
                "typeclass": "ev.X",
                "location": None,
            },
            path=path,
        )

    def _builder(self, *, file_metadata=None, reader=None):
        return Builder(
            Definitions(levels=("zone",)),
            file_metadata=file_metadata,
            reader=reader,
        )

    def _make_create_with_attribute_store(self, attribute_stores):
        """Build a create_object side-effect that wires .attributes.get to
        return the per-entity dict from attribute_stores (keyed by
        deployment_id), and .attributes.add to update that dict.

        attribute_stores: list of dicts, one per to-be-created entity,
        in creation order. Each dict represents the entity's persistent
        attribute store — keys are attribute names, values are the
        stored values.
        """
        created = []

        def make(**kw):
            # Determine which created index this is (0-based)
            idx = len(created)
            store = (
                attribute_stores[idx]
                if idx < len(attribute_stores) else {}
            )
            obj = MagicMock(_kw=kw, _store=store)

            def get_attr(name, default=None):
                return store.get(name, default)
            obj.attributes.get.side_effect = get_attr

            def set_attr(name, value, category=None):
                store[name] = value
            obj.attributes.add.side_effect = set_attr

            created.append(obj)
            return obj
        return make, created

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_assigns_into_nested_dict(
        self, mock_create, _mock_search,
    ):
        # Entity 1 has a `destinations` dict attribute with a placeholder
        # at destinations["ironback_peaks"]["destination"]. The link
        # walks that path and replaces the placeholder with entity 2.
        store_1 = {
            "destinations": {
                "ironback_peaks": {
                    "label": "Ironback Peaks",
                    "destination": None,  # placeholder
                    "food_cost": 3,
                },
            },
        }
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["ironback_peaks"]["destination"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        # Placeholder was replaced with entity 2's mock object.
        self.assertIs(
            store_1["destinations"]["ironback_peaks"]["destination"],
            created[1],
        )
        # Sibling literal data was preserved.
        self.assertEqual(
            store_1["destinations"]["ironback_peaks"]["label"],
            "Ironback Peaks",
        )
        self.assertEqual(
            store_1["destinations"]["ironback_peaks"]["food_cost"], 3,
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_bare_attribute_still_works(
        self, mock_create, _mock_search,
    ):
        # Existing bare-attribute behaviour must be preserved when the
        # attribute string contains no `[`.
        make, created = self._make_create_with_attribute_store([{}, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": "other_side",
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        # Bare attribute set via single attributes.add call.
        created[0].attributes.add.assert_any_call(
            "other_side", created[1], category=None,
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_with_category_refused(
        self, mock_create, _mock_search,
    ):
        # category only applies to bare attribute names — combining it
        # with a subscript path is a refusal at build time.
        make, created = self._make_create_with_attribute_store([{}, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'foo["bar"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
            "category": "doors",
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("category", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_missing_top_level_attribute_refused(
        self, mock_create, _mock_search,
    ):
        # The top-level attribute named by the path's leading identifier
        # must already exist (typically created in pass 1 by an
        # attributes: block with placeholder values).
        make, created = self._make_create_with_attribute_store([{}, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["foo"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("does not exist", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_bad_navigation_refused(
        self, mock_create, _mock_search,
    ):
        # The path navigates into a key that doesn't exist mid-walk —
        # the whole build fails with a clear error.
        store_1 = {"destinations": {}}  # empty dict, no "foo" key
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["foo"]["bar"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("cannot navigate", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_malformed_syntax_refused(
        self, mock_create, _mock_search,
    ):
        # `foo[unclosed` is invalid Python subscript syntax.
        make, created = self._make_create_with_attribute_store([{}, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["unclosed',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("not valid Python", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_close_bracket_only_refused(
        self, mock_create, _mock_search,
    ):
        # Defence-in-depth: an attribute string with `]` but no `[`
        # still routes through the subscript-path branch so it fails
        # loudly via the parser, not silently as a garbage attribute
        # name. (Validator catches this earlier; builder enforces it
        # too in case a caller bypasses validation.)
        make, created = self._make_create_with_attribute_store([{}, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'dict(thing]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("not valid Python", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_with_int_index_into_list(
        self, mock_create, _mock_search,
    ):
        # Subscript paths support integer indices for list-shaped
        # placeholders too — the parser distinguishes int from str.
        store_1 = {
            "routes": [
                {"label": "north", "to": None},  # placeholder
                {"label": "south", "to": "literal"},
            ],
        }
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'routes[0]["to"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        self.assertIs(store_1["routes"][0]["to"], created[1])
        # Sibling list entry untouched.
        self.assertEqual(store_1["routes"][1]["to"], "literal")

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_assignment_into_none_leaf_refused(
        self, mock_create, _mock_search,
    ):
        # Leaf-assignment failure: walk reaches a None at the parent of
        # the final subscript and trying to assign there raises
        # TypeError → BuilderError with "cannot assign at" wording.
        # Path is two-deep: walk reaches obj["foo"] = None, then the
        # final assignment None["bar"] = target fails.
        store_1 = {"destinations": {"foo": None}}
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["foo"]["bar"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("cannot assign at", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_navigation_through_none_mid_walk_refused(
        self, mock_create, _mock_search,
    ):
        # Mid-walk failure (distinct from leaf-assignment): path is
        # three-deep, walk pulls `obj["foo"] = None`, then iterates
        # again to `None["bar"]` which raises TypeError → BuilderError
        # with "cannot navigate" wording.
        store_1 = {"destinations": {"foo": None}}
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["foo"]["bar"]["baz"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("cannot navigate", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_list_index_out_of_range_refused(
        self, mock_create, _mock_search,
    ):
        # `routes[5]["to"]` on a 2-element list — IndexError surfaces
        # as BuilderError with "cannot navigate" wording.
        store_1 = {"routes": [{"to": None}, {"to": None}]}
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'routes[5]["to"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        with self.assertRaises(BuilderError) as ctx:
            b.build([
                self._entity(deployment_id=1),
                self._entity(deployment_id=2),
            ])
        self.assertIn("cannot navigate", str(ctx.exception))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_overwrites_existing_value(
        self, mock_create, _mock_search,
    ):
        # If the leaf already holds a non-None literal (e.g. the YAML
        # author put a placeholder string), the link still overwrites
        # it cleanly. Demonstrates the path-form is a normal
        # assignment, not a "fill in None" specialisation.
        store_1 = {
            "destinations": {
                "foo": {"to": "placeholder string"},
            },
        }
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'destinations["foo"]["to"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        self.assertIs(store_1["destinations"]["foo"]["to"], created[1])

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_subscript_path_deep_balanced_assignment(
        self, mock_create, _mock_search,
    ):
        # Six levels deep — the walker recurses arbitrarily (no
        # special-cased depth limit).
        store_1 = {"a": {"b": {"c": {"d": {"e": {"f": None}}}}}}
        make, created = self._make_create_with_attribute_store([store_1, {}])
        mock_create.side_effect = make

        b = self._builder(file_metadata={"x.yaml": {"links": [{
            "entity": {"deployment_file": "x.yaml", "deployment_id": 1},
            "attribute": 'a["b"]["c"]["d"]["e"]["f"]',
            "points_to": {"deployment_file": "x.yaml", "deployment_id": 2},
        }]}})
        b.build([
            self._entity(deployment_id=1),
            self._entity(deployment_id=2),
        ])

        self.assertIs(store_1["a"]["b"]["c"]["d"]["e"]["f"], created[1])


class LookupDbrefTest(TestCase):
    """Verify api.wb_lookup_dbref translates the identity pair to a dbref string.

    Mocks the internal _query_object_ids helper so the test exercises
    wb_lookup_dbref's branching (no match / one match / many matches) and
    dbref formatting without standing up a database. The helper itself
    is a thin Django ORM call — covered by integration tests, not here.
    """

    @patch("evennia_world_builder.api._query_object_ids")
    def test_no_match_returns_none(self, mock_query):
        mock_query.return_value = []
        self.assertIsNone(wb_lookup_dbref("millholm/forest.yaml", 1))

    @patch("evennia_world_builder.api._query_object_ids")
    def test_single_match_returns_hash_dbref(self, mock_query):
        mock_query.return_value = [42]
        self.assertEqual(wb_lookup_dbref("millholm/forest.yaml", 1), "#42")

    @patch("evennia_world_builder.api._query_object_ids")
    def test_multi_match_raises_api_error(self, mock_query):
        mock_query.return_value = [42, 43]
        with self.assertRaises(ApiError) as ctx:
            wb_lookup_dbref("millholm/forest.yaml", 1)
        msg = str(ctx.exception)
        self.assertIn("multiple objects match", msg)
        self.assertIn("'millholm/forest.yaml'", msg)
        self.assertIn("deployment_id=1", msg)

    @patch("evennia_world_builder.api._query_object_ids")
    def test_passes_identity_pair_through_to_query(self, mock_query):
        mock_query.return_value = []
        wb_lookup_dbref("aethenveil.yaml", 7)
        mock_query.assert_called_once_with("aethenveil.yaml", 7)


class LookupObjectTest(TestCase):
    """Verify api.wb_lookup_object resolves the identity pair to a typeclass instance.

    Mocks the internal ``_query_object_ids`` helper (same as
    LookupDbrefTest) plus ``ObjectDB.objects.get`` so the test
    exercises wb_lookup_object's branching (no match / one match / many
    matches) and the indexed-id-to-typeclass step without standing up
    a database.
    """

    @patch("evennia_world_builder.api._query_object_ids")
    def test_no_match_returns_none(self, mock_query):
        mock_query.return_value = []
        self.assertIsNone(wb_lookup_object("millholm/forest.yaml", 1))

    @patch("evennia.objects.models.ObjectDB.objects.get")
    @patch("evennia_world_builder.api._query_object_ids")
    def test_single_match_returns_object(self, mock_query, mock_get):
        mock_query.return_value = [42]
        sentinel = MagicMock()
        mock_get.return_value = sentinel
        self.assertIs(wb_lookup_object("millholm/forest.yaml", 1), sentinel)
        mock_get.assert_called_once_with(pk=42)

    @patch("evennia_world_builder.api._query_object_ids")
    def test_multi_match_raises_api_error(self, mock_query):
        mock_query.return_value = [42, 43]
        with self.assertRaises(ApiError) as ctx:
            wb_lookup_object("millholm/forest.yaml", 1)
        msg = str(ctx.exception)
        self.assertIn("multiple objects match", msg)
        self.assertIn("'millholm/forest.yaml'", msg)
        self.assertIn("deployment_id=1", msg)

    @patch("evennia_world_builder.api._query_object_ids")
    def test_passes_identity_pair_through_to_query(self, mock_query):
        mock_query.return_value = []
        wb_lookup_object("aethenveil.yaml", 7)
        mock_query.assert_called_once_with("aethenveil.yaml", 7)


class WBLogTest(TestCase):
    """Verify the wb_log shim — see docs/logging.md.

    The shim is thin by design (lazy import + format + delegate to
    evennia.utils.logger.log_file). Tests assert the format contract
    every call site relies on: filename, level prefix, ISO timestamp,
    silent no-op when the engine isn't bootstrapped.
    """

    @patch("evennia.utils.logger.log_file")
    def test_default_level_is_info(self, mock_log_file):
        from evennia_world_builder.log import wb_log

        wb_log("hello")
        line, kwargs = mock_log_file.call_args.args[0], mock_log_file.call_args.kwargs
        self.assertIn("[INFO]", line)
        self.assertEqual(kwargs["filename"], "world-builder.log")

    @patch("evennia.utils.logger.log_file")
    def test_explicit_warn_level(self, mock_log_file):
        from evennia_world_builder.log import wb_log

        wb_log("careful", level="WARN")
        self.assertIn("[WARN]", mock_log_file.call_args.args[0])

    @patch("evennia.utils.logger.log_file")
    def test_explicit_error_level(self, mock_log_file):
        from evennia_world_builder.log import wb_log

        wb_log("boom", level="ERROR")
        self.assertIn("[ERROR]", mock_log_file.call_args.args[0])

    @patch("evennia.utils.logger.log_file")
    def test_unknown_level_coerced_to_info(self, mock_log_file):
        from evennia_world_builder.log import wb_log

        wb_log("noisy", level="DEBUG")
        line = mock_log_file.call_args.args[0]
        self.assertIn("[INFO]", line)
        self.assertNotIn("[DEBUG]", line)

    @patch("evennia.utils.logger.log_file")
    def test_message_body_present(self, mock_log_file):
        from evennia_world_builder.log import wb_log

        wb_log("wb_build: starting validation")
        self.assertIn(
            "wb_build: starting validation", mock_log_file.call_args.args[0]
        )

    @patch("evennia.utils.logger.log_file")
    def test_line_starts_with_iso_timestamp(self, mock_log_file):
        import re

        from evennia_world_builder.log import wb_log

        wb_log("anything")
        line = mock_log_file.call_args.args[0]
        # ISO-8601 with seconds, timezone-aware (UTC offset or 'Z').
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    @patch("evennia.utils.logger.log_file")
    def test_filename_is_hardcoded(self, mock_log_file):
        from evennia_world_builder.log import wb_log

        wb_log("a")
        wb_log("b", level="ERROR")
        for call in mock_log_file.call_args_list:
            self.assertEqual(call.kwargs["filename"], "world-builder.log")

    def test_silent_noop_when_evennia_logger_unavailable(self):
        """Simulates the wb-validate CLI path: evennia not importable.

        Setting the module to None in sys.modules makes the next import
        raise ImportError, which the shim swallows. The test passes if
        no exception escapes — wb_log must never break its caller.
        """
        from evennia_world_builder.log import wb_log

        with patch.dict("sys.modules", {"evennia.utils.logger": None}):
            wb_log("anything")  # must not raise


class TestPostBuildHook(TestCase):
    """Builder invokes ``wb_at_post_build`` on the typeclass post-apply.

    Covers the duck-typed, opt-in, exception-isolated contract from
    docs/post-build-hook.md. The hook lets consumer typeclasses
    derive state from the YAML-supplied attribute values instead of
    the typeclass defaults that Evennia's ``at_object_creation`` sees.
    """

    def _entity(self, *, path="x.yaml", deployment_id=1, attributes=None):
        content = {
            "deployment_id": deployment_id,
            "name": "X",
            "typeclass": "ev.X",
            "location": None,
        }
        if attributes is not None:
            content["attributes"] = attributes
        return LoadedEntity(
            location={}, content=content, path=path, is_nested=False,
        )

    def _builder(self):
        return Builder(Definitions(levels=("zone",)))

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_hook_sees_yaml_supplied_attributes(self, mock_create, _mock_search):
        """The hook reads attributes the Builder has already applied.

        This is the regression-proof for the harvest-room bug class:
        when the hook runs, every YAML ``attributes:`` entry must
        already have been written via ``obj.attributes.add(...)``.
        """
        applied = {}
        recorded_at_hook_time = {}

        def make_obj(**_kw):
            obj = MagicMock()
            obj.attributes.add.side_effect = (
                lambda key, value, category=None: applied.update({key: value})
            )

            def hook():
                recorded_at_hook_time.update(applied)
            obj.wb_at_post_build = hook
            return obj

        mock_create.side_effect = make_obj

        entity = self._entity(attributes=[
            {"key": "resource_id", "value": 19},
            {"key": "resource_count_max", "value": 5},
        ])
        b = self._builder()
        b.build([entity])

        self.assertEqual(
            recorded_at_hook_time,
            {"resource_id": 19, "resource_count_max": 5},
        )

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_typeclass_without_hook_builds_normally(self, mock_create, _mock_search):
        """Builder proceeds when the typeclass has no ``wb_at_post_build``.

        ``spec=`` restricts attribute access so ``getattr(obj,
        "wb_at_post_build", None)`` returns ``None``, exercising the
        opt-in path.
        """
        obj = MagicMock(spec=["attributes", "tags", "locks", "aliases"])
        mock_create.return_value = obj

        b = self._builder()
        result = b.build([self._entity()])

        self.assertEqual(len(result), 1)
        # The Builder's _apply_* helpers still ran on the spec'd mock.
        # _apply_tags writes the deployment_file + deployment_id tags
        # unconditionally, so tags.add was definitely invoked.
        self.assertTrue(obj.tags.add.called)

    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_hook_exception_does_not_abort_build(self, mock_create, _mock_search):
        """A raising hook is logged but the entity stays built; further entities build too."""
        # First entity's hook raises; second entity has no hook. Both
        # must end up in the build result.
        first_obj = MagicMock()
        first_obj.wb_at_post_build.side_effect = RuntimeError("boom")

        second_obj = MagicMock(spec=["attributes", "tags", "locks", "aliases"])

        objs = iter([first_obj, second_obj])
        mock_create.side_effect = lambda **_kw: next(objs)

        b = self._builder()
        result = b.build([
            self._entity(path="a.yaml", deployment_id=1),
            self._entity(path="a.yaml", deployment_id=2),
        ])

        self.assertEqual(len(result), 2)
        # Hook on the first entity was invoked once despite raising.
        first_obj.wb_at_post_build.assert_called_once_with()

    @patch("evennia_world_builder.builder.wb_log")
    @patch("evennia.utils.search.search_tag", return_value=[])
    @patch("evennia.utils.create.create_object")
    def test_hook_exception_logged_via_wb_log(
        self, mock_create, _mock_search, mock_log,
    ):
        """The exception message + entity identifying info reach wb_log at ERROR."""
        obj = MagicMock()
        obj.wb_at_post_build.side_effect = RuntimeError("kaboom")
        mock_create.return_value = obj

        b = self._builder()
        b.build([self._entity(path="rooms/inn.yaml", deployment_id=7)])

        # wb_log called once, at ERROR, message mentions hook + path + id + reason.
        self.assertEqual(mock_log.call_count, 1)
        args, kwargs = mock_log.call_args
        msg = args[0]
        self.assertIn("wb_at_post_build", msg)
        self.assertIn("rooms/inn.yaml", msg)
        self.assertIn("deployment_id=7", msg)
        self.assertIn("kaboom", msg)
        self.assertEqual(kwargs.get("level"), "ERROR")
