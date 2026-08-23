# SPDX-License-Identifier: BSD-3-Clause
"""Public runtime API for consumer game code.

The functions in this module are the library's public surface for code
running inside Evennia *at game runtime* — commands, scripts, typeclass
methods — as distinct from the build-time pipeline (Builder/Validator/
Loader/Finder) which is invoked via ``wb_build``.

The motivation: builder-authored objects are identified by a stable
``entity_id`` (see ``docs/deployment-identity.md``). Dbrefs change across
redeploys; the id does not, and it survives the entity being moved to a
different file. Game code that needs to refer to a specific
library-built object should resolve the id to a dbref or object at
runtime rather than hard-coding a dbref that will go stale on the next
``wb_build``.

See ``docs/runtime-lookups.md`` for the contract.
"""
from .errors import ApiError


_TAG_CATEGORY_ENTITY_ID = "wb_entity_id"


def wb_lookup_dbref(entity_id: str) -> str | None:
    """Return the object's dbref, without instantiating it.

    Queries the underlying ``ObjectDB`` table via the Django ORM and
    returns the result as Evennia's ``#<id>`` dbref string. The
    typeclass is never instantiated — no ``at_init`` hook fires, no
    idmapper cache entry is loaded — so this is the right call when
    you only need a stable identifier (to compare, to hand to another
    helper, to log, etc.) and don't actually need to touch the object.

    Args:
        entity_id: The id the author declared on the entity in YAML.

    Returns:
        ``"#<id>"`` if exactly one object matches; ``None`` if no
        object matches.

    Raises:
        ApiError: If more than one object matches. This indicates a
            cleanup-integrity failure in the Builder — an ``entity_id``
            is globally unique, and the Validator refuses a repo that
            declares one twice. Same diagnostic shape as
            ``Builder._lookup_in_db``.
    """
    ids = _query_object_ids(entity_id)
    if not ids:
        return None
    if len(ids) > 1:
        raise ApiError(_multiple_match_message(entity_id, ids))
    return f"#{ids[0]}"


def wb_lookup_object(entity_id: str):
    """Return the typeclass-inflated object with this ``entity_id``.

    Resolves the id via the same indexed query ``wb_lookup_dbref`` uses
    (O(log n) on the Tag table — independent of how many entities the
    source file declares), then fetches the matching row through
    ``ObjectDB.objects.get(pk=...)`` so the returned handle is the live
    typeclass instance (attributes/tags/handlers all available).

    Prefer ``wb_lookup_dbref`` when you only need an identifier — this
    call inflates the typeclass and warms the idmapper cache.

    Args:
        entity_id: As in ``wb_lookup_dbref``.

    Returns:
        The Evennia object if exactly one matches; ``None`` if no
        object matches.

    Raises:
        ApiError: If more than one object matches (see
            ``wb_lookup_dbref`` for rationale).
    """
    ids = _query_object_ids(entity_id)
    if not ids:
        return None
    if len(ids) > 1:
        raise ApiError(_multiple_match_message(entity_id, ids))

    # Lazy import — Evennia must be bootstrapped before this fires.
    from evennia.objects.models import ObjectDB

    return ObjectDB.objects.get(pk=ids[0])


def _multiple_match_message(entity_id: str, ids: list) -> str:
    """The shared diagnostic for a violated uniqueness invariant."""
    return (
        f"multiple objects match entity_id={entity_id}; ids={list(ids)}. "
        f"This indicates a cleanup integrity failure in the Builder."
    )


def _query_object_ids(entity_id: str) -> list:
    """Return the ObjectDB primary keys carrying this ``entity_id``.

    One join on the ``db_tags`` M2M. The previous composite identity
    needed two chained ``.filter()`` calls to force separate joins, so a
    row had to carry both halves; an ``entity_id`` is globally unique,
    so the id alone is the query.

    ``db_tagtype__isnull=True`` excludes alias and permission tags;
    ``db_model__iexact="objectdb"`` scopes the join to the object-side
    tags only. Both mirror what Evennia's own ``get_by_tag`` does.
    """
    # Lazy imports — Evennia must be bootstrapped before this fires.
    from evennia.objects.models import ObjectDB

    return list(
        ObjectDB.objects.filter(
            db_tags__db_key__iexact=entity_id,
            db_tags__db_category__iexact=_TAG_CATEGORY_ENTITY_ID,
            db_tags__db_tagtype__isnull=True,
            db_tags__db_model__iexact="objectdb",
        ).values_list("id", flat=True).distinct()
    )
