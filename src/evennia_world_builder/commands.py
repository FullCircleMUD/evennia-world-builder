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

from evennia.commands.command import Command as BaseCommand
from evennia.utils.utils import run_async

from evennia_yaml_reader import ReaderError

# Optional integration with evennia-shards. When the shards library is
# installed and configured, ``preserve_tenant_context`` captures the
# active tenant at wrap time and re-applies it inside the deferred
# worker thread — without this, ObjectDB rows built in the worker
# would land ``shard_id=NULL`` because multitenant's threading.local
# tenant doesn't propagate across the ``run_async`` thread spawn. When
# the shards library isn't installed, the import fails and we fall
# back to an identity passthrough that's a no-op.
try:
    from evennia_shards import preserve_tenant_context
except ImportError:
    def preserve_tenant_context(fn):
        return fn

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
    ValidatorError,
)
from .finder import Finder
from .loader import Loader
from .log import wb_log
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
    messages: list, definitions, entities, refusal_label, *,
    resolve_cross_refs: bool,
    file_metadata: dict | None = None,
) -> bool:
    """Run a Validator pass over entities. Append every message to ``messages``.

    Returns True on a clean pass, False if the validator refused. Callers
    should return early on False. wb_build runs inside Evennia, so the
    validator gets evennia_runtime=True (Tier 3 predicates fire — e.g.
    typeclass-resolvable). ``resolve_cross_refs`` is True only for the
    whole-repo pre-validation path; scope-only validation trusts CI for
    cross-ref resolution and skips Tier 4 (its seen_ids index would be
    incomplete and produce false-positive misses on cross-file refs).
    ``file_metadata`` is the per-file metadata dict from Loader.LoadResult,
    used for file-level checks (incoming_exits shape + Tier 4 resolution).

    Output goes via the ``messages`` list so the caller can flush it to
    ``caller.msg`` from the reactor thread (this helper itself runs in a
    Twisted worker thread under run_async).
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
        messages.extend(validator.messages)
        messages.append(f"wb_build: refusing to build — {refusal_label}: {e}")
        wb_log(
            f"wb_build: validation refused — {refusal_label}: {e}",
            level="ERROR",
        )
        for finding in validator.messages:
            wb_log(f"  validator: {finding}", level="INFO")
        return False
    # On success: do not flush validator.messages (the success-path
    # messages are diagnostic noise for the operator). Failures still
    # get them above so findings reach the caller.
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
        # Reactor thread — keep this fast. Args parsing gives the
        # operator immediate feedback for malformed input. Everything
        # heavy (network/DB/build) is handed off to a Twisted worker
        # thread via run_async so the reactor stays free for player
        # input during long deployments.
        args = (self.args or "").strip()

        if not args:
            self.caller.msg(
                "wb_build: no scope specified. Use `wb_build all` to build "
                "the entire world, or specify a query like "
                "`wb_build zone=millholm`."
            )
            return

        try:
            query, flags = _parse_args(args)
        except ValueError as e:
            self.caller.msg(f"wb_build: {e}")
            return

        # Hand the pipeline off to a worker thread. caller.msg() can't
        # be called from the worker safely; the at_return / at_err
        # callbacks fire back on the reactor and flush the collected
        # message list there.
        #
        # The pipeline callable is wrapped with preserve_tenant_context
        # so any shards-tenant active on the reactor thread carries
        # into the worker — without it, every ObjectDB row built in
        # the worker would land unstamped. No-op when shards isn't
        # installed (see top-of-file import).
        self.caller.msg(f"wb_build {args} : running async (gameplay continues)…")
        run_async(
            preserve_tenant_context(self._run_pipeline), query, flags,
            at_return=self._on_async_return,
            at_err=self._on_async_err,
        )

    def _run_pipeline(self, query: dict, flags: set) -> list:
        """Worker-thread entrypoint: runs the entire build pipeline.

        Collects every operator-facing line into a list of messages and
        returns it. ``at_return`` flushes the list via ``caller.msg``
        on the reactor thread. Errors with operator-meaningful context
        (validation refusal, build failure, etc.) get appended as
        messages and the function returns normally; only unexpected
        exceptions bubble out for the ``at_err`` callback to handle.
        """
        messages: list = []

        scope_desc = "all" if not query else " ".join(
            f"{k}={v}" for k, v in query.items()
        )
        flag_desc = " ".join(f"--{f}" for f in sorted(flags)) if flags else ""
        wb_log(
            f"wb_build started: scope={scope_desc}"
            + (f" {flag_desc}" if flag_desc else "")
        )

        try:
            reader = get_configured_reader()
        except Exception as e:
            msg = (
                f"wb_build: could not construct reader "
                f"(check WORLDBUILDER_READER and WORLDBUILDER_READER_KWARGS): {e}"
            )
            messages.append(msg)
            wb_log(msg, level="ERROR")
            return messages

        try:
            definitions = Definitions.from_reader(reader)
        except ReaderError as e:
            msg = f"wb_build: could not load definitions.yaml: {e}"
            messages.append(msg)
            wb_log(msg, level="ERROR")
            return messages
        except DefinitionsError as e:
            msg = f"wb_build: definitions.yaml is malformed: {e}"
            messages.append(msg)
            wb_log(msg, level="ERROR")
            return messages

        try:
            definitions.validate_query(query)
        except DefinitionsError as e:
            msg = f"wb_build: {e}"
            messages.append(msg)
            wb_log(msg, level="ERROR")
            return messages

        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)

        # Decide whether to pre-validate the whole repo. The setting is the
        # consumer's persistent claim ("I have CI gating"); the flag is an
        # ad-hoc per-invocation override. Pre-validation runs whenever EITHER
        # the setting is False (default safe) OR the flag is present.
        force_validate = _FORCE_VALIDATE_FLAG in flags
        should_pre_validate = (not definitions.repo_ci_pre_validation) or force_validate

        messages.append("wb_build: starting validation")
        wb_log(
            "wb_build: validation started "
            f"({'pre-validate whole repo' if should_pre_validate else 'scope-only'})"
        )

        if should_pre_validate:
            try:
                load_result = loader.load(finder.find())
            except FinderManifestError as e:
                msg = f"wb_build: manifest error during pre-validation: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages
            except (LoaderMissingIndexError, LoaderMissingEntryError) as e:
                msg = f"wb_build: pre-validation load failed: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages
            except ReaderError as e:
                msg = f"wb_build: read error during pre-validation: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages

            all_entities = load_result.entities
            file_metadata = load_result.file_metadata

            if not _run_validator(
                messages, definitions, all_entities, "pre-validation failed",
                resolve_cross_refs=True,
                file_metadata=file_metadata,
            ):
                return messages

            entities = _filter_by_query(all_entities, query)
        else:
            try:
                found = finder.find(query)
            except FinderQueryError as e:
                msg = f"wb_build: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages
            except FinderManifestError as e:
                msg = f"wb_build: manifest error: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages

            try:
                load_result = loader.load(found)
            except (LoaderMissingIndexError, LoaderMissingEntryError) as e:
                msg = f"wb_build: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages
            except ReaderError as e:
                msg = f"wb_build: read error during loading: {e}"
                messages.append(msg)
                wb_log(msg, level="ERROR")
                return messages

            entities = load_result.entities
            file_metadata = load_result.file_metadata

            if not _run_validator(
                messages, definitions, entities, "validation failed",
                resolve_cross_refs=False,
                file_metadata=file_metadata,
            ):
                return messages

        messages.append("wb_build: validation complete")
        messages.append("wb_build: starting building")
        wb_log(f"wb_build: validation complete ({len(entities)} entities in scope)")
        wb_log("wb_build: build started")

        builder = Builder(
            definitions,
            file_metadata=file_metadata,
            reader=reader,
        )
        try:
            created = builder.build(entities)
        except BuilderError as e:
            msg = f"wb_build: build failed: {e}"
            messages.append(msg)
            wb_log(msg, level="ERROR")
            return messages

        if builder.deleted_count:
            messages.append(
                f"wb_build: cleaned up {builder.deleted_count} existing "
                f"object{'' if builder.deleted_count == 1 else 's'}"
            )
        for obj in created:
            messages.append(f"  {obj.dbref} {obj.key} ({obj.typeclass_path})")
        messages.append(
            f"wb_build: finished building "
            f"({len(created)} object{'' if len(created) == 1 else 's'})"
        )
        wb_log(
            f"wb_build: build complete "
            f"({len(created)} created, {builder.deleted_count} cleaned)"
        )
        return messages

    def _on_async_return(self, messages: list) -> None:
        """Reactor-thread callback: flush every message to the operator."""
        for msg in messages or []:
            self.caller.msg(msg)

    def _on_async_err(self, failure) -> None:
        """Reactor-thread callback for unexpected pipeline exceptions.

        Any error the pipeline catches itself goes to ``messages`` and
        comes back through ``_on_async_return``. This handler only
        fires for exceptions that escape the pipeline (e.g. a Loader
        bug, a programming error). The Twisted ``Failure`` carries the
        traceback; the full text goes to world-builder.log so a later
        operator can read it, the operator at the prompt gets a single
        line.
        """
        wb_log(
            f"wb_build: unexpected error during async build: "
            f"{failure.getErrorMessage()}\n{failure.getTraceback()}",
            level="ERROR",
        )
        self.caller.msg(
            f"wb_build: unexpected error during async build: "
            f"{failure.getErrorMessage()}"
        )
