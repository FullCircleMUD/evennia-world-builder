# SPDX-License-Identifier: BSD-3-Clause
"""
DemoDoor — a minimal closeable two-way exit.

Demonstrates the world-builder ``links:`` mechanism: each side of a
door pair has an ``other_side`` attribute set to its partner. Opening
or closing one side mirrors the state to the partner. Traversal is
blocked while the door is closed.

The pairing itself is *not* in this code — it's done in YAML via
``links:`` on the relevant world content file. See
DESIGN/links.md for the build pass that wires this up.

Intentionally minimal: no locking, smashing, hidden/invisible state,
or auto-close timer. Those are FCM concerns. This typeclass is the
smallest demonstration of how a paired-state attribute (``other_side``)
plus a value-by-reference YAML link unlock bidirectional synchronised
behaviour without bespoke wiring code.
"""
from evennia.typeclasses.attributes import AttributeProperty

from .exits import Exit


class DemoDoor(Exit):
    """
    A two-way closeable door. Closed by default. Pair via the
    world-builder ``links:`` mechanism to set ``other_side`` on both
    halves; open/close on one side mirrors to the other.
    """

    is_open = AttributeProperty(False)
    other_side = AttributeProperty(None)

    def at_traverse(self, traversing_object, target_location, **kwargs):
        if not self.is_open:
            traversing_object.msg(f"{self.key} is closed.")
            return
        return super().at_traverse(traversing_object, target_location, **kwargs)

    def open(self, opener):
        if self.is_open:
            return False, f"{self.key} is already open."
        self.is_open = True
        self.at_open(opener)
        return True, f"You open {self.key}."

    def close(self, closer):
        if not self.is_open:
            return False, f"{self.key} is already closed."
        self.is_open = False
        self.at_close(closer)
        return True, f"You close {self.key}."

    def at_open(self, opener):
        """Mirror open state to the partner via ``other_side``.

        Recursion guard: only act when the partner is in the opposite
        state. Avoids infinite mutual at_open/at_close calls.
        """
        other = self.other_side
        if other and not other.is_open:
            other.is_open = True
            if other.location:
                other.location.msg_contents(
                    f"{other.key} opens from the other side."
                )

    def at_close(self, closer):
        other = self.other_side
        if other and other.is_open:
            other.is_open = False
            if other.location:
                other.location.msg_contents(
                    f"{other.key} closes from the other side."
                )
