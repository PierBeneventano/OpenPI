# Stage 3 SDK And CLI Control Surface

This document defines the public SDK and CLI strategy for exposing MSc as a
powerful, expressive, agent-operable product surface while preserving the
research kernel.

Status: drafted on 2026-05-10. Pending user review before implementation.

## Decision

The Python SDK and CLI should be built together.

The SDK is the horizontal programmatic API. The CLI is the full expressive
operator and agent interface over that SDK. Every meaningful capability should
be available in both forms unless there is a documented reason not to expose it
in one surface.

The CLI must be rich enough that Codex, OpenClaude, OpenClaw, and future
orchestrators can operate the system through documented commands without
reaching into protected internals.

## Rule

The SDK/CLI may wrap, inspect, index, launch, supervise, and request actions,
but it must not change functional research logic:

- no prompt edits
- no LangGraph rewrites
- no stage order/routing changes
- no validator behavior changes
- no model policy changes
- no budget semantic changes
- no state/checkpoint semantic changes
- no artifact completion semantic changes

Mutating operations require capability checks and explicit confirmation.

## Design Goals

- Give human users and agent harnesses the same expressive control surface.
- Make every command testable by Codex before OpenClaude integration.
- Keep the first implementation Remote-SSH/local-process friendly.
- Return structured machine-readable data without sacrificing useful human CLI
  output.
- Wrap current entry points before replacing them.
- Preserve enough audit metadata that an operator can see what happened, who
  requested it, and which command or SDK call performed it.

## Non-Goals

- Do not build the VS Code extension in this stage.
- Do not fork OpenClaude in this stage.
- Do not expose hosted multi-user APIs in this stage.
- Do not refactor protected runtime modules to make the SDK pretty.
- Do not physically relocate artifacts as part of the first SDK/CLI work.

## Architecture

Stage 3 should introduce a public product package layered beside the current
kernel. Exact names can change during implementation, but the architecture
should preserve these boundaries:

- `msc_sdk`: Python package namespace for public product APIs.
- `msc_sdk.artifacts`: production artifact importer and read models from
  Stage 2.
- `msc_sdk.runs`: run inspection, launch request, resume request, and dry-run
  helpers.
- `msc_sdk.campaigns`: campaign inspection, plan review, stage status, and
  launch/repair request helpers.
- `msc_sdk.events`: append-only audit/event helpers.
- `msc_sdk.capabilities`: permission and confirmation model.
- `msc_sdk.validation`: self-checks, snapshot checks, command parity tests, and
  fixture validation.
- `msc` CLI: user- and agent-facing command surface backed by the SDK.

The current `consortium` runtime package remains protected. SDK calls should
prefer current public commands, documented files, or narrow wrappers around
existing entry points before any deeper integration is considered.

## Surface Parity Contract

For every stable SDK operation, there should be a CLI equivalent:

- SDK returns typed Python objects or dictionaries.
- CLI returns human output by default.
- CLI supports `--json` for machine clients.
- CLI supports `--dry-run` for any operation that could mutate state.
- CLI supports `--confirm <token>` or equivalent explicit confirmation for
  mutating operations.
- CLI exits with meaningful status codes.
- CLI stderr/stdout behavior is stable enough for agents.

For every stable CLI machine command, there should be a direct SDK equivalent:

- no shelling out required from Python clients
- same status enums
- same error codes/categories
- same capability requirements
- same redaction behavior

## Command Families

The exact command spelling can be revised during implementation, but the public
shape should cover the following families.

### Project

Purpose: discover repo/project context and setup state.

Candidate commands:

- `msc project inspect --json`
- `msc project doctor --json`
- `msc project readiness --json`

SDK equivalents:

- `ProjectClient.inspect()`
- `ProjectClient.doctor()`
- `ProjectClient.readiness()`

### Artifacts

Purpose: inspect, index, and read production artifact manifests without
mutating raw workspaces.

Candidate commands:

- `msc artifacts inspect <path> --json`
- `msc artifacts index <path> --out <index-dir> --json`
- `msc artifacts tree <path> --json`
- `msc artifacts show <artifact-id-or-path> --json`
- `msc artifacts validate <path> --structural --json`

