# SPDX-License-Identifier: BSD-3-Clause
"""Definitions — parsed contents of a content repo's definitions.yaml.

The library reads definitions.yaml once per command invocation; the parsed
Definitions object is then handed to the Finder, Loader, and any other
consumer of the manifest convention. As more declarations land in
definitions.yaml over time, they become typed fields on this class.
"""
from dataclasses import dataclass

from evennia_yaml_reader import Reader

from .errors import DefinitionsError


_DEFINITIONS_PATH = "definitions.yaml"


@dataclass(frozen=True)
class Definitions:
    """Parsed contents of definitions.yaml.

    Attributes:
        levels: Hierarchical level names declared by the consumer, in order.
                Empty tuple () is valid and means a flat world (only an
                empty query is meaningful — `find()` returns root, Loader
                walks everything from there).
        repo_ci_pre_validation:
                Consumer's assertion that the content repo has a CI gate
                running wb-validate on every PR (e.g. GitHub branch
                protection + required status check). False (the safe
                default) makes wb_build pre-validate the whole repo before
                every build; True trusts the gate and skips that work.
                The library cannot verify this assertion — see
                DESIGN/validation-gating.md for rationale.
        strict_attributes:
                Scaffolded for a future feature, currently inert. The
                planned behaviour: when True, the validator will reject
                any YAML-declared attribute whose key isn't declared on
                the entity's typeclass (catches typos like
                ``loafs_avilable`` silently creating junk attributes
                that no game logic reads).

                Until the feature lands, ``from_dict()`` refuses to
                parse ``strict-attributes: true`` — a consumer who
                flipped it would otherwise believe attribute validation
                was running when nothing is checked. Field stays in the
                schema so authors can hold the YAML key in mind, but
                only ``False`` is accepted right now.
    """

    levels: tuple = ()
    repo_ci_pre_validation: bool = False
    strict_attributes: bool = False

    @classmethod
    def from_reader(cls, reader: Reader, path: str = _DEFINITIONS_PATH) -> "Definitions":
        """Read and parse definitions.yaml using the supplied Reader."""
        result = reader.read(path)
        return cls.from_dict(result.parsed, source_path=path)

    @classmethod
    def from_dict(cls, data, *, source_path: str = "<dict>") -> "Definitions":
        """Construct from a parsed dict (useful for tests).

        Raises DefinitionsError if the data is malformed.
        """
        if data is None:
            return cls(levels=())
        if not isinstance(data, dict):
            raise DefinitionsError(
                f"{source_path}: expected a mapping, got {type(data).__name__}"
            )
        levels = data.get("levels", [])
        if not isinstance(levels, list):
            raise DefinitionsError(
                f"{source_path}: 'levels' must be a list, got {type(levels).__name__}"
            )
        for level in levels:
            if not isinstance(level, str):
                raise DefinitionsError(
                    f"{source_path}: 'levels' entries must be strings; got {level!r}"
                )

        gating = data.get("repo-ci-pre-validation", False)
        if not isinstance(gating, bool):
            raise DefinitionsError(
                f"{source_path}: 'repo-ci-pre-validation' must be a boolean, "
                f"got {type(gating).__name__}"
            )

        strict_attrs = data.get("strict-attributes", False)
        if not isinstance(strict_attrs, bool):
            raise DefinitionsError(
                f"{source_path}: 'strict-attributes' must be a boolean, "
                f"got {type(strict_attrs).__name__}"
            )
        if strict_attrs:
            # The setting is scaffolded but not yet functional. Refuse
            # rather than silently accept — a consumer who flips it
            # would otherwise believe typeclass-attribute validation
            # was running when in reality nothing is checked.
            raise DefinitionsError(
                f"{source_path}: 'strict-attributes: true' is not yet "
                "implemented (it's scaffolded for a planned future "
                "feature). Set it back to false. The library will start "
                "honouring true once typeclass-attribute introspection "
                "lands; until then, accepting true would be misleading."
            )

        return cls(
            levels=tuple(levels),
            repo_ci_pre_validation=gating,
            strict_attributes=strict_attrs,
        )

    def validate_query(self, query: dict) -> None:
        """Validate a query against the declared levels.

        A query is valid if:

        - Every key is a declared level (value of self.levels).
        - The keys present form a contiguous prefix of self.levels (skipping
          intermediate levels is not allowed).

        Raises DefinitionsError on any violation. Returns None on success.
        """
        for key in query:
            if key not in self.levels:
                raise DefinitionsError(
                    f"Query key {key!r} is not a declared level "
                    f"(declared: {list(self.levels)})"
                )

        seen_missing = False
        for level in self.levels:
            if level not in query:
                seen_missing = True
            elif seen_missing:
                raise DefinitionsError(
                    f"Query must form a contiguous prefix of levels "
                    f"{list(self.levels)}; got {dict(query)} "
                    f"(cannot skip an intermediate level)"
                )
