# Reengineering Storyboard

This storyboard describes how to move from the proof-of-concept codebase to the
V1 product while preserving the successful research behavior and the
researcher's current target-engine intent.

The work should be done in passes. Each pass should leave the codebase in a
working state with tests and documentation that explain the new boundary.

## Pass 0: Requirements And Failure Baseline

Goal:

Define the product contract before moving code.

Codebase after this pass:

```text
docs/agent_reference/README.md
docs/agent_reference/v1_product_requirements.md
docs/agent_reference/current_failure_points.md
docs/agent_reference/reengineering_storyboard.md
docs/research_engine_invariants.md
```

Done when:

```text
V1 user and outcomes are explicit
historical research behavior and researcher-intent preservation are explicit
OpenClaude steering requirement is explicit
failure points are named
next implementation passes are ordered
```

## Pass 1: Stage Contract Registry

Goal:

Extract the historical engine's implicit stage semantics into typed contracts
without changing runtime behavior.

Add:

```text
msc_sdk/stage_contracts.py
consortium/stage_contracts/
```

Each stage contract should include:

```text
id
kind
purpose
inputs
required_artifacts
optional_artifacts
validators
tools
budget_policy
failure_policy
routes
human_pause_policy
diagnostic_runtime_mapping
```

Done when:

```text
all target workflow stages have contracts
legacy reference stages are mapped where they still inform behavior
control nodes have contracts
contracts validate
VS Code graph can show purpose/inputs/outputs/tools/validators
runtime behavior is unchanged
```

## Pass 2: Graph IR Mirrors The Research Workflow

Goal:

Make the product graph an honest representation of the desired scientific
workflow, using the historical engine and [feedback.md](feedback.md) as source
material.

Add graph views:

```text
pipeline graph
control graph
runtime graph
```

Done when:

```text
graph includes council stages, control nodes, duality gate, and revision routes
loops and fan-out/fan-in are represented
graph nodes point to stage contracts
campaign graph matches the target scientific workflow
graph editing can target graph nodes by stable IDs
```

## Pass 3: Runner Emits Campaign Events

Goal:

Connect real execution to local campaign state.

Emit:

```text
CampaignExecutionStarted
GraphNodeStatusChanged
ArtifactIndexed
ValidationPassed
ValidationFailed
ApprovalRequested
InstructionSent
CampaignExecutionCompleted
CampaignExecutionFailed
```

Done when:

```text
starting campaign execution creates campaign execution events
active stage status updates in .msc/campaigns.db
generated artifacts appear in campaign artifacts
campaign execution exit updates runtime graph
legacy run_status.json remains compatibility output
```

## Pass 4: Human Decision And Approval Model

Goal:

Make pause-heavy human-in-the-loop behavior first-class.

Add operations:

```text
request approval
approve
reject
pause
resume
stop
choose recovery path
```

Done when:

```text
critical stages pause by default
stage failure stops and awaits human feedback
budget increases require approval
repair/retry loops require approval
approval history is visible in campaign events
```

## Pass 5: Research SDK And Agent-Facing CLI

Goal:

Expose expressive control operations that OpenClaude can safely call.

Add SDK/CLI operations:

```text
msc campaigns explain-node <campaign> <node>
msc campaigns propose-graph-change ...
msc campaigns approve <approval_id>
msc campaigns reject <approval_id>
msc campaigns pause <campaign>
msc campaigns resume <campaign>
msc campaigns stop <campaign>
msc campaigns reroute <campaign> --from <node> --to <node>
msc campaigns rewrite-stage <campaign> <node> --instruction ...
msc campaigns summarize-artifacts <campaign> ...
```

Done when:

```text
all steering actions are typed operations
operations create events
operations enforce budget and approval policy
OpenClaude can use commands without direct state mutation
```

## Pass 6: OpenClaude Steering Harness

Goal:

Make natural-language steering the primary control interface while preserving
state, approval, and budget boundaries.

OpenClaude should:

```text
read campaign state
explain graph nodes
summarize artifacts
propose graph changes
send steering instructions
request approvals
execute approved SDK/CLI operations
```

OpenClaude must not:

```text
bypass budget policy
start real spend without confirmation
mutate campaign DB directly
rewrite arbitrary artifacts without an event
launch a parallel orchestration path
```

Done when:

```text
natural-language steering maps to typed operations
unsafe actions become approval requests
OpenClaude actions are visible in event history
```

## Pass 7: VS Code Research Cockpit

Goal:

Make the extension the local product surface for campaign inspection and
control.

Views:

```text
Campaign Home
Graph
Steer
Artifacts
Settings
```

Done when:

```text
home shows campaign status, budget, artifacts, active approvals
graph shows pipeline/control/runtime modes
node details show contracts, status, artifacts, logs, routes, budget
artifact library previews all legacy artifact types
steer tab reflects OpenClaude and low-level run state
settings show readiness, model, budget, DB path
```

## Pass 8: Full Workflow Local Integration Test

Goal:

Prove that the full target research workflow can run through the product
architecture.

Test path:

```text
create campaign
approve graph
start campaign execution locally
pause at critical stage
steer through SDK/CLI
resume
index artifacts
fail a controlled stage and await feedback
pass the required duality gate before writeup
produce paper-like artifacts
```

Done when:

```text
runtime graph updates during real execution
paper-like artifacts are previewable
failure handling stops safely
events reconstruct the run
OpenClaude can explain what happened
```

## Pass 9: Cost And Lean Engine Work

Goal:

Only after V1 works, explore cheaper distilled pipelines.

Possible later variants:

```text
literature-only
experiment-design-only
paper-rewrite-only
cheap empirical scaffold
small-model local drafting
```

This is explicitly downstream. V1 optimizes trust and capability, not minimum
cost.

## Commit Discipline

Each pass should update:

```text
code
tests
docs/agent_reference
manual acceptance checklist
```

No pass should depend on OpenClaude bypassing the SDK/CLI. No pass should
weaken the ability to run the full target research workflow locally.
