r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

import os

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
SERVERNAME = "demo_game"


######################################################################
# evennia-world-builder library — INSTALLED_APPS registration so the
# library's AppConfig.ready() fires and auto-installs library-shipped
# commands (e.g. wb_build) into CharacterCmdSet.
######################################################################
INSTALLED_APPS = list(INSTALLED_APPS) + ["evennia_world_builder"]


######################################################################
# world-builder spike 1
######################################################################
# GitHub repo to fetch YAML from for the wbload spike command. PAT must
# be set (defaults to empty string and the command refuses to proceed).
# Production sets env vars; local development overrides via secret_settings.py.
WORLDBUILDER_GITHUB_PAT = os.environ.get(
    "WORLDBUILDER_GITHUB_PAT",
    "",
)
WORLDBUILDER_GITHUB_REPO = os.environ.get(
    "WORLDBUILDER_GITHUB_REPO",
    "FullCircleMUD/evennia-world-builder-test-yaml",
)
WORLDBUILDER_GITHUB_REF = os.environ.get(
    "WORLDBUILDER_GITHUB_REF",
    "main",
)
WORLDBUILDER_GITHUB_PATH = os.environ.get(
    "WORLDBUILDER_GITHUB_PATH",
    "hello.yaml",
)


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")


######################################################################
# Reader kwargs for the world-builder library.
######################################################################
# Built AFTER secret_settings import so PAT override (in secret_settings.py)
# is reflected. This is what wb_build (and any other library command using
# get_configured_reader()) reads to construct the configured Reader.
WORLDBUILDER_READER_KWARGS = {
    "repo": WORLDBUILDER_GITHUB_REPO,
    "ref": WORLDBUILDER_GITHUB_REF,
    "pat": WORLDBUILDER_GITHUB_PAT,
}
