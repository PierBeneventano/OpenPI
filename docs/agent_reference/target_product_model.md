# Target Product Model

This document is my current internal picture of the product we are building
toward. It is intentionally written as an alignment artifact rather than a
code map. If this is wrong, the implementation will drift, so this should be
edited whenever the target changes.

## One Sentence

The product is a local research campaign cockpit where a researcher defines one
research goal, the SDK instantiates a council-and-gate research workflow for
that goal, and OpenClaude helps the researcher steer the campaign through
structured human decision points until the campaign produces acceptable
research deliverables.

## Researcher Feedback Snapshot

[feedback.md](feedback.md) is a raw snapshot of the originating researcher's
current target-engine intent. It should stay unedited as a dated reference
point. It is not a code map and should not be treated as proof that the current
runtime behaves exactly as described.

The feedback is nevertheless important because it clarifies the scientific
shape the product should preserve:

- research direction is challenged by persona councils,
- specialist stages can use model councils for high-stakes work,
- scientific claims must pass explicit gates before paper generation,
- duality checking is a required back-end gate before writeup,
- revision should be possible from prior artifacts and researcher feedback,
- the system should be steerable by an AI assistant working with the
  researcher.

The product architecture remains the SDK/event/read-model architecture in this
document. The feedback describes desired research behavior that the SDK should
make explicit, inspectable, and controllable.

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

The base graph template should be distilled from the proven research method and
the researcher's feedback snapshot. The old LangGraph implementation is useful
source material, but its exact node count and internal topology are not the
north-star product contract.

The graph should include:

- agent stages,
- persona council stages,
- optional model council execution modes,
- gates,
- joins,
- loops,
- approval points,
- possible reroutes,
- failure routes,
- budget posture.

The SDK should productize this graph as structured specs. That means the old
LangGraph should not remain an opaque runtime authority, but the research
method inside it should absolutely be preserved where it matches the desired
scientific workflow: research framing, literature grounding, hypothesis and
goal formalization, theory/experiment execution, evidence synthesis, duality
checking, paper generation, critique, and revision.

The graph should remain connected to execution: when a stage runs, blocks,
reroutes, or is rerun with feedback, the graph node state should change through
events.

## Scientific Workflow Concepts

The product should expose the core scientific mechanisms as understandable
concepts, not hide them as backend trivia.

**Persona councils** challenge the research direction from distinct lenses such
as practical relevance, rigor/novelty, narrative strength, and empirical
grounding. Their verdicts, objections, and synthesized proposal should be
visible as first-class stage outputs.

**Model councils** are a high-stakes execution posture for selected stages:
multiple frontier models attempt the same specialist task, critique each other,
and synthesize a consensus. In the UX, model councils should feel like a
quality/spend mode on a stage rather than a separate subsystem the researcher
must operate manually.

**Duality check** is a required scientific gate before paper/writeup
generation. It should verify that formalized results are both practically
meaningful and technically/empirically defensible. Failing this gate should
create a human decision with clear recovery routes rather than silently
producing a paper.

**Revision from prior artifacts** is a normal campaign continuation pattern.
A researcher should be able to provide a prior paper, feedback, constraints, or
new evidence and then rewind, reroute, or continue the campaign from the
appropriate point.

## Model And Tier Posture

Model choices are product-visible because they affect quality, cost, and trust.
The target defaults can differ from current code, but changes to these defaults
should be intentional and documented.

Target model roles:

| Role | Target default |
|---|---|
| Practical persona | `claude-opus-4-6` |
| Rigor/novelty persona | `gpt-5.4` |
| Narrative persona | `gemini-3.1-pro-preview` |
| Empirical-grounding persona | `claude-opus-4-6` |
| Persona synthesis | `claude-opus-4-6` |
| Duality check | `claude-opus-4-6` |
| Model council members | `claude-opus-4-6`, `gpt-5.4`, `gemini-3.1-pro-preview`, `claude-sonnet-4-6` |
| Model council synthesis | `claude-opus-4-6` |

Target tier language:

| Tier | Product meaning |
|---|---|
| `scaffold` | Zero-spend graph/artifact planning and UI validation. |
| `lean` | Single-model execution for exploratory or low-cost campaigns. |
| `standard` | Persona council plus single-model specialist stages. |
| `serious` | Persona council, duality check, and model councils on selected high-stakes stages. |
| `ultra` | Adds empirical-grounding persona and broad model-council use across critical stages. |

The SDK should expose tier policy cleanly enough that OpenClaude can explain
the quality/cost tradeoff, request approval for upgrades, and apply stage-level
overrides without mutating hidden config files.

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

Events are the product source of truth. The UI, CLI, OpenClaude, optional
OpenClaw wrappers, and tests should reconstruct campaign state from events and
specs.

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
- `CouncilStarted`
- `CouncilVerdictRecorded`
- `DualityCheckCompleted`
- `RouteSelected`
- `StageRerunRequested`
- `CampaignExecutionResumed`
- `CampaignExecutionCheckpointed`
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

Bounded mechanical retries may be automatic when they are cheap,
non-directional, and do not change the scientific meaning of the campaign.
Scientific direction changes, failed gates, expensive work, budget increases,
repair, reroute, and rewind decisions should pause for the researcher, with
OpenClaude helping interpret the evidence and propose safe actions.

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

OpenClaude is the primary local AI coworker for the product. It should become a
natural-language steering layer over the SDK and should not bypass the SDK.

OpenClaude should translate user intent into typed operations:

- append feedback,
- explain stage,
- propose graph change,
- approve/reject decision,
- rerun or rewind stage,
- summarize artifacts,
- inspect budget,
- ask for missing evidence.

Capabilities that were historically described as OpenClaw supervision should be
available through OpenClaude and the SDK: liveness explanation, failure
classification, repair proposals, budget posture, task rewrites, reruns,
reroutes, and artifact summaries. OpenClaw may remain a thin optional wrapper a
researcher can place on top of the local product for convenience. It should not
be core product architecture and must not create another state authority.

## What Must Not Happen

- Do not treat prompt text as the product contract.
- Do not treat arbitrary files as artifacts.
- Do not use generated scaffold files as deliverables.
- Do not discard the proven LangGraph research method.
- Do not treat the exact old LangGraph node roster as the north-star product
  contract.
- Do not keep LangGraph as an opaque product architecture.
- Do not let UI state, status JSON, and campaign events disagree without a
  clear source of truth.
- Do not hide human decisions inside logs or prompts.
- Do not let OpenClaude or optional OpenClaw wrappers mutate state outside typed
  SDK operations.
- Do not expose "runs" as a separate researcher-facing concept when the
  campaign itself is the research attempt.

## Current Deviation From Target

The codebase is closer than it was, but still not fully aligned.

Known deviations:

- Some legacy execution code still exists as reference/proof material, but the
  target is to distill its successful research method into SDK-native graph and
  stage definitions.
- The researcher's feedback snapshot describes the desired scientific workflow
  more directly than the current legacy node roster does.
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
