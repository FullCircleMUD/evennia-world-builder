# SPDX-License-Identifier: BSD-3-Clause
"""Validator — checks LoadedEntities before they reach the Builder.

Predicate tiers (see docs/validator.md for the full architecture):

- **Tier 1 — stateless predicates** (``PER_ENTITY_PREDICATES``): pure
  ``(entity) -> finding | None``. Always run.
- **Tier 2 — stateful per-file checks**: methods that read/update
  ``self.seen_ids`` during the pass. Always run (only on entities that
  passed Tier 1, to avoid recording garbage).
- **Tier 3 — Evennia-runtime predicates** (``EVENNIA_ONLY_PREDICATES``):
  stateless predicates that need the consumer's typeclasses + Evennia
  runtime to be importable. Run only when the caller passes
  ``evennia_runtime=True`` to ``Validator.__init__``. ``wb_build``
  passes True; ``wb-validate`` (CLI) leaves it False.
- **Tier 4 — cross-ref resolution** (``_check_cross_refs``): a deferred
  phase after the per-entity loop that resolves cross-references against
  the fully-built ``seen_ids`` index. Runs only when the caller passes
  ``resolve_cross_refs=True`` to ``Validator.__init__``.

All findings are appended to ``self.messages``; errors are also tracked
separately, and ``validate()`` raises ``ValidatorError`` at the end of
the pass if any errors were collected. Per CLAUDE.md principle 4:
gather every finding, then refuse — never partial apply, never halt on
the first error. Operators see the complete list in one run.

Adding a stateless check: write a predicate function and append it to
``PER_ENTITY_PREDICATES`` (Tier 1) or ``EVENNIA_ONLY_PREDICATES``
(Tier 3). Adding a stateful check: write a method on Validator and call
it from the per-entity loop.
"""
import ast
import importlib

from .definitions import Definitions
from .errors import ValidatorError
from .loader import LoadedEntity


def _check_deployment_id_well_formed(entity: LoadedEntity) -> str | None:
    """Every entity must declare deployment_id as a non-negative integer.

    The field is the load-bearing handle for the deployment-identity
    contract (see docs/deployment-identity.md). Without it, neither
    cleanup nor cross-references can resolve.
    """
    content = entity.content if isinstance(entity.content, dict) else {}

    if "deployment_id" not in content:
        return f"{entity.path}: missing required field 'deployment_id'"

    value = content["deployment_id"]
    # bool is a subclass of int in Python — exclude it explicitly so that
    # `deployment_id: true` doesn't slip through as a valid integer.
    if not isinstance(value, int) or isinstance(value, bool):
        return (
            f"{entity.path}: 'deployment_id' must be an integer, "
            f"got {type(value).__name__}"
        )

    if value < 0:
        return f"{entity.path}: 'deployment_id' must be non-negative, got {value}"

    return None


# Tag categories starting with this prefix are reserved for the library
# (currently `wb_deployment_file` and `wb_deployment_id`, set automatically
# by the Builder). Authors can't use them in YAML — see docs/deployment-identity.md.
_RESERVED_TAG_CATEGORY_PREFIX = "wb_"


def _check_tags_field_shape(entity: LoadedEntity) -> str | None:
    """Tier 1: ``tags:`` (when present) is a list of well-formed entries.

    Each entry is either a non-empty string (shorthand for default category)
    or a mapping with at least ``key`` (non-empty string) and an optional
    ``category`` (string).
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "tags" not in content:
        return None

    tags = content["tags"]
    if not isinstance(tags, list):
        return (
            f"{entity.path}: 'tags' must be a list, "
            f"got {type(tags).__name__}"
        )

    for index, tag in enumerate(tags):
        prefix = f"{entity.path}: tags[{index}]"

        if isinstance(tag, str):
            if not tag.strip():
                return f"{prefix}: shorthand tag must be a non-empty string"
            continue

        if isinstance(tag, dict):
            if "key" not in tag:
                return f"{prefix}: tag mapping must include 'key'"
            key = tag["key"]
            if not isinstance(key, str) or not key.strip():
                return f"{prefix}: tag 'key' must be a non-empty string"
            if "category" in tag:
                category = tag["category"]
                if not isinstance(category, str):
                    return (
                        f"{prefix}: tag 'category' must be a string, "
                        f"got {type(category).__name__}"
                    )
            continue

        return (
            f"{prefix}: must be a string or a mapping, "
            f"got {type(tag).__name__}"
        )

    return None


def _check_tags_no_reserved_category(entity: LoadedEntity) -> str | None:
    """Tier 1: author tags must not use categories reserved by the library.

    Categories starting with ``wb_`` are reserved for tags the Builder
    sets automatically (e.g. ``wb_deployment_file``, ``wb_deployment_id``).
    An author writing one of these in YAML would collide with the
    auto-set value — explicit refusal beats silent overwrite.

    Defensively skips malformed entries so this predicate doesn't
    double-report shape problems already caught by
    ``_check_tags_field_shape``.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    tags = content.get("tags")
    if not isinstance(tags, list):
        return None

    for index, tag in enumerate(tags):
        if not isinstance(tag, dict):
            continue
        category = tag.get("category")
        if not isinstance(category, str):
            continue
        if category.startswith(_RESERVED_TAG_CATEGORY_PREFIX):
            key_repr = repr(tag.get("key"))
            return (
                f"{entity.path}: tags[{index}]: category {category!r} is "
                f"reserved for the library (key={key_repr}); the {_RESERVED_TAG_CATEGORY_PREFIX}* "
                f"namespace is set automatically by the Builder"
            )
    return None


