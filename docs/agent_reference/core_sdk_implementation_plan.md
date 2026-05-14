# Core SDK Implementation Plan

This plan implements the signed-off target model in
[`target_product_model.md`](target_product_model.md). The key product decision
is that a campaign is the research attempt. "Runs" may exist internally as
process/session metadata, but they should not be a separate researcher-facing
object.

## Goal

Build the core SDK around this chain:

```text
CampaignGoal
  -> ResearchGraphTemplate
  -> GraphSpec
  -> StageSpec
  -> RuntimeContext
  -> EventRecord
  -> ReadModel
```

The SDK should preserve the proven LangGraph research method while making it
typed, inspectable, steerable, and auditable.

## Design Commitments

- The legacy LangGraph pipeline is the source material for the standard
  research template.
- The SDK owns the product architecture.
- Campaign execution replaces the user-facing "run" concept.
- Prompts are first-class stage instruction payloads, not scaffold files.
- Events are the product source of truth.
- Artifacts are produced deliverables/evidence, not arbitrary files.
- Human feedback and decisions are typed operations/events.
- Compatibility with old result folders is temporary migration logic.

## Target Core Concepts

### CampaignGoal

The campaign goal is the task. It should contain:

- research objective,
- optional constraints,
- budget/model posture,
- output format preference,
- researcher-provided context.

The SDK should not ask for a separate run task after campaign creation.

### ResearchGraphTemplate

This is the standard research pipeline distilled from the legacy LangGraph. It
should include:

- stage ids and order,
- gate/control nodes,
- loops and reroutes,
- human inflection points,
- prompt/instruction payload ids,
- artifact contracts,
- validators,
- tool/model policies,
- failure policies.

The first production template should intentionally mirror the proven
LangGraph-derived research workflow before introducing variants.

### GraphSpec

The campaign-specific graph compiled from the template and goal. It should be
stable enough for UI display and precise enough for execution.

GraphSpec should not be a separate UI-only graph. It is the campaign graph.

### StageSpec

StageSpec should include:

- id, title, kind, purpose,
- instruction payload,
- declared inputs,
- declared outputs,
- tool/model permissions,
- validators,
- pause policy,
- failure policy,
- route options,
- budget posture.

StageSpec should preserve high-quality legacy prompts by reference or structured
payload, not by writing markdown scaffold files.

### RuntimeContext

The only interface stage adapters use to do product-relevant work. It should
provide:

- `read_input(...)`,
- `use_model(...)`,
- `use_tool(...)`,
- `write_artifact(...)`,
- `emit_event(...)`,
- `request_human_decision(...)`,
- `record_budget(...)`.

Adapters should not mutate campaign state directly.

### EventRecord

The event stream should describe campaign execution:

- `CampaignCreated`
- `GraphInstantiated`
- `CampaignExecutionStarted`
- `StageStarted`
- `ModelInvoked`
- `ToolInvoked`
- `ArtifactWritten`
- `ArtifactIndexed`
- `ValidationPassed`
- `ValidationFailed`
- `HumanDecisionRequired`
- `InstructionSent`
- `RouteSelected`
- `StageRerunRequested`
- `CampaignExecutionPaused`
- `CampaignExecutionCompleted`
- `CampaignExecutionFailed`

Existing `RunStarted` / `RunExited` events should be migrated or projected into
campaign execution state, not exposed as product concepts.

### ReadModel

Read models should answer product questions:

- campaign status,
- graph node statuses,
- current stage,
- pending human decisions,
- produced deliverables,
- feedback history,
- safe next actions,
- budget posture.

Read models should be projections from specs plus events.

## Implementation Sequence

### 1. Rename The Product Language

Replace user-facing run language with campaign execution language.

Tasks:

- Add campaign execution read-model fields.
- Keep process/session ids internal.
- Mark `RunStarted` and `RunExited` as compatibility events.
- Add projection aliases from old run events to campaign execution state.
- Update CLI text to avoid "start run" as the primary concept.

Acceptance:

- Product read models can describe campaign execution without a user-facing run
  object.
- Old events still project correctly.

### 2. Extract The LangGraph Research Template

Promote the proven LangGraph structure into a first-class
`ResearchGraphTemplate`.

Tasks:

