# Stage 4 Single Orchestrator Harness

This document defines the single orchestration harness that should sit between
the SDK/CLI product surface and the protected research kernel.

Status: drafted on 2026-05-10. Pending user review before implementation.

## Decision

Build one harness, not one harness per interface.

The harness should begin as an SDK-backed local orchestration library with a CLI
action runner. A long-lived daemon can be added later as an adapter for VS Code
live views, event streaming, or hosted workflows, but it should not be the first
required control-plane shape.

This keeps the early system simple enough for Codex to validate directly while
preserving a clean upgrade path for OpenClaude, VS Code, optional OpenClaw, and
future web surfaces.

## Rule

The harness may coordinate and supervise research work. It must not become the
research engine.

Allowed:

- launch existing preserved entry points
- run dry-runs and preflight checks
- supervise process and SLURM state
- read status, logs, budgets, and artifacts
- index derived manifests/read models
- emit events
- enforce capabilities and confirmations
- route human feedback into approved iteration/revision flows

Not allowed without explicit user approval:

- edit prompts
- rewrite LangGraph nodes, edges, routers, gates, retries, or validators
- alter stage order
- alter model policy or budget semantics
- alter checkpoint/state semantics
- reinterpret artifact completion
- mutate generated papers in place
- invent new research retry behavior

## Harness Position

```text
VS Code / OpenClaude / optional OpenClaw / future webapp
                    |
                    v
           SDK + expressive CLI
                    |
                    v
       single orchestrator harness
                    |
        +-----------+-----------+
        |                       |
        v                       v
preserved current entry     production artifact
points and kernel           manifests/read models
```

The harness is the only product-shell component allowed to decide how to invoke
current runtime entry points. User interfaces and agents should call the SDK/CLI
and should not learn protected internals.

## Responsibilities

### Lifecycle Coordination

The harness owns lifecycle requests at the product layer:

- prepare run
- dry-run launch
- launch run
- resume run
- prepare campaign
- validate campaign
- launch campaign stage
- approve or reject generated plan
- repair failed stage
- abort or cancel where supported
- archive through approved current behavior

Every mutation goes through capability and confirmation checks.

### Supervision

The harness observes liveness without changing kernel behavior:

- process PID state
- SLURM job state
- log recency
- workspace activity
- `.progress_heartbeat`
- budget ledger updates
- current status files

Supervision output should be evidence-based. If status is inferred, the output
must say so.

### Artifact And Read-Model Coordination

The harness coordinates derived indexes:

- call the Stage 2 importer
- refresh manifests
- produce graph read models
- produce artifact browser read models
- produce operator summaries for OpenClaude/OpenClaw

It must not make derived indexes the scientific source of truth. Raw workspaces
and protected artifact contracts remain authoritative.

### Event And Audit

The harness emits append-only events for:

- observations
- dry-run requests
- confirmation requests
- confirmed mutations
- denied actions
- launched processes
- status transitions
- artifact indexing
- feedback submissions
- revision requests
- failures and repair attempts

Events should include actor, capability, confirmation id when relevant, target,
source command, and a concise summary.

### Capability Enforcement

The harness enforces Stage 3 capabilities:

- read profiles can inspect but cannot mutate
- indexing writes only to approved derived index locations
- feedback writes are append-only
- launch/repair/resume/approval/archive/config changes require confirmation
- OpenClaw defaults to read-only plus explicitly confirmed actions
- OpenClaude v1 defaults to read plus append-only feedback and confirmed
  mutations

### Feedback Routing

The harness may turn human feedback into a new revision/iteration request. It
must link the request to the prior run/artifacts and route to an approved
pipeline start stage.

It must not overwrite historical `final_paper.*`, `review_verdict.json`,
revision logs, or other source artifacts.

## First Implementation Shape

The first implementation should be library plus CLI runner:

- Python API in the SDK for orchestrator operations.
- CLI commands expose the same operations through JSON.
- Confirmed mutation commands invoke existing preserved entry points.
- Read-only operations can run synchronously.
- Long-running launches return process/job/workspace references.
- Events are written to a project-local append-only event log.

Recommended event location:

- `.msc/events.jsonl` for project-local development, or
- `.msc_index/events.jsonl` if using an external index directory

Recommended action request location:

- `.msc/action_requests/`

Recommended derived index location:

- `.msc_index/`

These names are implementation candidates, not yet runtime commitments.

## Daemon Adapter Later

A daemon should be added only when a clear product need appears:

- VS Code needs low-latency live event streaming.
- OpenClaude needs a stable local RPC endpoint instead of repeated CLI calls.
- Multiple clients need coordinated access to one action queue.
- Hosted/web mode requires authentication and session management.

Daemon constraints:

- loopback-local by default
- authenticated before any nonlocal access
- no broad shell endpoint
- same SDK/harness/capability code path as CLI
- same confirmation tokens
- same event log
- safe degradation when daemon is down

If the daemon is down, running campaigns should continue. Users and agents
should still be able to inspect status through CLI file readers and current
entry points.

## Current Entry Points To Wrap

The harness should initially wrap these rather than replacing them:

