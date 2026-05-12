# Current Failure Points

This document names the critical flaws that prevent the V1 product requirements
from being implemented elegantly. These are not reasons to discard the
prototype. They are the areas where the proof-of-concept needs to be distilled
into product architecture.

## 1. Product Graph And Runtime Graph Are Different Objects

Symptoms:

```text
the VS Code campaign graph is scaffolded from campaign templates
msc run executes the historical LangGraph pipeline
runtime stage progress does not update campaign graph nodes
control nodes are not represented consistently in product state
```

Impact:

The UI cannot honestly explain what the engine is doing. A campaign can show a
planned graph while the runner executes a related but separate workflow.

Required fix:

Create a shared graph IR or faithful runtime graph projection that represents
pipeline nodes, control nodes, routes, loops, approvals, and runtime status.

## 2. Stage Contracts Are Implicit

Symptoms:

```text
inputs and outputs are hidden in prompts
validators are scattered through graph.py and supervision code
tools are bound inside agent modules
artifact expectations are partly encoded by filename conventions
```

Impact:

The product cannot display what a node requires, why it failed, or what it will
produce before running.

Required fix:

Introduce typed stage contracts with purpose, inputs, outputs, validators,
tools, budget policy, failure policy, and next routes.

## 3. Completion Semantics Are Fragmented

Symptoms:

```text
stage success may come from agent output
artifact existence
status JSON
validator result
review verdict
run completion
```

Impact:

The system cannot reliably tell the researcher whether a stage is truly done or
only verbally complete.

Required fix:

Centralize completion semantics:

```text
stage complete = required artifacts exist + validators pass + status event
```

## 4. Runtime Events Are Not Yet The Product Source Of Truth

Symptoms:

```text
CampaignStore exists
runner does not emit campaign events
stage transitions are written to run_status.json, not campaign state
generated artifacts are not indexed live into campaign artifacts
```

Impact:

Live graph, artifact library, steering history, approvals, and budget UI cannot
be trusted as a complete campaign record.

Required fix:

Bridge the runner to the campaign event store:

```text
RunStarted
GraphNodeStatusChanged
ArtifactIndexed
ValidationPassed
ValidationFailed
ApprovalRequested
InstructionSent
RunExited
```

## 5. Human-In-The-Loop Pauses Are Not First-Class

Symptoms:

```text
some gates exist
some steering exists
approval policy is not consistently modeled
pause/resume is not a typed campaign operation
```

Impact:

The system cannot safely pause more often than not, which is a V1 requirement.

Required fix:

Model pauses and approvals as product primitives. Every critical pause should
create an approval or decision event and expose safe next actions.

## 6. OpenClaude Is Not Yet A Steering Harness Over The SDK

Symptoms:

```text
OpenClaude readiness exists as placeholder context
natural-language steering is not mapped to typed campaign operations
OpenClaude could become a parallel pathway if integrated too early
```

Impact:

The researcher cannot yet steer primarily through natural language without
risking state drift or bypassed policy.

Required fix:

Define an agent-facing CLI/SDK command surface for steering:

```text
pause
resume
stop
reroute
rewrite-stage
approve
reject
request-repair
change-budget
change-tier
summarize-artifacts
explain-node
```

Then bind OpenClaude to those operations.

## 7. Failure Handling Defaults Toward Internal Recovery

Symptoms:

```text
repair loops exist
retry loops exist
validation can route back automatically
some failures become prompts to agents instead of user decisions
```

Impact:

V1 requires stopping on stage failure and awaiting human feedback. Current
behavior is too autonomous for a researcher-facing product.

Required fix:

Convert failures into explicit `ApprovalRequested` or `HumanDecisionRequired`
events with suggested recovery options.

## 8. The Full Historical Engine Is Too Entangled

Symptoms:

```text
one large graph.py contains stage roster, routers, gates, loops, special modes,
review logic, and product-relevant semantics
agent modules bind prompts and tools directly
runner handles setup, execution, status, steering, logging, and summaries
```

Impact:

It is hard to preserve the engine while exposing it cleanly. Product behavior
cannot be tested without dragging in runtime complexity.

Required fix:

Extract contracts and runtime events without rewriting the engine first. Preserve
behavior while gradually separating:

```text
stage definitions
graph/control IR
runner event emission
agent execution adapters
validators
product APIs
```

## 9. Budget Policy Is Not Fully Graph-Aware

Symptoms:

```text
budget tracking exists
per-stage policies are not always explicit
budget increases are not approval events
graph edits do not carry spend implications
```

Impact:

Researchers cannot reason about risk before approving work.

Required fix:

Attach budget policy to campaigns, stages, graph edits, and run actions. Show
estimated and actual spend in graph/runtime views.

## 10. Artifact Provenance Is Under-Modeled

Symptoms:

```text
artifacts are files
some manifests/status files exist
producer node, validator status, claim support, and failure context are not
centralized
```

Impact:

The artifact library cannot answer why an artifact exists, whether it is valid,
or what claim it supports.

Required fix:

Index artifacts into campaign state with producer, stage, type, status,
validator results, and relationships to claims/evidence.

