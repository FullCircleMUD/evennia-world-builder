# SPDX-License-Identifier: BSD-3-Clause
"""Builder — creates Evennia objects from validated LoadedEntities.

Cleanup-on-rebuild model (see DESIGN/deployment-identity.md):

- Every object the Builder creates is tagged with
  ``wb_deployment_file=<entity.path>`` and ``wb_deployment_id=<id>``.
- At the start of every ``build()`` call, the Builder sweeps every
  existing object tagged with any of the source files in the current
  build and deletes them.
- The build then creates fresh objects from the YAML.

Same YAML applied N times produces the same end state. Idempotency
emerges from "delete-everything-tagged-as-from-this-file, then build
it" — no diff machinery, no reconcile.

Per-entity construction currently lands typeclass + key + db.desc +
tags. Aliases, locks, attributes, exits, contents are upcoming spikes.
"""
from .definitions import Definitions
from .errors import BuilderError
from .loader import LoadedEntity


# Tag categories the Builder sets automatically. Keep in sync with the
# reserved-prefix check in validator.py — author-supplied tags using
# these categories are rejected at validate time.
_TAG_CATEGORY_DEPLOYMENT_FILE = "wb_deployment_file"
_TAG_CATEGORY_DEPLOYMENT_ID = "wb_deployment_id"


