# Orchestration Vision

This document captures the intended product architecture for rebuilding
PoggioAI/MSc into an elegant system while preserving the current research
results and pipeline behavior.

## Product Premise

The current system was built as a proof of concept and then pushed into a
minimum viable product. The foundations are messy, but the system can produce
good research outputs.

The refactor should therefore be aggressive around infrastructure and cautious
around research behavior:

- keep the results-producing process intact
- rebuild artifact storage, artifact access, orchestration, and UI from first
  principles
- expose a stable SDK and expressive CLI so external orchestrators do not need
  raw access to internals
- make powerful mutations explicit, auditable, and confirmation-gated

## Target Shape

```text
OpenClaude / VS Code dashboard / optional OpenClaw
                  |
                  v
expressive MSc CLI + SDK + permissioned control surface
                  |
                  v
single orchestrator harness
                  |
                  v
protected research kernel
                  |
                  v
production artifact store + derived read models
```

The research kernel keeps the existing prompts, graph, process, model policy,
budget enforcement, and output semantics. The layers around it become
production-grade.

## OpenClaude-First Interface

OpenClaude is a promising primary operator surface because it is an open-source
coding-agent CLI with tool-driven workflows, provider routing, a VS Code
extension, and a headless gRPC mode. This makes it a reasonable candidate for
controlling MSc through an explicit CLI/SDK rather than through unrestricted
filesystem access.

The intended user flow:

1. User connects to their working environment, likely Engaging via Remote SSH.
2. User opens the MSc codebase and a read-only VS Code dashboard.
3. User interfaces with the system through OpenClaude.
4. OpenClaude uses an MSc skill/playbook that calls the public MSc CLI/SDK.
5. The CLI/SDK controls research runs through the orchestrator harness.
6. The read-only dashboard reflects status, logs, budgets, and artifacts.

OpenClaude should not need to understand the internal LangGraph implementation.
It should operate through commands such as:

- `msc runs list`
- `msc runs status <run_id>`
- `msc runs artifacts <run_id>`
- `msc runs logs <run_id>`
- `msc campaign status <campaign>`
- `msc campaign heartbeat <campaign>`
- `msc campaign repair <campaign> <stage_id>`
- `msc artifact open <run_id> final-paper`
- `msc events follow`

Mutation commands should require explicit confirmation or a capability token.

## Optional OpenClaw Automation

OpenClaw should be a bonus automation path, not the assumed core interface.

Users may choose to run an OpenClaw script on Engaging if they want convenient
away-from-keyboard oversight through Telegram or another chat channel.

OpenClaw should operate through the same public CLI/SDK surface as OpenClaude.
It should not require full arbitrary access to all code and artifacts by
default.

Recommended capability levels:

- read-only: status, logs, budgets, artifact lists, summaries
- operator: heartbeat, launch pending stage, retry safe failures
- repair: invoke repair flow with confirmation
- admin: budget changes, archive/delete, status override, task rewrites

Default OpenClaw mode should be read-only plus confirmation-gated operator
actions.

## VS Code V1

VS Code v1 should be a read-only dashboard.

It should show:

- active runs and campaigns
- stage status and liveness
- logs and log tails
- budgets and spend
- artifact tree
- final papers and review artifacts
- OpenClaude/OpenClaw action history when available

It should not initially mutate campaign YAML, tasks, prompts, artifacts, stages,
or budgets. Mutation can come later through confirmation-gated orchestrator
commands.

## Production Artifact Layer

Artifact storage, reading, and organization can be redesigned. This is one of
the main areas where the proof-of-concept should be replaced.

Desired properties:

- stable run IDs and campaign IDs
- explicit artifact manifest per run/stage
- typed artifact metadata
- compatibility importer for old `results/` workspaces
- derived read models for dashboards and search
- append-only event log for lifecycle changes
- immutable original artifacts where possible
- clear separation between raw outputs and indexed summaries

The production artifact layer may use a database and object store, but it must
preserve the meaning of existing artifacts and support saved-workspace
regression tests.

## Kinks And Mitigations

### OpenClaude Tool Access Can Become Too Broad

Risk: OpenClaude or any coding agent can become another all-powerful actor with
raw filesystem and shell access.

Mitigation: expose a narrow CLI/SDK skill with explicit commands and
confirmation-gated mutations. Prefer capability-scoped commands over general
shell access for product workflows.

### OpenClaw On User Clusters May Feel Suspicious

Risk: users may not want a persistent agent with broad code/artifact access
running on their cluster.

Mitigation: make OpenClaw optional, cluster-local, and capability-limited. It
should call the same SDK/CLI as every other orchestrator and should have a
read-only default mode.

### Rebuilding Storage Can Break Old Workspaces

Risk: a cleaner artifact model might lose compatibility with existing results.

Mitigation: write an importer and regression tests over saved workspaces before
changing dashboard or artifact behavior. Keep raw artifacts immutable and build
new indexes from them.

### The CLI Can Accidentally Recreate The Old Mess

Risk: an expressive CLI can become a bag of ad hoc commands.

Mitigation: design commands around stable domain objects: runs, campaigns,
stages, artifacts, events, budgets, and capabilities. Keep internal file layout
out of the public contract.

### Orchestrator Harness May Drift Into Core Logic

Risk: the new harness starts adding special cases that change research behavior.

Mitigation: the harness may launch, supervise, pause, resume, and collect
artifacts. It must not change prompts, graph edges, stage order, validators,
model policy, or artifact completion semantics.

### Read-Only Dashboards Still Need Trust

Risk: even read-only UIs can leak sensitive logs, prompts, keys, generated code,
or unpublished results.

Mitigation: redact secrets, scope visible workspaces, and make artifact access
policy explicit before turning the dashboard into a webapp.
