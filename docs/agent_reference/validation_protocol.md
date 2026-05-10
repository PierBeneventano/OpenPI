# Implementation Validation Protocol

This document defines the recurring validation gate to run after production-lift
implementation stages.

Status: updated on 2026-05-10 after local Python environment installation,
dry-run validation, contract-test validation, and cheap-smoke harness creation.

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
validation gates, and it has an opt-in paid cheap-smoke harness for full pipeline
canary runs.

## Purpose

After each implementation stage, run the same validation gate to ensure product
work has not changed the protected research behavior or degraded artifact/read
quality.

This protocol has three layers:

- no-cost structural validation
- no-cost dry-run validation
- paid cheap-model smoke run with explicit user approval

The cheap smoke run is a canary, not a replacement for saved max/ultra
regression fixtures.

## Canonical Canary Task

Use the maintained quickstart task:

```bash
examples/quickstart/task.txt
```

This keeps the task stable across stages and avoids changing evaluation inputs.

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

## Paid Cheap Smoke Run

Run a full pipeline canary with cheap model surfaces only. This is the recurring
performance/behavior smoke test to use after implementation stages that affect
launching, orchestration, event capture, artifact import, run status, or model
policy plumbing.

Do not run it automatically. It requires an explicit environment flag because it
can spend provider budget and can take minutes.

```bash
MSC_PAID_SMOKE_APPROVED=1 scripts/validation/cheap_smoke_test.sh
```

Default cheap smoke policy:

- task: `examples/quickstart/task.txt`
- main model: `gpt-5-mini`
- deep literature search model: `openrouter/perplexity/sonar-pro`
- budget cap: `$5`
- timeout: `900` seconds
- counsel: off
- math agents: off
- tree search: off
- output: Markdown

Overrides:

```bash
MSC_SMOKE_BUDGET_USD=2 \
MSC_SMOKE_TIMEOUT_SECONDS=600 \
MSC_SMOKE_MODEL=gpt-5-mini \
MSC_SMOKE_DEEP_RESEARCH_MODEL=openrouter/perplexity/sonar-pro \
MSC_PAID_SMOKE_APPROVED=1 \
scripts/validation/cheap_smoke_test.sh
```

Purpose:

- prove launch path still works
- prove result workspace is created
- prove artifact/read-model importers can inspect a fresh run
- produce a cheap canary artifact set for UI/CLI validation
- evaluate whether each implementation stage preserved the ability to produce a
  coherent artifact, within the limits of cheap-model quality

Output:

- run workspace under `results/consortium_<timestamp>/`
- run log under `logs/validation/cheap_smoke_<timestamp>.log`
- heuristic report under `logs/validation/cheap_smoke_<timestamp>.json`

The wrapper runs `scripts/validation/analyze_smoke_workspace.py` after launch.
The analyzer checks:

- run status and summary artifacts
- experiment metadata
- effective model manifest
- budget state and budget ledger
- paper workspace presence
- final paper presence
- cheap model-surface enforcement
- placeholder strings in generated paper text
- rough section, body-length, and citation-marker heuristics

This is not a scientific-quality acceptance test.

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

Run when launch/harness behavior changes:

- paid cheap smoke run, with explicit approval.
- import the produced workspace.
- compare graph/status/artifact/budget read models against expectations.

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

For any real or cheap-smoke workspace, inspect:

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
- Add trend comparison across cheap-smoke reports, including stage-by-stage
  timing, cost, artifact completeness, and heuristic drift.