def _check_name_well_formed(entity: LoadedEntity) -> str | None:
    """Tier 1: every entity must declare ``name`` as a non-empty string.

    The Builder uses ``name`` as the Evennia object's ``key`` — what
    players see. No fallback to the source path because that would
    produce keys like ``millholm/bakery.yaml``, which is operator
    debug output leaking into player-visible content.
    """
    content = entity.content if isinstance(entity.content, dict) else {}

    if "name" not in content:
        return f"{entity.path}: missing required field 'name'"

    value = content["name"]
    if not isinstance(value, str):
        return (
            f"{entity.path}: 'name' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return f"{entity.path}: 'name' must be a non-empty string"
    return None


_CROSS_REF_KEYS = ("deployment_file", "deployment_id")

# A link entry's full key set. `entity`, `attribute`, `points_to` are
# required; `category` is optional. Anything else is rejected as an
# unexpected key. See docs/links.md for the spec.
_LINK_REQUIRED_KEYS = ("entity", "attribute", "points_to")
_LINK_OPTIONAL_KEYS = ("category",)
_LINK_ALL_KEYS = _LINK_REQUIRED_KEYS + _LINK_OPTIONAL_KEYS


def _check_cross_ref_dict_shape(value, entity_path: str, field_name: str) -> str | None:
    """Validate that a value is a strict ``{deployment_file, deployment_id}`` dict.

    Caller has already established that ``value`` is a dict (the
    null-or-other-shape decision is field-specific and stays in the
    calling predicate). This helper just enforces the strict shape:
    both keys required, no extras, ``deployment_file`` a non-empty
    string, ``deployment_id`` a non-negative integer (``bool`` excluded).

    Used by ``_check_location_well_formed`` and
    ``_check_destination_well_formed`` — both fields carry cross-ref
    dicts with identical structure, only the per-field "what counts as
    valid" decision differs.
    """
    expected = set(_CROSS_REF_KEYS)
    actual = set(value)
    missing = expected - actual
    if missing:
        return (
            f"{entity_path}: '{field_name}' cross-ref missing required key(s): "
            f"{sorted(missing)}"
        )
    extra = actual - expected
    if extra:
        return (
            f"{entity_path}: '{field_name}' cross-ref has unexpected key(s): "
            f"{sorted(extra)}"
        )

    deployment_file = value["deployment_file"]
    if not isinstance(deployment_file, str) or not deployment_file.strip():
        return (
            f"{entity_path}: '{field_name}' cross-ref 'deployment_file' "
            f"must be a non-empty string"
        )

    deployment_id = value["deployment_id"]
    # bool is an int subclass — reject it explicitly so {deployment_id: true}
    # doesn't slip through as a valid integer.
    if not isinstance(deployment_id, int) or isinstance(deployment_id, bool):
        return (
            f"{entity_path}: '{field_name}' cross-ref 'deployment_id' "
            f"must be an integer, got {type(deployment_id).__name__}"
        )
    if deployment_id < 0:
        return (
            f"{entity_path}: '{field_name}' cross-ref 'deployment_id' "
            f"must be non-negative, got {deployment_id}"
        )

    return None


def _check_subscript_path_shape(attribute: str) -> str | None:
    """Validate the subscript-path form of a links: `attribute` value.

    The subscript-path syntax (see docs/links.md) lets a link's
    `attribute:` field carry a Python expression like
    ``destinations["foo"]["bar"]`` so the Builder can navigate into an
    existing structured attribute and assign the resolved cross-ref
    at the leaf. This helper verifies the string parses cleanly into
    that shape so badly-formed paths surface at validate time, before
    any DB mutation, alongside other findings.

    Caller is expected to invoke this only when ``attribute`` actually
    contains ``[`` (bare attribute names go through
    ``attributes.add(...)`` and don't need this check).

    Returns ``None`` on a well-formed path, else an error string
    suffix the caller can prefix with ``{prefix}: 'attribute' ``.

    Accepted shapes:
        foo["bar"]
        foo["bar"]["baz"]
        foo[0]["bar"]              (int indices distinguished from "0")
        foo["bar"][2][3]

    Refused shapes:
        foo[unclosed               (SyntaxError)
        foo.bar                    (Attribute access, not Subscript chain)
        foo[bar]                   (bar is a Name, not a literal)
        foo()["bar"]               (Call, not bare Name at the head)
        ["bar"]                    (empty leading identifier)
    """
    try:
        tree = ast.parse(attribute, mode="eval").body
    except SyntaxError as e:
        return f"is not valid Python subscript syntax: {e.msg}"

    while isinstance(tree, ast.Subscript):
        try:
            ast.literal_eval(tree.slice)
        except (ValueError, SyntaxError):
            return (
                "contains a non-literal subscript "
                "(only string keys and integer indices are allowed)"
            )
        tree = tree.value

    if not isinstance(tree, ast.Name):
        return (
            "must start with a bare attribute name "
            "followed only by [..] subscripts"
        )

    return None


def _check_location_well_formed(entity: LoadedEntity) -> str | None:
    """Tier 1: every entity must declare ``location`` explicitly.

    Two valid shapes:

    - ``null`` — orphan placement (the dominant case for top-level rooms).
    - ``{deployment_file: <str>, deployment_id: <non-negative int>}`` — a
      cross-reference dict pointing at the entity that contains this one.
      The Loader synthesises this shape on every nested entity (a child
      of a ``contents:`` or ``exits:`` block), pointing at the immediate
      parent; top-level entities can also declare it directly to place
      themselves inside another entity (resolution against the
      ``seen_ids`` index lands as a separate Tier 4 check when it does).

    Why mandate the field even when the value can be null? Without an
    explicit declaration, an author who forgets the field can't tell
    whether they meant "orphan" or "should have had a parent and forgot."
    """
    content = entity.content if isinstance(entity.content, dict) else {}

    if "location" not in content:
        return f"{entity.path}: missing required field 'location'"

    value = content["location"]
    if value is None:
        return None

    if not isinstance(value, dict):
        return (
            f"{entity.path}: 'location' must be null or a cross-ref dict "
            f"{{deployment_file, deployment_id}}, got {type(value).__name__}"
        )

    return _check_cross_ref_dict_shape(value, entity.path, "location")


def _check_destination_well_formed(entity: LoadedEntity) -> str | None:
    """Tier 1: ``destination:`` (when present) is a cross-ref dict.

    Optional field — absence is fine (it just means the entity is not an
    exit). When present, must be a strict ``{deployment_file,
    deployment_id}`` cross-ref dict. The dict shape is identical to
    ``location:``'s cross-ref shape; the difference between the two
    fields is that ``location:`` accepts null (orphan) while
    ``destination:`` does not (an exit always points somewhere).

    The "is the entity actually meant to be an exit" question — i.e. is
    it inconsistent for this typeclass to have / lack a destination — is
    a separate Tier 3 concern; this Tier 1 predicate just shape-checks
    what's present.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "destination" not in content:
        return None

    value = content["destination"]
    if not isinstance(value, dict):
        return (
            f"{entity.path}: 'destination' must be a cross-ref dict "
            f"{{deployment_file, deployment_id}}, got {type(value).__name__}"
        )

    return _check_cross_ref_dict_shape(value, entity.path, "destination")


def _check_home_well_formed(entity: LoadedEntity) -> str | None:
    """Tier 1: ``home:`` (when present) is null or a cross-ref dict.

    Optional field — absence means "use Evennia's settings.DEFAULT_HOME"
    (typically Limbo). Two valid present-shapes:

    - ``null`` — translates to ``nohome=True`` at create_object time;
      the resulting object's ``home`` field is ``None``.
    - ``{deployment_file: <str>, deployment_id: <non-negative int>}`` —
      cross-ref dict pointing at the entity that should serve as this
      object's home. Same identity scheme as ``location:`` /
      ``destination:``; same Tier 4 resolution path.

    The Builder translates the YAML field into the appropriate
    create_object kwarg in step 4 of the home-field rollout.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "home" not in content:
        return None

    value = content["home"]
    if value is None:
        return None

    if not isinstance(value, dict):
        return (
            f"{entity.path}: 'home' must be null or a cross-ref dict "
            f"{{deployment_file, deployment_id}}, got {type(value).__name__}"
        )

    return _check_cross_ref_dict_shape(value, entity.path, "home")


def _check_location_not_null_when_destination_present(entity: LoadedEntity) -> str | None:
    """Tier 1: an entity with ``destination:`` must have non-null ``location:``.

    Any entity carrying ``destination:`` is an exit (whether nested in
    an ``exits:`` block, or a top-level connector entity). An exit must
    live in a room — ``location: null`` would orphan it, making it
    unreachable.

    Defensive: only fires when ``location:`` is *present and null*.
    A missing ``location`` field is caught by
    ``_check_location_well_formed`` as a separate finding; this
    predicate stays quiet on that case to avoid double-reporting.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "destination" not in content:
        return None
    if "location" not in content:
        return None  # let _check_location_well_formed handle the missing field
    if content["location"] is None:
        return (
            f"{entity.path}: entity declares 'destination:' but 'location:' is null — "
            f"an exit must be in a room"
        )
    return None


def _check_no_author_location_on_nested(entity: LoadedEntity) -> str | None:
    """Tier 1: nested entities must not declare ``location:`` themselves.

    A nested entity's placement is implicit in the YAML structure that
    nests it — the Loader synthesises ``content["location"]`` as a
    cross-ref dict pointing at the parent. An author who writes
    ``location:`` on a nested entity is either confused or fighting the
    structural convention; refuse explicitly so the synthesis isn't a
    silent overwrite.

    The Loader records ``had_author_location`` from the *original*
    mapping before any synthesis, so this predicate just reads the flag.
    """
    if entity.is_nested and entity.had_author_location:
        return (
            f"{entity.path}: nested entity declares 'location:' — placement "
            f"is implicit (set by the parent in `contents:`)"
        )
    return None


def _check_attributes_field_shape(entity: LoadedEntity) -> str | None:
    """Tier 1: ``attributes:`` (when present) is a list of well-shaped records.

    Each entry must be a mapping with ``key`` (non-empty string),
    ``value`` (any type — string, int, float, bool, null, list, dict),
    and an optional ``category`` (string when present).

    The Builder writes each entry via
    ``obj.attributes.add(key, value, category=category)``. Evennia
    accepts arbitrary serialisable values, so we don't constrain
    ``value`` here — that's deliberately Evennia's call, not ours.

    Note: ``value`` must be *present* (the YAML key must be there) but
    its content can be ``null`` — that's a legitimate value (sets the
    attribute to None). Missing the ``value`` key entirely is a refusal
    because a half-declared attribute is almost certainly an author
    typo.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "attributes" not in content:
        return None

    attributes = content["attributes"]
    if not isinstance(attributes, list):
        return (
            f"{entity.path}: 'attributes' must be a list, "
            f"got {type(attributes).__name__}"
        )

    for index, attr in enumerate(attributes):
        prefix = f"{entity.path}: attributes[{index}]"

        if not isinstance(attr, dict):
            return (
                f"{prefix}: must be a mapping, got {type(attr).__name__}"
            )

        if "key" not in attr:
            return f"{prefix}: missing 'key'"
        key = attr["key"]
        if not isinstance(key, str) or not key.strip():
            return f"{prefix}: 'key' must be a non-empty string"

        if "value" not in attr:
            return f"{prefix} ({key!r}): missing 'value'"

        if "category" in attr:
            category = attr["category"]
            if not isinstance(category, str):
                return (
                    f"{prefix} ({key!r}): 'category' must be a string, "
                    f"got {type(category).__name__}"
                )

    return None


