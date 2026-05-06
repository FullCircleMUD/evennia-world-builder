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
