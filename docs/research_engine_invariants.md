# Research Engine Invariants

This document defines what must survive the major product overhaul of
PoggioAI/MSc. The existing codebase proved that a multi-agent research system
can produce useful work, but much of the implementation was accumulated under
proof-of-concept pressure. The redesign should preserve the research kernel,
not the accidental architecture around it.

## Core Thesis

A research engine is a stateful artifact-producing workflow where specialist
agents transform research objects, validators decide whether those objects are
good enough, and bounded loops improve weak work under budget and human
control.

The product should not be designed around agent names, scripts, YAML files, or
one large graph module. It should be designed around explicit research
transitions:

```text
given current research state,
perform a bounded expert operation,
produce durable artifacts,
validate those artifacts,
record the event,
route to the next transition.
```

## What Made The Prototype Special

The powerful part of the proof of concept was not that it had many agents. It
was that it forced research through a structured process:

```text
question
  -> debate
  -> literature grounding
  -> hypotheses and goals
  -> execution plan
  -> theory and/or empirical tracks
  -> evidence synthesis
  -> writeup
  -> critique
  -> repair or finish
```

That process should become a clean product architecture.

## Product Primitives

The redesigned system should be built from five durable primitives.

### Campaign

A campaign is the local research container. It owns the objective, budget
policy, planned graph, runtime graph, artifacts, approvals, runs, events, and
steering history.

Campaign state should be local-first and repo-native:

```text
.msc/campaigns.db
.msc/events/campaigns.jsonl
.msc/snapshots/<campaign_id>.graph.json
campaigns/<campaign_id>/campaign.json
campaigns/<campaign_id>/graph.json
campaigns/<campaign_id>/artifacts.json
results/<campaign_id>/...
```

SQLite is the operational index. Normal files remain the visible research
output. Bundle JSON is import/export/snapshot, not the mutable brain.

### Graph

The graph is the research plan and runtime control surface. It should support
three views:

```text
Pipeline graph:
  the readable happy-path research process

Control graph:
  gates, routers, loops, retries, approvals, fan-out, fan-in

Runtime graph:
  what actually happened in this run, including skipped nodes, failures,
  artifacts, spend, and human interventions
```

The historical engine is not a pure DAG. It is a directed state machine with a
DAG-like happy path plus intentional feedback loops. The product must describe
that honestly.

### Stage

A stage is a typed research operation. Agents are one possible implementation
of a stage, not the product abstraction itself.

Each stage should declare:

```text
purpose
inputs
required outputs
optional outputs
validators
allowed tools
budget policy
failure policy
next routes
human approval requirements
```

No stage should be considered complete because an agent says it is complete. A
stage is complete when its required artifacts exist and validate.

### Artifact

Artifacts are first-class research objects. They make the work inspectable,
resumable, reviewable, and steerable.

Examples:

```text
research_proposal.md
literature_matrix.json
brainstorm.md
research_goals.json
track_decomposition.json
experiment_plan.json
experiment_results.json
paper.tex
review_verdict.json
```

Every meaningful artifact should have provenance:

```text
producer stage
path
kind
schema or validator
status
claim/evidence relationship when applicable
```

### Event

Every meaningful mutation should be an event:

```text
CampaignCreated
ObjectiveUpdated
BudgetPolicySet
GraphPlanned
GraphApproved
RunStarted
GraphNodeStatusChanged
ArtifactDeclared
ArtifactIndexed
InstructionSent
ApprovalRequested
ApprovalDecided
RunExited
CampaignExported
```

Events make the system auditable, recoverable, replayable, and safe for
assistant steering.

## Invariants To Preserve

### 1. Research State Is Central

There must be one durable campaign/run state that records objective, graph,
artifacts, validation results, budget, approvals, steering messages, and
runtime events.

Agents, files, and chat history are not source-of-truth by themselves.

### 2. Artifacts Are Completion Evidence

The engine works because agents externalize reasoning into files. Preserve the
rule:

```text
stage completion = required artifacts exist + validators pass
```

Do not trust unstructured final messages as completion evidence.

### 3. Stage Contracts Are Explicit

The prototype hid many contracts in prompts, file naming conventions, and gate
code. The redesign should make contracts explicit and inspectable.

The VS Code UI should be able to show a researcher what a stage expects before
the stage runs.

### 4. Validators Are Peers To Agents

Validation gates are part of the intelligence. They prevent fluent but weak
research output from flowing downstream.

Preserve validators for:

```text
literature feasibility
brainstorm structure
track decomposition
experiment completion
artifact completeness
claim traceability
paper quality
review verdict
budget limits
approval policy
```

### 5. Loops Are Explicit And Bounded

Research is not acyclic. Loops are allowed, but every loop must have:

```text
entry condition
max retries
budget policy
state diff
exit condition
event trail
```

Unbounded autonomous repair is not a product behavior.

### 6. Specialist Power Comes From Tool Boundaries

An agent role is not just a name. It is:

```text
prompt + tools + artifact contract + validators + budget policy
```

Preserve specialist tool boundaries for literature search, experimentation,
proof work, code execution, writing, review, and artifact inspection.

### 7. Memory Is Structured

The system must remember decisions, not just messages:

```text
why a hypothesis survived
which papers were considered
which experiments failed
which reviewer concern remains unresolved
which artifact supports which claim
```

Long prompt history is not enough.

### 8. Budget And Approval Are Control Logic

Budget is not only billing. It is a safety and product trust mechanism.

Preserve:

```text
campaign budget caps
per-stage budget caps
tier policy
approval before expensive transitions
explicit real-run confirmation
zero-spend scaffold mode
auditable spend events
```

### 9. Steering Mutates State Through Events

Human steering and OpenClaude steering must not bypass campaign policy or mutate
arbitrary files directly.

Steering should create typed events and proposals:

```text
InstructionSent
ObjectiveUpdated
GraphChangeProposed
GraphChangeApproved
BudgetIncreaseRequested
ArtifactRevisionRequested
RunPaused
RunResumed
```

### 10. The System Must Explain Itself

A researcher should always be able to answer:

```text
What is the engine trying to do?
Why is this node next?
What evidence exists?
What is missing?
What did this cost?
What can I steer?
What happens if I approve this?
```

If the graph cannot explain itself, the product is not ready.

## What Not To Preserve

The following are implementation artifacts, not invariants:

```text
YAML as operational campaign state
implicit file naming as control logic
agent names as the primary product abstraction
one giant graph.py as the product brain
hidden prompt-level contracts
unbounded repair loops
scattered status JSON as source of truth
campaign orchestration detached from product state
OpenClaude as a parallel control plane
```

Compatibility utilities may remain where useful, but they should not define
the next architecture.

## Redesign Direction

The next architecture should treat the VS Code extension as a local research
cockpit:

```text
Campaign Home:
  what am I studying, what is active, what has it produced?

Graph:
  what is planned, what happened, what is blocked, what can be approved?

Artifacts:
  what evidence exists, where did it come from, what claims does it support?

Steering:
  how can I redirect the research without breaking auditability?
```

The old engine should be mined for proven research transitions, validators,
toolkits, and prompts. Those pieces should be reassembled as typed stages over
the campaign state/event model.

## Design Test

Any major redesign proposal should be rejected unless it preserves these
properties:

```text
durable local state
artifact-first completion
typed stage contracts
bounded loops
observable validation
budget/approval policy
event-sourced steering
normal repo files for research outputs
explainable graph UI
```

The goal is not to make the prototype prettier. The goal is to make the
research engine legible, scalable, and trustworthy while preserving the
behaviors that made the prototype worth saving.
