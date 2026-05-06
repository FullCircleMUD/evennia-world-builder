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

Per-entity construction lands typeclass + key + location + db.desc +
aliases + locks + attributes + tags. ``contents:`` recursion is
handled upstream by the Loader's flatten + the ``_built_by_id`` map
that the Builder maintains during a single build() pass — children's
location cross-refs resolve to their parent's just-built Evennia
object via direct dict lookup. ``exits:`` and cross-file location
refs land in spike 4.
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
        # Populated during build(); maps (deployment_file, deployment_id) →
        # freshly-created Evennia object. Lets a child entity's
        # location-cross-ref resolve to its parent's just-built object via
        # a single dict lookup. Reset at the start of every build() call.
        self._built_by_id: dict = {}

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
        - ``content["location"]`` is either ``null`` (orphan placement)
          or a cross-ref dict ``{deployment_file, deployment_id}``
          pointing at another entity. The Loader synthesises this on
          every nested entity at flatten time; top-level entities can
          declare it directly. Resolution happens in-pass via
          ``self._built_by_id`` — the parent is always already in the
          map by the time the child needs it (depth-first pre-order).
        - ``content["typeclass"]`` selects the typeclass; Tier 3 (under
          ``evennia_runtime=True``) verifies resolvability.

        And these optional fields:

        - ``content["description"]`` becomes ``db.desc`` (default "").
        - ``content["tags"]`` is normalised and applied; the load-bearing
          ``wb_deployment_file`` / ``wb_deployment_id`` pair is appended
          automatically.

        Raises BuilderError on creation, tag-application, cleanup
        deletion, or unresolved-location-ref failure.
        """
        self.deleted_count = 0
        self._built_by_id = {}

        # Lazy import — Evennia bootstrap must complete before this is reachable.
        from evennia.utils.create import create_object

        file_paths = {e.path for e in entities}
        self._cleanup(file_paths)

        created = []
        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            # Validator's Tier 1 predicates guarantee these fields are present.
            key = content["name"]
            typeclass = content["typeclass"]
            desc = content.get("description", "")

            try:
                location = self._resolve_location(content["location"], entity.path)
            except BuilderError:
                raise
            except Exception as e:
                raise BuilderError(
                    f"failed to resolve location for {entity.path!r}: {e}"
                ) from e

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

            # Stash by (file, id) so any child entity following in this
            # build pass can resolve its location-cross-ref to this obj.
            self._built_by_id[(entity.path, content["deployment_id"])] = obj

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

    def _resolve_location(self, loc_ref, entity_path: str):
        """Turn a content['location'] value into the arg for create_object.

        ``None`` ⇒ ``None`` (orphan placement).

        Cross-ref dict ``{deployment_file, deployment_id}`` ⇒ the
        Evennia object stashed under that key in ``self._built_by_id``.
        The Loader synthesises this dict on every nested entity at
        flatten time, pointing at the parent's `(path, deployment_id)`;
        the parent is built earlier in this same build() pass (Loader
        emits depth-first pre-order), so the lookup always hits.

        Cross-file refs to entities not built in this invocation (i.e.
        another file's content already in the DB from a previous build)
        will land as a fall-through to a tag-search query in spike 4;
        for now they raise ``BuilderError``.

        Validator's ``_check_location_well_formed`` has already
        guaranteed the value is null or a well-shaped cross-ref dict, so
        this method trusts shape.
        """
        if loc_ref is None:
            return None

        key = (loc_ref["deployment_file"], loc_ref["deployment_id"])
        try:
            return self._built_by_id[key]
        except KeyError:
            raise BuilderError(
                f"{entity_path!r}: location refers to "
                f"(deployment_file={key[0]!r}, deployment_id={key[1]!r}) "
                f"but no such entity has been built in this pass — "
                f"author the parent earlier in build order, or (spike 4) "
                f"ensure the cross-file parent exists in the DB"
            )

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
