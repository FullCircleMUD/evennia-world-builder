# Logging

How the library emits durable log output, separate from operator-facing messaging and from Evennia's generic `server.log`.

The library writes its own log file, `world-builder.log`, co-located with Evennia's standard logs (the `LOG_DIR` configured by the consumer's gamedir). All library code that needs to record a durable event routes through a single helper, `wb_log`, which wraps Evennia's built-in `evennia.utils.logger.log_file()`. The standalone CLI (`wb-validate`) runs without an Evennia engine bootstrapped, so the helper is a silent no-op in that context; CLI output continues to flow through `print` to stdout/stderr as before.

## Why a dedicated log file

The library has two distinct output channels that should not be confused:

1. **Operator-facing messages.** `wb_build` collects strings into a `messages` list on a Twisted worker thread and flushes them via `caller.msg()` on the reactor thread when the async pipeline returns. `wb-validate` prints to stdout/stderr. These are *ephemeral, addressed to the human triggering the build* — they belong on the terminal/in-game window, not in a log file.
2. **Durable forensic records.** Unexpected exceptions, build lifecycle events, validator refusals, builder cleanup counts. These need to survive the operator session: they are read later, by the same operator or a different one, to answer "what happened during yesterday's build" or "why did the 02:14 build refuse." A log file is the right surface.

Without a dedicated file, the only durable record is whatever Twisted dumps into `server.log` when an exception escapes `_on_async_err` — which is a thin slice of what's worth recording, mixed with everything else Evennia is logging. A dedicated `world-builder.log` lets an operator tail one file to follow library activity and grep its history without sifting Evennia noise.

## Why `evennia.utils.logger.log_file`

Evennia's `log_file(msg, filename="world-builder.log")` already handles every concern a custom logger would have to solve:

- Writes into `settings.LOG_DIR` — same directory as `server.log` and `portal.log` — without the library hard-coding a path.
- Thread-safe via Evennia's interruptable thread pool, so the `wb_build` worker thread (Twisted's thread pool, not the reactor) can call it without locking concerns.
- No dependency on Python's `logging` module hierarchy, so it can't be silently rerouted by a consumer's logging config.
- Already a documented Evennia surface; consumers reading `world-builder.log` find it next to logs they already know.

The library does not implement its own file rotation, level filtering, or destination dispatch. If those become real needs later, Evennia's logging surface is the place to extend, not this library.

## Filename

Hardcoded to `world-builder.log`. Not configurable.

**Why hardcoded.** A configurable filename is a footgun for very little gain: two operators tailing different files because one consumer renamed it, scripts and runbooks bit-rotting when the name drifts, and the library having to validate the consumer's choice. The library is one of many things logging into `LOG_DIR`; owning a fixed name in that namespace is a smaller surface than exposing yet another setting. If a consumer has a genuine conflict on that filename, they can rename their own file — this library got there first.

## Line format

Every line emitted by `wb_log` has the shape:

```
<ISO-8601 timestamp> [<LEVEL>] <message>
```

Example: `2026-05-13T14:22:01 [INFO] wb_build: starting validation`.

**Why a timestamp.** Evennia's `log_file` does not prepend one, and a forensic log without per-line time context is hard to correlate with other logs or with operator memory of when something happened. ISO-8601 sorts lexically and parses unambiguously.

**Why a level prefix.** Severity becomes filterable with plain `grep`, without committing the library to Python's `logging` module. Levels are deliberately small: `INFO`, `WARN`, `ERROR`. No `DEBUG` (the library has no chatty inner loops worth logging at that volume) and no `CRITICAL` (failure to apply is not a process-ending event for the consumer — the operator gets a refusal and tries again).

## CLI behaviour

When `wb-validate` runs, Evennia is not bootstrapped — `evennia.utils.logger` may not even be importable. `wb_log` detects this and becomes a silent no-op.

**Why silent and not a fallback file.** `wb-validate`'s purpose is foreground CI/pre-commit feedback; its existing `print` calls to stdout/stderr already give CI everything it needs in the build artifact. A fallback log file in the CWD would litter content repos and CI workspaces with stray files; a stderr fallback would duplicate output that already flows through `print`. Silent no-op is the smallest, least-surprising behaviour.

The detection is by `ImportError` on `evennia.utils.logger`, evaluated lazily on first call. The CLI path therefore never imports the engine, preserving `wb-validate`'s "no Evennia required" property documented in [cli.md](cli.md).

## What the library logs

The shim is in place independently of which sites call it. Wiring decisions are tracked in [progress.md](progress.md) as they ship. The candidate sites identified during design discussion:

- **Unexpected exceptions** in `wb_build`'s `_on_async_err` — full traceback to `world-builder.log` instead of letting Twisted's `Failure` dump it into `server.log`. Highest-value site: today, this is the only durable record at all, and it lands in the wrong file.
- **Pipeline lifecycle** in `_run_pipeline` — build start (with scope query), validation pass/fail, refusal reasons, build complete (with counts). Mirrors operator messages to durable storage. Useful for "what was built last night" forensics.
- **Validator refusals** — `validator.messages` and `validator.errors` on a refusal. An authoring-mistake audit trail.
- **Builder DB transitions** — cleanup counts and per-object create dbrefs. A durable record of what state the database moved through.

These are wired in deliberately, not en masse — the library does not log every internal function call. The discipline is "log what an operator would want to read later", not "log everything."

## Consumer impact

None beyond what Evennia already requires. The consumer's gamedir already has `LOG_DIR` configured (it has to, for `server.log` to work). No new setting, no new install step, no settings.py edit. `world-builder.log` appears on first emission alongside the existing logs and grows from there.

## Out of scope

- **Log rotation.** Deferred to Evennia / the operator's deployment infrastructure (logrotate, journald, etc.). The library does not own retention.
- **Structured / JSON logging.** The line format is human-readable. If a consumer wants machine-readable events later, that's an additive layer on top, not a replacement.
- **Per-call-site level configuration.** No `WORLDBUILDER_LOG_LEVEL` setting, no filtering at emit time. Every `wb_log` call lands in the file. If volume becomes a real problem, that's a signal to log less, not to filter at runtime.
- **Routing to Python's `logging` module.** The library does not register loggers under its package namespace. Consumers that want to route library events into their own logging hierarchy would need a new bridge; deliberately deferred until someone asks.
