# Productization Guardrails

These guardrails protect the research engine while the surrounding product
harness evolves.

## Preserve The LangGraph Engine

Do not change prompts, process, or LangGraph pipeline logic unless the user
explicitly asks for a runtime behavior change.

Preserve:

- prompts and prompt templates
- base pipeline stage roster and ordering described in
  [`../architecture.md`](../architecture.md)
- math-enabled pipeline insertion point and ordering
- routers, validation gates, follow-up loops, strict review gates, and
  track fan-out/fan-in behavior
- `ResearchState` append-only message semantics
- checkpoint and resume behavior
- artifact enforcement behavior
- tier-driven model policy and effective model manifests
- budget fail-closed behavior when pricing data is available

## Prefer Wrappers Over Rewrites

For VS Code, OpenClaw, WhatsApp, and webapp work, prefer adding a control plane
or adapter around existing entry points:

- `msc run ...`
- `msc campaign ...`
- `python scripts/campaign_heartbeat.py --campaign ...`
- `python scripts/campaign_cli.py --campaign ... <subcommand>`
- OpenClaw gateway on Engaging
- filesystem artifacts under `results/`

Avoid duplicating runner logic in UI code.

Old infrastructure may be removed or replaced, but the research kernel should
remain stable. Treat productization as a harness rewrite around the preserved
engine, not as an engine rewrite.

## Rebuild Product Shell Layers

The product shell can be rebuilt above and around the artifact/read model layer
as long as it preserves command semantics and does not reinterpret the research
workflow.

Rebuildable layers include:

- user interfaces: VS Code, WhatsApp, webapp, dashboards, artifact viewers
- control plane: commands, approvals, event streams, audit logs, action queues
- compatibility adapter: wrappers around `msc`, campaign scripts, and OpenClaw
- artifact/read model layer: derived indexes, caches, searchable metadata
- execution substrate: SLURM wrappers, process supervision, file watchers,
  Remote SSH assumptions, deployment scripts
- notification layer: WhatsApp, Slack, Telegram, email, web push

These layers may observe, launch, supervise, and route operator intent. They
must not silently change prompts, stage order, routing, validation behavior,
model policy, budget policy, or artifact completion semantics.

## Treat Artifacts As Contracts

Product surfaces should read existing artifacts rather than inventing competing
status sources.

Important contracts include:

- `run_status.json`
- `STATUS.txt`
- `run_summary.json`
- `experiment_metadata.json`
- `effective_models.json`
- `budget_state.json`
- `budget_ledger.jsonl`
- `paper_workspace/`
- campaign status and per-stage workspaces

If a new product index or database is added, it should be derived from these
artifacts unless a migration is explicitly approved.

## Keep Engaging-First Workflows Intact

The near-term operator flow is Engaging/HPC-native:

- users may connect to Engaging first
- VS Code should support Remote SSH use
- extension/backend logic may run on the remote VS Code host
- OpenClaw and live steering services should remain loopback-local unless an
  authenticated control plane is introduced
- SLURM, campaign heartbeat, and filesystem liveness checks remain part of the
  operational model

## Protect Expensive Research Outputs

Because max-mode or ultra-mode experiments are expensive to rerun:

- avoid changes that alter prompts, model routing, stage order, artifact gates,
  or validation behavior without approval
- preserve backwards compatibility with existing `results/` and campaign
  workspaces
- add smoke tests and contract tests before changing status/artifact parsing
- prefer dry-run validation when testing harness behavior
- do not require rerunning large campaigns to validate UI or control-plane work

## Security And Isolation

Before exposing anything beyond a trusted Engaging login session:

- keep OpenClaw gateway and steering endpoints off the public internet
- require authentication for any web-facing control plane
- treat API keys, campaign workspaces, logs, and generated code as sensitive
- isolate generated code execution before multi-user hosting
- make operator actions auditable
