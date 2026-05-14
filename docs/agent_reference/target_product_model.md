# Target Product Model

This document is my current internal picture of the product we are building
toward. It is intentionally written as an alignment artifact rather than a
code map. If this is wrong, the implementation will drift, so this should be
edited whenever the target changes.

## One Sentence

The product is a local research campaign cockpit where a researcher defines one
research goal, the SDK instantiates the proven LangGraph-derived research
pipeline for that goal, and the researcher iteratively steers the campaign
through structured human decision points until the campaign produces acceptable
research deliverables.

## Core Product Promise

A researcher should be able to answer these questions at any time:

- What is the campaign trying to accomplish?
- What stage is running, blocked, complete, or waiting for me?
- Why is the system allowed to move from one stage to the next?
- What did each stage actually produce?
- What decisions or feedback have I given?
- What can I safely do next?

If the UI cannot answer those questions from structured state, the system is
not finished.

## Semantic Center

The product should have one semantic center:

```text
CampaignGoal
  -> ResearchGraphTemplate
  -> GraphSpec
  -> StageSpec
  -> RuntimeContext
  -> EventRecord
  -> ReadModel
```

These are not merely implementation classes. They are the system's grammar.
Everything else should either feed this grammar or be projected from it.

## Campaign

A campaign is the research attempt. In product language, the campaign is the
run. There should not be a separate first-class "run" object that the
researcher has to understand.

A campaign owns:

- the research objective,
- the graph being attempted,
- the budget and model posture,
- the event stream,
- the current execution/progress state,
- the human feedback and approvals,
- stage iterations and reroutes,
- the produced deliverables.

A campaign is not a folder of files. Files are evidence of work, but the
campaign's truth comes from typed state and events.

The campaign goal and the task are the same thing. The UI should not ask the
researcher to separately understand "campaign objective" versus "run task."

## Graph

The graph is the planned research workflow for the campaign goal. It should be
visible when the campaign opens, before execution starts.

The base graph template should be distilled from the current legacy LangGraph
research pipeline because that pipeline is the proof that the method can
produce strong papers when a human stays sufficiently involved.

The graph should include:

- agent stages,
- gates,
- joins,
- loops,
- approval points,
- possible reroutes,
- failure routes,
- budget posture.

The SDK should productize this graph as structured specs. That means the old
LangGraph should not remain an opaque runtime authority, but the research
method inside it should absolutely be preserved: its stage ordering, prompts,
control logic, reroutes, and human-review dynamics are source material for the
standard campaign template.

The graph should remain connected to execution: when a stage runs, blocks,
reroutes, or is rerun with feedback, the graph node state should change through
events.

## Stage

A stage is a typed unit of research work. A stage should declare:

- purpose,
- prompt/instruction payload,
- required inputs,
- required outputs,
- optional supporting outputs,
- tools it may use,
- validators that define completion,
- model/budget posture,
- pause policy,
- failure policy,
- possible next routes.

The prompt is not the contract. The filesystem is not the contract. A stage's
contract must be inspectable without running the model.

However, the prompt is still an important part of the stage. The high-quality
legacy prompts should be preserved and made modular inside the SDK stage
definitions. They should not be converted into scaffold files or hidden inside
uninspectable runtime code.

## Execution

Execution should be campaign execution, not a separate user-facing run object.
Internally, the system may need process ids, execution sessions, retries, or
attempt ids, but those are implementation details. The researcher should see
the campaign progressing through its graph.

Execution should happen through a runtime context. An adapter can call tools,
call models, and write artifacts, but it should not silently mutate product
state.

The runtime context is responsible for turning real work into events:

- campaign execution started,
- stage started,
- tool/model usage,
- artifact written,
- validation passed or failed,
- human decision required,
- route selected,
- campaign execution paused, completed, or failed.

Adapters may wrap old functions temporarily, but they must communicate through
the SDK boundary. They should not write status files as product truth.

## Events

Events are the product source of truth. The UI, CLI, OpenClaude, OpenClaw, and
tests should reconstruct campaign state from events and specs.

Important event families:

- `CampaignCreated`
- `GraphProjected`
- `CampaignExecutionStarted`
- `StageStarted`
- `ArtifactWritten`
- `ArtifactIndexed`
- `ValidationPassed`
- `ValidationFailed`
- `HumanDecisionRequired`
- `InstructionSent`
- `ApprovalRequested`
- `ApprovalDecided`
- `RouteSelected`
- `StageRerunRequested`
- `CampaignExecutionPaused`
- `CampaignExecutionCompleted`
- `CampaignExecutionFailed`

Legacy or low-level events may still contain process/session details, but those
details should project into campaign execution state rather than becoming a
separate product concept called "runs."

