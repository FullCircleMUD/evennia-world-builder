"""
Room

Rooms are simple containers that has no location of their own.

"""

from evennia.objects.objects import DefaultRoom
from evennia.typeclasses.attributes import AttributeProperty

from .objects import ObjectParent


class Room(ObjectParent, DefaultRoom):
    """
    Rooms are like any Object, except their location is None
    (which is default). They also use basetype_setup() to
    add locks so they cannot be puppeted or picked up.
    (to change that, use at_object_creation instead)

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Objects.
    """

    pass


class BakeryRoom(DefaultRoom):
    """Minimal custom-typeclass smoke target for evennia-world-builder.

    Inherits directly from DefaultRoom (skipping the gamedir's empty
    `Room` base class) to keep the smoke focused. Demonstrates the two
    canonical Evennia patterns for class-declared default attributes,
    so the YAML attributes-override story can be exercised against
    each:

    - ``room_type`` and ``loaves_available`` are set imperatively in
      ``at_object_creation``. The Builder's ``create_object`` call
      triggers this once; later YAML attribute writes then override.
    - ``num_widgets`` uses ``AttributeProperty``, the modern descriptor
      that lazily backs to ``db.num_widgets`` with a default value.

    Deliberately trivial. Not a model for real game typeclasses.
    """

    num_widgets = AttributeProperty(5)

    def at_object_creation(self):
        super().at_object_creation()
        self.db.room_type = "bakery"
        self.db.loaves_available = 10


class PostBuildHookRoom(DefaultRoom):
    """Live demonstration of the ``wb_at_post_build`` hook.

    Authored content (e.g. ``description: 'done via core yaml'`` in the
    YAML) lands first via the Builder's standard apply pipeline. After
    every ``_apply_*`` step has run for this entity, the Builder
    duck-type-invokes ``wb_at_post_build`` on the just-built object;
    here, the hook overwrites ``db.desc`` with a marker string.

    Operators verify the hook fired by ``look``-ing at the resulting
    room after ``wb_build``. The diagnostic is the description text:

    - ``DONE VIA POST BUILD HOOK`` → hook ran (expected end state).
    - ``done via core yaml`` → hook did NOT run (regression — the
      Builder didn't invoke the hook, or this typeclass spelled the
      method name wrong, or some other consumer-side mistake).

    Deliberately trivial. The whole point is one observable side
    effect that survives ``look`` without needing the Evennia shell.
    See DESIGN/post-build-hook.md for the contract.
    """

    def wb_at_post_build(self):
        self.db.desc = "DONE VIA POST BUILD HOOK"