## 11. Graph Editing Has No Safe Operation Model

Symptoms:

```text
graph editing is not yet exposed
runtime graph changes are not proposals/events
rerouting is encoded inside graph logic, not product operations
```

Impact:

V1 cannot support intuitive graph editing without risking inconsistent runtime
state.

Required fix:

Represent graph edits as typed proposals and approvals:

```text
GraphChangeProposed
GraphChangeApproved
GraphVersionCreated
GraphVersionActivated
```

## 12. Tests Do Not Yet Protect Product Semantics

Symptoms:

```text
tests cover pieces of CLI/UI/store
no full local campaign run event bridge test
no graph contract validation suite
no OpenClaude steering command contract tests
```

Impact:

The overhaul can regress the behaviors that made the prototype valuable.

Required fix:

Add contract tests for stage definitions, graph IR, event emission, artifact
indexing, approval pauses, and OpenClaude-safe CLI operations.

## 13. Runs Are Not Isolated As First-Class Views

Symptoms:

```text
the graph view overlays current run progress onto campaign-level state
past runs that reached later stages can make the current run look further along
there is no dedicated per-run section or run selector
artifact status does not clearly distinguish latest run, selected run, and all-time campaign history
```

Impact:

Researchers cannot tell what happened in the current execution versus what was
produced by an earlier attempt. This makes integration testing confusing and
can make failed or partial runs look healthier than they are.

Required fix:

Make runs first-class product objects in the cockpit. The campaign graph should
support a selected run overlay, a latest-run default, and a separate campaign
history/timeline view. Runtime status must be scoped by `run_id`.

## 14. Human Decision Required Has No Product Control Surface

Symptoms:

```text
the runner can mark a campaign or stage as human_decision_required
the VS Code UI does not expose approve/reject/resume/reroute/rewrite controls
there is no ergonomic way for OpenClaude or the researcher to move the run forward
failure recovery remains an internal implementation concept rather than a user decision
```

Impact:

The product can correctly stop, but the researcher cannot yet steer it forward.
This violates the V1 human-in-the-loop requirement.

Required fix:

Expose decision objects and safe next actions in both CLI and VS Code:

```text
approve
reject
resume
reroute
rewrite-stage
rerun-stage
abort
```

OpenClaude should call these same operations rather than bypassing campaign
state.

## 15. Artifact Catalog Is Too Noisy For Researchers

Symptoms:

```text
artifact lists include system prompts, run metadata, generated intermediate data, logs, and summaries
deliverables are not separated from diagnostics
required node outputs are visually mixed with optional/supporting files
the artifact library does not yet express audience: researcher deliverable vs system trace
```

Impact:

Researchers expect concrete deliverables from each node: proposal, literature
matrix, hypotheses, experiment plan, proofs, reports, writeup, review, and
validation results. The current catalog exposes too much machinery and makes
the product feel less trustworthy.

Required fix:

Classify artifacts by audience and role:

```text
deliverable
evidence
diagnostic
log
prompt
system_state
```

Default the UI to deliverables and required outputs. Keep diagnostics available
behind filters or an advanced/system view.

## Summary

The central failure is not messy code by itself. The central failure is that the
research engine's real semantics are implicit. V1 requires those semantics to
become explicit product objects.

## First-Principles Rebuild Direction

The clean replacement architecture is now specified in
[`first_principles_rebuild.md`](first_principles_rebuild.md). The new center of
gravity is not the historical LangGraph workflow. It is the typed runtime
kernel:

```text
RunSpec -> GraphSpec -> StageSpec -> RuntimeContext -> ArtifactRecord
        -> ValidationResult -> EventRecord -> ReadModel
```

New implementation work should target `msc_sdk/kernel/` first and treat the
historical runner, graph, filesystem conventions, VS Code state, and OpenClaude
harness as adapters rather than sources of product truth.

## Kernel Rebuild Status

The kernel now owns the fundamental semantics that used to be fragmented across
the prototype:

```text
completion = required artifacts + validators + events
runtime state = event projection
pause/resume = typed decisions + checkpoints
budget/model/tool policy = RuntimeContext-enforced
artifact truth = schema validation + claim/evidence links
stage preconditions = InputSpec resolution
stage implementation = adapter registry binding
product state = CampaignReadModel projection from KernelRunReadModel
campaign graph/artifact views = projection from campaign events
```

The remaining work is no longer to patch these primitives into the old graph.
It is to express the full research workflow as `GraphSpec`/`StageSpec`
contracts, bind production adapters for each stage, and retire legacy views once
they are served by kernel events.

## Reengineering Cutover Status

The contraction pass has begun:

```text
historical contracts -> compiled kernel GraphSpec
campaign graph JSON -> kernel graph projection
campaign graph/artifact reads -> campaign event projection
legacy optional dependencies -> quarantined test boundary
full scaffold happy path -> kernel-native adapters
StageContract graph builders -> retired
```

The LangGraph implementation should now be treated as proof/reference material.
It may inform adapters, but it should not own product graph state, completion
semantics, or researcher-facing runtime state.
