# V1 Product Requirements

## Product Promise

V1 is a local-first coresearch cockpit for technically capable researchers. It
should let a researcher create and steer research campaigns where agents drive
substantial parts of theory, coding, literature review, writeup, critique, and
reasoning while the human remains firmly in the loop.

The closest product analogy is a competent engineer using Codex to code at much
higher throughput while still understanding, reviewing, and directing the work.
PoggioAI/MSc should do that for research.

## Primary User

The primary user is a researcher with technical ability who can forward-drive
their own research, inspect code and artifacts, make scientific judgments, and
intervene when the system needs direction.

They want the system to increase output, not replace their agency.

## Critical V1 Outcome

The critical requirement is reliable production of paper-like artifacts that a
researcher can evaluate.

V1 must preserve the full historical research engine because the current engine
has demonstrated useful output quality. Later work may produce leaner or cheaper
engines, but V1 is not a minimization exercise.

## Required Product Shape

V1 should expose three cooperating surfaces:

```text
VS Code Extension:
  local research cockpit, graph, artifacts, campaign state, settings

Research SDK / CLI:
  typed, auditable controls for runs, steering, graph changes, approvals,
  artifacts, budgets, and failure handling

OpenClaude Harness:
  natural-language steering layer over the SDK/CLI, not a parallel control
  plane
```

OpenClaude is part of V1 steering, but it must operate through campaign APIs,
events, approvals, and budget policy.

## User Workflows

### Create Campaign

The researcher can create a campaign with:

```text
title
objective
budget policy
model/tier policy
output format
graph template
human-in-the-loop policy
```

The full planned research graph is visible before any run begins.

### Inspect Planned Graph

The graph must include both research stages and control nodes:

```text
agent/research nodes
validation gates
routers
fan-out/fan-in tracks
approval pauses
failure stops
repair/retry loops
```

Each node must explain:

```text
purpose
inputs
outputs
artifact contracts
validators
tools
budget policy
possible next routes
human decision points
```

### Edit Graph

V1 should support intuitive graph editing before execution. Examples:

```text
disable or enable math/theory track
pause before selected stages
change budget/tier on a stage
skip a noncritical stage
reroute after a failed gate
request rewrite or re-run of a stage
add human notes to stage instructions
```

Graph edits must be represented as typed events/proposals and should not mutate
runtime state silently.

### Run Full Historical Engine

V1 must be able to run the full historical research engine locally. The product
graph and runtime graph must reflect the actual engine, not a separate mock.

The system should keep all artifact types currently produced by the legacy
engine and index them into campaigns.

### Human-In-The-Loop Pauses

V1 should pause more often than not at scientifically or financially important
points.

Required pause points:

```text
before real spend begins
after graph planning and before approval
after persona/debate research framing
after literature feasibility assessment
after hypotheses/goals are formalized
before expensive experiment execution
before theory/proof branch escalation
after experiment results synthesis
before writeup generation
after reviewer verdict
after any failed stage
before repair/retry loops
before budget increases
before graph reroutes
```

The default posture is conservative: when uncertain, stop and ask.

### Artifact Evaluation

All current research-engine artifacts should remain visible and inspectable in
campaigns, including markdown, JSON, LaTeX, PDFs, logs, code, figures, review
verdicts, experiment metadata, and budget artifacts.

The artifact library should support:

```text
preview
open in VS Code
filter by stage/type/status
show missing required artifacts
show producer node
show validation status
show claim/evidence links when available
```

### OpenClaude Steering

The researcher should primarily steer through natural language using the
OpenClaude harness. OpenClaude must translate requests into SDK/CLI operations,
for example:

```text
pause this run
stop after the current stage
reroute to literature review
rewrite the research plan with this constraint
raise the budget for experimentation to $X
disable math agents
ask the reviewer to focus on novelty
re-run experiment design with a simpler baseline
summarize all artifacts supporting claim Y
```

OpenClaude must not bypass approval, budget, or campaign state. It should
propose or execute typed operations depending on policy.

### Failure Handling

When a stage fails, V1 should stop and await human feedback.

The product should show:

```text
failed node
failure reason
logs
missing/invalid artifacts
last model/tool action when available
suggested recovery actions
budget consumed
safe next options
```

Automatic repair should not proceed by default in V1.

## Non-Goals For V1

```text
campaign portability between machines
hosted web application
Slack interface
lean replacement engine
full external OpenClaw orchestration
unattended autonomous repair
cheap-only research mode
```

These can be revisited after the local product loop is stable.

## Acceptance Criteria

V1 is credible when a researcher can:

```text
create a campaign
inspect the full planned graph and control nodes
approve the graph
start the full historical engine
watch runtime graph updates
pause at critical decisions
steer through OpenClaude
inspect all generated artifacts
see why a stage passed or failed
stop on failure and choose a recovery path
produce paper-like artifacts suitable for human evaluation
```

The key bar is not a beautiful dashboard. The key bar is a trustworthy research
machine that exposes its reasoning, artifacts, costs, and control points.
