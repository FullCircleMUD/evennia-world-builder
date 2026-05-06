# SPDX-License-Identifier: BSD-3-Clause
"""Builder — creates Evennia objects from validated LoadedEntities.

v0 is the minimum viable end-to-end: for each entity, create a single
Evennia object using the entity's ``content`` to populate ``key`` and
``db.desc``, with an optional ``typeclass`` override. No idempotency —
running the builder twice creates duplicates. No exits, no fixtures, no
attributes beyond ``desc``.

This is deliberate. We ship the smallest thing that proves end-to-end
"YAML → Evennia object in the DB" works, then discover what's missing
by running it against real content.
"""
from .definitions import Definitions
from .errors import BuilderError


_DEFAULT_TYPECLASS = "evennia.objects.objects.DefaultRoom"


class Builder:
    """Creates Evennia objects from validated LoadedEntities.

    Construction:
        definitions: parsed Definitions (provides level vocabulary; not
                     yet used in v0 but available for future placement
                     decisions, e.g. building exits at zone boundaries).
    """

    def __init__(self, definitions: Definitions):
        self.definitions = definitions

    def build(self, entities: list) -> list:
        """Create one Evennia object per entity. Return the created objects.

        For each entity:

        - ``content["name"]`` becomes the object's ``key``. Falls back to
          ``entity.path`` if no name field is present.
        - ``content["description"]`` becomes ``db.desc`` (empty string default).
        - ``content["typeclass"]`` selects the typeclass (dotted path);
          defaults to ``evennia.objects.objects.DefaultRoom``.

        Raises BuilderError on creation failure (wraps Evennia exceptions).
        """
        # Lazy import — Evennia bootstrap must complete before this is reachable.
        from evennia.utils.create import create_object

        created = []
        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            key = content.get("name") or entity.path
            desc = content.get("description", "")
            typeclass = content.get("typeclass", _DEFAULT_TYPECLASS)

            try:
                obj = create_object(
                    typeclass=typeclass,
                    key=key,
                    attributes=[("desc", desc)],
                )
            except Exception as e:
                raise BuilderError(
                    f"failed to create object for {entity.path!r}: {e}"
                ) from e
            created.append(obj)

        return created