- `msc run ...`
- `msc resume ...`
- `msc status`
- `msc logs`
- `msc budget`
- `msc campaign ...`
- `python -m consortium.runner ...`
- `python scripts/campaign_heartbeat.py --campaign ...`
- `python scripts/campaign_cli.py --campaign ... status`
- `python scripts/campaign_cli.py --campaign ... stage-logs`
- `python scripts/campaign_cli.py --campaign ... stage-artifacts`
- `python scripts/campaign_cli.py --campaign ... budget`
- `python scripts/campaign_cli.py --campaign ... launchable`
- `python scripts/campaign_cli.py --campaign ... approve-plan`
- `python scripts/campaign_cli.py --campaign ... reject-plan`
- `python scripts/campaign_cli.py --campaign ... launch`
- `python scripts/campaign_cli.py --campaign ... repair`
- `python scripts/campaign_cli.py --campaign ... archive`

Replacement is allowed later only after SDK/CLI parity tests prove the new
surface explains the same behavior.

## Action Flow

### Read-Only Flow

1. Caller requests status, graph, logs, budget, artifacts, or events.
2. SDK/CLI calls harness read operation.
3. Harness reads current files and/or derived manifests.
4. Harness returns structured output with provenance.
5. No confirmation is required.

### Mutating Flow

1. Caller requests a mutating operation with `--dry-run` or no confirmation.
2. Harness validates target, capability, state, and likely risk.
3. Harness creates an action request with a confirmation token.
4. User confirms explicitly.
5. Harness executes through the preserved current entry point.
6. Harness emits an event with command, actor, target, and result.

### Launch Flow

1. Validate task/campaign/stage references.
2. Check capability and confirmation.
3. Run preflight/dry-run where supported.
4. Invoke current entry point.
5. Capture process id, SLURM id, workspace path, stdout/stderr paths when
   available.
6. Emit launch event.
7. Refresh read model.

### Repair Flow

1. Confirm target stage is failed or repairing.
2. Require repair capability and confirmation.
3. Invoke current repair entry point.
4. Preserve repair logs and status.
5. Emit repair event.
6. Do not invent repair actions outside current repair behavior.

## Interfaces

The harness should expose stable SDK functions first. CLI commands should wrap
these functions.

Candidate SDK object:

```python
class Orchestrator:
    def inspect_project(self) -> ProjectSummary: ...
    def status(self, target: TargetRef) -> StatusSummary: ...
    def graph(self, target: TargetRef) -> GraphReadModel: ...
    def artifacts(self, target: TargetRef) -> ArtifactTree: ...
    def logs(self, target: TargetRef, tail: int = 200) -> LogSummary: ...
    def budget(self, target: TargetRef) -> BudgetSummary: ...
    def request_action(self, action: ActionRequest) -> ConfirmationRequest: ...
    def execute_action(self, request_id: str, confirmation: str) -> ActionResult: ...
```

Candidate CLI families are the Stage 3 command families. Stage 4 should not add
a separate competing CLI namespace unless implementation shows a concrete need.

## State And Files

Harness-owned product state should be separate from kernel state:

- action requests
- confirmation records
- product events
- derived indexes
- UI caches
- capability profiles

Kernel-owned state remains protected:

- checkpoint state
- run status
- campaign status
- budget ledgers
- generated artifacts
- prompt and graph behavior

## Security Boundaries

- No public network listener in the first implementation.
- Daemon, if added, is loopback-only by default.
- Nonlocal access requires authentication and explicit user approval.
- Secret values are redacted in events, manifests, logs, and JSON output.
- Full log/prompt/LLM-call exposure should be opt-in because unpublished
  research and API metadata can be sensitive.
- OpenClaw receives a capability profile, not raw unlimited authority.

## Validation Plan

Before implementation:

- user reviews this harness boundary
- user confirms library plus CLI runner before daemon
- user confirms action flow and capability defaults

During implementation:

- unit tests for action request/confirmation behavior
- unit tests for capability denial
- snapshot tests for read-only status/graph/artifact outputs
- dry-run tests for launch, repair, approve/reject, resume, and archive
- parity tests showing SDK and CLI call the same harness functions
- fixture tests using checked-in campaign specs and later real workspaces
- no protected kernel edits without explicit user approval

## User Evaluation Checkpoint

The user should review:

- whether library plus CLI runner is the right first implementation
- whether a daemon should be deferred until VS Code/OpenClaude needs it
- whether the harness responsibilities are broad enough
- whether any listed responsibilities risk touching core research logic
- whether action request and confirmation flow is acceptable
- whether default OpenClaude/OpenClaw profiles feel safe
- whether event/audit data is sufficient for trust and debugging

## Exit Criteria

Stage 4 is approved when:

- the single-harness boundary is accepted
- first implementation shape is accepted
- daemon deferral or daemon-first approach is decided
- action flow is accepted
- security and capability defaults are accepted
- current entry points to wrap are accepted

## Open Risks

- If the SDK/CLI is not implemented first, the harness could become a parallel
  interface with its own semantics.
- If event logging is too weak, OpenClaude/OpenClaw actions will be hard to
  audit.
- If confirmation tokens are too awkward, users may bypass the harness and
  return to raw scripts.
- If daemon work starts too early, product complexity may outrun validation.
- If real workspace fixtures remain unavailable, read-model compatibility will
  stay only partially proven.
