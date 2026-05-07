# SPDX-License-Identifier: BSD-3-Clause
# Documentation: see builder.md (co-located).
from .definitions import Definitions
from .errors import BuilderError
from .loader import LoadedEntity


# Reserved tag categories — keep in sync with validator's `wb_*` prefix check.
_TAG_CATEGORY_DEPLOYMENT_FILE = "wb_deployment_file"
_TAG_CATEGORY_DEPLOYMENT_ID = "wb_deployment_id"


class Builder:
    def __init__(self, definitions: Definitions):
        self.definitions = definitions
        self.deleted_count: int = 0
        self._built_by_id: dict = {}

    def build(self, entities: list) -> list:
        self.deleted_count = 0
        self._built_by_id = {}

        # Lazy import — Evennia must be bootstrapped before this fires.
        from evennia.utils.create import create_object

        file_paths = {e.path for e in entities}
        self._cleanup(file_paths)

        created = []
        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            # Tier 1 has already validated shape.
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

            # Stash for child cross-ref resolution within this build pass.
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
        # Lazy import — Evennia must be bootstrapped before this fires.
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
        content = entity.content if isinstance(entity.content, dict) else {}
        for alias in content.get("aliases", []):
            obj.aliases.add(alias)

    def _apply_locks(self, obj, entity: LoadedEntity) -> None:
        content = entity.content if isinstance(entity.content, dict) else {}
        lockstring = content.get("locks")
        if lockstring is None:
            return
        obj.locks.add(lockstring)

    def _apply_attributes(self, obj, entity: LoadedEntity) -> None:
        # YAML attributes overwrite typeclass at_object_creation defaults.
        content = entity.content if isinstance(entity.content, dict) else {}
        for attr in content.get("attributes", []):
            obj.attributes.add(
                attr["key"], attr["value"], category=attr.get("category"),
            )

    def _apply_tags(self, obj, entity: LoadedEntity) -> None:
        content = entity.content if isinstance(entity.content, dict) else {}
        for tag in content.get("tags", []):
            key, category = _normalise_tag(tag)
            obj.tags.add(key, category=category)

        deployment_id = content.get("deployment_id")
        obj.tags.add(entity.path, category=_TAG_CATEGORY_DEPLOYMENT_FILE)
        obj.tags.add(str(deployment_id), category=_TAG_CATEGORY_DEPLOYMENT_ID)


def _normalise_tag(tag) -> tuple[str, str | None]:
    if isinstance(tag, str):
        return tag, None
    return tag["key"], tag.get("category")
