# Implementation Validation Protocol

This document defines the recurring validation gate to run after production-lift
implementation stages.

Status: revised on 2026-05-10 after user review of the local smoke strategy.
Full paid pipeline smoke tests are deferred until explicit Engaging integration
testing. Local product-shell stages should use no-cost, fixture-backed
validation only.

## Current Local Readiness Check

Checked on 2026-05-10:

- `OPENROUTER_API_KEY` is not present in the shell environment.
- An OpenRouter key was stored in `/Users/ttcc/Desktop/Msc/.msc/.env` with
  owner-only file permissions.
- The stored OpenRouter key was verified with OpenRouter's key-info endpoint.
- `~/.msc/.env` is absent.
- repo-root `.env` is absent.
- `python3` exists but is Python 3.9.6, below the package requirement of
  Python >= 3.10.
- `.venv/bin/python` exists in this checkout and is Python 3.10.20.
- `.venv/bin/msc --help` succeeds.
- `scripts/validation/contract_tests.sh` succeeds.
- `scripts/validation/cheap_dry_run.sh` succeeds.

Therefore, this checkout is ready for build-and-test iteration using no-cost
validation gates. It is not considered ready for recurring full-pipeline smoke
testing because the intended integration target is Engaging and local runs may
generate or exercise cluster-oriented artifacts in ways that are not useful for
product-shell validation.

## Purpose

After each implementation stage, run the same local validation gate to ensure
product work has not changed protected files, broken the SDK/CLI surface, or
degraded artifact/read-model quality.

The local gate has these layers:

- no-cost structural validation
- no-cost dry-run validation
- fixture-backed SDK/CLI/importer/dashboard validation
- protected-file and redaction checks

Full paid pipeline smoke tests are not part of the local recurring stage gate.
They should be reintroduced only as a separately approved Engaging integration
test once the user is ready to validate launch behavior on the intended cluster
environment.

## Deferred Full-Pipeline Canary Task

Keep the maintained cheap smoke task available for future Engaging integration
testing:

```bash
scripts/validation/tasks/cheap_smoke_task.txt
```

This task is no longer part of the local after-every-stage gate. It remains a
stable future canary input because it avoids external literature search and
arXiv metadata availability. When integration testing resumes on Engaging, use
this task only with an explicit integration-test plan, budget approval, and a
no-submit/no-surprise guard appropriate to the environment.

## Key And Environment Checks

Check for the key without printing it:

```bash
if [ -n "$OPENROUTER_API_KEY" ]; then
  echo "OPENROUTER_API_KEY present in shell"
else
  echo "OPENROUTER_API_KEY absent from shell"
fi
```

Check user config without printing secrets:

```bash
test -f "$HOME/.msc/.env" && rg -q '^OPENROUTER_API_KEY=' "$HOME/.msc/.env"
```

Expected setup before running:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
msc setup
msc doctor
```

The dry-run validates key presence but should not make a paid research run.

Helper script:

```bash
scripts/validation/cheap_dry_run.sh
```

The script defaults to `../.msc/.env` relative to the repo root and can be
overridden with `MSC_CONFIG_DIR`. It restores `.llm_config.yaml` after the run
because the preserved CLI writes the selected tier/budget into that local config
file as part of normal launch preparation.

## Structural Contract Tests

Run:

```bash
scripts/validation/contract_tests.sh
```

Purpose:

- keep the current CLI contract covered while the product shell is lifted
- isolate tests from the real OpenRouter config by using a temporary `HOME`
- avoid accidental dependence on `/Users/ttcc/Desktop/Msc/.msc/.env`

Current expected result:

```text
17 passed
```

## No-Cost Dry Run

Canonical command:

```bash
msc run \
  --tier budget \
  --model gpt-5-mini \
  --budget 1 \
  --output-format markdown \
  --no-counsel \
  --no-math \
  --no-tree-search \
  --dry-run \
  --task-file examples/quickstart/task.txt