def _check_locks_field_shape(entity: LoadedEntity) -> str | None:
    """Tier 1: ``locks:`` (when present) is a non-empty string.

    Optional field. When supplied, the value is an Evennia lockstring
    like ``"examine:all();get:false();puppet:perm(Wizards)"`` — a
    semicolon-joined sequence of ``<lock>:<func()>`` clauses. We don't
    parse the contents here (Evennia validates that itself when the
    lockstring is applied); just enforce the outer shape and reject
    empty/whitespace-only since "I want no extra locks" is best
    expressed by omitting the field, not by writing an empty string.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "locks" not in content:
        return None
    value = content["locks"]
    if not isinstance(value, str):
        return (
            f"{entity.path}: 'locks' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return (
            f"{entity.path}: 'locks' must be a non-empty string "
            f"(omit the field if you don't want any custom locks)"
        )
    return None


def _check_aliases_field_shape(entity: LoadedEntity) -> str | None:
    """Tier 1: ``aliases:`` (when present) is a list of non-empty strings.

    Optional field — absence is fine, empty list is fine. When present,
    every entry must be a non-empty string. No dict form, no shorthand
    forks; aliases are just alternate names for the object.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "aliases" not in content:
        return None

    aliases = content["aliases"]
    if not isinstance(aliases, list):
        return (
            f"{entity.path}: 'aliases' must be a list, "
            f"got {type(aliases).__name__}"
        )

    for index, alias in enumerate(aliases):
        prefix = f"{entity.path}: aliases[{index}]"
        if not isinstance(alias, str):
            return (
                f"{prefix}: must be a string, got {type(alias).__name__}"
            )
        if not alias.strip():
            return f"{prefix}: must be a non-empty string"
    return None


