# SPDX-License-Identifier: BSD-3-Clause
"""
Open / close commands for ``DemoDoor`` exits.

The DemoDoor typeclass owns the open/close logic and the partner
mirroring; these commands are just the player-facing dispatch — find
the named door in the caller's room (or in their location's exits)
and forward to ``door.open(caller)`` / ``door.close(caller)``.
"""
from evennia.commands.command import Command


def _find_door(caller, search_term):
    """Find a DemoDoor among the caller's room contents + exits.

    Returns the matched door object or None. Errors are messaged to
    the caller; the caller's command implementation should just bail
    on a None return.
    """
    if not search_term:
        caller.msg("Open/close what?")
        return None
    matches = caller.search(search_term, quiet=True)
    if not matches:
        caller.msg(f"You see no {search_term} here.")
        return None
    # Filter to objects that look like a DemoDoor (duck-typed: has
    # is_open and other_side attrs). Avoids importing the typeclass
    # so this command file doesn't drag in typeclass module load.
    doors = [
        m for m in matches
        if hasattr(m, "is_open") and hasattr(m, "other_side")
    ]
    if not doors:
        caller.msg(f"{search_term} is not a door.")
        return None
    if len(doors) > 1:
        caller.msg(f"More than one {search_term} here — be more specific.")
        return None
    return doors[0]


class CmdOpen(Command):
    """
    Open a door.

    Usage:
        open <door>
    """
    key = "open"
    locks = "cmd:all()"

    def func(self):
        door = _find_door(self.caller, self.args.strip())
        if door is None:
            return
        success, message = door.open(self.caller)
        self.caller.msg(message)
        if success and self.caller.location:
            self.caller.location.msg_contents(
                f"{self.caller.key} opens {door.key}.",
                exclude=[self.caller],
            )


class CmdClose(Command):
    """
    Close a door.

    Usage:
        close <door>
    """
    key = "close"
    locks = "cmd:all()"

    def func(self):
        door = _find_door(self.caller, self.args.strip())
        if door is None:
            return
        success, message = door.close(self.caller)
        self.caller.msg(message)
        if success and self.caller.location:
            self.caller.location.msg_contents(
                f"{self.caller.key} closes {door.key}.",
                exclude=[self.caller],
            )
