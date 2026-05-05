# SPDX-License-Identifier: BSD-3-Clause
"""Validator — checks LoadedEntities before they reach the Builder.

The validator runs a single pass over the loaded entities. Two tiers of
check fire per entity inside that pass:

- **Stateless predicates** (``PER_ENTITY_PREDICATES``) — pure functions
  ``(entity) -> finding | None``. Each one inspects a single semantic
  concern and returns ``None`` (pass) or a finding string (fail).
- **Stateful checks** — methods that read and update the validator's
  accumulating state (the per-file ``{deployment_file: {ids}}`` index)
  alongside the predicate sweep. Used for checks that need to know
  about other entities, e.g. duplicate deployment_id detection.

Stateful checks only run on entities that pass every stateless
predicate, so they never operate on malformed data.

All findings are appended to ``self.messages``; errors are also tracked
separately, and ``validate()`` raises ``ValidatorError`` at the end of
the pass if any errors were collected. Per CLAUDE.md principle 4:
gather every finding, then refuse — never partial apply, never halt on
the first error. Operators see the complete list in one run.

Adding a stateless check: write a predicate function and append it to
``PER_ENTITY_PREDICATES``. Adding a stateful check: write a method on
Validator and call it from the per-entity loop.

Cross-file reference resolution will land as a third tier (a deferred
check phase after the per-entity loop) once the cross-ref YAML shape
is settled — see DESIGN/deployment-identity.md.
"""
from .definitions import Definitions
from .errors import ValidatorError
from .loader import LoadedEntity


def _check_deployment_id_well_formed(entity: LoadedEntity) -> str | None:
    """Every entity must declare deployment_id as a non-negative integer.

    The field is the load-bearing handle for the deployment-identity
    contract (see DESIGN/deployment-identity.md). Without it, neither
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

    PER_ENTITY_PREDICATES = (
        _check_deployment_id_well_formed,
    )

    def __init__(self, definitions: Definitions):
        self.definitions = definitions
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

    def _run_stateless_predicates(self, entity: LoadedEntity) -> bool:
        """Run every PER_ENTITY_PREDICATE against entity. Return True if all pass."""
        clean = True
        for predicate in self.PER_ENTITY_PREDICATES:
            finding = predicate(entity)
            if finding is not None:
                self._record_finding(finding)
                clean = False
        return clean

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
