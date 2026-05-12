# SPDX-License-Identifier: BSD-3-Clause
"""Public runtime API for consumer game code.

The functions in this module are the library's public surface for code
running inside Evennia *at game runtime* — commands, scripts, typeclass
methods — as distinct from the build-time pipeline (Builder/Validator/
Loader/Finder) which is invoked via ``wb_build``.

The motivation: builder-authored objects are identified by the stable
``(deployment_file, deployment_id)`` pair (see
``DESIGN/deployment-identity.md``). Dbrefs change across redeploys; the
identity pair does not. Game code that needs to refer to a specific
library-built object should resolve the identity pair to a dbref or
object at runtime rather than hard-coding a dbref that will go stale on
the next ``wb_build``.

See ``DESIGN/runtime-lookups.md`` for the contract.
"""
from .errors import ApiError


_TAG_CATEGORY_DEPLOYMENT_FILE = "wb_deployment_file"
_TAG_CATEGORY_DEPLOYMENT_ID = "wb_deployment_id"


def wb_lookup_dbref(deployment_file: str, deployment_id: int) -> str | None:
    """Return the dbref of the object identified by the pair, without instantiating it.

    Queries the underlying ``ObjectDB`` table via the Django ORM and
    returns the result as Evennia's ``#<id>`` dbref string. The
    typeclass is never instantiated — no ``at_init`` hook fires, no
    idmapper cache entry is loaded — so this is the right call when
    you only need a stable identifier (to compare, to hand to another
    helper, to log, etc.) and don't actually need to touch the object.

    Args:
        deployment_file: The full repo-root-relative path the entity
            was built from, exactly as it appears in the YAML source
            tree (e.g. ``"millholm/forest.yaml"``).
        deployment_id:   The author-supplied integer id, unique within
            the file.

    Returns:
        ``"#<id>"`` if exactly one object matches; ``None`` if no
        object matches.

    Raises:
        ApiError: If more than one object matches. This indicates a
            cleanup-integrity failure in the Builder — the
            ``(deployment_file, deployment_id)`` pair is supposed to
            be globally unique. Same diagnostic shape as
            ``Builder._lookup_in_db``.
    """
    ids = _query_object_ids(deployment_file, deployment_id)
    if not ids:
        return None
    if len(ids) > 1:
        raise ApiError(
            f"multiple objects match (deployment_file={deployment_file!r}, "
            f"deployment_id={deployment_id}); ids={list(ids)}. This indicates "
            f"a cleanup integrity failure in the Builder."
        )
    return f"#{ids[0]}"


def wb_lookup_object(deployment_file: str, deployment_id: int):
    """Return the typeclass-inflated object identified by the pair.

    Resolves the identity pair via the same indexed two-tag query
    ``wb_lookup_dbref`` uses (O(log n) on the Tag table — independent of
    how many entities share the same ``deployment_file``), then
    fetches the matching row through ``ObjectDB.objects.get(pk=...)``
    so the returned handle is the live typeclass instance
    (attributes/tags/handlers all available).

    Prefer ``wb_lookup_dbref`` when you only need an identifier — this
    call inflates the typeclass and warms the idmapper cache.

    Args:
        deployment_file: As in ``wb_lookup_dbref``.
        deployment_id:   As in ``wb_lookup_dbref``.

    Returns:
        The Evennia object if exactly one matches; ``None`` if no
        object matches.

    Raises:
        ApiError: If more than one object matches (see
            ``wb_lookup_dbref`` for rationale).
    """
    ids = _query_object_ids(deployment_file, deployment_id)
    if not ids:
        return None
    if len(ids) > 1:
        raise ApiError(
            f"multiple objects match (deployment_file={deployment_file!r}, "
            f"deployment_id={deployment_id}); ids={list(ids)}. This indicates "
            f"a cleanup integrity failure in the Builder."
        )

    # Lazy import — Evennia must be bootstrapped before this fires.
    from evennia.objects.models import ObjectDB

    return ObjectDB.objects.get(pk=ids[0])


def _query_object_ids(deployment_file: str, deployment_id: int) -> list:
    """Return the ObjectDB primary keys matching the identity pair.

    Two chained ``.filter()`` calls force two separate joins on the
    ``db_tags`` M2M, so a row must carry BOTH the deployment_file tag
    AND the deployment_id tag to be returned (a single multi-arg
    filter would AND the conditions on a single joined row, which is
    not what we want).

    ``db_tagtype__isnull=True`` excludes alias and permission tags;
    ``db_model__iexact="objectdb"`` scopes the join to the object-side
    tags only. Both mirror what Evennia's own ``get_by_tag`` does.
    """
    # Lazy imports — Evennia must be bootstrapped before this fires.
    from evennia.objects.models import ObjectDB

    return list(
        ObjectDB.objects.filter(
            db_tags__db_key__iexact=deployment_file,
            db_tags__db_category__iexact=_TAG_CATEGORY_DEPLOYMENT_FILE,
            db_tags__db_tagtype__isnull=True,
            db_tags__db_model__iexact="objectdb",
        ).filter(
            db_tags__db_key__iexact=str(deployment_id),
            db_tags__db_category__iexact=_TAG_CATEGORY_DEPLOYMENT_ID,
            db_tags__db_tagtype__isnull=True,
            db_tags__db_model__iexact="objectdb",
        ).values_list("id", flat=True).distinct()
    )