If something matters to the researcher, it should probably be an event.

## Artifacts

Artifacts are not any file in a folder. Artifacts are researcher-meaningful
outputs or supporting evidence produced by a stage.

Artifact categories:

- `deliverable`: primary thing the researcher cares about.
- `evidence`: supporting material that explains or backs a deliverable.
- `diagnostic`: useful for debugging but not a research output.
- `log`: raw execution trace.
- `prompt`: prompt or scaffold material.
- `system_state`: status files, budget state, manifests, run metadata.

The UI should default to `deliverable` and high-value `evidence`. It should not
default to prompts, logs, generated scaffolds, or status files.

Declared outputs are planned obligations. They are not produced artifacts until
a stage writes them during a run and they pass the relevant indexing and
validation path.

Generated scaffold files should not exist in the product path. If contract
summaries are useful for debugging, they should be called contract summaries
and kept out of the artifact deliverables view.

## Human Feedback

Human feedback is not a chat side-channel. It is a campaign event.

Feedback should be attachable to:

- the whole campaign,
- a stage,
- a pending decision,
- an artifact.

The system should make feedback visible and auditable. Later runs should be
able to consume feedback through typed context, not by scraping arbitrary notes.

The normal loop should be:

```text
stage produces output
  -> human reviews at an inflection point
  -> feedback is recorded
  -> graph reroutes or rewinds to the appropriate earlier stage
  -> stage reruns with the feedback in context
```

The goal is not constant human rescue. The goal is a small number of
repeatable, high-leverage inflection points where humans have final say over
quality and direction.

## Human Decisions

The system should pause when continuing would be unsafe, ambiguous, expensive,
or scientifically questionable.

A pause should explain:

- what happened,
- why human judgment is needed,
- what evidence is relevant,
- what safe next actions are available.

Examples of safe next actions:

- approve continuation,
- reject and stop,
- revise the stage instruction,
- rerun the stage,
- reroute the graph,
- increase budget,
- mark artifact accepted,
- request more evidence.

## User Experience

The extension should feel like a research cockpit, not a file browser.

The first screen should show:

- campaigns,
- status,
- whether human action is needed,
- key deliverables,
- budget posture.

Inside a campaign, the researcher should see:

- graph,
- current campaign execution state,
- feedback/decision panel,
- produced deliverables,
- diagnostics only when requested.

The UI must avoid false state. It should not show "no data" before loading,
"no active run" when the campaign is the run, or
"artifact exists" when the file is only a scaffold.

The main action should be to start or continue the campaign graph, not to start
a separate run. The researcher should observe stage outputs as the graph
progresses and intervene at clear decision points.

## OpenClaude And OpenClaw

OpenClaude should become a natural-language steering layer over the SDK. It
should not bypass the SDK.

OpenClaude should translate user intent into typed operations:

- append feedback,
- explain stage,
- propose graph change,
- approve/reject decision,
- rerun or rewind stage,
- summarize artifacts,
- inspect budget,
- ask for missing evidence.

OpenClaw should become a repair/execution assistant over the same event and
operation model. It should not create another state authority.

## What Must Not Happen

- Do not treat prompt text as the product contract.
- Do not treat arbitrary files as artifacts.
- Do not use generated scaffold files as deliverables.
- Do not discard the proven LangGraph research method.
- Do not keep LangGraph as an opaque product architecture.
- Do not let UI state, status JSON, and campaign events disagree without a
  clear source of truth.
- Do not hide human decisions inside logs or prompts.
- Do not let OpenClaude or OpenClaw mutate state outside typed SDK operations.
- Do not expose "runs" as a separate researcher-facing concept when the
  campaign itself is the research attempt.

## Current Deviation From Target

The codebase is closer than it was, but still not fully aligned.

Known deviations:

- Some legacy execution code still exists as reference/proof material, but the
  target is to distill its successful research method into SDK-native graph and
  stage definitions.
- Some old result folders contain scaffold prompt files from earlier product
  behavior.
- Some adapters are still thin wrappers rather than fully kernel-native stages.
- Artifact classification still has compatibility logic for old runs.
- The UI is improving, but it still needs a stronger deliverables-first
  campaign summary.
- The schema still contains run-shaped concepts that should be collapsed into
  campaign execution state or internal process/session metadata.

These are acceptable transitional facts only if they keep shrinking.

## Alignment Test

For any future feature, ask:

```text
Does this strengthen the chain:
CampaignGoal -> ResearchGraphTemplate -> GraphSpec -> StageSpec -> RuntimeContext -> EventRecord -> ReadModel?
```

If yes, it probably belongs.

If it creates a parallel truth source, a prompt-only contract, or a file-system
shortcut, it is probably another patch.
