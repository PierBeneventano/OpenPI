# Stage 1 Artifact And Read-Model Audit

This audit records the existing artifact contracts and fixture candidates that
future product layers must preserve while replacing artifact storage, indexing,
and dashboard read models.

Status: drafted on 2026-05-10. Pending user review and real workspace fixture
selection.

## Rule

Raw historical artifacts are source evidence. A production artifact layer may
index, copy, cache, summarize, and present them differently, but it must not
silently change their meaning or mutate historical generated papers in place.

Human feedback may create a new steering, iteration, or revision request that
routes work back into an earlier approved pipeline stage. That new attempt must
link back to the source artifacts it revises.

## Current Local Evidence

The local checkout contains artifact contracts and campaign specifications, but
it does not contain real completed run workspaces under `results/`.

Observed local `results/` contents:

- `results/.gitkeep`

Therefore, the checked-in repository can support Stage 1 contract design, but it
cannot fully validate a production importer until real workspace fixtures are
added or made available.

## Source Contracts To Preserve

The canonical format reference is `docs/data_formats.md`. Production readers
must preserve the semantics of at least these files:

- `experiment_metadata.json`: run provenance, environment, selected model, task
  preview, and CLI/runtime arguments.
- `run_summary.json`: completion summary, stage list, total cost, token usage,
  final paper pointer, and workspace path.
- `budget_state.json`: latest cumulative budget state.
- `budget_ledger.jsonl`: append-only per-call spend ledger.
- `agent_llm_calls.jsonl`: append-only LLM call log.
- `STATUS.txt`: terminal completion/incompletion/error status.
- `campaign_status.json`: campaign-level stage status, workspace, process,
  SLURM, log, timing, missing-artifact, and failure state.
- `paper_workspace/track_decomposition.json`: task decomposition into theory
  and empirical tracks.
- `paper_workspace/followup_decision.json`: accept/revise/reject decision after
  review.
- `paper_workspace/review_verdict.json`: reviewer scores, verdict, weaknesses,
  and actionable feedback.
- `math_workspace/claim_graph.json`: mathematical claim graph, node statuses,
  and dependency edges.

Additional artifact semantics protected by Stage 0:

- `effective_models.json`
- `run_token_usage.json`
- `paper_workspace/final_paper.pdf`
- `paper_workspace/final_paper.tex`
- `paper_workspace/final_paper.md`
- `paper_workspace/paper_contract.json`
- campaign stage workspaces
- campaign memory and stage summaries

## Existing Read/Status Behavior

Current campaign status behavior is defined by `consortium/campaign/status.py`
and `scripts/campaign_cli.py`. These files are protected execution semantics
for Stage 0 purposes, but they are also important read-model evidence.

Important fields currently exposed by `campaign_status.json` and
`campaign_cli.py status`:

- campaign name
- campaign directory
- status timestamp
- `is_complete`
- `has_failure`
- per-stage status
- per-stage workspace
- PID and SLURM job id
- attempt id
- stdout/stderr log paths
- liveness summary and liveness detail
- artifact completeness
- missing required artifacts
- started/completed timestamps
- fail reason
- repair attempt count
- campaign budget summary

Important per-stage artifact behavior:

- Required artifacts are listed in campaign YAML under
  `success_artifacts.required`.
- Optional artifacts are listed under `success_artifacts.optional`.
- A required artifact may be a file or a directory when the path ends with `/`.
- Completion is based on required artifact presence plus current campaign
  status behavior.
- Some campaign YAMLs define validators such as minimum file size, required
  strings, forbidden strings, required JSON keys, numeric thresholds, and list
  length limits. Production readers must preserve these validator definitions
  as audit evidence even if validation execution remains delegated to the
  current kernel.

Important liveness evidence:

- PID checks are local-node only.
- SLURM job checks are cross-node when a job id is available.
- Log recency and workspace mtime are fallback signals.
- `.progress_heartbeat` and `budget_ledger.jsonl` are activity hints.

## Fixture Candidates Already In Git

These are useful for parser and contract tests, but they are not a substitute
for real completed/failed/stalled run fixtures.

### Maintained Sample Fixtures

- `examples/quickstart/campaign.yaml`
  - Minimal dynamic-planning campaign example.
  - Useful for campaign spec parsing, setup/tutorial flows, and dashboard empty
    campaign states.
- `examples/quickstart/task.txt`
  - Maintained task fixture for setup and campaign examples.
