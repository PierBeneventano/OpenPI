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

## Working Premise

The product work should wrap the existing engine. It should not casually rewrite
the prompts, LangGraph pipeline, stage order, artifact contracts,
model-tier semantics, budget enforcement, or campaign state machine.

The current system may have outputs from expensive max-mode or ultra-mode runs
that cannot be cheaply regenerated. Product development must therefore bias
toward compatibility, reproducibility, and inspection over broad internal
refactors.

## Agent Operating Rule

When proposing or making changes, agents should first classify the change:

1. Harness/interface change: VS Code UI, webapp, API wrapper, OpenClaw bridge,
   status viewer, artifact browser, packaging, deployment.
2. Runtime behavior change: LangGraph nodes, routers, validation gates, prompts,
   model policy, state schema, artifact semantics, budget enforcement.

Harness/interface changes are the preferred productization path. Runtime
behavior changes require explicit justification and should preserve documented
contracts unless the user approves a migration plan.
