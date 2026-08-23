# SPDX-License-Identifier: BSD-3-Clause
"""builder — creates Evennia objects from validated LoadedEntities.

The Builder is the only component in the pipeline that mutates the
consumer's database. By the time Builder.build() runs, the Validator has
guaranteed every entity's mandatory fields are present and well-shaped,
every declared typeclass actually resolves (under evennia_runtime=True),
and every cross-ref resolves in the build set (under
resolve_cross_refs=True). The Builder trusts those guarantees and skips
re-checking shape.

See docs/builder.md for the architectural rationale (clean-then-rebuild,
two-pass entity creation, DB fallback for cross-file refs, no partial
state) and docs/deployment-identity.md for the file_id + entity_id
identity scheme referenced throughout this module.
"""
import ast

from django.core.exceptions import ObjectDoesNotExist

from evennia_yaml_reader import Reader

from .definitions import Definitions
from .errors import BuilderError
from .loader import LoadedEntity
from .log import wb_log


# Reserved tag categories — the deployment-identity pair the Builder writes
# onto every created object. Cleanup uses the file category to find existing
# objects to delete on rebuild; cross-file cross-refs use both to look up
# parents already in the DB from a prior build. Keep in sync with the
# validator's `wb_*` prefix check — adding a new `wb_*` category here without
# a matching update to the validator's reserved-prefix check would let an
# author tag collide with a Builder-set tag silently.
_TAG_CATEGORY_FILE_ID = "wb_file_id"
_TAG_CATEGORY_ENTITY_ID = "wb_entity_id"

# Per-entity post-apply hook — see docs/post-build-hook.md. Consumer
# typeclasses that need to derive state from YAML-supplied attributes
# define this method; the Builder duck-type-invokes it after every
# `_apply_*` step inside `_build_one`. Exceptions inside the hook are
# logged via `wb_log` and do NOT abort the build.
_WB_AT_POST_BUILD_ATTR = "wb_at_post_build"


