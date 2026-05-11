# Architecture Overview

This document describes the current PoggioAI/MSc runtime architecture.

## Entry Points

Preferred user-facing entrypoints:

- `msc run ...` for a single research run
- `msc campaign ...` for campaign orchestration
- `msc doctor`, `msc config`, `msc notify`, and related operational commands

Supported direct-script entrypoints remain available for automation and HPC:

- `python launch_multiagent.py ...`
- `python scripts/campaign_heartbeat.py --campaign ...`
- `python scripts/campaign_cli.py --campaign ... <subcommand>`

Runtime configuration is resolved from three layers:

1. Existing shell environment variables
2. `~/.msc/.env`
3. Repo-root `.env`

Model/runtime settings are read from the project-root `.llm_config.yaml`, which `msc run` auto-generates from the selected tier unless `custom_llm_config: true` is set in `~/.msc/config.yaml`.

## Pipeline Shape

The historical LangGraph workflow is built in
[`consortium/graph.py`](../consortium/graph.py). This workflow is not a pure
DAG. It is a directed state machine with a readable happy path, optional
fan-out/fan-in tracks, and explicit feedback loops.

Base pipeline: 16 stages

1. `persona_council`
2. `literature_review_agent`
3. `brainstorm_agent`
4. `formalize_goals_entry`
5. `formalize_goals_agent`
6. `research_plan_writeup_agent`
7. `experiment_literature_agent`
8. `experiment_design_agent`
9. `experimentation_agent`
10. `experiment_verification_agent`
11. `experiment_transcription_agent`
12. `formalize_results_agent`
13. `resource_preparation_agent`
14. `writeup_agent`
15. `proofreading_agent`
16. `reviewer_agent`

Math-enabled pipeline: 22 stages

The six math stages are inserted between `research_plan_writeup_agent` and `experiment_literature_agent`:

1. `math_literature_agent`
2. `math_proposer_agent`
3. `math_prover_agent`
4. `math_rigorous_verifier_agent`
5. `math_empirical_verifier_agent`
6. `proof_transcription_agent`

## Routing and Validation

The stage roster above is only part of the full graph. The runtime also includes routers and gates that are not counted as user-facing pipeline stages:

- Track decomposition validation before execution fans out
- `track_router` fan-out into theory and empirical work
- `track_merge` fan-in before synthesis and writeup
- Intermediate artifact validation and strict review gates
- Follow-up loops when review or validation requires additional work

This is why the implementation includes more graph nodes than the 16/22 visible pipeline stages.

## Core Runtime Modules

- [`consortium/runner.py`](../consortium/runner.py): CLI/direct-script
  execution, workspace setup, env/bootstrap, resume logic, steering server, and
  graph invocation.
- [`consortium/config.py`](../consortium/config.py): `.llm_config.yaml`
  loading and model-param filtering.
- [`consortium/graph.py`](../consortium/graph.py): historical stage roster,
  routing, validation gates, feedback loops, and LangGraph construction.
- [`consortium/state.py`](../consortium/state.py): `ResearchState` schema
  passed between LangGraph nodes.
- [`consortium/agents/`](../consortium/agents): specialist agent builders,
  prompts, and tool surfaces.
- [`consortium/supervision/`](../consortium/supervision): artifact, review,
  paper-quality, and traceability validators.
- [`msc_sdk/campaign_store.py`](../msc_sdk/campaign_store.py): new local-first
  campaign state/index layer.

## Campaign Architecture

The product-facing campaign architecture is moving to a local-first state
model. SQLite is the operational index, normal files remain visible research
outputs, and JSON bundles are explicit import/export snapshots.

Current local-first layout:

```text
.msc/
  campaigns.db
  events/campaigns.jsonl
  snapshots/<campaign_id>.graph.json
campaigns/<campaign_id>/
  campaign.json
  graph.json
  artifacts.json
  events.jsonl
  README.md
results/<campaign_id>/
  <stage_id>/artifacts...
```

Public campaign commands:

```bash
msc campaigns create --title ... --objective ... --template consortium_scaffold --json
msc campaigns list --json
msc campaigns inspect <campaign> --json
msc campaigns graph <campaign> --json
msc campaigns artifacts <campaign> --json
msc campaigns events <campaign> --json
msc campaigns export <campaign> --json
msc campaigns import <bundle_dir> --json
```

The older `msc campaign ...` and campaign heartbeat scripts remain compatibility
surfaces for existing automation. They should not define the next product
architecture.

## Workspace and Outputs

Single runs write into `results/consortium_<timestamp>/` and include:

- `run_summary.json`
- `experiment_metadata.json`
- `budget_state.json`
- `paper_workspace/` and other stage artifacts
- checkpoint/state data for resume support

Product campaigns write to the local campaign store and keep artifacts as normal
files under `results/<campaign_id>/...`. Older run and campaign status JSON
files may still be emitted for compatibility.

## Redesign Invariants

The overhaul is governed by
[`research_engine_invariants.md`](research_engine_invariants.md). In short:

- Campaign/run state is central.
- Artifacts are first-class completion evidence.
- Stage contracts are explicit and typed.
- Validators are peers to agents.
- Loops are intentional, bounded, observable, and budgeted.
- Specialist power comes from prompt, tool, artifact, and validator boundaries.
- Budget and approval policy are part of the engine.
- Human and assistant steering mutate state through events.
- The graph must explain itself to the researcher.

## Invariants

- OpenRouter is the required LLM backend for the main engine
- The selected tier is the baseline profile, but persisted config overrides can change model, budget, output format, mode, and feature toggles
- `messages` in `ResearchState` remain append-only through LangGraph reducers
- Budget enforcement is fail-closed when pricing data is available
- Project-root `.llm_config.yaml` is the runner-consumed model/budget config file
