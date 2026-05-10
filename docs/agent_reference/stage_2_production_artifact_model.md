# Stage 2 Production Artifact Model And Importer Design

This document defines the production artifact layer that can replace the old
proof-of-concept artifact organization while preserving the research kernel and
historical output semantics.

Status: drafted on 2026-05-10 as a contract-first design. Pending user review
and later validation against real workspace fixtures.

## Rule

The production artifact system is a shell around research outputs. It may
organize, index, mirror, summarize, and expose artifacts through clean domain
objects, but it must not alter the prompts, graph, stage order, validators,
model policy, budget behavior, state/checkpoint behavior, or artifact
completion semantics of the preserved kernel.

Raw generated artifacts are immutable source evidence. Derived indexes and read
models are rebuildable.

## Design Goals

- Give VS Code, OpenClaude, optional OpenClaw, and future webapp surfaces one
  stable way to read runs, campaigns, stages, artifacts, events, and budgets.
- Hide legacy filesystem quirks behind a clear read model.
- Preserve enough provenance to audit how every artifact was produced.
- Support human feedback as append-only steering/revision input, not mutation
  of historical papers.
- Allow contract-first progress while real regression fixtures are gathered.

## Non-Goals

- Do not change runtime generation paths in the research kernel.
- Do not move LangGraph logic into the artifact layer.
- Do not reinterpret success or failure beyond existing status/artifact
  contracts.
- Do not require max/ultra reruns for artifact-model validation.
- Do not make the VS Code v1 dashboard mutating.

## Storage Principle

The production layer should distinguish three storage classes:

- Source workspace: the existing or future run/campaign directory produced by
  the kernel. This remains the primary evidence.
- Production manifest: a normalized description of source artifacts,
  relationships, provenance, and known validation state.
- Derived index: UI/search/cache data generated from the manifest and source
  artifacts. It can be deleted and rebuilt.

The preferred first implementation is sidecar-first: write production manifests
outside or alongside source workspaces without moving raw artifacts. Direct
relocation can come later only after import/export compatibility is proven.

## Domain Objects

### Project

A project groups campaigns, runs, setup state, and integration configuration.

Minimum fields:

- `project_id`
- `root_path`
- `display_name`
- `created_at`
- `updated_at`
- `default_results_path`
- `environment_kind`: local, engaging_remote_ssh, hosted, unknown
- `credential_profile_ref`: reference only, never raw secret material

### Run

A run is one execution workspace produced by the research pipeline. It may be a
standalone run or the workspace for one campaign stage.

Minimum fields:

- `run_id`
- `workspace_path`
- `run_kind`: standalone, campaign_stage, iterate_revision, unknown
- `status`: pending, planning, active, stalled, repairing, completed, failed,
  partial, incomplete, unknown
- `status_reason`
- `task_preview`
- `task_file`
- `started_at`
- `last_activity_at`
- `completed_at`
- `current_stage`
- `stages_completed`
- `model`
- `effective_models_ref`
- `metadata_ref`
- `final_paper_ref`
- `budget_ref`
- `parent_run_id`
- `source_artifact_refs`
- `campaign_id`
- `campaign_stage_id`

### Campaign

A campaign is a DAG of stages driven by campaign YAML, status files, heartbeat
behavior, and stage workspaces.

Minimum fields:

- `campaign_id`
- `campaign_name`
- `campaign_dir`
- `spec_file`
- `workspace_root`
- `status`: pending, planning, active, stalled, completed, failed, unknown
- `planning_enabled`
- `human_plan_review`
- `repair_enabled`
- `budget_ref`
- `notification_summary`
- `stages`
- `plan_ref`
- `plan_approval_ref`
- `archive_manifest_ref`

### Stage

A stage is a campaign node and its attached run/workspace state.

Minimum fields:

- `stage_id`
- `campaign_id`
- `display_name`
- `depends_on`
- `context_from`
- `memory_dirs`
- `launcher_script`
- `args`
- `env_summary`
- `status`
- `workspace_path`
- `run_id`
- `attempt_id`
- `pid`
- `slurm_job_id`
- `stdout_log_ref`
- `stderr_log_ref`
- `started_at`
- `completed_at`
- `required_artifact_refs`
- `optional_artifact_refs`
- `missing_artifacts`
- `artifact_validators`
- `fail_reason`
- `repair_attempts`
- `liveness`

### Artifact

An artifact is a file or directory with source provenance and UI meaning.

Minimum fields:

- `artifact_id`
- `kind`: metadata, status, budget, ledger, token_log, log, paper, paper_part,
  review, revision, math, campaign_plan, approval, memory, config, archive,
  task, unknown
- `source_path`
- `source_workspace_path`
- `owner_type`: project, campaign, stage, run
- `owner_id`
- `relative_path`
- `media_type`
- `size_bytes`
- `modified_at`
- `content_hash`
- `immutable_source`
- `derived`
- `required`
- `validator_spec`
- `validation_result`
- `parent_artifact_ids`
- `produced_by_stage`
- `attempt_id`

