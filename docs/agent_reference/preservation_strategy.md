# Preservation Strategy

This document describes how to rebuild infrastructure around PoggioAI/MSc while
preserving the current research process and output quality.

## Decision

Prompts, process, and LangGraph logic are protected. Product work should replace
old infrastructure around the system without changing the research kernel.

Protected behavior includes:

- prompt templates and agent instructions
- stage roster and stage ordering
- routers, gates, validation loops, and retry behavior
- model policy and tier semantics
- budget enforcement
- workspace and artifact contracts
- campaign heartbeat semantics
- OpenClaw/campaign automation contracts

## Build Around A Research Kernel

Treat the current engine as a stable kernel with adapters around it:

```text
VS Code / WhatsApp / Webapp
          |
          v
control plane or local extension adapter
          |
          v
existing CLI, campaign scripts, OpenClaw gateway
          |
          v
preserved LangGraph research engine
          |
          v
existing results/, logs, budgets, papers, status artifacts
```

The product should initially read and operate through existing entry points
rather than copying their logic.

## Recommended Layers

1. User interfaces: Remote-SSH VS Code extension, WhatsApp integration, and
   later web UI.
2. Control plane: commands, approvals, repair requests, launch requests, status
   API, event stream, audit log, and operator chat history.
3. Compatibility adapter: stable Python/CLI wrapper that calls existing `msc`,
   `scripts/campaign_cli.py`, `scripts/campaign_heartbeat.py`, and OpenClaw
   operations and normalizes responses.
4. Artifact/read model layer: read-only index derived from `results/`, campaign
   status, logs, budgets, paper workspaces, and review artifacts.
5. Execution substrate: SLURM, process supervision, SSH sessions, file watching,
   containerization, storage mounts, and deployment scripts.
6. Research kernel: current `consortium` runtime, prompts, graph, state,
   supervision, runner, model policy, and budget tracking.

## Rebuildable Product Shell

There are several layers beyond the data/artifact layer that can be rebuilt
while preserving pipeline accuracy. They are part of the product shell, not the
research kernel.

Safe-to-rebuild layers:

- user interfaces: VS Code, WhatsApp, webapp, dashboards, artifact viewers
- control plane: command API, approvals, action queue, audit logs, event stream
- compatibility adapter: normalized wrapper around current CLI/scripts/OpenClaw
- artifact/read model layer: derived indexes, caches, database tables, search
- execution substrate: process supervision, Remote SSH assumptions, SLURM
  launch wrappers, containers, deployment and restart logic
- notification layer: WhatsApp, Slack, Telegram, email, web push

These layers may observe, launch, supervise, route operator intent, cache
derived state, and present artifacts. They should not reinterpret the scientific
workflow.

Protected research-kernel behavior:

- prompt text and prompt templates
- graph nodes and edges
- stage roster and ordering
- route, gate, retry, validation, and follow-up behavior
- model selection and tier policy
- state schema and checkpoint compatibility
- budget enforcement
- artifact meaning and completion semantics

The adapter/control plane should express operator actions as auditable commands
such as `launch_campaign`, `run_heartbeat`, `repair_stage`, `approve_plan`,
`tail_logs`, or `list_artifacts`. It should not silently edit tasks, mutate
outputs, change prompts, reorder stages, or alter model policy.

## Validation Without Expensive Reruns

Because max-mode and ultra-mode quality cannot be cheaply re-evaluated, validate
product harness work without requiring new large experiments:

- parse saved workspaces and campaign artifacts as regression fixtures
- snapshot API responses derived from `campaign_cli.py status`, dashboards, logs,
  budgets, and artifact lists
- run unit tests for artifact parsers and status normalization
- run `msc run --dry-run` for environment/argument validation
- defer full paid smoke runs until explicit Engaging integration testing
- avoid large-run validation as a requirement for UI-only or adapter-only work

## Acceptable Product Changes

These changes are generally safe if they preserve existing contracts:

- VS Code views over existing artifacts
- artifact browser and log tailing
- OpenClaw chat UI backed by existing gateway or scripts
- campaign status dashboards
- control-plane APIs that call existing entry points
- action queues and audit logs for operator commands
- wrappers around `campaign_cli.py`
- filesystem watchers for `results/`
- derived databases or caches that can be rebuilt from artifacts
- Remote-SSH-first extension workflows
- process supervisors or deployment wrappers that preserve command semantics

## High-Risk Changes

These require explicit approval and stronger validation:

- prompt edits
- graph node or edge changes
- route/gate/retry behavior changes
- model tier changes
- budget enforcement changes
- state schema changes that break existing checkpoints
- artifact format changes that break old workspaces
- campaign heartbeat semantics changes

## Migration Principle

If old infrastructure is removed, replace it at the boundary. The new component
should prove that it can produce the same commands, read the same artifacts, and
preserve the same operational semantics before the old path is deleted.
