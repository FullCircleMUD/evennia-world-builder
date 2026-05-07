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
ultimately exercise. Validation gating (when wb_build pre-validates
the whole repo vs trusting the consumer's CI gate) is documented in
DESIGN/validation-gating.md.
"""
import pprint

from evennia.commands.command import Command as BaseCommand

from .builder import Builder
from .config import get_configured_reader
from .definitions import Definitions
from .errors import (
    BuilderError,
    DefinitionsError,
    FinderManifestError,
    FinderQueryError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ReaderError,
    ValidatorError,
)
from .finder import Finder
from .loader import Loader
from .validator import Validator


_ALL_TOKEN = "all"
_FORCE_VALIDATE_FLAG = "force-validate"


def _parse_args(args_str: str) -> tuple[dict, set]:
    """Parse ``all | level=value...  [--flag...]`` into ``(query, flags)``.

    Returns:
        query: dict of level → value. Empty dict means the literal ``all``
               token was supplied (build-everything scope).
        flags: set of flag names (with the leading ``--`` stripped).

    Raises:
        ValueError: if no scope token is present, if a non-flag token
                    is malformed, or if either side of an ``=`` is empty.
    """
    pairs: dict = {}
    flags: set = set()
    positional: list = []

    for token in args_str.split():
        if token.startswith("--"):
            flags.add(token[2:])
        else:
            positional.append(token)

    if not positional:
        raise ValueError(
            "no scope specified — use 'all' or one or more level=value pairs"
        )

    if positional == [_ALL_TOKEN]:
        return pairs, flags

    for token in positional:
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
    return pairs, flags


def _filter_by_query(entities: list, query: dict) -> list:
    """Return the subset of entities whose location matches every (k, v) in query.

    Used after a whole-repo pre-validation pass to extract the build-scoped
    subset without re-running the Loader. An empty query matches everything.
    """
    if not query:
        return entities
    return [
        e for e in entities
        if all(e.location.get(k) == v for k, v in query.items())
    ]


def _run_validator(
    caller, definitions, entities, refusal_label, *,
    resolve_cross_refs: bool,
    file_metadata: dict | None = None,
) -> bool:
    """Run a Validator pass over entities. Surface every message via caller.

    Returns True on a clean pass, False if the validator refused. Callers
    should return early on False. wb_build runs inside Evennia, so the
    validator gets evennia_runtime=True (Tier 3 predicates fire — e.g.
    typeclass-resolvable). ``resolve_cross_refs`` is True only for the
    whole-repo pre-validation path; scope-only validation trusts CI for
    cross-ref resolution and skips Tier 4 (its seen_ids index would be
    incomplete and produce false-positive misses on cross-file refs).
    ``file_metadata`` is the per-file metadata dict from Loader.LoadResult,
    used for file-level checks (incoming_exits shape + Tier 4 resolution).
    """
    validator = Validator(
        definitions,
        evennia_runtime=True,
        resolve_cross_refs=resolve_cross_refs,
        file_metadata=file_metadata,
    )
    try:
        validator.validate(entities)
    except ValidatorError as e:
        for msg in validator.messages:
            caller.msg(msg)
        caller.msg(f"wb_build: refusing to build — {refusal_label}: {e}")
        return False
    for msg in validator.messages:
        caller.msg(msg)
    return True


class CmdWBBuild(BaseCommand):
    """Build world content from the configured manifest source.

    Usage:
        wb_build all [--force-validate]
        wb_build <level>=<value> [<level>=<value> ...] [--force-validate]

    A bare ``wb_build`` with no scope does nothing — the explicit
    ``all`` keyword is required to build the entire world. This is a
    deliberate guard rail against an accidental full-world rebuild.

    Validation gating: ``definitions.yaml`` carries a
    ``repo-ci-pre-validation`` flag (default ``false``). When false,
    wb_build pre-validates the whole repo before every build — safe
    but expensive at scale. When the consumer has set up CI to gate
    PRs (GitHub branch protection + required wb-validate check), they
    can flip the flag to true and wb_build will skip the whole-repo
    walk and trust the gate. ``--force-validate`` overrides the flag
    for one invocation: pre-validate this run regardless.

    Examples (assuming ``levels: [zone, room]``):

        wb_build all
            Build everything in the manifest.

        wb_build zone=millholm
            Build everything under zone=millholm.

        wb_build zone=millholm room=bakery
            Build the single room.

        wb_build zone=millholm room=bakery --force-validate
            Same, but pre-validate the whole repo first regardless of
            the gating setting.
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

        try:
            query, flags = _parse_args(args)
        except ValueError as e:
            caller.msg(f"wb_build: {e}")
            return

        try:
            reader = get_configured_reader()
        except Exception as e:
            caller.msg(
                f"wb_build: could not construct reader "
                f"(check WORLDBUILDER_READER and WORLDBUILDER_READER_KWARGS): {e}"
            )
            return

        try:
            definitions = Definitions.from_reader(reader)
        except ReaderError as e:
            caller.msg(f"wb_build: could not load definitions.yaml: {e}")
            return
        except DefinitionsError as e:
            caller.msg(f"wb_build: definitions.yaml is malformed: {e}")
            return

        try:
            definitions.validate_query(query)
        except DefinitionsError as e:
            caller.msg(f"wb_build: {e}")
            return

        caller.msg(
            f"wb_build: levels={list(definitions.levels)}, query={query}, "
            f"flags={sorted(flags)}"
        )

        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)

        # Decide whether to pre-validate the whole repo. The setting is the
        # consumer's persistent claim ("I have CI gating"); the flag is an
        # ad-hoc per-invocation override. Pre-validation runs whenever EITHER
        # the setting is False (default safe) OR the flag is present.
        force_validate = _FORCE_VALIDATE_FLAG in flags
        should_pre_validate = (not definitions.repo_ci_pre_validation) or force_validate

        if should_pre_validate:
            reason = (
                "--force-validate flag set"
                if force_validate
                else "definitions.repo-ci-pre-validation is False"
            )
            caller.msg(f"wb_build: pre-validating whole repo ({reason})")
            try:
                load_result = loader.load(finder.find())
            except FinderManifestError as e:
                caller.msg(f"wb_build: manifest error during pre-validation: {e}")
                return
            except (LoaderMissingIndexError, LoaderMissingEntryError) as e:
                caller.msg(f"wb_build: pre-validation load failed: {e}")
                return
            except ReaderError as e:
                caller.msg(f"wb_build: read error during pre-validation: {e}")
                return

            all_entities = load_result.entities
            file_metadata = load_result.file_metadata

            caller.msg(
                f"wb_build: pre-validation loaded {len(all_entities)} "
                f"entit{'y' if len(all_entities) == 1 else 'ies'} (whole repo)"
            )
            if not _run_validator(
                caller, definitions, all_entities, "pre-validation failed",
                resolve_cross_refs=True,
                file_metadata=file_metadata,
            ):
                return

            entities = _filter_by_query(all_entities, query)
            caller.msg(
                f"wb_build: filtered to {len(entities)} entit"
                f"{'y' if len(entities) == 1 else 'ies'} matching scope"
            )
        else:
            caller.msg(
                "wb_build: skipping pre-validation "
                "(definitions.repo-ci-pre-validation is True; trusting CI gate)"
            )
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

            try:
                load_result = loader.load(found)
            except (LoaderMissingIndexError, LoaderMissingEntryError) as e:
                caller.msg(f"wb_build: {e}")
                return
            except ReaderError as e:
                caller.msg(f"wb_build: read error during loading: {e}")
                return

            entities = load_result.entities
            file_metadata = load_result.file_metadata

            caller.msg(
                f"wb_build: Loader returned {len(entities)} entit"
                f"{'y' if len(entities) == 1 else 'ies'}"
            )
            if not _run_validator(
                caller, definitions, entities, "validation failed",
                resolve_cross_refs=False,
                file_metadata=file_metadata,
            ):
                return

        for i, entity in enumerate(entities, 1):
            caller.msg(
                f"  [{i}] {entity.path} — location={entity.location}"
            )
            caller.msg(f"      content: {pprint.pformat(entity.content)}")

        builder = Builder(definitions, file_metadata=file_metadata)
        try:
            created = builder.build(entities)
        except BuilderError as e:
            caller.msg(f"wb_build: build failed: {e}")
            return

        if builder.deleted_count:
            caller.msg(
                f"wb_build: Builder cleaned up {builder.deleted_count} existing "
                f"object{'' if builder.deleted_count == 1 else 's'} "
                f"(cleanup-on-rebuild)"
            )
        caller.msg(
            f"wb_build: Builder created {len(created)} object{'' if len(created) == 1 else 's'}:"
        )
        for obj in created:
            caller.msg(f"  {obj.dbref} {obj.key} ({obj.typeclass_path})")
