# Extension UI Implementation Plan

This plan describes how the VS Code extension should implement the user
experience described in [`target_product_model.md`](target_product_model.md).
The extension should feel like a research cockpit, not a file explorer or
process monitor.

## Goal

When a researcher opens a campaign, they should see the research graph for
their question, understand current campaign execution state, inspect produced
deliverables, and provide feedback at meaningful inflection points.

The UI should not ask the researcher to manage "runs" as a separate product
object. The campaign is the research attempt.

## UX Principles

- Show the research graph as the primary organizing object.
- Make campaign execution state obvious.
- Default to produced deliverables, not internal files.
- Surface human decisions before diagnostics.
- Use language that matches the product model: campaign, stage, feedback,
  decision, deliverable, continue, rerun, rewind.
- Hide process/session details unless the user opens diagnostics.
- Never show false empty, false active, or false completed states.

## Primary User Flow

### 1. Create Campaign

The user provides:

- campaign title,
- research goal/task,
- budget/model posture,
- output preference.

The system creates:

- campaign,
- standard research graph from the LangGraph-derived template,
- planned stage outputs,
- initial campaign event stream.

The user should not provide a separate run task.

### 2. Open Campaign

The campaign workspace should show:

- research goal,
- campaign status,
- graph overview,
- current stage or next action,
- pending human decision if any,
- key produced deliverables,
- feedback history.

The graph should be visible even before execution starts.

### 3. Start Or Continue Campaign

Primary action:

```text
Start Campaign
Continue Campaign
```

Not:

```text
Start Run
Run Again
```

If execution is already active, show:

- active stage,
- elapsed time,
- latest event,
- latest produced artifact,
- stop/pause controls if available.

Process ids and low-level logs belong in diagnostics.

### 4. Observe Stage Outputs

As each stage completes, the UI should show:

- stage status,
- produced deliverables,
- validation result,
- route selected,
- next stage.

Declared outputs that are not yet produced should be labeled as planned, not
missing artifacts in the main deliverables view.

### 5. Human Inflection Point

At review points, show a decision panel:

- what needs review,
- relevant artifacts,
- recommended next actions,
- feedback box,
- approve/reject/rerun/rewind/reroute controls.

This should be a first-class surface, not buried inside diagnostics or logs.

### 6. Feedback And Rerun

The user can submit feedback attached to:

- campaign,
- stage,
- artifact,
- pending decision.

The UI should then expose typed actions:

- continue,
- rerun this stage,
- rewind to earlier stage,
- reroute graph,
- revise stage instruction.

The feedback becomes campaign context for future execution.

## Campaign List View

The campaign list should prioritize:

- campaign title,
- research goal summary,
- status,
- human action needed,
- current stage,
- latest deliverable,
- budget posture.

Avoid:

- raw paths as primary labels,
- run ids,
- process status as campaign status,
- artifact counts that include prompts/logs/system files.

## Campaign Workspace Layout

Recommended tabs:

- `Graph`
- `Decisions`
- `Deliverables`
- `Feedback`
- `Diagnostics`

Diagnostics can include logs, process/session ids, raw events, command output,
and prompt/system-state files.

### Header

Show:

- campaign title,
- research goal,
- campaign status,
- budget posture,
- primary action.

Primary action names:

- `Start Campaign`
- `Continue Campaign`
- `Pause Campaign`
- `Stop Campaign`

### Graph Tab

Show:

- full stage graph,
- current/blocked/completed status,
- human pause nodes,
- reroute/loop edges,
- selected stage details.

Stage detail panel should include:

- purpose,
- instruction summary,
- required inputs,
- expected deliverables,
- validators,
- feedback attached to stage,
- safe next actions.

### Decisions Tab

Show pending and historical human decisions.

Pending decision card:

- title,
- reason,
- affected stage/artifact,
- evidence links,
- safe actions.

Actions:

- approve,
- reject,
- rerun stage,
- rewind to stage,
- propose reroute,
- revise instruction.

### Deliverables Tab

