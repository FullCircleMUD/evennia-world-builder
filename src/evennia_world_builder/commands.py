# SPDX-License-Identifier: BSD-3-Clause
"""Library-shipped admin commands.

Conventions for any command shipping from world-builder:

- Key is prefixed `wb_` so it namespaces cleanly and a stray short
  command name (`build`) cannot accidentally invoke library work.
- Locked to `cmd:superuser()` — only the actual superuser may invoke.
- Auto-installed by world-builder's AppConfig (see apps.py) into
  AccountCmdSet, so the command works both OOC and IC. The consumer
  game does not need to import or wire these manually.

See docs/discovery-and-loading.md for the pipeline these commands
ultimately exercise.
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
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ValidatorError,
)
from .finder import Finder
from .loader import Loader
from .log import wb_log
from .validator import Validator


_ALL_TOKEN = "all"

SHARD_LEVEL = "shard"
"""The level name that carries the shard id when co-installed with shards.

Level names are otherwise consumer-chosen. This is the one naming rule the
shards pairing imposes, and it is what lets ``wb_build`` tell which shard a
scope belongs to. See docs/interoperability.md.
"""


def active_shard_id() -> str | None:
    """Return this process's shard id, or ``None`` if not sharded.

    "Sharded" means the shards library is installed **and** the role is not
    ``monolith`` — a successful import is not the test, because monolith is
    a non-sharded install where no shard context is ever set. Returning
    ``None`` is the signal for callers to skip every shard check and behave
    exactly as they do standalone.
    """
    try:
        from evennia_shards import ROLE_MONOLITH, get_role, get_shard_id
    except ImportError:
        return None

    if get_role() == ROLE_MONOLITH:
        return None
    return get_shard_id()


def check_shard_scope(query: dict) -> str | None:
    """Return an operator-facing refusal, or ``None`` if the scope is fine.

    Three refusals, all no-ops off a sharded deployment:

    - the build-everything scope, which spans every shard's content and so
      can only ever be partly correct on one process;
    - a query that doesn't name the shard level at all;
    - a query naming a shard this process doesn't own. The router's
      ``SHARD_ID`` is mandated to be ``"router"``, so this rejects
      ``wb_build`` there — where a build would create rooms carrying no
      shard stamp — without needing a role check of its own.
    """
    shard_id = active_shard_id()
    if shard_id is None:
        return None

    if not query:
        return f"wb_build: `{_ALL_TOKEN}` is not available on a sharded deployment. Build one shard at a time."

    if next(iter(query)) != SHARD_LEVEL:
        return f"wb_build: the query must start with '{SHARD_LEVEL}='."

    if query[SHARD_LEVEL] != shard_id:
        return "wb_build: you can only run this from the shard it is building on."

    return None


def check_shard_levels(definitions) -> str | None:
    """Return a refusal if the shard level was never adopted, else ``None``.

    Checked once ``definitions.yaml`` is parsed, because that is the first
    point the declared levels are known. Nothing else catches this: a
    consumer who co-installs shards but keeps their own level names has a
    query that validates perfectly against their own declarations, so the
    breach would otherwise surface only as a confusing "not a declared
    level" error. No-op off a sharded deployment.
    """
    if active_shard_id() is None:
        return None

    levels = definitions.levels
    if not levels or levels[0] != SHARD_LEVEL:
        return f"wb_build: the first level in definitions.yaml must be '{SHARD_LEVEL}'."
    return None


def _parse_args(args_str: str) -> dict:
    """Parse ``all | level=value...`` into a query dict.

    Returns:
        query: dict of level → value. Empty dict means the literal ``all``
               token was supplied (build-everything scope).

    Raises:
        ValueError: if any flag token is present (the command takes none),
                    if no scope token is present, if a token is malformed,
                    or if either side of an ``=`` is empty.
    """
    pairs: dict = {}
    positional: list = []

    for token in args_str.split():
        if token.startswith("--"):
            # No flags exist. Refusing beats ignoring: a silently dropped
            # token reads to the operator as though it took effect.
            raise ValueError(
                f"Unknown flag {token!r} — wb_build takes no flags"
            )
        positional.append(token)

    if not positional:
        raise ValueError(
            "no scope specified — use 'all' or one or more level=value pairs"
        )

    if positional == [_ALL_TOKEN]:
        return pairs

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

    return pairs


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
) -> dict | None:
    """Run a Validator pass over entities. Append every message to ``messages``.

    Returns the entity index (``{entity_id: file path}``) on a clean
    pass, or None if the validator refused — callers should return early
    on None. The index goes on to the Builder, which needs it to get
    from a reference back to the YAML that declares its target.

    wb_build runs inside Evennia, so the validator gets
    evennia_runtime=True (Tier 3 predicates fire — e.g.
    typeclass-resolvable). ``file_metadata`` is the per-file metadata
    dict from Loader.LoadResult, used for file-level checks (file_id,
    incoming_exits shape, and Tier 4 resolution).

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
        entity_paths = validator.validate(entities)
    except ValidatorError as e:
        messages.extend(validator.messages)
        messages.append(f"wb_build: refusing to build — {refusal_label}: {e}")
        wb_log(
            f"wb_build: validation refused — {refusal_label}: {e}",
            level="ERROR",
        )
        for finding in validator.messages:
            wb_log(f"  validator: {finding}", level="INFO")
        return None
    # On success: do not flush validator.messages (the success-path
    # messages are diagnostic noise for the operator). Failures still
    # get them above so findings reach the caller.
    return entity_paths