- Inventory current LangGraph stages, gates, loops, joins, and reroutes.
- Map each legacy node to a template node.
- Preserve stage ordering and control logic.
- Preserve human-in-the-loop points.
- Preserve legacy prompt payloads.
- Preserve tool/model expectations.

Acceptance:

- One template represents the full standard research pipeline.
- Template parity tests compare it against the current legacy graph/source
  material.

### 3. Make Prompts Stage Payloads

Move prompts from opaque legacy builder internals into typed stage instruction
payloads.

Tasks:

- Define `InstructionPayload` or equivalent.
- Link each StageSpec to the relevant legacy prompt builder/content.
- Keep prompts inspectable through SDK/CLI.
- Remove scaffold prompt file generation from product code.
- Keep contract summaries as optional diagnostics only if needed.

Acceptance:

- A stage can expose its instruction payload without running.
- No new campaign writes prompt/scaffold files as artifacts.

### 4. Collapse Run Tables Into Campaign Execution

Do not necessarily delete storage immediately, but change product semantics.

Tasks:

- Treat process ids, execution attempts, retries, and logs as internal execution
  metadata.
- Project them into campaign execution state.
- Rename or wrap SDK methods so product code uses campaign execution methods.
- Stop exposing run history as a primary UX object.

Acceptance:

- The UI can start/continue a campaign without asking for a separate run task.
- A campaign's timeline still preserves execution attempts for audit/debugging.

### 5. Implement Campaign Execution Engine

Execute GraphSpec through RuntimeContext.

Tasks:

- Start at the first runnable stage.
- Emit stage lifecycle events.
- Validate required inputs before a stage runs.
- Execute stage adapter.
- Index produced artifacts.
- Run validators.
- Route to next stage.
- Pause at declared human inflection points.
- Support reroute/rewind with feedback context.

Acceptance:

- A minimal graph can execute through campaign execution events.
- Human feedback can trigger a stage rerun or rewind.

### 6. Adapter Cutover

Wrap or port legacy agents into stage adapters.

Tasks:

- Start with persona, literature, brainstorm, and research plan stages.
- Adapter inputs must come from RuntimeContext.
- Adapter outputs must use `write_artifact`.
- Adapter prompts should come from StageSpec instruction payloads.
- Adapter execution must not write status files as product truth.

Acceptance:

- Early stages produce real deliverables through SDK artifact events.
- The extension can observe progress from events.

### 7. Artifact Semantics Hardening

Separate planned outputs from produced artifacts permanently.

Tasks:

- Keep declared outputs as obligations.
- Mark produced artifacts only when written by RuntimeContext or trusted legacy
  bridge.
- Classify prompt/log/diagnostic/system files separately.
- Remove compatibility classifiers once old folders are no longer needed.

Acceptance:

- Default artifact views show only deliverables/evidence.
- Scaffold/prompt files never appear as produced deliverables.

### 8. Human Decision Model

Make human steering repeatable and typed.

Tasks:

- Define decision types.
- Define safe next actions.
- Attach feedback to stage/artifact/decision/campaign.
- Support approve, reject, rerun stage, rewind to stage, reroute, revise
  instruction, and request evidence.

Acceptance:

- Human review points are visible before execution.
- Feedback changes future execution context in a typed way.

## Near-Term Refactor Targets

- Delete or quarantine `write_scaffold_artifacts(...)`.
- Rename "run" UI/CLI concepts to campaign execution concepts.
- Extract prompt payload references from legacy agent builders.
- Create a template parity suite against the LangGraph pipeline.
- Make the first several stage adapters SDK-native.
- Strengthen event projection tests for rerun/rewind behavior.

## Non-Goals

- Do not invent a new research method before preserving the proven one.
- Do not make a generic workflow engine first.
- Do not optimize for multi-campaign cloud orchestration yet.
- Do not expose storage implementation details to the researcher.

## Checkpoint Tests

Keep these green while migrating:

```bash
pytest tests/test_campaign_store.py tests/test_research_kernel.py tests/test_stage_contracts.py -q
pytest tests/test_msc_sdk_cli_surface.py -q
python -m compileall msc_sdk consortium/cli/commands/campaigns.py
```

Add new suites for:

- research template parity,
- campaign execution projection,
- human decision/rerun/rewind semantics,
- prompt payload exposure,
- artifact audience classification.
