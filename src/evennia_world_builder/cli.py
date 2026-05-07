# SPDX-License-Identifier: BSD-3-Clause
"""Standalone CLI entry points for world-builder.

The tools here run independently of any Evennia process so they can be
invoked from a developer's shell, a pre-commit hook, or CI. They share
the library's pipeline (Reader / Definitions / Finder / Loader /
Validator) — only the invocation differs from the in-game ``wb_build``
command.

Currently shipped:

- ``wb-validate`` — runs the validator pipeline against a content repo
  and prints any messages the validator emits. Real validator checks
  layer in next; v0 emits a single proof-of-life message per run.
"""
import argparse
import sys

from .definitions import Definitions
from .errors import (
    DefinitionsError,
    FinderError,
    LoaderError,
    ReaderError,
    ValidatorError,
)
from .finder import Finder
from .loader import Loader
from .readers import LocalReader
from .validator import Validator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wb-validate",
        description=(
            "Validate a world-builder content repo. Runs Reader → "
            "Definitions → Finder → Loader → Validator and prints any "
            "validator messages."
        ),
    )
    parser.add_argument(
        "--reader",
        choices=["local"],
        default="local",
        help="Reader implementation to use (default: local).",
    )
    parser.add_argument(
        "--root",
        help="Filesystem path to the content repo root (required when --reader=local).",
    )
    return parser


def _build_reader(args: argparse.Namespace):
    """Construct the configured Reader from CLI args.

    Encapsulated so the GitHub variant slots in by extending the choices
    + branch here, without touching the rest of the CLI.
    """
    if args.reader == "local":
        if not args.root:
            raise SystemExit("wb-validate: --root is required when --reader=local")
        return LocalReader(root=args.root)
    raise SystemExit(f"wb-validate: unknown reader {args.reader!r}")


def validate(argv: list[str] | None = None) -> int:
    """Entry point for the ``wb-validate`` console script.

    Runs the full pre-Builder pipeline and prints whatever the Validator
    emits via ``validator.messages``. Returns 0 on a clean run, 1 on
    any pipeline failure (reader, definitions, finder, loader, or
    validator error).
    """
    args = _build_parser().parse_args(argv)
    reader = _build_reader(args)

    # Build the pipeline. Errors before validation are fatal in their
    # own right — print and exit. Validation errors are different: we
    # still want to surface every collected finding to the operator
    # even though the run is being refused, so we catch ValidatorError
    # separately and fall through to the message-printing block.
    try:
        definitions = Definitions.from_reader(reader)
        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)
        load_result = loader.load(finder.find())
        # wb-validate always loads the whole repo (empty-query find), so
        # it can run Tier 4 cross-ref resolution against a complete
        # seen_ids index. evennia_runtime stays False — the consumer's
        # gamedir is generally not on sys.path in CI. file_metadata
        # carries any file-level keys (incoming_exits, etc.).
        validator = Validator(
            definitions,
            resolve_cross_refs=True,
            file_metadata=load_result.file_metadata,
        )
    except (ReaderError, DefinitionsError, FinderError, LoaderError) as e:
        print(f"wb-validate: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    entities = load_result.entities

    exit_code = 0
    try:
        validator.validate(entities)
    except ValidatorError:
        exit_code = 1

    print(f"wb-validate: reader={args.reader} root={getattr(args, 'root', None)}")
    print(f"wb-validate: loaded {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}")
    for msg in validator.messages:
        print(msg)
    if exit_code:
        print(
            f"wb-validate: refusing — {len(validator.errors)} validation error"
            f"{'s' if len(validator.errors) != 1 else ''}",
            file=sys.stderr,
        )
    return exit_code


def _validate_main() -> None:
    """Console-script shim: forward sys.argv and propagate exit code."""
    sys.exit(validate(sys.argv[1:]))