SDK equivalents:

- `ArtifactClient.inspect(path)`
- `ArtifactClient.index(path, out)`
- `ArtifactClient.tree(path)`
- `ArtifactClient.show(ref)`
- `ArtifactClient.validate(path, mode="structural")`

### Runs

Purpose: list, inspect, resume, and prepare run operations.

Candidate commands:

- `msc runs list --json`
- `msc runs inspect <run-id-or-path> --json`
- `msc runs logs <run-id-or-path> --stage <stage> --json`
- `msc runs budget <run-id-or-path> --json`
- `msc runs dry-run --task-file <path> --tier <tier> --json`
- `msc runs resume <run-id-or-path> --dry-run --json`
- `msc runs resume <run-id-or-path> --confirm <token> --json`

SDK equivalents:

- `RunClient.list()`
- `RunClient.inspect(ref)`
- `RunClient.logs(ref, stage=None)`
- `RunClient.budget(ref)`
- `RunClient.dry_run(task_file, tier)`
- `RunClient.resume(ref, confirmation=None)`

### Campaigns

Purpose: inspect, validate, plan-review, and operate campaigns through the
preserved current behavior.

Candidate commands:

- `msc campaigns list --json`
- `msc campaigns inspect <campaign> --json`
- `msc campaigns graph <campaign> --json`
- `msc campaigns status <campaign> --json`
- `msc campaigns logs <campaign> <stage-id> --json`
- `msc campaigns artifacts <campaign> <stage-id> --json`
- `msc campaigns budget <campaign> --json`
- `msc campaigns launchable <campaign> --json`
- `msc campaigns validate <campaign> --json`
- `msc campaigns approve-plan <campaign> --dry-run --json`
- `msc campaigns approve-plan <campaign> --confirm <token> --json`
- `msc campaigns reject-plan <campaign> --confirm <token> --json`
- `msc campaigns launch <campaign> <stage-id> --dry-run --json`
- `msc campaigns launch <campaign> <stage-id> --confirm <token> --json`
- `msc campaigns repair <campaign> <stage-id> --dry-run --json`
- `msc campaigns repair <campaign> <stage-id> --confirm <token> --json`

SDK equivalents:

- `CampaignClient.list()`
- `CampaignClient.inspect(ref)`
- `CampaignClient.graph(ref)`
- `CampaignClient.status(ref)`
- `CampaignClient.logs(ref, stage_id)`
- `CampaignClient.artifacts(ref, stage_id)`
- `CampaignClient.budget(ref)`
- `CampaignClient.launchable(ref)`
- `CampaignClient.validate(ref)`
- `CampaignClient.approve_plan(ref, confirmation=None)`
- `CampaignClient.reject_plan(ref, confirmation=None)`
- `CampaignClient.launch_stage(ref, stage_id, confirmation=None)`
- `CampaignClient.repair_stage(ref, stage_id, confirmation=None)`

### Events

Purpose: provide an audit trail for humans, Codex, OpenClaude, and optional
OpenClaw.

Candidate commands:

- `msc events list --json`
- `msc events show <event-id> --json`
- `msc events append-feedback <artifact-or-run-ref> --dry-run --json`
- `msc events append-feedback <artifact-or-run-ref> --confirm <token> --json`

SDK equivalents:

- `EventClient.list()`
- `EventClient.show(event_id)`
- `EventClient.append_feedback(ref, feedback, confirmation=None)`

### Capabilities

Purpose: make permissions visible and enforceable.

Candidate commands:

- `msc capabilities current --json`
- `msc capabilities explain <operation> --json`
- `msc capabilities request <operation> --dry-run --json`

SDK equivalents:

- `CapabilityClient.current()`
- `CapabilityClient.explain(operation)`
- `CapabilityClient.request(operation, dry_run=True)`

## Capability Model

Initial capabilities:

