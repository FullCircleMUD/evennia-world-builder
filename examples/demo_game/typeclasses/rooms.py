"""
Room

Rooms are simple containers that has no location of their own.

"""

from evennia.objects.objects import DefaultRoom

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
    `Room` base class) to keep the smoke focused: one extra attribute
    set at creation time. Proves that a YAML-declared
    `typeclass: typeclasses.rooms.BakeryRoom` lands as the actual
    Python class on the created object and class-defined behaviour
    fires correctly.

    Deliberately trivial. Not a model for real game typeclasses.
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.room_type = "bakery"