class Builder:
    """Creates Evennia objects from validated LoadedEntities.

    Construction:
        definitions: parsed Definitions (provides level vocabulary; not
                     yet used in v0 but available for future placement
                     decisions, e.g. building exits at zone boundaries).

    Attributes (populated during build()):
        deleted_count: number of existing objects swept by the
                       cleanup-on-rebuild pass at the start of build().
                       Reset to 0 at the start of every build() call.
    """

    def __init__(self, definitions: Definitions):
        self.definitions = definitions
        self.deleted_count: int = 0

    def build(self, entities: list) -> list:
        """Clean up prior deployments of these files, then create fresh.

        Step 1: collect the unique source-file set from the entities
        being built. Sweep every existing Evennia object tagged with
        ``wb_deployment_file=<file>`` for any file in that set and
        delete it. The number deleted lands in ``self.deleted_count``.

        Step 2: for each entity, create one Evennia object:

        Validator's Tier 1 predicates guarantee these mandatory fields
        are present and well-shaped:

        - ``content["name"]`` becomes the object's ``key``.
        - ``content["location"]`` becomes the object's ``location``
          (currently must be ``null``; cross-ref dicts land in spike 4).
        - ``content["typeclass"]`` selects the typeclass; Tier 3 (under
          ``evennia_runtime=True``) verifies resolvability.

        And these optional fields:

        - ``content["description"]`` becomes ``db.desc`` (default "").
        - ``content["tags"]`` is normalised and applied; the load-bearing
          ``wb_deployment_file`` / ``wb_deployment_id`` pair is appended
          automatically.

        Raises BuilderError on creation, tag-application, or cleanup
        deletion failure.
        """
        self.deleted_count = 0

        # Lazy import — Evennia bootstrap must complete before this is reachable.
        from evennia.utils.create import create_object

        file_paths = {e.path for e in entities}
        self._cleanup(file_paths)

        created = []
        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            # Validator's Tier 1 predicates guarantee these fields are present.
            key = content["name"]
            location = content["location"]
            typeclass = content["typeclass"]
            desc = content.get("description", "")

            try:
                obj = create_object(
                    typeclass=typeclass,
                    key=key,
                    location=location,
                    attributes=[("desc", desc)],
                )
            except Exception as e:
                raise BuilderError(
                    f"failed to create object for {entity.path!r}: {e}"
                ) from e

            try:
                self._apply_aliases(obj, entity)
            except Exception as e:
                raise BuilderError(
                    f"failed to apply aliases for {entity.path!r}: {e}"
                ) from e

            try:
                self._apply_locks(obj, entity)
            except Exception as e:
                raise BuilderError(
                    f"failed to apply locks for {entity.path!r}: {e}"
                ) from e

            try:
                self._apply_attributes(obj, entity)
            except Exception as e:
                raise BuilderError(
                    f"failed to apply attributes for {entity.path!r}: {e}"
                ) from e

            try:
                self._apply_tags(obj, entity)
            except Exception as e:
                raise BuilderError(
                    f"failed to apply tags for {entity.path!r}: {e}"
                ) from e

            created.append(obj)

        return created

    def _cleanup(self, file_paths) -> None:
        """Delete every existing object tagged with any of these source files.

        Runs once at the start of build() per the cleanup-on-rebuild
        model. Looks each file path up via Evennia's tag search and
        deletes whatever it finds. Exits attached to a deleted room are
        cleaned up by Evennia automatically.

        Updates ``self.deleted_count`` with the running total.
        """
        # Lazy import — same reason as create_object above.
        from evennia.utils.search import search_tag

        for path in file_paths:
            try:
                existing = list(search_tag(
                    key=path, category=_TAG_CATEGORY_DEPLOYMENT_FILE,
                ))
            except Exception as e:
                raise BuilderError(
                    f"cleanup: failed to query existing objects for "
                    f"deployment_file={path!r}: {e}"
                ) from e

            for obj in existing:
                try:
                    obj.delete()
                except Exception as e:
                    raise BuilderError(
                        f"cleanup: failed to delete existing "
                        f"{getattr(obj, 'dbref', '?')} "
                        f"(deployment_file={path!r}): {e}"
                    ) from e
                self.deleted_count += 1

    def _apply_aliases(self, obj, entity: LoadedEntity) -> None:
        """Apply each alias from ``content['aliases']`` to the object.

        The validator's ``_check_aliases_field_shape`` predicate has
        already guaranteed the field is a list of non-empty strings if
        present, so this trusts shape.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        for alias in content.get("aliases", []):
            obj.aliases.add(alias)

    def _apply_locks(self, obj, entity: LoadedEntity) -> None:
        """Apply the lockstring from ``content['locks']`` if present.

        Evennia's ``obj.locks.add(lockstring)`` parses a semicolon-joined
        sequence of ``<lock>:<func()>`` clauses and adds/updates each
        named lock — locks not mentioned in the YAML lockstring keep
        their typeclass defaults.

        The validator's ``_check_locks_field_shape`` predicate
        guarantees the field is a non-empty string if present.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        lockstring = content.get("locks")
        if lockstring is None:
            return
        obj.locks.add(lockstring)

    def _apply_attributes(self, obj, entity: LoadedEntity) -> None:
        """Apply each attribute from ``content['attributes']`` to the object.

        Each entry has the shape ``{"key": str, "value": Any,
        "category": str?}``. The Builder writes via
        ``obj.attributes.add(key, value, category=category)`` — Evennia's
        attribute store handles arbitrary serialisable Python values,
        and stores ``category=None`` (default category) when the YAML
        omits it.

        YAML attributes overwrite anything the typeclass set during
        ``at_object_creation`` (since this method runs AFTER
        ``create_object``). That's the contract: typeclass declares
        defaults; YAML overrides per-instance.

        The validator's ``_check_attributes_field_shape`` predicate has
        already guaranteed shape, so this trusts it.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        for attr in content.get("attributes", []):
            obj.attributes.add(
                attr["key"], attr["value"], category=attr.get("category"),
            )

    def _apply_tags(self, obj, entity: LoadedEntity) -> None:
        """Apply author-supplied tags + the auto-set deployment pair.

        Author tags first, then the load-bearing identity pair. The
        validator has already rejected any author-supplied tag whose
        category begins with ``wb_``, so the order can't produce a
        collision; this ordering just keeps the auto-set pair as the
        last word about identity.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        for tag in content.get("tags", []):
            key, category = _normalise_tag(tag)
            obj.tags.add(key, category=category)

        deployment_id = content.get("deployment_id")
        obj.tags.add(entity.path, category=_TAG_CATEGORY_DEPLOYMENT_FILE)
        obj.tags.add(str(deployment_id), category=_TAG_CATEGORY_DEPLOYMENT_ID)


def _normalise_tag(tag) -> tuple[str, str | None]:
    """Turn a YAML tag entry into ``(key, category)``.

    Shorthand string ⇒ ``(string, None)`` (Evennia's default category).
    Dict form ⇒ ``(tag["key"], tag.get("category"))``.

    The validator's ``_check_tags_field_shape`` predicate has already
    rejected anything else by the time we reach this code, so this
    function trusts shape.
    """
    if isinstance(tag, str):
        return tag, None
    return tag["key"], tag.get("category")