Default filters:

- produced only,
- deliverables/evidence only.

Do not show by default:

- prompt files,
- scaffold files,
- logs,
- status JSON,
- budget ledgers,
- raw process metadata.

The user may opt into:

- planned outputs,
- diagnostics,
- all audiences.

Deliverable rows should show:

- stage,
- artifact name,
- type,
- produced time,
- validation state,
- preview/open actions.

### Feedback Tab

Show:

- feedback composer,
- feedback history,
- target selector,
- suggested follow-up action.

Feedback targets:

- whole campaign,
- current stage,
- selected graph node,
- selected artifact,
- pending decision.

### Diagnostics Tab

Show technical details only when requested:

- raw events,
- command failures,
- process/session metadata,
- low-level logs,
- prompt/system files,
- CLI readiness,
- OpenClaude/OpenClaw readiness.

## Terminology Changes

Replace:

```text
Start Run -> Start Campaign / Continue Campaign
Run Again -> Rerun Stage / Continue Campaign
No active run -> Campaign not started / Campaign paused / No local process attached
Artifacts -> Deliverables by default
Missing artifacts -> Planned outputs
Settings -> Diagnostics
```

Keep "run" only in diagnostics if referring to a process/session id from legacy
or internal execution.

## Data Requirements From SDK

The extension should consume read models, not infer state from files.

Needed read-model fields:

- campaign status,
- campaign execution status,
- current stage id,
- pending decisions,
- safe next actions,
- graph nodes/edges,
- stage instruction summaries,
- planned outputs,
- produced deliverables,
- artifact audience/role,
- validation results,
- feedback history,
- diagnostics.

## Implementation Sequence

### 1. Language Cleanup

Update all visible labels from run-centric to campaign-centric language.

Acceptance:

- The normal researcher workflow never says "Start Run."
- Run/process language appears only in diagnostics.

### 2. Campaign Execution Header

Replace session-only run strip with campaign execution summary.

Acceptance:

- Header shows current stage, pending decision, and primary next action.
- Existing process/session data is projected as internal metadata.

### 3. Deliverables-First Artifact View

Rename or reframe the artifact tab around deliverables.

Acceptance:

- Default view excludes prompts/scaffolds/logs/system files.
- Planned outputs are visible only through an explicit filter.
- Legacy scaffold files never appear as main deliverables.

### 4. Decision Surface

Add a first-class decisions panel.

Acceptance:

- Human pauses are visible.
- Pending decisions show reason, evidence, and safe actions.
- Feedback and decision actions write typed SDK events.

### 5. Feedback-To-Rerun Workflow

Connect feedback to graph actions.

Acceptance:

- User can attach feedback to a stage or artifact.
- User can request rerun/rewind with feedback context.
- UI shows feedback history on the affected stage.

### 6. Graph Stage Detail Upgrade

Make selected graph nodes explain themselves.

Acceptance:

- Stage detail shows purpose, instruction summary, expected deliverables,
  validators, pause policy, and safe actions.

### 7. Diagnostics Quarantine

Move low-level outputs into diagnostics.

Acceptance:

- Raw events, process ids, command output, prompt files, and logs are opt-in.
- Main tabs remain researcher-facing.

## Visual/Interaction Notes

- Use restrained dense UI, not a landing page.
- Keep graph and decision state visible without decorative clutter.
- Use status color sparingly and consistently.
- Avoid oversized cards for operational data.
- Treat deliverables as the main content, not file rows.
- Make loading states explicit.
- Do not show empty states until data has loaded.

## Acceptance Tests

The extension should have smoke tests for:

- campaign-centric labels,
- no default "Start Run" language,
- deliverables default filters,
- prompt/scaffold exclusion,
- feedback submission protocol,
- decision tab presence,
- loading state correctness.

Manual checks:

- create a fresh campaign,
- open graph before execution,
- start/continue campaign,
- observe produced deliverables,
- submit feedback,
- rerun/rewind a stage,
- inspect diagnostics only when requested.