### Budget

A budget object normalizes run and campaign spending without changing the
underlying budget ledger semantics.

Minimum fields:

- `budget_id`
- `owner_type`: run, stage, campaign
- `owner_id`
- `usd_limit`
- `total_usd`
- `by_model`
- `last_updated`
- `ledger_refs`
- `token_usage_refs`
- `pricing_source`
- `reconciliation_status`: direct, merged_private_ledger, estimated,
  unavailable

### Event

An event is append-only audit data for observations and actions.

Minimum fields:

- `event_id`
- `timestamp`
- `actor`: system, user, orchestrator, OpenClaude, OpenClaw, future_slack,
  importer, unknown
- `event_type`: observed, indexed, launched, completed, failed, repaired,
  approved, rejected, budget_alert, feedback_submitted, revision_requested,
  archived, capability_denied
- `target_type`: project, campaign, stage, run, artifact, budget
- `target_id`
- `source_ref`
- `mutation`
- `confirmation_id`
- `capability`
- `summary`
- `details_ref`

### Human Feedback

Human feedback is an append-only steering artifact. It may request a new
revision/iteration attempt, but it must not edit historical generated paper
artifacts in place.

Minimum fields:

- `feedback_id`
- `created_at`
- `created_by`
- `source_artifact_ids`
- `source_run_id`
- `requested_start_stage`
- `intent`: revise, reroute, clarify, accept, reject
- `feedback_text_ref`
- `revision_request_ref`
- `resulting_run_id`
- `status`: drafted, submitted, accepted_for_run, superseded

## Manifest Contract

Each imported run or campaign should produce a manifest. The manifest is the
stable source for product read models.

Recommended filename:

- `.msc/manifest.json` for a sidecar inside a workspace, or
- `.msc_index/manifests/<manifest_id>.json` for an external project index

Recommended top-level shape:

```json
{
  "manifest_version": 1,
  "manifest_id": "string",
  "created_at": "ISO-8601",
  "source_root": "absolute-or-project-relative-path",
  "importer": {
    "name": "msc-artifact-importer",
    "version": "0.1",
    "mode": "read_only"
  },
  "project": {},
  "campaigns": [],
  "runs": [],
  "stages": [],
  "artifacts": [],
  "budgets": [],
  "events": [],
  "warnings": []
}
```

The manifest should be deterministic for the same source tree except for
explicit import timestamps and importer version metadata. Snapshot tests should
normalize those volatile fields.

## Importer Design

### Importer Modes

- `inspect`: read source artifacts and emit a manifest to stdout or memory.
- `index`: write a derived manifest/index without modifying source artifacts.
- `sidecar`: write `.msc/manifest.json` next to the source workspace.
- `repair-index`: rebuild derived indexes from raw source artifacts.

The first implementation should support `inspect` and `index`. `sidecar` should
require explicit confirmation because it writes into or near historical
workspaces.

### Import Sources

Supported initial sources:

- repo root
- `results/`
- a single run workspace
- a campaign workspace root
- a campaign YAML
- an archive directory containing `manifest.json`

### Discovery Order

The importer should discover evidence in this order:

1. Explicit user-provided path.
2. Campaign YAML and `workspace_root`.
3. `campaign_status.json` under campaign workspace.
4. Run-level files such as `run_summary.json`, `experiment_metadata.json`,
   `run_status.json`, `STATUS.txt`, and `.progress_heartbeat`.
5. Known artifact directories such as `paper_workspace/` and
   `math_workspace/`.
6. Logs under `logs/`, root `*.log`, root `consortium_*.out`, and recorded
   metadata log paths.
7. Budget files and ledgers.
8. Archive manifests and moved-file metadata.

### Normalization Rules

- Normalize paths relative to the project root when possible.
- Preserve absolute original paths in provenance fields when they appear in
  historical manifests.
- Never expose raw secret values from YAML/env fields.
- Preserve declared validators even when not executing them.
- Preserve missing-artifact lists from status files.
- Preserve old status strings and map them into product status enums with a
  `raw_status` field when needed.
- Prefer explicit status files over inference.
- Use inference only with `confidence: inferred`.

### Status Mapping

Initial normalized statuses:

- `pending`
- `planning`
- `active`
- `stalled`
- `repairing`
- `completed`
- `failed`
- `partial`
- `incomplete`
- `unknown`

Mapping guidance:

- `in_progress` becomes `active` unless liveness signals indicate `stalled`.
- `running` becomes `active`.
- `repairing` remains `repairing`.
- `COMPLETE` in `STATUS.txt` becomes `completed`.
- `INCOMPLETE` in `STATUS.txt` becomes `incomplete`.
- `ERROR` in `STATUS.txt` becomes `failed`.
- A present final paper may support `completed`, but should not override an
  explicit failed/error status without a warning.