class CmdWBBuild(BaseCommand):
    """Build world content from the configured manifest source.

    Usage:
        wb_build all
        wb_build <level>=<value> [<level>=<value> ...]

    A bare ``wb_build`` with no scope does nothing — the explicit
    ``all`` keyword is required to build the entire world. This is a
    deliberate guard rail against an accidental full-world rebuild.

    Every invocation pre-validates the whole repo before building,
    however small the requested scope. There is no gated mode and no
    flag to skip it: correctness of cross-file references and of
    repo-wide identity uniqueness can only be established at full
    scope, and the cost of the walk belongs to the Reader rather than
    to the validation pass.

    Examples (assuming ``levels: [zone, room]``):

        wb_build all
            Build everything in the manifest.

        wb_build zone=millholm
            Build everything under zone=millholm.

        wb_build zone=millholm room=bakery
            Build the single room.

    On a sharded deployment (the shards library installed and the role
    not ``monolith``), the first declared level must be ``shard`` and its
    value must match the shard this process is running as — content can
    only be built from the process that owns it. ``wb_build all`` is
    refused there; build one shard at a time.
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
            query = _parse_args(args)
        except ValueError as e:
            self.caller.msg(f"wb_build: {e}")
            return

        # Refuse before dispatch on a sharded deployment: content may
        # only be built from the process that owns it. Synchronous so
        # the refusal reaches the operator directly rather than through
        # the async callbacks. No-op when not sharded.
        refusal = check_shard_scope(query)
        if refusal:
            self.caller.msg(refusal)
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
            preserve_tenant_context(self._run_pipeline), query,
            at_return=self._on_async_return,
            at_err=self._on_async_err,
        )

    def _run_pipeline(self, query: dict) -> list:
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
        wb_log(f"wb_build started: scope={scope_desc}")

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

        # The shard level can only be checked once definitions.yaml is
        # parsed. Ahead of validate_query so a consumer who never adopted
        # the mandate gets told that, rather than a generic "not a
        # declared level" for the shard key they were required to pass.
        refusal = check_shard_levels(definitions)
        if refusal:
            messages.append(refusal)
            wb_log(refusal, level="ERROR")
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

        # One path: load and validate the whole repo, then narrow to the
        # requested scope. Cross-file references and repo-wide identity
        # uniqueness are only checkable at full scope, so the walk runs
        # however small the build.
        messages.append("wb_build: starting validation")
        wb_log("wb_build: validation started (whole repo)")

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

        entity_paths = _run_validator(
            messages, definitions, all_entities, "pre-validation failed",
            resolve_cross_refs=True,
            file_metadata=file_metadata,
        )
        if entity_paths is None:
            return messages

        entities = _filter_by_query(all_entities, query)

        messages.append("wb_build: validation complete")
        messages.append("wb_build: starting building")
        wb_log(f"wb_build: validation complete ({len(entities)} entities in scope)")
        wb_log("wb_build: build started")

        builder = Builder(
            definitions,
            file_metadata=file_metadata,
            reader=reader,
            entity_paths=entity_paths,
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
