# Stage 0 Preservation Inventory

This inventory establishes the boundary between the protected research kernel
and the infrastructure that can be rebuilt during the production lift.

Status: approved by user on 2026-05-10.

## Rule

Anything that can change the functional research logic, output quality, model
behavior, validation behavior, budget behavior, or artifact meaning requires
explicit user approval before modification.

The old proof-of-concept infrastructure can be replaced, but only after the new
layer wraps or preserves the current entry points and artifact semantics.

## Protected Research Kernel

These modules are protected because they define research behavior, agent
behavior, graph logic, validation behavior, model policy, budget enforcement, or
artifact semantics.

Do not edit without explicit user approval:

- `consortium/graph.py`
- `consortium/graph_config.py`
- `consortium/state.py`
- `consortium/runner.py`
- `consortium/args.py`
- `consortium/config.py`
- `consortium/utils.py`
- `consortium/models.py`
- `consortium/llm.py`
- `consortium/counsel.py`
- `consortium/persona_council.py`
- `consortium/budget.py`
- `consortium/token_usage_tracker.py`
- `consortium/paper_contract.py`
- `consortium/iterate.py`
- `consortium/context_compaction.py`
- `consortium/workflow_utils.py`
- `consortium/mode.py`
- `consortium/prereqs.py`

Protected directories:

- `consortium/prompts/`
- `consortium/agents/`
- `consortium/supervision/`
- `consortium/tree_search/`
- `consortium/toolkits/`
- `consortium/external_tools/`

Protected campaign behavior:

- `consortium/campaign/spec.py`
- `consortium/campaign/runner.py`
- `consortium/campaign/status.py`
- `consortium/campaign/planner.py`
- `consortium/campaign/planner_prompt.py`
- `consortium/campaign/repair_agent.py`
- `consortium/campaign/memory.py`
- `consortium/campaign/budget_manager.py`

Rationale:

- These files determine what the system does scientifically.
- They define prompts, stages, state, gates, retries, validation, cost control,
  experiment execution, campaign semantics, and generated artifact meaning.
- Changes here may alter output quality in ways that cannot be evaluated
  cheaply without large max/ultra reruns.

## Approval-Required Artifact Semantics

The storage layout may be rebuilt, but the meaning of these artifacts must be
preserved or migrated deliberately:

- `run_status.json`
- `STATUS.txt`
- `run_summary.json`
- `experiment_metadata.json`
- `effective_models.json`
- `budget_state.json`
- `budget_ledger.jsonl`
- `run_token_usage.json`
- `paper_workspace/`
- `paper_workspace/final_paper.pdf`
- `paper_workspace/final_paper.tex`
- `paper_workspace/final_paper.md`
- `paper_workspace/review_verdict.json`
- `paper_workspace/followup_decision.json`
- `paper_workspace/track_decomposition.json`
- `math_workspace/claim_graph.json`
- campaign status files
- campaign stage workspaces
- campaign memory and stage summaries

Rationale:

- Product surfaces can index, relocate, and organize artifacts differently.
- They must not silently reinterpret whether a stage succeeded, what a paper
  means, how much budget was spent, or what model/policy produced an output.

Generated paper artifacts are immutable source evidence once produced. Human
feedback should not mutate those historical outputs in place. Instead, feedback
may create a new steering, iteration, or revision request that routes the work
back into an earlier approved pipeline stage to seek different or better
results. The new attempt should be linked to the prior artifacts for audit.

## Current Entry Points To Preserve Or Wrap

Preferred public/user entry points:

- `msc run ...`
- `msc campaign ...`
- `msc doctor`
- `msc config`
- `msc runs`
- `msc resume`
- `msc status`
- `msc logs`
- `msc budget`
- `msc notify`
- `msc openclaw ...`

Package script entry points:

- `msc = consortium.cli.main:cli`
- `consortium = consortium.runner:main`

Supported direct-script and automation entry points:

- `python launch_multiagent.py ...`
- `python -m consortium.runner ...`
- `python scripts/campaign_heartbeat.py --campaign ...`
- `python scripts/campaign_cli.py --campaign ... <subcommand>`
- `python scripts/run_campaign_planner.py ...`
- `python scripts/preflight_check.py ...`
- `python scripts/campaign_monitor.py ...`
- `bash scripts/launch_openclaw_gateway.sh`
- `bash scripts/launch_multiagent_slurm.sh`
- `bash scripts/launch_orchestrator_engaging.sh`
- `bash scripts/slurm_pipeline_v5.sh`
- `bash scripts/slurm_pipeline_v5_rigorous.sh`
- `bash scripts/submit_orchestrator.sh`

Machine-readable campaign bridge commands currently provided by
`scripts/campaign_cli.py`:

- `status`
- `dashboard`
- `stage-logs`
- `stage-artifacts`
- `launch`
- `repair`
- `budget`
- `analyze-logs`
- `set-stage-status`
- `distill`
- `rewrite-task`
- `launchable`
- `check-credits`
- `validate-pipeline`
- `approve-plan`
- `reject-plan`
- `show-plan`
- `archive`
- `archive-all`
- `init-campaign`

Rationale:

- Future SDK/CLI/control-plane work should wrap these surfaces first.
- Replacement is allowed only after compatibility is proven.

## Replaceable Product-Shell Infrastructure

These areas are candidates for aggressive redesign, provided the protected
kernel and artifact semantics above are preserved.

Replaceable or rebuildable:

- artifact storage layout under `results/`
- artifact indexing and search
- artifact read models for dashboards
- log tailing and log presentation
- status aggregation
- campaign/run dashboard logic
- VS Code extension UI
- OpenClaude fork/integration
- OpenClaude MSc skill/playbook
- OpenClaw setup/permissioning wrapper
- Telegram setup flow
- future Slack setup
- setup/tutorial UX
- deployment scripts and process supervision wrappers
- Docker packaging
- Remote-SSH convenience tooling
- control-plane daemon or local service
- SDK and public CLI organization
- notification presentation and routing

Rationale:

- These layers affect usability, packaging, observability, and trust.
- They can be made production-grade without changing the research process.

## Replacement Constraints

Any replacement must:

- preserve protected research behavior
- wrap existing entry points before removing them
- preserve or deliberately migrate artifact semantics
- include regression coverage against saved workspaces where possible
- require confirmation for mutation operations
- keep read-only operation as the default posture
- avoid requiring max/ultra reruns for validation

## Stage 0 Review Checklist

The user should review:

- whether any protected files or directories are missing
- whether any listed protected modules can be safely downgraded to replaceable
- whether any replaceable infrastructure should be protected for now
- whether the entry point list captures all currently important workflows
- whether the artifact semantics list includes all quality-critical artifacts

Stage 1 may begin after this inventory is accepted or revised.
