# SPDX-License-Identifier: BSD-3-Clause
"""Library-shipped admin commands.

Conventions for any command shipping from world-builder:

- Key is prefixed `wb_` so it namespaces cleanly and a stray short
  command name (`build`) cannot accidentally invoke library work.
- Locked to `cmd:superuser()` — only the actual superuser may invoke.
- Auto-installed by world-builder's AppConfig (see apps.py) into
  AccountCmdSet, so the command works both OOC and IC. The consumer
  game does not need to import or wire these manually.

See DESIGN/discovery-and-loading.md for the pipeline these commands
ultimately exercise.
"""
import pprint

from evennia.commands.command import Command as BaseCommand

from .config import get_configured_reader
from .definitions import Definitions
from .errors import (
    DefinitionsError,
    FinderManifestError,
    FinderQueryError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ReaderError,
)
from .finder import Finder
from .loader import Loader


def _parse_kv_args(args_str: str) -> dict:
    """Parse 'key1=val1 key2=val2' into a dict.

    Empty input returns an empty dict (the "build all" query).

    Raises ValueError if any token isn't of the form key=value or if
    either side of the `=` is empty.
    """
    pairs: dict = {}
    for token in args_str.split():
        if "=" not in token:
            raise ValueError(
                f"Argument {token!r} is not of the form key=value"
            )
        key, _, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(
                f"Argument {token!r}: both key and value must be non-empty"
            )
        pairs[key] = value
    return pairs


class CmdWBBuild(BaseCommand):
    """Build world content from the configured manifest source.

    Usage:
        wb_build all
        wb_build <level>=<value> [<level>=<value> ...]

    A bare ``wb_build`` with no scope does nothing — the explicit
    ``all`` keyword is required to build the entire world. This is a
    deliberate guard rail against an accidental full-world rebuild.

    Examples (assuming the consumer's definitions.yaml declares
    ``levels: [zone, room]``):

        wb_build all
            Build everything in the manifest.

        wb_build zone=millholm
            Build everything under zone=millholm.

        wb_build zone=millholm room=bakery
            Build the single room.
    """

    key = "wb_build"
    locks = "cmd:superuser()"
    help_category = "World Builder"

    def func(self):
        caller = self.caller
        args = (self.args or "").strip()

        if not args:
            caller.msg(
                "wb_build: no scope specified. Use `wb_build all` to build "
                "the entire world, or specify a query like "
                "`wb_build zone=millholm`."
            )
            return

        if args == "all":
            query: dict = {}
        else:
            try:
                query = _parse_kv_args(args)
            except ValueError as e:
                caller.msg(f"wb_build: {e}")
                return

        # Get a configured reader (resolved class + WORLDBUILDER_READER_KWARGS).
        try:
            reader = get_configured_reader()
        except Exception as e:
            caller.msg(
                f"wb_build: could not construct reader "
                f"(check WORLDBUILDER_READER and WORLDBUILDER_READER_KWARGS): {e}"
            )
            return

        # Load definitions.yaml from the source.
        try:
            definitions = Definitions.from_reader(reader)
        except ReaderError as e:
            caller.msg(f"wb_build: could not load definitions.yaml: {e}")
            return
        except DefinitionsError as e:
            caller.msg(f"wb_build: definitions.yaml is malformed: {e}")
            return

        # Semantic validation: query keys must be a contiguous prefix of levels.
        try:
            definitions.validate_query(query)
        except DefinitionsError as e:
            caller.msg(f"wb_build: {e}")
            return

        caller.msg(
            f"wb_build: levels={list(definitions.levels)}, query={query}"
        )

        # Walk the manifest tree to find the entry point matching the query.
        finder = Finder(reader, definitions)
        try:
            found = finder.find(query)
        except FinderQueryError as e:
            caller.msg(f"wb_build: {e}")
            return
        except FinderManifestError as e:
            caller.msg(f"wb_build: manifest error: {e}")
            return

        caller.msg(
            f"wb_build: Finder located path={found.path!r}, "
            f"kind={found.kind}, location={found.location}"
        )

        # Recursively read all leaf content under the located entry point.
        loader = Loader(reader, definitions)
        try:
            entities = loader.load(found)
        except LoaderMissingIndexError as e:
            caller.msg(f"wb_build: {e}")
            return
        except LoaderMissingEntryError as e:
            caller.msg(f"wb_build: {e}")
            return
        except ReaderError as e:
            caller.msg(f"wb_build: read error during loading: {e}")
            return

        caller.msg(
            f"wb_build: Loader returned {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}"
        )
        for i, entity in enumerate(entities, 1):
            caller.msg(
                f"  [{i}] {entity.path} — location={entity.location}"
            )
            caller.msg(f"      content: {pprint.pformat(entity.content)}")
