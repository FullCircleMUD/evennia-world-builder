# Validation Gating

How evennia-world-builder decides whether a `wb_build` invocation pre-validates the whole repo, or trusts external CI gating to have done the job already. The library never *verifies* gating; it relies on the consumer's assertion and provides clear cost/safety trade-offs for getting it wrong.

## The problem

Cross-reference validation requires the whole repo's `seen_ids` index. For a 10-room MUD that's free; for a 10,000-entity world that's a multi-second-to-multi-minute walk over GitHub or filesystem on every `wb_build`, including single-room redeploys. Always pre-validating is a real operational tax for large worlds; never pre-validating means broken cross-refs can sneak through.

The right answer depends on whether the *content repo* has external pre-deploy validation gating (e.g. GitHub PR + branch protection + required `wb-validate` status check). If yes, `wb_build` can trust the gate and skip the whole-repo walk. If no, `wb_build` must pre-validate every time.

## What we don't do (and why)

We considered, and rejected, three alternatives:

- **Library inspects branch-protection rules** (e.g. via GitHub API): requires admin-scoped tokens consumers don't always have, doesn't generalise across platforms (GitLab/Gitea/etc.), proves *configuration exists* not that *the configuration validates anything*.
- **Hash the repo contents and store the hash as a "blessing"**: the verifier needs all the contents to recompute, defeating the purpose. Per-file salted hashes don't catch cross-file changes (the most important class of break). Git commit hashes only work for git-backed readers.
- **Reader class-attribute `gated: bool`**: dishonest — `GitHubReader.gated = True` would be a claim about *platform capability*, not about whether the consumer *actually configured* CI on their repo. Gives false security to anyone who skipped the CI setup step.

The deeper reason all three fail: "validated" is not a property of the data, it's a claim about *work that was done* against a snapshot. That claim has to live somewhere outside the data, in the system that did the work and remembers what it did. **CI is exactly that system.** Branch protection + required status checks are the gate; the git commit on `main` is the blessing token; the deployment infrastructure trusts only blessed refs.

The library's role is to be invokable from the system that gates (provide `wb-validate`), and to default-safe when the consumer hasn't claimed otherwise.

## The model

One consumer-side assertion in `definitions.yaml`:

```yaml
# DO NOT flip this without setting up CI pre-validation on your content repo.
# (See the comment block in the template for full warning text.)
repo-ci-pre-validation: false
```

One ad-hoc per-invocation flag for `wb_build`:

```
wb_build zone=millholm file=bakery --force-validate
```

The flag's intent is simple: **validate this run regardless of any other settings.**

### Decision matrix

| `repo-ci-pre-validation` | `--force-validate` | `wb_build` behaviour |
|---|---|---|
| `false` (default) | absent | Pre-validate whole repo, then scoped build. |
| `false` | present | Pre-validate whole repo, then scoped build. (Flag is redundant but harmless.) |
| `true` | absent | Skip pre-validation, scoped build only. Trusts the CI gate. |
| `true` | present | **Pre-validate whole repo anyway.** Flag overrides setting. |

The setting is the consumer's persistent claim about their pipeline. The flag is an ad-hoc paranoid override ("CI flaked yesterday; I want to be sure for this deploy").

### Why default false

A library that defaults to `repo-ci-pre-validation: true` would be giving consumers a false sense of security: "the library is doing the safe thing" when in fact it's trusting an unverified setup. Defaulting to `false` makes consumers explicitly opt in *after* setting up CI. The cost (slow whole-repo walks) is the natural pressure that motivates them to actually set up the CI gate.

The cost scales with the world: small MUDs pay nothing meaningful, large MUDs feel real pain. The pain *is* the incentive — and it's an honest one.

## Three-tier cross-reference correctness

Even when `wb_build` skips pre-validation, there are still three layers that catch broken cross-refs:

1. **CI gate (primary)** — `wb-validate` runs against the whole repo on every PR. Branch protection blocks merge on failure. Cross-refs are validated *before* anything reaches the deploy branch.
2. **Builder DB-lookup (build-time, post-Validator)** — when the Builder creates an exit (or any cross-ref structure), it looks up the target by `(deployment_file, deployment_id)` tags **in the Evennia DB**, not in the YAML. Refuses with a clear error if the target hasn't been deployed yet. Catches deploy-order errors that whole-repo YAML validation can't see ("you're trying to wire an exit to a file that exists in YAML but hasn't been built into the DB yet").
3. **Inline scope-bounded validator (always)** — `deployment_id` well-formed, per-file uniqueness, and any other per-entity / per-file checks always run on the loaded scope. Free, doesn't need the whole repo.

Tier 1 is the operational gate. Tier 2 is the deploy-time integrity check. Tier 3 is the always-on baseline. Together they cover the failure modes; none of them require the library to inspect or verify CI configuration.

## CI workflow shape (consumer-side)

The consumer adds a workflow to their *content* repo. Example for GitHub:

```yaml
# .github/workflows/validate.yml
name: Validate world content
on:
  pull_request:
    paths: ['**/*.yaml', '**/*.yml']
jobs:
  wb-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install evennia-world-builder
      - run: wb-validate --reader=local --root=.
```

Then in the repo's branch-protection settings: require pull request before merging, require status checks to pass, select `wb-validate`. After that, `main` is structurally incapable of receiving content that didn't pass validation. The consumer flips `repo-ci-pre-validation: true` in `definitions.yaml` and `wb_build` becomes fast.

## What the library does NOT promise

- Does not inspect the consumer's CI setup or branch-protection rules.
- Does not verify the assertion in `definitions.yaml` matches reality.
- Does not prevent a consumer from lying to themselves (flipping the flag without setting up CI).

What it does instead: **default to safe**, **make the cost of unsafe-but-fast obvious** (the assertion sits next to a long warning comment), and **provide `wb-validate` as the tool the consumer's CI runs**. The chain of trust is the consumer's deploy infrastructure, not the library.
