# First-Principles Rebuild

This document defines the target architecture without preserving historical
implementation choices. The goal is the same end result as the current system:
a local research machine that can plan, execute, validate, steer, and produce
paper-like artifacts. The implementation should no longer be organized around
the accumulated prototype.

## Core Principle

The research system is not an agent graph. It is a typed artifact-producing
runtime:

```text
RunSpec
  -> GraphSpec
  -> StageSpec
  -> RuntimeContext
  -> ArtifactRecord
  -> ValidationResult
  -> EventRecord
  -> ReadModel
```

Agents, tools, LangGraph, OpenClaude, VS Code, and CLI commands are adapters
around this kernel. They are not the semantic center.

## Non-Negotiable Rules

1. No product feature may infer truth from free-form agent output.
2. No product feature may depend on ambient environment variables as its primary
   API.
3. No stage may be complete unless required artifacts exist and validators pass.
4. No steering action may mutate files or runtime state without an event.
5. No graph route may exist only inside prompt text or ad hoc Python branches.
6. No UI surface may invent state that is not present in read models.
7. No budget, retry, loop, approval, or repair behavior may be implicit.

## Target Runtime Objects

### RunSpec

Immutable input to one execution:

```text
run id
campaign id
objective
graph spec
workspace
budget policy
model/tool policy
human approval policy
metadata
```

The runner consumes a `RunSpec`. It should not read mutable project config to
discover semantics mid-run.

### GraphSpec

Typed research state machine:

```text
stages
entry stage
routes
branch/join relationships
loop bounds
failure routes
approval pauses
```

The graph is compiled from stage contracts. It is not separately scaffolded for
the UI.

### StageSpec

Typed research operation:

```text
id
kind
purpose
inputs
outputs
validators
tools
budget policy
failure policy
routes
pause policy
```

An agent is only one possible implementation of a stage handler.

### RuntimeContext

Explicit context passed to stage handlers:

```text
run spec
stage spec
artifact repository
validator registry
event bus
budget ledger
decision queue
```

This replaces implicit `os.getenv(...)`, global `.llm_config.yaml`, scattered
status JSON, and ad hoc workspace path conventions as the primary runtime API.

### ArtifactRecord

Durable research object:

```text
stage id
path
kind
role
required flag
checksum
size
producer
schema id
validation status
claim/evidence links
```

Files remain visible in the repo, but the artifact record is the product object.

### ValidationResult

Validator output must be structured:

```text
validator id
passed
message
details
artifact ids
suggested recovery actions
```

Validators are peers to agents, not cleanup utilities.

### EventRecord

Every mutation is an event:

```text
RunStarted
StageStarted
ArtifactWritten
ArtifactIndexed
ValidationPassed
ValidationFailed
HumanDecisionRequired
ApprovalDecided
StageCompleted
RunCompleted
RunFailed
```

Events are the audit trail and the source for product read models.

## Dependency Direction

Allowed:

```text
VS Code -> CLI/SDK -> Kernel read models / operations
OpenClaude -> CLI/SDK -> Kernel operations
CLI -> Kernel
Runner -> Kernel
Stage handlers -> RuntimeContext
Validators -> RuntimeContext + ArtifactRecord
```

Forbidden:

```text
VS Code -> graph.py internals
OpenClaude -> direct file mutation
Stage handlers -> ambient campaign env vars
Validators -> prompt text as contract
Kernel -> historical graph module
Product state -> run_status.json as source of truth
```

## First Code Slice

The new first-principles kernel begins in:

```text
msc_sdk/kernel/
```

It is intentionally free of LangGraph, LiteLLM, Click, campaign SQLite, and the
historical `consortium.graph` module. Its tests live in:

```text
tests/test_research_kernel.py
```

This first slice proves the central invariant:

```text
stage complete = required artifacts exist + declared validators pass + events emitted
```

It also establishes two more rules in code:

```text
product read state = projection of kernel events
human pause policy = executable kernel behavior
branches, joins, and loops = explicit kernel scheduling
budget policy = enforced by RuntimeContext charges and kernel ledger
human decisions = typed queue records with approve/reject events
```

The read-model projector lives beside the kernel so VS Code, CLI, OpenClaude,
and tests can all consume the same current-state view instead of inferring state
from logs, status files, subprocesses, or directory scans.

The scheduler supports deterministic branch fan-out, join barriers, route
conditions, and bounded loops. When a loop limit is reached, the kernel emits a
human decision event instead of continuing autonomously.

Budget spend is charged through `RuntimeContext.charge_budget(...)`. Run and
stage caps are enforced by the kernel ledger, and budget failures produce
`BudgetExceeded` plus `HumanDecisionRequired` events.

Human stops create durable decision records. A decision has an id, stage, reason,
allowed actions, status, actor, and timestamps. Approval and rejection emit
events that project back into the canonical run read model.

## Rebuild Order

1. Expand `msc_sdk.kernel` until it can express the ideal product semantics.
2. Add model/tool policy enforcement.
3. Add resume semantics on top of approved decisions.
4. Define the ideal campaign graph in contracts, not in `graph.py`.
5. Write pure stage handlers for planning, literature, hypothesis generation,
   experiment design, execution, synthesis, writeup, and review.
6. Bind specialist agents as implementations of stage handlers.
7. Bind OpenClaude and VS Code only to kernel operations/read models.
8. Retire legacy compatibility paths after the new kernel can produce the full
   research artifact set.

## Remaining Fundamental Issues

The major architectural risks still to remove are:

1. Model/tool policy is declared but not yet enforced by the kernel.
2. Stage handlers are not yet isolated by declared tool permissions.
3. Artifacts do not yet include claim/evidence relationships, schema validation,
   or explicit upstream input dependencies.
4. Approved decisions do not yet resume a paused run from a durable checkpoint.
5. Agent implementations are not yet bound to `StageSpec` as swappable adapters.
6. The product shell still needs to consume kernel read models directly rather
   than historical campaign-store projections.

## Design Standard

When adding any future feature, ask:

```text
Which typed object owns this behavior?
Which event records the mutation?
Which validator decides success?
Which artifact proves completion?
Which read model should the UI render?
```

If the answer is "the prompt", "the filesystem", "graph.py", "the UI", or
"whatever the runner currently does", the design is not clean enough.
