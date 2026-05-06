"""Demo command using the evennia-world-builder library reader.

Calls the configured reader (default: GitHubReader) to fetch and parse
a YAML file. Reads GitHub-specific settings from Django settings,
constructs the reader with the appropriate kwargs, and emits both the
raw bytes and the parsed dict.

Promoted from the original spike: the urllib/yaml fetch logic now
lives in evennia_world_builder.GitHubReader; this command is the thin
consumer-side wrapper that wires settings into the library.

See evennia-world-builder/DESIGN/spike-1-load-from-github.md for the
spike contract.
"""
import pprint

from django.conf import settings

from evennia.commands.command import Command as BaseCommand

from evennia_world_builder import (
    Definitions,
    FoundLocation,
    Loader,
    ReaderAuthError,
    ReaderError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
    get_reader_class,
)


class CmdWBLoad(BaseCommand):
    """Demo: fetch a YAML file using the configured world-builder reader.

    Usage:
        wbload

    Reads PAT, repo, ref, and path from Django settings (PAT lives in
    secret_settings.py for local dev). Resolves the configured reader
    via WORLDBUILDER_READER (default: evennia_world_builder.GitHubReader),
    constructs it with the kwargs, calls read(), and emits both raw
    bytes and parsed dict.
    """

    key = "wbload"
    locks = "cmd:perm(Developer)"
    help_category = "Spike"

    def func(self):
        caller = self.caller

        kwargs = {
            "repo": getattr(settings, "WORLDBUILDER_GITHUB_REPO", ""),
            "ref": getattr(settings, "WORLDBUILDER_GITHUB_REF", ""),
            "pat": getattr(settings, "WORLDBUILDER_GITHUB_PAT", ""),
        }
        path = getattr(settings, "WORLDBUILDER_GITHUB_PATH", "")
        missing = [
            name
            for name, value in (
                ("WORLDBUILDER_GITHUB_PAT", kwargs["pat"]),
                ("WORLDBUILDER_GITHUB_REPO", kwargs["repo"]),
                ("WORLDBUILDER_GITHUB_REF", kwargs["ref"]),
                ("WORLDBUILDER_GITHUB_PATH", path),
            )
            if not value
        ]
        if missing:
            caller.msg(
                "Spike: missing setting(s) in secret_settings.py: "
                f"{', '.join(missing)}. (After editing secret_settings.py, "
                "a full `evennia stop && evennia start` is required — "
                "`@reload` does not re-import settings.)"
            )
            return

        try:
            reader = get_reader_class()(**kwargs)
            result = reader.read(path)
        except ReaderAuthError:
            caller.msg("Spike: auth failed (401). Check PAT scope and expiry.")
            return
        except ReaderNotFoundError:
            caller.msg(
                "Spike: file not found (404). Check repo/ref/path settings."
            )
            return
        except ReaderNetworkError as e:
            caller.msg(f"Spike: network error: {e}")
            return
        except ReaderParseError as e:
            caller.msg(f"Spike: YAML parse failed: {e}")
            return
        except ReaderError as e:
            caller.msg(f"Spike: reader error: {e}")
            return

        caller.msg(
            f"Spike: fetched {len(result.raw_bytes)} bytes from "
            f"{kwargs['repo']}@{kwargs['ref']}:{path}"
        )
        caller.msg("--- raw ---")
        caller.msg(result.raw_bytes.decode("utf-8", errors="replace"))
        caller.msg("--- parsed ---")
        caller.msg(pprint.pformat(result.parsed))


class CmdWBFlatten(BaseCommand):
    """Smoke test: load a single YAML file and print the flattened entities.

    Usage:
        wbflatten [<path>]

    Defaults to ``millholm/bakery.yaml`` if no path supplied. Reads via
    the configured Reader (GitHubReader by default), constructs a Loader
    against the same Reader + Definitions, and calls ``Loader.load`` on
    a FoundLocation pointing directly at the file. Prints one block per
    LoadedEntity emitted by the flatten — top-level entity first, each
    item from its ``contents:`` block following with ``is_nested=True``.

    Spike 2 step 1 only: this just exercises the Loader's flatten
    behaviour. No validator, no Builder, no location synthesis yet.
    """

    key = "wbflatten"
    locks = "cmd:perm(Developer)"
    help_category = "Spike"

    def func(self):
        caller = self.caller
        path = self.args.strip() or "millholm/bakery.yaml"

        kwargs = {
            "repo": getattr(settings, "WORLDBUILDER_GITHUB_REPO", ""),
            "ref": getattr(settings, "WORLDBUILDER_GITHUB_REF", ""),
            "pat": getattr(settings, "WORLDBUILDER_GITHUB_PAT", ""),
        }
        missing = [
            name
            for name, value in (
                ("WORLDBUILDER_GITHUB_PAT", kwargs["pat"]),
                ("WORLDBUILDER_GITHUB_REPO", kwargs["repo"]),
                ("WORLDBUILDER_GITHUB_REF", kwargs["ref"]),
            )
            if not value
        ]
        if missing:
            caller.msg(
                "wbflatten: missing setting(s) in secret_settings.py: "
                f"{', '.join(missing)}."
            )
            return

        try:
            reader = get_reader_class()(**kwargs)
            definitions = Definitions.from_reader(reader)
            loader = Loader(reader, definitions)
            entities = loader.load(
                FoundLocation(path=path, kind="file", location={}),
            )
        except ReaderAuthError:
            caller.msg("wbflatten: auth failed (401). Check PAT scope and expiry.")
            return
        except ReaderNotFoundError:
            caller.msg(
                f"wbflatten: file not found (404): {path!r}. "
                "Check repo/ref settings and the path argument."
            )
            return
        except ReaderNetworkError as e:
            caller.msg(f"wbflatten: network error: {e}")
            return
        except ReaderParseError as e:
            caller.msg(f"wbflatten: YAML parse failed: {e}")
            return
        except ReaderError as e:
            caller.msg(f"wbflatten: reader error: {e}")
            return

        caller.msg(
            f"wbflatten: loaded {len(entities)} entit"
            f"{'y' if len(entities) == 1 else 'ies'} from "
            f"{kwargs['repo']}@{kwargs['ref']}:{path}"
        )
        for index, entity in enumerate(entities):
            caller.msg(
                f"--- [{index}] is_nested={entity.is_nested} "
                f"path={entity.path!r} location={entity.location!r} ---"
            )
            caller.msg(pprint.pformat(entity.content))
