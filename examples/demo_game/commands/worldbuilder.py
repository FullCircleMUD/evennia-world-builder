"""Spike 1 commands for world-builder.

Throwaway command(s) used to validate the riskiest external integration
before any library code lands: pulling a YAML file from a private
GitHub repo into memory inside the running Evennia server. This module
will be removed or replaced once the spike's findings inform the
library design.

See world-builder/DESIGN/spike-1-load-from-github.md for the spike's
contract, scope, and deferred questions.
"""
import pprint
import urllib.error
import urllib.request

import yaml
from django.conf import settings

from evennia.commands.command import Command as BaseCommand


_USER_AGENT = "worldbuilder-spike-1"
_GITHUB_API_VERSION = "2022-11-28"
_REQUEST_TIMEOUT_SECONDS = 10


class CmdWBLoad(BaseCommand):
    """Spike 1: fetch a YAML file from the configured private GitHub repo.

    Usage:
        wbload

    Reads PAT, repo, ref, and path from secret_settings.py, pulls the
    file via GitHub's Contents API, decodes it, and parses via
    yaml.safe_load. Prints both the raw bytes and the parsed dict.

    Spike scope only — this is throwaway code that will be replaced
    when the world-builder library exposes proper apply primitives.
    """

    key = "wbload"
    locks = "cmd:perm(Developer)"
    help_category = "Spike"

    def func(self):
        caller = self.caller

        pat = getattr(settings, "WORLDBUILDER_GITHUB_PAT", "")
        repo = getattr(settings, "WORLDBUILDER_GITHUB_REPO", "")
        ref = getattr(settings, "WORLDBUILDER_GITHUB_REF", "")
        path = getattr(settings, "WORLDBUILDER_GITHUB_PATH", "")

        missing = [
            name
            for name, value in (
                ("WORLDBUILDER_GITHUB_PAT", pat),
                ("WORLDBUILDER_GITHUB_REPO", repo),
                ("WORLDBUILDER_GITHUB_REF", ref),
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

        url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {pat}",
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
                caller.msg("Spike: auth failed (401). Check PAT scope and expiry.")
            elif e.code == 404:
                caller.msg(
                    "Spike: file not found (404). Check repo/ref/path settings."
                )
            else:
                caller.msg(f"Spike: HTTP {e.code}: {e.reason}")
            return
        except urllib.error.URLError as e:
            caller.msg(f"Spike: network error: {e.reason}")
            return

        try:
            decoded = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            caller.msg(
                f"Spike: fetched {len(raw_bytes)} bytes, but UTF-8 decode failed: {e}"
            )
            return

        caller.msg(
            f"Spike: fetched {len(raw_bytes)} bytes from {repo}@{ref}:{path}"
        )
        caller.msg("--- raw ---")
        caller.msg(decoded)

        try:
            parsed = yaml.safe_load(raw_bytes)
        except yaml.YAMLError as e:
            caller.msg(f"Spike: YAML parse failed: {e}")
            return

        caller.msg("--- parsed ---")
        caller.msg(pprint.pformat(parsed))