def _check_description_field_shape(entity: LoadedEntity) -> str | None:
    """Tier 1: ``description:`` (when present) must be a string.

    Optional field — absence is fine. The Builder writes the value to
    ``db.desc`` verbatim; whatever the author writes (including empty
    or whitespace-only) lands as-is. The shape check is just to catch
    "I accidentally wrote a list / a number / a dict" situations early
    instead of letting them bubble up as a confusing Evennia error.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    if "description" not in content:
        return None
    value = content["description"]
    if not isinstance(value, str):
        return (
            f"{entity.path}: 'description' must be a string, "
            f"got {type(value).__name__}"
        )
    return None


def _check_typeclass_well_formed(entity: LoadedEntity) -> str | None:
    """Tier 1: every entity must declare ``typeclass`` as a non-empty string.

    No default fallback exists — making the field mandatory keeps the
    contract honest once the Builder grows beyond rooms (exits, objects,
    NPCs each want different defaults, and "DefaultRoom for everything"
    quietly produces wrong objects). If an author wants
    ``evennia.objects.objects.DefaultRoom``, they declare it.
    """
    content = entity.content if isinstance(entity.content, dict) else {}

    if "typeclass" not in content:
        return f"{entity.path}: missing required field 'typeclass'"

    value = content["typeclass"]
    if not isinstance(value, str):
        return (
            f"{entity.path}: 'typeclass' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return f"{entity.path}: 'typeclass' must be a non-empty string"
    return None


def _resolve_typeclass(typeclass: str):
    """Best-effort import of a dotted typeclass path. Returns class or None.

    None means the path is malformed or the import / attribute lookup
    failed. Tier 3's ``_check_typeclass_resolvable`` predicate is the
    one that surfaces those failures as findings; the other Tier 3
    predicates that consume this helper skip cleanly when it returns
    None to avoid double-reporting.
    """
    module_path, _, class_name = typeclass.rpartition(".")
    if not module_path or not class_name:
        return None
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return None
    return getattr(module, class_name, None)


def _check_destination_required_for_exit_typeclass(entity: LoadedEntity) -> str | None:
    """Tier 3: typeclass inheriting from ``DefaultExit`` ⇒ ``destination:`` required.

    Resolves the entity's typeclass; if it inherits from Evennia's
    ``DefaultExit``, ``destination:`` must be present (well-formed shape
    is checked separately by Tier 1's ``_check_destination_well_formed``).

    Skips silently on shape problems with ``typeclass`` (covered by
    Tier 1's ``_check_typeclass_well_formed``) or import errors
    (covered by Tier 3's ``_check_typeclass_resolvable``) so the
    operator sees one finding per authoring mistake, not several.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    typeclass = content.get("typeclass")
    if not isinstance(typeclass, str) or not typeclass.strip():
        return None

    cls = _resolve_typeclass(typeclass)
    if cls is None:
        return None

    from evennia.objects.objects import DefaultExit
    if not issubclass(cls, DefaultExit):
        return None

    if "destination" not in content:
        return (
            f"{entity.path}: typeclass {typeclass!r} inherits from DefaultExit "
            f"but no 'destination:' is set — exits must declare a destination"
        )
    return None


def _check_destination_forbidden_for_non_exit_typeclass(entity: LoadedEntity) -> str | None:
    """Tier 3: typeclass NOT inheriting from ``DefaultExit`` ⇒ ``destination:`` forbidden.

    Catches the inverse mistake — author wrote a destination on a
    non-exit entity (typo: meant to write description, or copy-pasted
    from an exit and forgot to remove the field). Lower-frequency than
    the missing-destination case but cheap to add and gives the author
    a clear validate-time error instead of a quietly-broken object.

    Skips silently on the same shape/import problems as the inverse
    predicate, for the same single-finding reason.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    typeclass = content.get("typeclass")
    if not isinstance(typeclass, str) or not typeclass.strip():
        return None

    cls = _resolve_typeclass(typeclass)
    if cls is None:
        return None

    from evennia.objects.objects import DefaultExit
    if issubclass(cls, DefaultExit):
        return None

    if "destination" in content:
        return (
            f"{entity.path}: typeclass {typeclass!r} does not inherit from DefaultExit "
            f"but 'destination:' is set — non-exits must not declare a destination"
        )
    return None


def _check_typeclass_resolvable(entity: LoadedEntity) -> str | None:
    """Tier 3: the declared typeclass dotted-path must be importable.

    Runs only when the caller asserts an Evennia-runtime environment
    (``Validator(definitions, evennia_runtime=True)``). The consumer's
    typeclass module must be on ``sys.path`` for this to succeed; in the
    standalone ``wb-validate`` CLI it usually isn't, which is why this
    predicate is gated.

    No ``typeclass:`` key on the entity ⇒ skip (Builder will fall back
    to its default). The shape check (must-be-non-empty-string when
    present) is a separate Tier 1 predicate landing alongside the rest
    of the field-shape checks; this predicate trusts shape and verifies
    only resolvability.
    """
    content = entity.content if isinstance(entity.content, dict) else {}
    typeclass = content.get("typeclass")
    if typeclass is None:
        return None
    if not isinstance(typeclass, str) or not typeclass.strip():
        # Shape check belongs in Tier 1; here we just defer cleanly.
        return None

    module_path, _, class_name = typeclass.rpartition(".")
    if not module_path or not class_name:
        return f"{entity.path}: typeclass {typeclass!r} is not a dotted path"

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return (
            f"{entity.path}: typeclass {typeclass!r} — module "
            f"{module_path!r} could not be imported ({e})"
        )
    if not hasattr(module, class_name):
        return (
            f"{entity.path}: typeclass {typeclass!r} — module "
            f"{module_path!r} loaded but class {class_name!r} not found"
        )
    return None


class Validator:
    """Validates LoadedEntities via a predicate-list pipeline.

    Construction:
        definitions: parsed Definitions (provides level vocabulary;
                     used in the proof-of-life message and reserved
                     for future checks that depend on level names).

    Attributes (populated during validate()):
        messages: list of human-readable strings produced during
                  validate(). Includes a leading proof-of-life line
                  plus one entry per failed check. Callers print
                  these — the Validator does no I/O of its own.
        errors:   subset of messages corresponding to actual errors.
                  Empty after a clean run; non-empty implies validate()
                  raised ValidatorError.
        seen_ids: ``{deployment_file: set(deployment_ids)}`` —
                  accumulated as the per-entity loop runs. Used for
                  in-pass duplicate detection and (eventually) backward
                  cross-reference resolution.
    """

    # Stateless predicates that fire on every entity, top-level or nested.
    # The architecture briefly carried a TOP_LEVEL_PREDICATES split during
    # spike 2 design (nested entities were going to skip location-related
    # checks because they had no explicit location). The Loader now
    # synthesises a cross-ref `location:` dict on every nested entity at
    # flatten time, so `_check_location_well_formed` applies uniformly:
    # top-level entities get author-written values, nested entities get
    # Loader-synthesised values, both refused if malformed.
    # `_check_no_author_location_on_nested` gates on the LoadedEntity's
    # is_nested flag directly, no tier split needed.
    PER_ENTITY_PREDICATES = (
        _check_deployment_id_well_formed,
        _check_name_well_formed,
        _check_typeclass_well_formed,
        _check_location_well_formed,
        _check_destination_well_formed,
        _check_home_well_formed,
        _check_location_not_null_when_destination_present,
        _check_no_author_location_on_nested,
        _check_description_field_shape,
        _check_aliases_field_shape,
        _check_locks_field_shape,
        _check_attributes_field_shape,
        _check_tags_field_shape,
        _check_tags_no_reserved_category,
    )

    EVENNIA_ONLY_PREDICATES = (
        _check_typeclass_resolvable,
        _check_destination_required_for_exit_typeclass,
        _check_destination_forbidden_for_non_exit_typeclass,
    )

    def __init__(
        self, definitions: Definitions, *,
        evennia_runtime: bool = False,
        resolve_cross_refs: bool = False,
        file_metadata: dict | None = None,
    ):
        self.definitions = definitions
        self.evennia_runtime = evennia_runtime
        # When True, run the Tier 4 deferred phase: walk every entity's
        # location/destination cross-refs and verify they resolve in the
        # seen_ids index. Caller asserts they're passing whole-repo
        # entities (the only case where unresolved-ref findings are
        # meaningful). wb_build pre-validation and wb-validate both
        # pass True; tests default False to keep narrow-scope cases
        # working.
        self.resolve_cross_refs = resolve_cross_refs
        # Per-file metadata extracted by the Loader (file-level keys
        # like ``incoming_exits:`` that live alongside ``entities:`` in
        # the YAML wrapper mapping). Validator runs file-level shape
        # checks against this dict and Tier 4 walks ``incoming_exits:``
        # entries for cross-ref resolution.
        self.file_metadata: dict = dict(file_metadata or {})
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.seen_ids: dict[str, set[int]] = {}

    def validate(self, entities: list) -> list:
        """Run all checks against every entity. Raise on findings.

        Returns the entities unchanged on a clean run. Raises
        ValidatorError after the full pass if any check failed —
        callers should still read self.messages to surface the
        complete list of findings to the operator.
        """
        self.messages.append(f"VALIDATOR: {self.definitions}")

        for entity in entities:
            stateless_clean = self._run_stateless_predicates(entity)
            if not stateless_clean:
                # Skip stateful checks — they would operate on bad data
                # (e.g. trying to record a non-integer in seen_ids).
                continue
            self._check_and_record_unique_id(entity)

        # File-level shape checks (run once per file, regardless of
        # how many entities were declared in it).
        self._check_file_metadata_shape()

        if self.resolve_cross_refs:
            self._check_cross_refs(entities)

        if self.errors:
            n = len(self.errors)
            raise ValidatorError(
                f"{n} validation error{'s' if n != 1 else ''} — see messages"
            )

        return entities

    def _record_finding(self, finding: str) -> None:
        """All findings flow through here so accumulation stays uniform."""
        self.messages.append(finding)
        self.errors.append(finding)

    def _active_predicates(self) -> tuple:
        """The predicate tuple appropriate for the current caller context.

        Tier 1 (PER_ENTITY_PREDICATES) always runs; Tier 3 (Evennia-runtime)
        opts in via ``evennia_runtime=True``.
        """
        active = self.PER_ENTITY_PREDICATES
        if self.evennia_runtime:
            active = active + self.EVENNIA_ONLY_PREDICATES
        return active

    def _run_stateless_predicates(self, entity: LoadedEntity) -> bool:
        """Run every active predicate against entity. Return True if all pass."""
        clean = True
        for predicate in self._active_predicates():
            finding = predicate(entity)
            if finding is not None:
                self._record_finding(finding)
                clean = False
        return clean

    def _check_file_metadata_shape(self) -> None:
        """File-level Tier 1 — shape-check ``incoming_exits:`` per file.

        ``self.file_metadata`` is a ``{file_path: {key: value, ...}}``
        dict populated by the Loader. The library currently recognises
        one file-level key, ``incoming_exits:``; future file-level keys
        slot in here. Each file gets its incoming_exits list checked
        once, regardless of how many entities it contained.

        For each path in ``file_metadata`` with an ``incoming_exits:``
        entry: must be a list of strict ``{deployment_file,
        deployment_id}`` cross-ref dicts (same shape as ``location:`` /
        ``destination:``, via the shared helper). Findings name the
        path and the array index for easy author location.
        """
        for path, meta in self.file_metadata.items():
            if not isinstance(meta, dict):
                self._record_finding(
                    f"{path}: file metadata must be a mapping, "
                    f"got {type(meta).__name__}"
                )
                continue
            self._check_incoming_exits_shape(path, meta)
            self._check_links_shape(path, meta)

    def _check_incoming_exits_shape(self, path: str, meta: dict) -> None:
        if "incoming_exits" not in meta:
            return
        value = meta["incoming_exits"]
        if not isinstance(value, list):
            self._record_finding(
                f"{path}: 'incoming_exits' must be a list, "
                f"got {type(value).__name__}"
            )
            return
        for index, ref in enumerate(value):
            prefix = f"{path}: incoming_exits[{index}]"
            if not isinstance(ref, dict):
                self._record_finding(
                    f"{prefix}: must be a cross-ref dict "
                    f"{{deployment_file, deployment_id}}, "
                    f"got {type(ref).__name__}"
                )
                continue
            finding = _check_cross_ref_dict_shape(
                ref, path, f"incoming_exits[{index}]",
            )
            if finding is not None:
                self._record_finding(finding)

    def _check_links_shape(self, path: str, meta: dict) -> None:
        """File-level Tier 1 — shape-check ``links:`` per file.

        Each entry must have ``entity``, ``attribute``, ``points_to``
        (required) and may have ``category`` (optional). ``entity`` and
        ``points_to`` are ``{deployment_file, deployment_id}`` cross-ref
        dicts; ``attribute`` and ``category`` are non-empty strings.
        Findings name the path and array index for easy author location.
        See docs/links.md.
        """
        if "links" not in meta:
            return
        value = meta["links"]
        if not isinstance(value, list):
            self._record_finding(
                f"{path}: 'links' must be a list, "
                f"got {type(value).__name__}"
            )
            return
        for index, link in enumerate(value):
            self._check_one_link_shape(path, index, link)

    def _check_one_link_shape(self, path: str, index: int, link) -> None:
        prefix = f"{path}: links[{index}]"
        if not isinstance(link, dict):
            self._record_finding(
                f"{prefix}: must be a link dict "
                f"{{entity, attribute, points_to[, category]}}, "
                f"got {type(link).__name__}"
            )
            return

        actual = set(link)
        missing = set(_LINK_REQUIRED_KEYS) - actual
        if missing:
            self._record_finding(
                f"{prefix}: missing required key(s): {sorted(missing)}"
            )
        extra = actual - set(_LINK_ALL_KEYS)
        if extra:
            self._record_finding(
                f"{prefix}: has unexpected key(s): {sorted(extra)}"
            )

        for field in ("entity", "points_to"):
            if field not in link:
                continue  # already flagged via missing-required
            ref = link[field]
            if not isinstance(ref, dict):
                self._record_finding(
                    f"{prefix}: '{field}' must be a cross-ref dict "
                    f"{{deployment_file, deployment_id}}, "
                    f"got {type(ref).__name__}"
                )
                continue
            finding = _check_cross_ref_dict_shape(
                ref, path, f"links[{index}].{field}",
            )
            if finding is not None:
                self._record_finding(finding)

        if "attribute" in link:
            attribute = link["attribute"]
            if not isinstance(attribute, str) or not attribute.strip():
                self._record_finding(
                    f"{prefix}: 'attribute' must be a non-empty string"
                )
            elif "[" in attribute or "]" in attribute:
                # Either bracket triggers the subscript-path check, so
                # malformed inputs like 'foo]' or 'dict(thing]' are
                # caught at validate time instead of being silently
                # set as garbage attribute names. See docs/links.md
                # § Subscript-path attribute syntax.
                finding = _check_subscript_path_shape(attribute)
                if finding is not None:
                    self._record_finding(
                        f"{prefix}: 'attribute' {finding}"
                    )
                # category: cannot be combined with subscript-path syntax
                # (categories apply to top-level Evennia attribute keys; a
                # path navigates inside an already-set attribute value).
                if "category" in link:
                    self._record_finding(
                        f"{prefix}: 'category' cannot be used with "
                        f"subscript-path attribute (category only applies "
                        f"to bare attribute names)"
                    )

        if "category" in link:
            category = link["category"]
            if not isinstance(category, str) or not category.strip():
                self._record_finding(
                    f"{prefix}: 'category' must be a non-empty string when present"
                )

    def _check_cross_refs(self, entities: list) -> None:
        """Tier 4: every cross-ref must resolve in the seen_ids index.

        Runs after the per-entity loop, when ``self.seen_ids`` reflects
        every clean entity in the build set. Walks:

        - ``content["location"]`` per entity (single cross-ref dict, optional)
        - ``content["destination"]`` per entity (single cross-ref dict, optional)
        - ``self.file_metadata[path]["incoming_exits"]`` per file
          (list of cross-ref dicts, optional)

        For each well-shaped ref, verifies the
        ``(deployment_file, deployment_id)`` pair appears in
        ``self.seen_ids``.

        Defensive about malformed shapes — ``isinstance(ref, dict)`` and
        a key-presence check skip any cross-ref that Tier 1 has already
        flagged. Tier 4 records one finding per unresolved well-shaped
        ref; mistakes Tier 1 caught don't double-report here.

        Same-file forward refs resolve correctly: by the time this
        method runs, every entity has been added to ``seen_ids``
        (regardless of YAML-file order), so a top-level entity declaring
        ``location:`` to point at another entity later in the same file
        will resolve. The Builder's same-file forward-ref refusal is a
        separate decision at create time, not a validate-time concern.
        """
        for entity in entities:
            content = entity.content if isinstance(entity.content, dict) else {}
            for field_name in ("location", "destination", "home"):
                self._check_one_cross_ref(
                    entity.path, content.get(field_name), field_name,
                )

        # File-level incoming_exits — walk per file, name the file path
        # in the finding so the author can locate the bad ref.
        for path, meta in self.file_metadata.items():
            if not isinstance(meta, dict):
                continue
            incoming = meta.get("incoming_exits")
            if isinstance(incoming, list):
                for index, ref in enumerate(incoming):
                    self._check_one_cross_ref(
                        path, ref, f"incoming_exits[{index}]",
                    )

            # File-level links — both `entity` and `points_to` are
            # cross-refs that must resolve. Tier 1 has already filtered
            # out anything malformed; here we just check that whatever
            # well-shaped refs remain land in seen_ids. See docs/links.md.
            links = meta.get("links")
            if isinstance(links, list):
                for index, link in enumerate(links):
                    if not isinstance(link, dict):
                        continue
                    for field in ("entity", "points_to"):
                        self._check_one_cross_ref(
                            path, link.get(field),
                            f"links[{index}].{field}",
                        )

    def _check_one_cross_ref(self, source_path: str, ref, field_name: str) -> None:
        """Verify a single cross-ref dict resolves in self.seen_ids.

        Defensive: silently skips non-dict values and dicts missing
        either required key (Tier 1 has already flagged those). Records
        a finding only for well-shaped refs whose target isn't in the
        index — the operator gets one finding per real authoring
        mistake, not several layered ones.
        """
        if not isinstance(ref, dict):
            return
        if "deployment_file" not in ref or "deployment_id" not in ref:
            return
        target_file = ref["deployment_file"]
        target_id = ref["deployment_id"]
        if target_id not in self.seen_ids.get(target_file, set()):
            self._record_finding(
                f"{source_path}: '{field_name}' cross-ref to "
                f"(deployment_file={target_file!r}, deployment_id={target_id}) "
                f"does not resolve to any entity in this build"
            )

    def _check_and_record_unique_id(self, entity: LoadedEntity) -> None:
        """Record the entity's deployment_id; flag if already seen for this file.

        Maintains ``self.seen_ids[entity.path]`` as the running set of ids
        observed for that file. Detection is "second-occurrence flags";
        the originally-recorded entity is left alone so the operator can
        find both by searching for the conflicting id within the named
        file.
        """
        deployment_id = entity.content["deployment_id"]
        seen = self.seen_ids.setdefault(entity.path, set())
        if deployment_id in seen:
            self._record_finding(
                f"{entity.path}: duplicate deployment_id={deployment_id} "
                f"(already declared elsewhere in this file)"
            )
            return
        seen.add(deployment_id)
