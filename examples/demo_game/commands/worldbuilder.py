"""Demo command using the world-builder library reader.

Calls the configured reader (default: GitHubReader) to fetch and parse
a YAML file. Reads GitHub-specific settings from Django settings,
constructs the reader with the appropriate kwargs, and emits both the
raw bytes and the parsed dict.

Promoted from the original spike: the urllib/yaml fetch logic now
lives in world_builder.GitHubReader; this command is the thin
consumer-side wrapper that wires settings into the library.

See world-builder/DESIGN/spike-1-load-from-github.md for the spike
contract.
"""
import pprint

from django.conf import settings

from evennia.commands.command import Command as BaseCommand

from world_builder import (
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
    via WORLDBUILDER_READER (default: world_builder.GitHubReader),
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
            "path": getattr(settings, "WORLDBUILDER_GITHUB_PATH", ""),
            "ref": getattr(settings, "WORLDBUILDER_GITHUB_REF", ""),
            "pat": getattr(settings, "WORLDBUILDER_GITHUB_PAT", ""),
        }
        missing = [
            name
            for name, value in (
                ("WORLDBUILDER_GITHUB_PAT", kwargs["pat"]),
                ("WORLDBUILDER_GITHUB_REPO", kwargs["repo"]),
                ("WORLDBUILDER_GITHUB_REF", kwargs["ref"]),
                ("WORLDBUILDER_GITHUB_PATH", kwargs["path"]),
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
            result = reader.read()
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
            f"{kwargs['repo']}@{kwargs['ref']}:{kwargs['path']}"
        )
        caller.msg("--- raw ---")
        caller.msg(result.raw_bytes.decode("utf-8", errors="replace"))
        caller.msg("--- parsed ---")
        caller.msg(pprint.pformat(result.parsed))