- `examples/quickstart/expected_outputs/final_paper_sample.md`
  - Sample final-paper shape for UI preview tests.
  - Not scientific regression evidence.

### Current Campaign Specs

- `campaign_template.yaml`
  - Production-facing template for new dynamic campaigns.
  - Useful for setup wizard defaults and documentation validation.
- `campaign_muon_v5_iterate.yaml`
  - Current iterate campaign with strict paper artifacts, editorial artifacts,
    PDF requirement, review-score threshold, revision changelog expectations,
    and artifact validators.
  - Useful for read-model coverage of single-stage iteration and revision
    semantics.
- `campaign_muon_v5_iterate_rigorous.yaml`
  - Current rigorous replay campaign with counsel enabled, rate-limit
    environment overrides, resume behavior, and the same strict artifact
    contract.
  - Useful for read-model coverage of environment metadata and resumed
    high-rigor iteration.
- `engaging_config.yaml`
  - Engaging/HPC environment configuration fixture for setup and deployment
    documentation.

### Archived Campaign Specs And Manifests

- `archive/muon_campaign_v5_20260318_194213/campaign_v5.yaml`
  - Archived dynamic-planning campaign with planning, repair, SLURM launcher,
    Telegram, ntfy, and human plan review.
- `archive/muon_campaign_v5_20260318_194213/manifest.json`
  - Records a completed six-stage campaign summary:
    `discovery_plan`, `planning_counsel`, `theory1`, `theory2`,
    `experiment1`, and `paper1`.
  - The manifest references an archived results directory that is not present
    in this checkout.
- `archive/muon_campaign_v5_20260318_194213/automation_tasks/*.txt`
  - Legacy task files for stage-context and campaign import tests.
- `archive/muon_v6_test_20260318_194341/campaign_muon_v6_test.yaml`
  - Archived test campaign with dynamic planning and auto-approved planning.
- `archive/muon_v6_test_20260318_194341/manifest.json`
  - Records pending `discovery_plan` and `planning_counsel` stages.
- `archive/v1_20260318/manifest.json`
- `archive/v2_20260318/manifest.json`
- `archive/v3_20260318/manifest.json`
- `archive/v4_20260318/manifest.json`
  - Deprecated campaign migration evidence.
  - Useful for importer compatibility around legacy campaign references.

## Missing Canonical Regression Fixtures

Before Stage 2 can fully validate an importer, the project should preserve at
least one fixture for each class below. These can live outside git if they are
large, but their location and expected checksums should be documented.

Required fixture classes:

- Completed single-run workspace with a final paper and `STATUS.txt` marked
  complete.
- Failed single-run workspace with error status and partial artifacts.
- Active or stalled workspace with liveness evidence such as
  `.progress_heartbeat`, log activity, or stale workspace writes.
- Completed campaign workspace with `campaign_status.json`, stage workspaces,
  logs, and budget files.
- Failed or repairing campaign workspace with missing artifacts, fail reason,
  and repair log if available.
- Iteration/revision workspace that links prior paper artifacts to feedback and
  a routed revision attempt.
- Budget-rich workspace with `budget_state.json`, `budget_ledger.jsonl`, and
  token usage logs.
- Math-enabled workspace containing `math_workspace/claim_graph.json`.
- Paper workspace containing strict paper contract files, review verdict,
  persona verdicts, revision plan, revision changelog, and final PDF/TEX.

Recommended naming for fixture classes:

- `completed_single_run`
- `failed_single_run`
- `stalled_single_run`
- `completed_campaign`
- `failed_or_repairing_campaign`
- `iterate_revision`
- `budget_rich_run`
- `math_enabled_run`
- `strict_paper_workspace`

## Read-Model Requirements For Product V1

The VS Code v1 dashboard is read-only. It should derive its state from raw
artifacts and compatibility importers, not by importing or modifying protected
research-kernel behavior.

### Run Read Model

Minimum fields:

- run id
- run path
- run kind: single run, campaign stage, iterate/revision, or unknown
- task preview or task file pointer
- status: pending, planning, in progress, repairing, completed, failed,
  incomplete, stalled, unknown
- current or completed stage list
- start/update/end timestamps when known
- elapsed time and staleness
- provenance: git commit, dirty flag, platform, Python version
- selected model and effective model manifest when available
- budget limit, total spend, by-model spend, and latest budget update
- token totals when available
- final paper artifact pointers
- paper contract artifact pointers
- review verdict and follow-up decision pointers
- math claim graph pointer when present
- log pointers
- liveness signals
- failure reason and missing artifacts
- parent/source artifact links for feedback-driven revision attempts