```

Expected behavior:

- validates setup
- resolves budget tier
- resolves cheap model policy
- generates or validates the tier-derived config
- exits before launching the research pipeline
- does not spend provider budget

Expected cheap model policy:

- model: `gpt-5-mini`
- counsel: off
- math agents: off
- tree search: off
- output: markdown

## Deferred Paid Full Smoke Run

Do not run full paid smoke tests during the local product-shell lift.

The existing helper remains in the repository for later integration work:

```bash
MSC_PAID_SMOKE_APPROVED=1 scripts/validation/cheap_smoke_test.sh
```

Current status:

- deferred until Engaging integration testing
- not required after local Stage 1-5 product-shell implementation stages
- not evidence of scientific output quality
- not a replacement for saved max/ultra reference fixtures
- must not be used to justify changes to protected research-kernel behavior

Reason:

- The local development environment is not the intended Engaging launch
  environment.
- The preserved pipeline may generate Engaging/SLURM-oriented scripts or
  artifacts that are not meaningful local product-shell validation.
- A full smoke run can spend provider budget and take a long time.
- Earlier cheap smoke attempts produced useful metadata canaries but did not
  produce reliable quality evidence.
- Product-shell work before Stage 6 can be validated more cleanly with
  fixtures, contract tests, JSON schema tests, dry-runs, and redaction checks.

When the user is ready for Engaging integration testing, create a separate
integration validation plan that states:

- where the test runs: Engaging, local, or hosted
- whether SLURM submission is expected or forbidden
- exact model, budget, timeout, and task
- expected artifacts and status
- how generated workspaces will be preserved or ignored
- stop conditions for provider, cluster, or quality regressions

Until that plan exists, do not run `cheap_smoke_test.sh` as a stage gate.

## Historical Smoke Analyzer

`scripts/validation/analyze_smoke_workspace.py` remains useful for inspecting
historical or future smoke workspaces. It can report:

- run status and summary artifacts
- experiment metadata
- effective model manifest
- budget state and budget ledger
- paper workspace presence
- final paper presence
- cheap model-surface enforcement
- placeholder strings in generated paper text
- rough section and body-length heuristics
- optional citation-marker heuristics when explicitly enabled

The analyzer is not a scientific-quality acceptance test. It should be treated
as a workspace-shape diagnostic, not as proof that generated research output is
good.

## Stage Gate Checklist

Run after every implementation stage:

- `git diff` confirms protected research-kernel files were not touched unless
  explicitly approved.
- `scripts/validation/contract_tests.sh` passes.
- no-cost dry-run passes.
- SDK/CLI self-tests pass once implemented.
- artifact importer snapshots pass against checked-in fixtures.
- CLI JSON schemas remain stable.
- confirmation-gated mutation commands refuse to execute without confirmation.
- redaction tests confirm no API keys or notification tokens are emitted.
- read models still parse existing fixture specs and manifests.

Do not run as a local stage gate:

- paid full pipeline smoke
- local SLURM/Engaging submission simulations
- max/ultra quality reruns

Run later, under a separate Engaging integration plan:

- paid full pipeline smoke
- optional real launch/submission validation
- import the produced workspace
- compare graph/status/artifact/budget read models against expectations

## Kernel Invariant Heuristics

These checks guard output quality by making sure product work did not alter the
pipeline that produces high-quality outputs.

Heuristics:

- protected files unchanged unless approved
- prompt files unchanged unless approved
- LangGraph stage roster/order unchanged unless approved
- router/gate/retry/validator code unchanged unless approved
- model policy unchanged unless approved
- budget enforcement semantics unchanged unless approved
- checkpoint/state semantics unchanged unless approved
- artifact completion semantics unchanged unless approved

These are stronger guarantees than judging a cheap-model output alone.

## Artifact Quality Heuristics

For any preserved real workspace or future approved integration-smoke
workspace, inspect:

- `STATUS.txt` or `run_status.json`
- `experiment_metadata.json`
- `effective_models.json`
- `budget_state.json`
- `budget_ledger.jsonl`
- `run_summary.json`
- `paper_workspace/`
- final paper artifact when present
- review and feedback artifacts when present

Heuristics:

- required files exist for the selected mode
- no obvious placeholder strings such as `TODO`, `Research Paper Title`, or
  `Author Names` in final paper artifacts
- paper has recognizable sections when a paper is expected
- review verdict JSON has expected keys when review runs
- budget files parse and reconcile roughly with summary values
- effective model manifest reports the intended tier/model
- generated artifacts are treated as immutable source evidence
- derived manifests can be deleted and rebuilt

## Node And Graph Heuristics

For graph/read-model validation:

- all expected nodes appear
- dependencies match campaign spec or preserved graph contract
- status mapping is stable
- missing artifacts are reported rather than hidden
- liveness is evidence-backed and marks inferred states as inferred
- failures and repair states remain visible
- revision attempts link back to prior source artifacts

## Acceptance Interpretation

Passing the dry-run and heuristics means the product shell is likely still
wrapping the same research behavior.

It does not prove max/ultra scientific quality. That still requires preserved
fixtures, output audits, or a deliberate expensive reference rerun approved by
the user.

## Open Work

- Add real completed/failed/stalled workspace fixtures.
- Add SDK/CLI `selftest` commands from Stage 3.
- Add artifact importer snapshots from Stage 2.
- Add protected-file checksum checks.
- Add trend comparison across future Engaging integration-smoke reports,
  including stage-by-stage timing, cost, artifact completeness, and heuristic
  drift.
