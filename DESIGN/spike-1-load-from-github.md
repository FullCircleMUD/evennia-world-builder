# Spike 1 — Load YAML from a Private GitHub Repo

The first proof-of-concept for world-builder. Validates that an Evennia superuser command can authenticate against a private GitHub repository, fetch a YAML file into memory, and parse it via `yaml.safe_load`. Everything else — schema, validation, DB writes, idempotency — is deliberately deferred to subsequent spikes.

## Why this spike first

The riskiest external integration is private-GitHub-to-running-Evennia. Authentication, network access from inside Twisted's reactor, and where credentials live at runtime are unknowns that can surprise us in ways pure Python data manipulation cannot. Everything downstream — parsing, validating, creating Evennia objects — is well-trodden territory. Validating the integration first lets every subsequent spike build on a proven foundation.

## What the spike validates

- Authentication mechanism for private GitHub access from inside an Evennia process.
- Choice of Python fetch library works in this environment.
- Where credentials live at runtime in the local development setup.
- PyYAML works cleanly in the Evennia env (parse step).

## In scope

- Fetching a YAML file from a private GitHub repo using a PAT.
- Storing the PAT and repo URL in the demo game's `secret_settings.py`.
- Returning the fetched bytes.
- Parsing the bytes via `yaml.safe_load` to a Python dict.
- Surfacing both forms so we can verify each works.
- Running as a superuser command in a demo Evennia gamedir locally.
- Synchronous execution.

## Out of scope

- A validator seam — deferred to a later spike.
- `deferToThread` / async-safe execution — production requirement, not a spike concern.
- Schema validation, schema design.
- DB writes / Evennia object creation.
- Identity, lookup, plan/execute split, extensibility — all deferred.
- The library/consumer boundary for fetch+auth — no library code at this stage; the spike is purely consumer-side.
- Multi-file discovery / indexing convention for the content repo — deferred.

## Concrete decisions

| Decision | Value |
|---|---|
| Auth mechanism | Personal Access Token (PAT) |
| PAT location | `secret_settings.py` (local); env var `WORLDBUILDER_GITHUB_PAT` (production) |
| Repo coords (REPO/REF/PATH) | `settings.py` via `os.environ.get(NAME, default)`; env var or `secret_settings.py` overrides |
| Fetch library | trial-and-error; start lightest (`urllib` → `requests` → `PyGithub` if needed) |
| Test repo | `FCM/libraries/world-builder-test-yaml/` (private) |
| Spike runs in | a demo gamedir under `world-builder/examples/` |
| Async / reactor | sync is fine for the spike |
| Success signal | YAML content fetched into memory; `yaml.safe_load` succeeds; both raw bytes and parsed dict are observable |

## Captured for later, not part of this spike

- **Indexing convention for the content repo.** When the loader needs to do partial-scope deploys (one district, one zone), it needs to discover what YAML files exist and select the right ones — possibly an `index.yaml` manifest, or directory conventions, or both. [TBD — needs discussion when a subsequent spike requires partial-scope selection.]
- **Library/consumer boundary for fetch+auth.** Once the spike succeeds, we need to decide whether the library exposes a `fetch_from_url` primitive (library knows about HTTP/GitHub) or only an `apply_yaml_text` primitive (consumer fetches; library accepts content). [TBD — needs discussion before extracting library code from the spike.]
- **`deferToThread` for production.** Synchronous network calls block the Twisted reactor; production code must offload network calls via `deferToThread` matching FCM's existing pattern. Out of scope for the spike, but a hard requirement before any production-like usage.
- **Production credential storage.** Environment variables for production deployment; the spike uses `secret_settings.py` for local development only. [TBD — needs design when production deployment begins.]
