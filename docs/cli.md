# Standalone CLI

evennia-world-builder ships console-script CLIs that run independently of any Evennia process. They share the library's pipeline (`Reader → Definitions → Finder → Loader → Validator`) — only the invocation and output channel differ from the in-game `wb_build` command. CLIs exist so authors can validate content from a developer shell, a pre-commit hook, or CI without standing up Evennia.

## Currently shipped

### `wb-validate`

Runs the full pre-Builder pipeline against a content repo and prints any validator messages.

```
wb-validate --reader=local --root=./world
```

**Behaviour:**

- **Clean run** — exit 0; prints reader summary, entity count, and the `VALIDATOR:` proof-of-life line.
- **Validator refusal** — exit 1; prints every finding the validator gathered, plus a "refusing — N validation errors" line on stderr. The Validator gathers findings across all entities before raising, so the operator sees the complete list in one run.
- **Pipeline error** (reader, definitions, finder, loader) — exit 1; prints a single typed error line on stderr. These are infrastructure failures, not content failures, so the validator never gets to run.

## Reader dispatch

`--reader=<name>` selects a Reader class; reader-specific kwargs come from sibling flags. Currently supported:

| `--reader` | Required flags | Maps to |
|---|---|---|
| `local` | `--root` | `LocalReader(root=...)` |

Adding a future reader (e.g. `--reader=github --repo=... --ref=... --pat=...`) means extending the parser's `choices`, adding the reader-specific flags, and a branch in `_build_reader()`. No other CLI surface changes.

## Decisions

- **Console-script entry point.** Registered via `[project.scripts]` in `pyproject.toml`. Cross-platform: setuptools generates a `.exe` launcher on Windows and an executable Python script on Linux/macOS, both pointing at the same entry function.
- **`validate(argv)` is the real entry, `_validate_main()` is the shim.** Keeps the function testable without subprocess; the shim forwards `sys.argv[1:]` and propagates the return code via `sys.exit()`.
- **Pipeline errors and validator errors take different exit paths.** Both exit 1, but pipeline errors (no `definitions.yaml`, malformed YAML, etc.) print one typed error and stop; validator errors print every finding so operators see the full picture. Different failure modes deserve different output shapes.
- **Validator findings to stdout; halt summary to stderr.** Findings are the operator's signal; they belong in the same stream as a clean run's report. The "refusing — N errors" line is a meta-message about how the run terminated; stderr keeps it from polluting piped/captured output.
- **No Evennia import on the CLI path.** `wb-validate` does not run inside an Evennia process and does not need Django settings or typeclass machinery. The Validator's checks are intentionally Evennia-free; only the Builder touches Evennia. Keeps the CLI fast to start and usable in CI.

## Future variants

The same `--reader=...` dispatch will host `wb-build` (apply against a real Evennia DB) and any future read-only inspection tools. The `validate()` shape — parse args, build reader, run pipeline, print messages, exit — is reusable for each.

## See also

- [validator.md](validator.md) — the checks the CLI surfaces.
- [reader-api.md](reader-api.md) — Reader contract; the same readers serve in-game and standalone.
- [library-commands.md](library-commands.md) — the in-game counterpart (`wb_build`).
