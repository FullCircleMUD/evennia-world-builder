# SPDX-License-Identifier: BSD-3-Clause
# Documentation: see builder.md (co-located).
import ast

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

        # Pass 4 — links: walk `links:` for files in scope and assign
        # each declared `entity.attribute = points_to`. Runs after pass 3
        # so the cache is fully warmed (own builds + DB-resolved cross-refs
        # + incoming_exits restorations). See DESIGN/links.md.
        self._run_pass_4(file_paths)

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

        # Optional `home:` field. Three paths per Evennia create_object
        # semantics (manager.py:683-688):
        #   - field absent → no kwarg → settings.DEFAULT_HOME (Limbo).
        #   - null → nohome=True → object's home is None.
        #   - cross-ref dict → resolve, pass home=<resolved obj>.
        # Note: passing home=None to create_object does NOT yield None;
        # it's a falsy value that falls through to settings.DEFAULT_HOME.
        # Translation must use nohome=True for the null case.
        if "home" in content:
            home_value = content["home"]
            if home_value is None:
                create_kwargs["nohome"] = True
            else:
                try:
                    home = self._resolve_cross_ref(
                        home_value, entity.path, "home",
                    )
                except BuilderError:
                    raise
                except Exception as e:
                    raise BuilderError(
                        f"failed to resolve home for {entity.path!r}: {e}"
                    ) from e
                create_kwargs["home"] = home

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

    def _run_pass_4(self, file_paths_in_scope: set) -> None:
        """Walk ``links:`` for every file in scope; assign each declared link.

        For each file path with a ``links:`` list in file_metadata:
        - For each link entry, resolve ``entity`` and ``points_to`` via
          ``_resolve_cross_ref`` (cache → DB).
        - Call ``entity_obj.attributes.add(attribute, points_to_obj,
          category=category)`` — same write path as a per-entity
          ``attributes:`` block.

        The Validator has guaranteed shape by this point. Builder
        re-resolves with the runtime ``_resolve_cross_ref`` so cross-file
        targets DB-fall-through correctly. An unresolvable side raises
        ``BuilderError`` and refuses the build (no partial state).

        See DESIGN/links.md for the design rationale.
        """
        if not self.file_metadata:
            return
        for path in file_paths_in_scope:
            meta = self.file_metadata.get(path)
            if not isinstance(meta, dict):
                continue
            links = meta.get("links")
            if not isinstance(links, list):
                continue
            for index, link in enumerate(links):
                if not isinstance(link, dict):
                    continue
                self._apply_one_link(path, index, link)

    def _apply_one_link(self, path: str, index: int, link: dict) -> None:
        """Resolve and apply a single link entry.

        Wraps every step in BuilderError so failures surface with the
        link's source file, its index in the ``links:`` list, and the
        offending field name when applicable.
        """
        try:
            entity_obj = self._resolve_cross_ref(
                link["entity"], path, f"links[{index}].entity",
            )
        except BuilderError:
            raise
        except Exception as e:
            raise BuilderError(
                f"failed to resolve links[{index}].entity for {path!r}: {e}"
            ) from e

        try:
            points_to_obj = self._resolve_cross_ref(
                link["points_to"], path, f"links[{index}].points_to",
            )
        except BuilderError:
            raise
        except Exception as e:
            raise BuilderError(
                f"failed to resolve links[{index}].points_to for {path!r}: {e}"
            ) from e

        attribute = link["attribute"]
        category = link.get("category")
        try:
            if "[" in attribute or "]" in attribute:
                # Either bracket triggers the subscript-path branch so
                # malformed inputs like 'foo]' fail loudly via the path
                # parser instead of being silently set as garbage
                # attribute names. The validator catches these at
                # validate time too; this is defence-in-depth.
                if category is not None:
                    raise BuilderError(
                        f"links[{index}] in {path!r}: subscript-path attribute "
                        f"{attribute!r} cannot be used with 'category' (category "
                        f"only applies to bare attribute names)"
                    )
                self._assign_via_subscript_path(
                    entity_obj, attribute, points_to_obj,
                )
            else:
                entity_obj.attributes.add(
                    attribute, points_to_obj, category=category,
                )
        except BuilderError:
            raise
        except Exception as e:
            raise BuilderError(
                f"failed to apply links[{index}] in {path!r} "
                f"(attribute={attribute!r}): {e}"
            ) from e

    def _assign_via_subscript_path(self, entity_obj, attribute_path: str, target):
        """Walk a subscript path on an existing attribute and assign target.

        Supports paths like ``foo["bar"]["baz"]`` or ``foo[0]["baz"]`` —
        the leading bare identifier names a top-level attribute that
        must already exist on entity_obj (typically created by an
        ``attributes:`` block in pass 1 with placeholder values like
        ``null``); each subsequent subscript walks into the nested
        dict/list structure; the final subscript receives the target.

        After mutation the top-level attribute is re-assigned so the
        change is persisted (Evennia's attribute store returns plain
        dicts/lists that don't auto-persist nested mutations).
        """
        try:
            tree = ast.parse(attribute_path, mode="eval").body
        except SyntaxError as e:
            raise BuilderError(
                f"attribute path {attribute_path!r} is not valid Python "
                f"subscript syntax: {e.msg}"
            ) from e

        subscripts = []
        while isinstance(tree, ast.Subscript):
            try:
                subscripts.append(ast.literal_eval(tree.slice))
            except (ValueError, SyntaxError) as e:
                raise BuilderError(
                    f"attribute path {attribute_path!r} contains a non-literal "
                    f"subscript: {e}"
                ) from e
            tree = tree.value

        if not isinstance(tree, ast.Name):
            raise BuilderError(
                f"attribute path {attribute_path!r} must start with a bare "
                f"attribute name, got {ast.dump(tree)}"
            )

        name = tree.id
        subscripts.reverse()

        if not subscripts:
            # No subscripts — fall through to the bare-attribute path. Should
            # not happen because the caller dispatches on `[` presence, but be
            # defensive.
            entity_obj.attributes.add(name, target)
            return

        top = entity_obj.attributes.get(name)
        if top is None:
            raise BuilderError(
                f"attribute path {attribute_path!r}: top-level attribute "
                f"{name!r} does not exist on entity (declare it in an "
                f"attributes: block with placeholder values before this link runs)"
            )

        obj = top
        for key in subscripts[:-1]:
            try:
                obj = obj[key]
            except (KeyError, IndexError, TypeError) as e:
                raise BuilderError(
                    f"attribute path {attribute_path!r}: cannot navigate "
                    f"to {key!r}: {type(e).__name__}: {e}"
                ) from e

        try:
            obj[subscripts[-1]] = target
        except (KeyError, IndexError, TypeError) as e:
            raise BuilderError(
                f"attribute path {attribute_path!r}: cannot assign at "
                f"{subscripts[-1]!r}: {type(e).__name__}: {e}"
            ) from e

        # Re-save the mutated top-level value so Evennia persists the change.
        # (attributes.get returns plain Python objects, not _SaverDict, so
        # nested mutations don't auto-persist.)
        entity_obj.attributes.add(name, top)

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
