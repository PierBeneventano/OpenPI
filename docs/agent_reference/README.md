# Agent Reference

This directory is the canonical working reference for Codex and other agents
helping turn PoggioAI/MSc into a product.

Use this directory before changing product harnesses, interfaces, deployment
wrappers, VS Code integrations, OpenClaw integrations, or webapp-adjacent code.
The goal is to preserve the research system while adding product surfaces
around it.

## Primary References

- [`../architecture.md`](../architecture.md): current runtime architecture,
  pipeline shape, entry points, core modules, campaign architecture, and
  invariants.
- [`../data_formats.md`](../data_formats.md): canonical run, campaign, budget,
  status, paper, math, and review artifact formats.
- [`../engaging_setup.md`](../engaging_setup.md): supported SLURM/HPC setup and
  Engaging-oriented operational workflow.
- [`../../OpenClaw_Use_Guide.md`](../../OpenClaw_Use_Guide.md): current
  OpenClaw/campaign automation contract.
- [`../../CAMPAIGN_QUICKSTART.md`](../../CAMPAIGN_QUICKSTART.md): supported
  campaign operator workflow.
- [`preservation_strategy.md`](preservation_strategy.md): productization
  strategy for rebuilding infrastructure around the preserved research engine.
- [`orchestration_vision.md`](orchestration_vision.md): target product shape
  using an SDK/CLI core, OpenClaude-first GUI control, and optional OpenClaw
  cluster automation.
- [`production_lift_plan.md`](production_lift_plan.md): multistage production
  lift blueprint with evaluation gates and an append-only progress log.
- [`stage_0_preservation_inventory.md`](stage_0_preservation_inventory.md):
  protected research-kernel inventory, replaceable infrastructure inventory,
  and current entry points for the production lift.
- [`stage_1_artifact_read_model_audit.md`](stage_1_artifact_read_model_audit.md):
  artifact contract audit, fixture candidates, missing regression fixtures, and
  read-model requirements for the first product surfaces.
- [`stage_2_production_artifact_model.md`](stage_2_production_artifact_model.md):
  production artifact domain model, manifest contract, importer design, and
  dashboard read-model contracts.
- [`stage_3_sdk_cli_control_surface.md`](stage_3_sdk_cli_control_surface.md):
  co-designed Python SDK and expressive CLI contract for humans, Codex,
  OpenClaude, OpenClaw, and future product harnesses.
- [`stage_4_orchestrator_harness.md`](stage_4_orchestrator_harness.md):
  single orchestrator harness boundary, action flow, capability enforcement,
  event audit model, and daemon deferral strategy.
- [`stage_5_vscode_dashboard.md`](stage_5_vscode_dashboard.md):
  Remote-SSH-first VS Code read-only dashboard contract, graph/artifact/log
  views, refresh model, and OpenClaude handoff placeholder.
- [`stage_6_openclaude_integration.md`](stage_6_openclaude_integration.md):
  OpenClaude fork/configuration strategy, MSc skill/playbook scope, shared
  OpenRouter launch environment, capabilities, and chat confirmation flow.
- [`stage_7_guided_setup.md`](stage_7_guided_setup.md): guided setup/tutorial
  flow for OpenRouter, readiness checks, OpenClaude launch setup, and optional
  OpenClaw/Telegram configuration.
- [`stage_8_openclaw_automation.md`](stage_8_openclaw_automation.md): optional
  OpenClaw/Telegram automation boundary, capability profiles, and safety model.
- [`stage_9_slack_webapp_later.md`](stage_9_slack_webapp_later.md): later
  Slack and webapp planning, hosted security requirements, and artifact access
  policy.
- [`validation_protocol.md`](validation_protocol.md): recurring no-cost
  dry-run, optional cheap-smoke, and heuristic quality gate for implementation
  stages.

## Working Premise

The product work should wrap the existing engine. It should not rewrite anything
that touches the functional research logic: prompts, LangGraph pipeline, stage
order, routing, validation, model-tier semantics, budget enforcement, or
campaign state machine.

The current system may have outputs from expensive max-mode or ultra-mode runs
that cannot be cheaply regenerated. Product development must therefore bias
toward preserving result quality while aggressively replacing poor proof-of-
concept infrastructure around storage, artifact organization, orchestration,
interfaces, and deployment.

## Agent Operating Rule

When proposing or making changes, agents should first classify the change:

1. Harness/interface change: VS Code UI, webapp, API wrapper, OpenClaw bridge,
   status viewer, artifact browser, packaging, deployment.
2. Runtime behavior change: LangGraph nodes, routers, validation gates, prompts,
   model policy, state schema, artifact semantics, budget enforcement.

Harness/interface changes are the preferred productization path. Runtime
behavior changes require explicit justification and should preserve documented
contracts unless the user approves a migration plan.

Artifact storage, artifact reading, artifact organization, control surfaces, and
operator harnesses may be rebuilt from first principles, but only around the
preserved research behavior.
