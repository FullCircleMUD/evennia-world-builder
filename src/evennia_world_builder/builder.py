# SPDX-License-Identifier: BSD-3-Clause
# Documentation: see builder.md (co-located).
from .definitions import Definitions
from .errors import BuilderError
from .loader import LoadedEntity
from .readers.base import Reader


# Reserved tag categories — keep in sync with validator's `wb_*` prefix check.
_TAG_CATEGORY_DEPLOYMENT_FILE = "wb_deployment_file"
_TAG_CATEGORY_DEPLOYMENT_ID = "wb_deployment_id"


class Builder:
    def __init__(
        self, definitions: Definitions, *,
        file_metadata: dict | None = None,
        reader: Reader | None = None,
    ):
        self.definitions = definitions
        self.deleted_count: int = 0
        self._built_by_id: dict = {}
        # Per-file metadata from Loader.LoadResult — file-level keys
        # like incoming_exits: extracted by the Loader. Pass 3 walks
        # this dict's incoming_exits lists for files in the build set.
        self.file_metadata: dict = dict(file_metadata or {})
        # Reader used by pass 3 to fetch canonical files when an
        # incoming_exits target is missing from both _built_by_id and
        # the DB. Optional — Builder constructed without a reader can
        # still build entities; pass 3 just refuses if it ever needs
        # to fetch a missing dep.
        self._reader: Reader | None = reader

    def build(self, entities: list) -> list:
        self.deleted_count = 0
        self._built_by_id = {}

        # Lazy import — Evennia must be bootstrapped before this fires.
        from evennia.utils.create import create_object

        file_paths = {e.path for e in entities}
        self._cleanup(file_paths)

        # Pass 1+2: non-exits first so their dbrefs land in _built_by_id,
        # then exits (their destinations may point at any non-exit).
        # `incoming_exits:` is metadata for pass 3 (dependency phase); it
        # is intentionally NOT consulted during passes 1/2 — the entity's
        # own typeclass/location/destination determine which pass it lands
        # in, regardless of any incoming-exit registrations it declares.
        non_exits = [e for e in entities if "destination" not in (e.content or {})]
        exits = [e for e in entities if "destination" in (e.content or {})]

        created = []
        for entity in non_exits + exits:
            created.append(self._build_one(entity, create_object))

        # Pass 3 — incoming_exits dependency restore. Walks
        # file_metadata for files in the build set; for each registered
        # ref that's missing from both _built_by_id and the DB, fetches
        # the canonical file via self._reader and builds the target.
        created.extend(self._run_pass_3(file_paths, create_object))

        return created

    def _build_one(self, entity: LoadedEntity, create_object) -> object:
        """Build a single entity through create_object + apply_* steps.

        Used by passes 1, 2, and 3. Resolves the entity's location and
        (when present) destination via _resolve_cross_ref, calls
        create_object, stashes the result in _built_by_id keyed by
        (path, deployment_id), then applies aliases/locks/attributes/
        tags. Wraps every step in BuilderError so failures surface with
        contextual messages naming the offending entity.
        """
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

        # Stash for cross-ref resolution within this build pass.
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

        return obj

    def _run_pass_3(self, file_paths_in_scope: set, create_object) -> list:
        """Walk incoming_exits for every file in scope; build any missing refs.

        For each file path in the build set that has file_metadata:
        - For each `(deployment_file, deployment_id)` ref in its
          ``incoming_exits:`` list:
          - If already in ``_built_by_id`` (built during pass 2): skip.
          - If found via DB tag-search: cache it back into the map and
            skip (it already exists, no need to rebuild).
          - Otherwise: fetch the canonical file via the Reader, find
            the entity by deployment_id, and build it through
            ``_build_one``.

        The fetched entity goes through ``Loader._flatten_top_level``
        first so location synthesis applies to nested entities — a
        nested exit's location is the parent room (correctly resolved
        via DB fallback when out of scope).
        """
        if not self.file_metadata:
            return []

        created = []
        for path in file_paths_in_scope:
            meta = self.file_metadata.get(path)
            if not isinstance(meta, dict):
                continue
            incoming = meta.get("incoming_exits")
            if not isinstance(incoming, list):
                continue
            for ref in incoming:
                if not isinstance(ref, dict):
                    continue
                if "deployment_file" not in ref or "deployment_id" not in ref:
                    continue
                key = (ref["deployment_file"], ref["deployment_id"])

                if key in self._built_by_id:
                    continue

                obj = self._lookup_in_db(*key)
                if obj is not None:
                    self._built_by_id[key] = obj
                    continue

                # Truly missing — fetch and build from canonical file.
                target_entity = self._fetch_canonical_entity(*key)
                if target_entity is None:
                    raise BuilderError(
                        f"pass 3: incoming_exits ref "
                        f"(deployment_file={key[0]!r}, deployment_id={key[1]!r}) "
                        f"declared by {path!r} not found in canonical file"
                    )
                created.append(self._build_one(target_entity, create_object))

        return created

    def _fetch_canonical_entity(
        self, deployment_file: str, deployment_id: int,
    ) -> LoadedEntity | None:
        """Fetch a canonical file via the Reader; find the entity by id.

        Runs the file through ``Loader._flatten_top_level`` so location
        synthesis applies to nested entities (the dependency target is
        typically a nested exit whose location is the parent room).
        Returns the LoadedEntity with matching deployment_id, or None
        if the file doesn't contain such an id.
        """
        if self._reader is None:
            raise BuilderError(
                f"pass 3: cannot fetch canonical file {deployment_file!r} — "
                f"Builder constructed without a reader"
            )

        # Lazy import to avoid circular references at module load time.
        from .loader import Loader

        try:
            result = self._reader.read(deployment_file)
        except Exception as e:
            raise BuilderError(
                f"pass 3: failed to read canonical file {deployment_file!r}: {e}"
            ) from e

        loader = Loader(self._reader, self.definitions)
        try:
            entities = loader._flatten_top_level(
                parsed=result.parsed, path=deployment_file, location={},
            )
        except Exception as e:
            raise BuilderError(
                f"pass 3: failed to flatten canonical file {deployment_file!r}: {e}"
            ) from e

        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            if content.get("deployment_id") == deployment_id:
                return entity
        return None

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