class Builder:
    """Apply validated entities to the Evennia database, idempotently.

    Clean-then-rebuild, not diff-then-reconcile (see docs/builder.md):
    every build() call sweeps prior deployments of the affected files and
    recreates from the current YAML, so the same YAML applied N times
    produces the same end state. Object dbrefs increment (Evennia never
    reuses them), but the count of objects tagged with each ``file_id``
    stays at exactly the YAML's declared count.

    Instances are reusable across multiple build() calls — deleted_count
    and _built_by_id are per-build state, reset at the start of each call.
    """

    def __init__(
        self, definitions: Definitions, *,
        file_metadata: dict | None = None,
        reader: Reader | None = None,
        entity_paths: dict | None = None,
    ):
        """Construct a Builder against a Definitions and optional file context.

        definitions is stored for future use (level vocabulary may inform
        placement decisions like building exits at zone boundaries);
        current logic doesn't consult it.

        file_metadata is the per-file metadata dict from
        Loader.LoadResult.file_metadata — file-level keys extracted by
        the Loader. Consumed for each file's ``file_id`` (the cleanup
        sweep key and the tag every object from that file carries),
        ``incoming_exits`` (walked by pass 3 for cross-file dependency
        restore) and ``links`` (walked by pass 4 for cross-entity
        attribute references; see docs/links.md).

        entity_paths is the entity index returned by Validator.validate()
        — ``{entity_id: file path}``. A reference names no file, so this
        is the only way pass 3 can get from a registered dependency back
        to the YAML that declares it. Optional for the same reason as
        reader: a Builder without one still builds, and pass 3 refuses
        only if it actually needs the lookup.

        reader is the configured Reader, used by pass 3 to fetch
        canonical files when an incoming_exits target is missing from
        both _built_by_id and the DB. Optional — a Builder constructed
        without a reader can still build entities; pass 3 raises
        BuilderError only if it actually needs to fetch a missing dep.
        """
        self.definitions = definitions
        self.deleted_count: int = 0
        # {entity_id: object} for everything built or DB-resolved during
        # this build() call. An entity_id is globally unique, so this is
        # a flat map rather than the per-file nesting identity used to
        # need.
        self._built_by_id: dict = {}
        # {entity_id: file path} from Validator.validate(). Pass 3 reads
        # it to locate a missing dependency's canonical file.
        self.entity_paths: dict = dict(entity_paths or {})
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
        """Apply entities to the database; return the created objects.

        The single public method. Raises BuilderError on any failure —
        each step wraps its underlying exception with a contextual
        message naming the offending entity path and (where applicable)
        the field that failed, via `from e` so the original exception is
        preserved for debugging. `_resolve_cross_ref` raises BuilderError
        directly with more specific context, so the build loop re-raises
        that as-is rather than wrapping it again. Callers (`wb_build`)
        catch BuilderError, surface it via caller.msg, and refuse without
        continuing — partial state is never returned.

        Algorithm:
        1. Reset deleted_count and _built_by_id (per-build state).
        2. Lazy-import evennia.utils.create.create_object.
        3. Cleanup pass — sweep prior deployments of the entities' source
           files (see _cleanup).
        4. Partition entities into non_exits and exits (on whether
           "destination" is in content), then build non_exits before
           exits so every exit's destination cross-ref resolves to an
           already-built room. See docs/builder.md for why two passes
           are required, not just preferred.
        5. Build each entity via _build_one, in partitioned order.
        6. Pass 3 — restore any incoming_exits dependency missing from
           both _built_by_id and the DB (see _run_pass_3).
        7. Pass 4 — assign every links: entry (see _run_pass_4).
        8. Return the objects created in passes 1+2+3. Pass 4 mutates
           already-built objects and returns nothing new.

        Field expectations (guaranteed by the Validator's Tier 1
        predicates before this runs): content["name"] and
        content["typeclass"] are non-empty strings; content["location"]
        is null (orphan) or a reference naming the target's entity_id;
        content["entity_id"] is a UUID string, and the entity's file
        declares a file_id in file_metadata.
        Optional: content["description"] (default ""), content["destination"]
        (marks the entity an exit, built in pass 2), content["home"] (null
        -> nohome=True, since passing home=None to create_object falls
        through to settings.DEFAULT_HOME rather than yielding None — see
        evennia/objects/manager.py), content["aliases"], content["locks"],
        content["attributes"], content["tags"].
        """
        self.deleted_count = 0
        self._built_by_id = {}

        # Lazy import — Evennia must be bootstrapped before this fires.
        from evennia.utils.create import create_object

        file_paths = {e.path for e in entities}
        self._cleanup(self._file_ids_for(file_paths))

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
        # + incoming_exits restorations). See docs/links.md.
        self._run_pass_4(file_paths)

        return created

    def _file_id_for(self, path: str) -> str:
        """The declared ``file_id`` for a source file path.

        Every entity file declares one — the Validator refuses a build
        otherwise — so a miss here means the Builder was handed metadata
        the Validator never saw. Refuse rather than skip: a file with no
        file_id can't be swept, and silently not sweeping is how a
        rebuild leaves the previous deployment's objects behind.
        """
        meta = self.file_metadata.get(path)
        file_id = meta.get("file_id") if isinstance(meta, dict) else None
        if not file_id:
            raise BuilderError(
                f"{path!r}: no 'file_id' in file metadata — cannot scope "
                f"cleanup or tag the objects this file creates"
            )
        return file_id

    def _file_ids_for(self, file_paths) -> dict:
        """``{file_id: path}`` for every path in the build set.

        Path is carried alongside purely so failures name the file the
        operator recognises rather than a UUID.
        """
        return {self._file_id_for(path): path for path in file_paths}

    def _build_one(self, entity: LoadedEntity, create_object) -> object:
        """Build a single entity through create_object + apply_* steps.

        Used by passes 1, 2, and 3. Resolves the entity's location and
        (when present) destination via _resolve_cross_ref, translates an
        optional home: field (null -> nohome=True; a cross-ref dict ->
        home=<resolved obj>; absent -> no kwarg, falls back to
        settings.DEFAULT_HOME), calls create_object, stashes the result
        in _built_by_id keyed by entity_id, then applies
        aliases/locks/attributes/tags and finally invokes the optional
        wb_at_post_build hook. Wraps every step in BuilderError so
        failures surface with contextual messages naming the offending
        entity.
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

        # Stash for reference resolution within this build pass.
        self._built_by_id[content["entity_id"]] = obj

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

        # Per-entity post-apply hook (see docs/post-build-hook.md).
        # Fires after every `_apply_*` step has run, so consumer
        # typeclasses observe the YAML-supplied values, not the
        # typeclass defaults that `at_object_creation` saw.
        self._invoke_post_build_hook(obj, entity)

        return obj

    def _run_pass_3(self, file_paths_in_scope: set, create_object) -> list:
        """Walk incoming_exits for every file in scope; build any missing refs.

        For each file path in the build set that has file_metadata:
        - For each ``entity_id`` reference in its ``incoming_exits:``
          list:
          - If already in ``_built_by_id`` (built during pass 2): skip.
          - If found via DB tag-search: cache it back into the map and
            skip (it already exists, no need to rebuild).
          - Otherwise: look the id up in ``entity_paths`` to find its
            canonical file, fetch that file via the Reader, and build
            the entity through ``_build_one``.

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
                if not isinstance(ref, str):
                    continue

                if ref in self._built_by_id:
                    continue

                obj = self._lookup_in_db(ref)
                if obj is not None:
                    self._built_by_id[ref] = obj
                    continue

                # Truly missing — fetch and build from canonical file.
                target_entity = self._fetch_canonical_entity(ref)
                if target_entity is None:
                    raise BuilderError(
                        f"pass 3: incoming_exits reference to "
                        f"entity_id={ref} declared by {path!r} not found "
                        f"in its canonical file"
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

        See docs/links.md for the design rationale.
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

    def _fetch_canonical_entity(self, entity_id: str) -> LoadedEntity | None:
        """Fetch an entity's canonical file via the Reader; find it by id.

        A reference names no file, so the path comes from the entity
        index the Validator returned. Runs the file through
        ``Loader._flatten_top_level`` so location synthesis applies to
        nested entities (the dependency target is typically a nested
        exit whose location is the parent room). Returns the
        LoadedEntity with matching entity_id, or None if the file
        doesn't declare it.
        """
        canonical_path = self.entity_paths.get(entity_id)
        if canonical_path is None:
            raise BuilderError(
                f"pass 3: entity_id={entity_id} is not in the entity index, "
                f"so its canonical file is unknown. Either the reference is "
                f"a typo, or the Builder was constructed without the index "
                f"Validator.validate() returns."
            )

        if self._reader is None:
            raise BuilderError(
                f"pass 3: cannot fetch canonical file {canonical_path!r} — "
                f"Builder constructed without a reader"
            )

        # Lazy import to avoid circular references at module load time.
        from .loader import Loader

        try:
            result = self._reader.read(canonical_path)
        except Exception as e:
            raise BuilderError(
                f"pass 3: failed to read canonical file {canonical_path!r}: {e}"
            ) from e

        loader = Loader(self._reader, self.definitions)
        try:
            entities = loader._flatten_top_level(
                parsed=result.parsed, path=canonical_path, location={},
            )
        except Exception as e:
            raise BuilderError(
                f"pass 3: failed to flatten canonical file {canonical_path!r}: {e}"
            ) from e

        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            if content.get("entity_id") == entity_id:
                return entity
        return None

    def _resolve_cross_ref(self, ref, entity_path: str, field_name: str):
        """Turn a reference into a create_object argument.

        Single helper for ``location:``, ``destination:``, ``home:`` and
        a links entry's entity / points_to — the only difference between
        them is the value the caller passes; the lookup is identical.
        The Validator's shape checks have already guaranteed the value
        is null or a well-formed entity_id, so this method trusts shape.

        None -> None (orphan placement).
        entity_id -> a two-step lookup: try _built_by_id first (hit when
        the target was built earlier in this build() call), then fall
        through to _lookup_in_db for a target already in the database
        from a previous build. A DB hit is cached back into
        _built_by_id so subsequent references to the same target in this
        pass don't re-query. If both miss, raises BuilderError naming
        the field and the id.

        The reference names no file, which is exactly why nothing here
        cares which file the target lives in: an entity that moved
        between files still resolves through the same two lookups.
        References to entities not built in this invocation resolve via
        the DB fallback — this is what lets operators rebuild a single
        file and have exits and locations pointing elsewhere still
        resolve, as long as the target has been built at some point.

        Same-file forward refs (a top-level entity's location pointing at
        another top-level entity later in the same file) miss the lookup
        because the parent hasn't been built yet at this point in the
        iteration, and raise BuilderError — the author has to reorder.
        Validator Tier 4 sees forward refs as valid (its index is fully
        built before Tier 4 runs); this refusal is the load-bearing
        distinction between "the reference is correct in the abstract"
        and "the reference can be used at this point in the build." The
        restriction does not apply to destinations on exits — the
        two-pass build in build() builds every non-exit before any exit,
        so destinations always resolve as long as the target is in the
        build set, regardless of YAML order.
        """
        if ref is None:
            return None

        if ref in self._built_by_id:
            return self._built_by_id[ref]

        # Fall through to DB tag-search for references to entities
        # already in the DB from a previous build invocation. Cache hits
        # back into _built_by_id so subsequent references to the same
        # target in this build pass don't re-query.
        obj = self._lookup_in_db(ref)
        if obj is not None:
            self._built_by_id[ref] = obj
            return obj

        raise BuilderError(
            f"{entity_path!r}: '{field_name}' reference to entity_id={ref} "
            f"does not resolve — neither built in this pass nor present in the DB. "
            f"Likely causes: typo in the reference, target never built, "
            f"or same-file forward ref (author the target earlier in the file)."
        )

    def _lookup_in_db(self, entity_id: str):
        """Find an existing object carrying this entity_id, if any.

        The DB-side counterpart to the in-build _built_by_id map — used
        by _resolve_cross_ref to resolve references to entities already
        in the database from a previous build invocation.

        One indexed tag query. Under the previous composite identity
        this had to fetch every object sharing a file tag and filter
        client-side by the second tag; an entity_id is globally unique,
        so the id alone is the query.

        Returns the matching object, None (no match), or raises
        BuilderError (more than one match — see below).
        """
        # Lazy import — Evennia must be bootstrapped before this fires.
        from evennia.utils.search import search_tag

        try:
            matches = list(search_tag(
                key=entity_id, category=_TAG_CATEGORY_ENTITY_ID,
            ))
        except Exception as e:
            raise BuilderError(
                f"DB lookup: failed to query existing objects for "
                f"entity_id={entity_id}: {e}"
            ) from e

        if not matches:
            return None
        if len(matches) > 1:
            # Should be unreachable if cleanup-on-rebuild has held its
            # invariant. If it ever happens, fail loudly rather than
            # silently picking one — the operator needs to know.
            raise BuilderError(
                f"DB has multiple objects tagged entity_id={entity_id}; "
                f"this indicates a cleanup integrity failure"
            )
        return matches[0]

    def _cleanup(self, file_ids: dict) -> None:
        """Delete every existing object tagged with any file_id in scope.

        Called once at the start of build() with ``{file_id: path}``.
        For each: search_tag for existing objects from prior deployments
        of that file, then delete each one (Evennia relocates any
        contents, including a player standing in a deleted room, to the
        home location automatically).

        Sweeping on ``file_id`` rather than the path is what makes a
        rename safe. A file's objects carry the id it declared, so
        renaming or relocating the YAML changes nothing about what a
        rebuild sweeps — under a path-keyed sweep the old objects would
        be orphaned with a tag no file would ever claim again, while the
        new path built duplicates alongside them.

        One tag-search per file rather than one per entity: a file's full
        state replaces whatever was there, so sweeping by file means
        entities added since the last build land fresh, entities removed
        are deleted, and entities changed are recreated — all for free,
        without a separate "find what's here that shouldn't be" pass for
        orphan removal.

        Both search_tag and obj.delete() failures are wrapped as
        BuilderError with context (file path, dbref where applicable). A
        failure here aborts the whole build before any new object is
        created, so the no-partial-state invariant holds even when
        cleanup itself fails.
        """
        # Lazy import — Evennia must be bootstrapped before this fires.
        from evennia.utils.search import search_tag

        for file_id, path in file_ids.items():
            try:
                existing = list(search_tag(
                    key=file_id, category=_TAG_CATEGORY_FILE_ID,
                ))
            except Exception as e:
                raise BuilderError(
                    f"cleanup: failed to query existing objects for "
                    f"{path!r} (file_id={file_id}): {e}"
                ) from e

            for obj in existing:
                # Skip ghosts: an earlier delete in this same cleanup
                # pass may have cascaded (Evennia auto-deletes exits
                # whose destination was deleted, etc.). The tag-side row
                # outlives the cascade for this query, so search_tag can
                # return a handle whose underlying db row is already
                # gone. Cleanup's post-condition ("the object doesn't
                # exist after this") is already met — skip silently.
                #
                # Two ghost-detection signals:
                #   1. `pk is None` — fast path for objects deleted via
                #      Evennia's `.delete()` method, which clears pk.
                #   2. `ObjectDoesNotExist` raised during this delete —
                #      catches DATABASE-LEVEL cascade deletes (where
                #      Django removed the row directly via ON_DELETE
                #      foreign keys) which never notify the Python
                #      wrapper. Such wrappers still have a cached pk
                #      and `_is_deleted=False`, so signal 1 misses them
                #      — but their internal field accessors raise
                #      `ObjectDoesNotExist` because the underlying row
                #      is gone (see evennia/utils/idmapper/models.py).
                if getattr(obj, "pk", None) is None:
                    continue
                try:
                    obj.delete()
                except ObjectDoesNotExist:
                    # Cascade-ghost — already gone, post-condition met.
                    continue
                except Exception as e:
                    raise BuilderError(
                        f"cleanup: failed to delete existing "
                        f"{getattr(obj, 'dbref', '?')} "
                        f"from {path!r}: {e}"
                    ) from e
                self.deleted_count += 1

    def _apply_aliases(self, obj, entity: LoadedEntity) -> None:
        """Add each content["aliases"] string via obj.aliases.add().

        No-op when the field is absent or empty. The Validator's
        _check_aliases_field_shape predicate has already guaranteed the
        field is a list of non-empty strings if present.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        for alias in content.get("aliases", []):
            obj.aliases.add(alias)

    def _apply_locks(self, obj, entity: LoadedEntity) -> None:
        """Add content["locks"] via obj.locks.add(), if present.

        Evennia's lock system parses the semicolon-joined
        <lock>:<func()> clauses and adds/updates each named lock; locks
        not mentioned in the YAML keep their typeclass defaults — this is
        partial-update behaviour, not replace-all-locks. The Validator's
        _check_locks_field_shape predicate has already guaranteed the
        field is a non-empty string if present.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        lockstring = content.get("locks")
        if lockstring is None:
            return
        obj.locks.add(lockstring)

    def _apply_attributes(self, obj, entity: LoadedEntity) -> None:
        """Add each content["attributes"] record via obj.attributes.add().

        YAML wins over typeclass defaults: because this runs after
        create_object, an attribute with the same key as one set in
        at_object_creation (or backed by an AttributeProperty descriptor)
        overrides the default — typeclass declares defaults, YAML
        overrides per-instance. Value can be any YAML type (Evennia's
        attribute store handles arbitrary serialisable Python values; the
        Validator does no type check on value). Category is optional —
        when omitted, the attribute uses Evennia's default (uncategorised)
        category.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        for attr in content.get("attributes", []):
            obj.attributes.add(
                attr["key"], attr["value"], category=attr.get("category"),
            )

    def _apply_tags(self, obj, entity: LoadedEntity) -> None:
        """Add each content["tags"] entry, then the auto-set identity pair.

        Author tags first: each entry normalised to (key, category) via
        _normalise_tag, then added via obj.tags.add(). Then the identity
        pair is always appended last — wb_file_id (the file this object
        came from, and therefore what a rebuild sweeps) and wb_entity_id
        (what this object *is*, and what every reference resolves
        against). The wb_* category prefix is reserved for
        library-controlled tags; the Validator's
        _check_tags_no_reserved_category predicate rejects any author tag
        using a wb_* category, so the auto-set pair can't collide. Adding
        the auto-set pair last keeps it the final word about identity.

        Neither value is derived from the path, so renaming or moving the
        YAML leaves every object's identity intact.
        """
        content = entity.content if isinstance(entity.content, dict) else {}
        for tag in content.get("tags", []):
            key, category = _normalise_tag(tag)
            obj.tags.add(key, category=category)

        obj.tags.add(
            self._file_id_for(entity.path), category=_TAG_CATEGORY_FILE_ID,
        )
        obj.tags.add(content["entity_id"], category=_TAG_CATEGORY_ENTITY_ID)

    def _invoke_post_build_hook(self, obj, entity: LoadedEntity) -> None:
        """Invoke ``obj.wb_at_post_build()`` if the typeclass defines it.

        Duck-typed and opt-in — typeclasses without the method get a
        silent no-op. The hook fires once per entity, at the end of
        ``_build_one``, after every ``_apply_*`` step has run. By that
        point all YAML-supplied attributes, tags, locks, and aliases
        are in place on the object, so the hook observes final values
        rather than the typeclass defaults that ``at_object_creation``
        saw.

        Exceptions inside the hook are caught and logged to
        ``world-builder.log`` via ``wb_log``; the entity remains built
        and the build pass continues. Consumer hook bugs must not be
        able to turn a successful apply into "no partial state" abort.

        See docs/post-build-hook.md for the contract, the comparison
        to ``evennia-mob-spawner``'s ``ms_at_post_spawn``, and what is
        deliberately not included.
        """
        hook = getattr(obj, _WB_AT_POST_BUILD_ATTR, None)
        if not callable(hook):
            return
        try:
            hook()
        except Exception as e:
            wb_log(
                f"{type(obj).__name__}.{_WB_AT_POST_BUILD_ATTR}() raised "
                f"on {entity.path!r} entity_id="
                f"{entity.content.get('entity_id') if isinstance(entity.content, dict) else '?'}: {e}",
                level="ERROR",
            )


def _normalise_tag(tag) -> tuple[str, str | None]:
    """Turn a YAML tag entry into (key, category).

    Shorthand string -> (string, None), Evennia's default category. Dict
    form {key, category?} -> (tag["key"], tag.get("category")). The
    Validator's _check_tags_field_shape predicate has already rejected
    anything else by the time we reach this code, so this function trusts
    shape.

    A free function (not a method) because it doesn't depend on Builder
    state — keeping it module-scoped makes it independently testable and
    reusable if other components ever need the same normalisation.
    """
    if isinstance(tag, str):
        return tag, None
    return tag["key"], tag.get("category")