- `read.project`
- `read.artifacts`
- `read.runs`
- `read.campaigns`
- `read.logs`
- `read.budget`
- `read.events`
- `write.index`
- `write.feedback`
- `mutate.launch`
- `mutate.resume`
- `mutate.repair`
- `mutate.plan_approval`
- `mutate.stage_status`
- `mutate.budget`
- `mutate.archive`
- `mutate.config`

Default agent profile:

- read capabilities
- `write.index` only to a designated derived index directory
- no mutation capabilities unless explicitly granted

OpenClaude v1 profile:

- read capabilities
- `write.feedback` for append-only human-approved steering notes
- mutation capabilities only after user confirmation

OpenClaw optional profile:

- read capabilities
- optional confirmed launch/repair/plan approval capabilities
- no direct code/prompt/artifact mutation by default

## Confirmation Contract

Any mutating command should support a two-step flow:

1. Request: command returns a proposed action, risk summary, capability needed,
   and confirmation token.
2. Execute: command runs only when the matching confirmation token is supplied.

This applies to:

- run launch
- run resume
- campaign stage launch
- repair
- abort/cancel
- budget increase
- stage status override
- plan approval/rejection
- archive/delete
- config mutation
- task/prompt/campaign rewrite
- artifact mutation

## Agent Self-Validation

Codex should be able to validate all stable entry points before OpenClaude is
integrated.

The SDK/CLI should include an agent-operable validation suite:

- command discovery: list every public operation and its capabilities
- schema validation: confirm every `--json` command returns expected fields
- parity validation: compare CLI JSON output with SDK object output
- fixture validation: run commands against checked-in sample specs and later
  real workspace fixtures
- dry-run validation: prove mutating commands refuse execution without
  confirmation
- redaction validation: prove secrets are not emitted in JSON or human output
- exit-code validation: prove success, user error, missing fixture, capability
  denied, and internal error cases are distinguishable

Candidate commands:

- `msc selftest commands --json`
- `msc selftest sdk-cli-parity --json`
- `msc selftest fixtures --json`
- `msc selftest permissions --json`

SDK equivalents:

- `ValidationClient.commands()`
- `ValidationClient.sdk_cli_parity()`
- `ValidationClient.fixtures()`
- `ValidationClient.permissions()`

The goal is that a future OpenClaude skill can run the same validation suite to
confirm it has full expression of the SDK through the CLI.

## Error Contract

Machine-readable errors should include:

- `ok`: false
- `error_code`
- `error_category`: user_input, missing_file, invalid_state,
  capability_denied, confirmation_required, validation_failed, runtime_error,
  unavailable
- `message`
- `details`
- `suggested_next_actions`

Human output may be friendlier, but it should map back to these error
categories.

## Implementation Sequence

Suggested implementation order:

1. Define SDK data classes/types for Stage 2 domain objects.
2. Implement pure artifact/read-model SDK operations against contract fixtures.
3. Add CLI `--json` wrappers over the SDK.
4. Add command discovery and schema snapshots.
5. Add capability and confirmation primitives.
6. Wrap existing read-only commands with parity tests.
7. Add dry-run request flows for mutating operations.
8. Only then wire confirmed mutation commands to existing preserved entry
   points.

## User Evaluation Checkpoint

The user should review:

- whether SDK and CLI parity matches the intended product direction
- whether the command families are expressive enough for OpenClaude
- whether confirmation flow is strict enough
- whether OpenClaude and optional OpenClaw capability profiles are acceptable
- whether self-validation commands cover what an agent harness needs before it
  is trusted to operate MSc
- whether Stage 4 should build a daemon/control harness immediately or keep the
  first implementation as library plus CLI

## Exit Criteria

Stage 3 is approved when:

- SDK/CLI parity is accepted
- command families are accepted or revised
- capability and confirmation contracts are accepted
- self-validation strategy is accepted
- the first implementation scope is chosen

## Validation Plan

Before runtime implementation:

- documentation review
- command-shape review
- threat review for capabilities and confirmations

During implementation:

- unit tests for SDK data models and pure readers
- snapshot tests for CLI JSON output
- parity tests between SDK and CLI
- fixture tests against checked-in specs/manifests
- later real-workspace fixture tests
- no protected kernel edits without explicit user approval