### Liveness Model

The product read model should expose liveness as evidence, not as a hidden
decision.

Minimum liveness fields:

- `pid`
- `pid_alive`
- `slurm_job_id`
- `slurm_alive`
- `log_active`
- `workspace_active`
- `heartbeat_active`
- `last_activity_at`
- `last_activity_age_seconds`
- `overall`: alive, likely_alive, likely_dead, dead, stalled, unknown

### Validation Model

The importer should initially perform structural validation only:

- file exists
- directory exists
- minimum size when declared
- JSON parseable when kind requires JSON
- declared validator specs captured

Semantic validation should remain delegated to protected existing validators
until explicitly approved.

## Read Models

The importer manifest should compile into three dashboard-friendly read models.

### Graph Read Model

Purpose: drive the VS Code graph tab.

Minimum fields:

- graph id
- graph kind: campaign, run, revision_chain
- nodes: campaigns, stages, runs, artifacts, feedback requests
- edges: dependency, context_from, produced, revised_from, budget_for
- node status
- liveness summary
- budget summary
- artifact completeness summary
- latest event summary

### Artifact Browser Read Model

Purpose: drive read-only artifact inspection.

Minimum fields:

- artifact tree grouped by campaign, stage, run, and kind
- immutable/derived badges
- required/optional badges
- missing/invalid indicators
- preview hints for markdown, TeX, PDF, JSON, logs, and images
- parent/source links for revision attempts

### Operator Summary Read Model

Purpose: give OpenClaude/OpenClaw/CLI a compact context object.

Minimum fields:

- active campaigns
- failed or stalled stages
- launchable stages
- latest budget state
- latest artifacts
- pending approvals
- recommended next read-only inspection commands
- mutation commands available only by capability and confirmation

## Compatibility With Current Commands

Stage 2 should preserve compatibility with current command behavior by treating
these as reference outputs:

- `msc runs`
- `msc status`
- `msc logs`
- `msc budget`
- `scripts/campaign_cli.py status`
- `scripts/campaign_cli.py stage-logs`
- `scripts/campaign_cli.py stage-artifacts`
- `scripts/campaign_cli.py budget`
- `scripts/campaign_cli.py launchable`

Future SDK/CLI work may replace these surfaces, but the first artifact model
should be able to explain the same status, budget, log, and artifact facts.

## Security And Privacy Rules

- Do not store raw API keys in manifests.
- Redact notification tokens, webhook URLs, Telegram chat ids when displayed,
  and any future Slack credentials.
- Treat private token ledgers as sensitive; expose summarized spend by default.
- Avoid indexing full prompt or LLM-call content into broad UI surfaces unless
  the user explicitly opts in.
- Keep immutable source paths auditable without unnecessarily leaking absolute
  cluster home paths in exported/shareable views.

## User Evaluation Checkpoint

The user should review:

- whether the domain objects match the product mental model
- whether sidecar-first indexing is acceptable before artifact relocation
- whether human feedback/revision requests are represented correctly
- whether the read models cover the VS Code graph tab, artifact browser, budget
  panel, and OpenClaude skill context
- whether any fields should be hidden, redacted, or treated as sensitive
- answered on 2026-05-10: Stage 3 should expose this model through both a
  Python SDK and expressive CLI JSON commands, built together with parity tests
  so agent harnesses can validate the full surface before OpenClaude
  integration

## Exit Criteria

Stage 2 is approved when:

- the production domain model is accepted or revised
- the manifest contract is accepted
- importer modes and source discovery rules are accepted
- read-model requirements are accepted
- the user either provides real fixtures for validation or explicitly accepts
  that implementation may begin with contract fixtures and deferred real-run
  validation

## Validation Plan

Validation possible before real fixtures:

- Parse maintained campaign specs and archived manifests.
- Snapshot normalized manifests generated from YAML-only and manifest-only
  inputs.
- Unit-test status mapping and path normalization.
- Unit-test redaction rules for secrets.
- Unit-test artifact classification for known filenames.
- Confirm no protected runtime files are edited.

Validation requiring real fixtures:

- Import completed, failed, stalled, repairing, iterate, budget-rich,
  math-enabled, and strict paper workspaces.
- Compare normalized status against `STATUS.txt`, `run_status.json`,
  `campaign_status.json`, and current CLI status outputs.
- Compare budget summaries against ledgers and budget state files.
- Verify final paper, review, revision, and math artifacts are discoverable.
- Snapshot graph, artifact browser, and operator summary read models.

## Implementation Notes For Later Stages

- Start with a pure reader/importer package and tests.
- Make writes opt-in and separate from read APIs.
- Keep manifests versioned from the first implementation.
- Prefer JSON output for machine clients and concise rich output for humans.
- Treat the production manifest as an adapter output, not as a new source of
  scientific truth.
- Defer physical artifact relocation until import, indexing, and UI read models
  are proven against real workspaces.
