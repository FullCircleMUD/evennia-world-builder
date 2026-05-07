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

        # Two-pass: non-exits first so their dbrefs land in _built_by_id,
        # then exits (their destinations may point at any non-exit).
        non_exits = [e for e in entities if "destination" not in (e.content or {})]
        exits = [e for e in entities if "destination" in (e.content or {})]

        created = []
        for entity in non_exits + exits:
            content = entity.content if isinstance(entity.content, dict) else {}
            # Tier 1 has already validated shape.
            key = content["name"]
            typeclass = content["typeclass"]
            desc = content.get("description", "")

            try:
                location = self._resolve_cross_ref(
                    content["location"], entity.path, "location",
                )
            except BuilderError:
                raise
            except Exception as e:
                raise BuilderError(
                    f"failed to resolve location for {entity.path!r}: {e}"
                ) from e

            create_kwargs = dict(
                typeclass=typeclass,
                key=key,
                location=location,
                attributes=[("desc", desc)],
            )

            if "destination" in content:
                try:
                    destination = self._resolve_cross_ref(
                        content["destination"], entity.path, "destination",
                    )
                except BuilderError:
                    raise
                except Exception as e:
                    raise BuilderError(
                        f"failed to resolve destination for {entity.path!r}: {e}"
                    ) from e
                create_kwargs["destination"] = destination

            try:
                obj = create_object(**create_kwargs)
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

    def _resolve_cross_ref(self, ref, entity_path: str, field_name: str):
        if ref is None:
            return None

        key = (ref["deployment_file"], ref["deployment_id"])
        if key in self._built_by_id:
            return self._built_by_id[key]

        # Fall through to DB tag-search for cross-file refs to entities
        # already in the DB from a previous build invocation. Cache hits
        # back into _built_by_id so subsequent refs to the same target
        # in this build pass don't re-query.
        obj = self._lookup_in_db(*key)
        if obj is not None:
            self._built_by_id[key] = obj
            return obj

        raise BuilderError(
            f"{entity_path!r}: '{field_name}' cross-ref to "
            f"(deployment_file={key[0]!r}, deployment_id={key[1]!r}) "
            f"does not resolve — neither built in this pass nor present in the DB. "
            f"Likely causes: typo in the cross-ref, target file never built, "
            f"or same-file forward ref (author the target earlier in the file)."
        )

    def _lookup_in_db(self, deployment_file: str, deployment_id: int):
        # Lazy import — Evennia must be bootstrapped before this fires.
        from evennia.utils.search import search_tag

        try:
            candidates = list(search_tag(
                key=deployment_file, category=_TAG_CATEGORY_DEPLOYMENT_FILE,
            ))
        except Exception as e:
            raise BuilderError(
                f"DB lookup: failed to query existing objects for "
                f"deployment_file={deployment_file!r}: {e}"
            ) from e

        target_id_str = str(deployment_id)
        matches = [
            obj for obj in candidates
            if target_id_str in (obj.tags.get(
                category=_TAG_CATEGORY_DEPLOYMENT_ID, return_list=True,
            ) or [])
        ]
        if not matches:
            return None
        if len(matches) > 1:
            # Should be unreachable if cleanup-on-rebuild has held its
            # invariant. If it ever happens, fail loudly rather than
            # silently picking one — the operator needs to know.
            raise BuilderError(
                f"DB has multiple objects tagged "
                f"(deployment_file={deployment_file!r}, deployment_id={deployment_id}); "
                f"this indicates a cleanup integrity failure"
            )
        return matches[0]

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
                # Skip ghosts: an earlier delete in this same cleanup
                # pass may have cascaded (Evennia auto-deletes exits
                # whose destination was deleted, etc.). The tag-side row
                # outlives the cascade for this query, so search_tag can
                # return a handle whose underlying db row is already
                # gone. Cleanup's post-condition ("the object doesn't
                # exist after this") is already met — skip silently.
                if getattr(obj, "pk", None) is None:
                    continue
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