### Campaign Read Model

Minimum fields:

- campaign id/name
- campaign path
- campaign spec path
- workspace root
- planning configuration summary
- repair configuration summary
- notification configuration summary without leaking secrets
- stage DAG: stage id, dependencies, context sources, and launcher type
- per-stage status
- per-stage workspace
- per-stage attempt id, PID, and SLURM job id
- per-stage stdout/stderr log paths
- per-stage artifact completeness and missing artifacts
- per-stage start/completion timestamps
- per-stage liveness summary
- per-stage repair attempts
- campaign-level budget summary
- plan approval/rejection state when dynamic planning is active
- generated campaign plan pointer when available

### Artifact Read Model

Minimum fields:

- artifact id
- source path
- owning run/campaign/stage
- artifact kind: status, budget, log, paper, review, math, plan, memory,
  config, metadata, unknown
- media/type hint
- size and modified timestamp
- immutable source flag
- derived/indexed flag
- required/optional relationship if declared by a campaign spec
- validator definitions if declared
- validation result if known
- source attempt and parent artifact links for revision workflows

### Event Read Model

Minimum fields:

- event id
- timestamp
- actor: system, user, orchestrator, OpenClaude, OpenClaw, future Slack, or
  unknown
- event type: observed, launched, completed, failed, repaired, approved,
  rejected, budget alert, artifact indexed, feedback submitted, revision
  requested
- target: run, campaign, stage, artifact, or budget
- source command or file when known
- mutation flag
- confirmation id for mutation events
- summary

## Artifact Immutability And Feedback Routing

Production storage should distinguish three categories:

- Raw source artifact: generated by the research kernel or historical run;
  immutable for audit.
- Derived read model: generated by importers/indexers; can be deleted and
  rebuilt.
- Human steering artifact: user feedback, plan approval, revision request, or
  orchestration instruction; append-only and linked to the source evidence it
  comments on.

When a human decides that a paper should be steered toward a better result, the
product should create a new revision/iteration attempt. It should not overwrite
the original `final_paper.*`, `review_verdict.json`, or related source
artifacts.

## Stage 1 Validation Plan

Validation that can run with the current checkout:

- Parse all maintained and archived campaign YAML files.
- Parse archive manifests.
- Confirm required/optional artifact declarations can be extracted from current
  campaign specs.
- Confirm the read-model requirements cover fields exposed by
  `scripts/campaign_cli.py status`, `stage-logs`, `stage-artifacts`, `budget`,
  and `launchable`.
- Confirm no runtime code, prompts, graph logic, artifact behavior, or campaign
  behavior changes are made.

Validation blocked until real fixtures are available:

- Import completed, failed, active/stalled, and repairing workspaces.
- Compare imported status to existing `campaign_status.json`, `STATUS.txt`, and
  `run_summary.json`.
- Compare imported budget totals to `budget_state.json`, `budget_ledger.jsonl`,
  and private token usage logs when available.
- Validate paper, review, revision, and math artifact discovery against real
  workspaces.
- Snapshot importer outputs.

## User Evaluation Checkpoint

Before Stage 2 implementation/design approval, the user should evaluate:

- whether the missing fixture classes match the real runs they care about
- which completed campaign or run should become the gold regression fixture
- whether sample/archived specs are enough to begin Stage 2 contract design
  while real fixtures are gathered
- whether the read-model fields are sufficient for the VS Code graph tab,
  artifact browser, log viewer, budget panel, and OpenClaude skill context
- whether any artifacts should be upgraded to approval-required semantics

## Exit Criteria

Stage 1 can be considered approved when:

- this audit is reviewed by the user
- the user identifies where real workspace fixtures will come from, or accepts
  a temporary contract-first Stage 2 with fixture validation deferred
- read-model requirements are accepted or revised
- missing fixture risks are acknowledged in the production lift log

## Current Risks

- The repo contains no real `results/` workspaces, so importer compatibility
  cannot yet be proven.
- Some archived manifests reference result directories that are not present in
  this checkout.
- Campaign modules mix read/status behavior with protected execution semantics,
  so production readers should derive data from files and public commands first
  rather than refactoring protected modules.
- Strict paper artifact validators in current iterate campaigns encode
  scientific-quality expectations. They must be preserved as evidence even if
  future storage changes make their location cleaner.
