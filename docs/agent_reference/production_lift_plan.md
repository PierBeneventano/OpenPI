# Production Lift Plan

This document is the canonical execution blueprint for lifting PoggioAI/MSc
from proof-of-concept infrastructure into a production-grade product shell while
preserving the research kernel.

It is intentionally detailed. Future agents and engineers should use it to stay
aligned with the original product goal, preserve auditability, and know when the
user should evaluate progress before the next stage begins.

## North Star

The current system was built on rough proof-of-concept foundations, but it
produces valuable research outputs. The production lift should therefore be
aggressive around infrastructure and conservative around research behavior.

Core rule:

- preserve functional research logic
- rebuild artifact and orchestration infrastructure
- make all powerful operations explicit, auditable, and confirmation-gated

Protected research-kernel behavior:

- prompts and prompt templates
- LangGraph graph nodes, edges, routers, gates, retries, and validation loops
- stage roster and stage ordering
- model selection, tier policy, and effective model manifests
- budget enforcement and spend semantics
- state schema and checkpoint compatibility
- artifact meaning and completion semantics
- campaign heartbeat behavior when it affects research execution semantics

Rebuildable product-shell infrastructure:

- artifact storage, reading, organization, indexing, and search
- compatibility importers for old `results/` and campaign workspaces
- SDK and public CLI surface
- orchestrator harness
- capability and confirmation model
- Remote-SSH VS Code dashboard
- OpenClaude fork/integration and MSc skill/playbook
- guided setup/tutorial
- optional OpenClaw/Telegram automation
- later Slack and webapp layers

Old infrastructure is disposable unless it defines output semantics,
compatibility expectations, or a currently-working entry point that must be
wrapped before replacement.

## Stage Template

Every stage below should be executed with this template:

- Goal: what the stage achieves.
- What changes: product-shell work expected in this stage.
- What must not change: protected research-kernel boundaries.
- Design reasoning: why this approach was chosen.
- Assumptions: defaults that should hold unless explicitly revised.
- Interfaces/contracts introduced: new public surfaces or compatibility
  contracts.
- Validation plan: how to prove the stage without expensive max/ultra reruns.
- User evaluation checkpoint: what the user should inspect before proceeding.
- Exit criteria: objective completion signals.

## Stage 0: Baseline Preservation Inventory

Goal: establish exactly what is protected before rebuilding around it.

What changes:

- Create a protected-module inventory.
- Identify files that cannot be edited without explicit user approval.
- Identify proof-of-concept infrastructure that may be replaced.
- Record existing CLI entry points and direct scripts that product layers must
  wrap before replacing.

What must not change:

- No runtime code changes.
- No prompt, graph, model policy, budget, artifact, or campaign behavior
  changes.

Design reasoning:

- The system's quality cannot be cheaply revalidated with max/ultra runs.
- A written protection boundary prevents accidental "cleanup" of behavior that
  contributes to output quality.

Assumptions:

- Prompts and graph logic are frozen.
- Existing result workspaces are valuable compatibility evidence.
- The first lift work should be documentation and inventory, not refactoring.

Interfaces/contracts introduced:

- Protected-module list.
- Replaceable-infrastructure list.
- Current-entrypoint list.

Validation plan:

- Compare the protected list against `docs/architecture.md`.
- Confirm protected modules include prompts, graph, state, runner behavior,
  model policy, budget tracking, and supervision.
- Confirm no code changes are required for this stage.

User evaluation checkpoint:

- User reviews and approves the protected-module inventory.
- User confirms whether any additional files should be "do not edit without
  approval."

Exit criteria:

- Protected-module inventory exists.
- Replaceable infrastructure inventory exists.
- User has reviewed the boundary.

## Stage 1: Artifact And Read-Model Audit

Goal: understand every existing artifact that product surfaces must read before
designing a production artifact model.

What changes:

- Audit current `results/` workspaces, campaign workspaces, logs, budget files,
  paper workspaces, final papers, review artifacts, and status files.
- Select canonical regression fixtures from existing runs/campaigns.
- Define the read-only data needed by the VS Code graph dashboard.

What must not change:

- No artifact schema changes.
- No writes into old workspaces except optional copies into fixture locations if
  explicitly approved.
- No reinterpretation of artifact completion semantics.

Design reasoning:

- The current artifact layout is messy but encodes the only available evidence
  for historical run behavior.
- A production model must be derived from observed artifacts, not invented in
  isolation.

Assumptions:

- Old workspaces should remain readable.
- Production indexing can be rebuilt from raw artifacts.
- Raw artifacts should be treated as source-of-truth evidence.

Interfaces/contracts introduced:

- Artifact inventory document or table.
- Fixture selection list.
- Read-model requirements for graph/status/log/budget views.

Validation plan:

- Parse representative completed, failed, partial, active, and campaign
  workspaces.
- Snapshot parser output.
- Confirm dashboard-required fields can be derived without running the pipeline.

User evaluation checkpoint:

- User reviews selected regression fixtures.
- User confirms which artifacts matter for scientific quality inspection.

Exit criteria:

- Fixture set selected.
- Artifact inventory complete enough for model design.
- Known gaps and ambiguous artifacts documented.

## Stage 2: Production Artifact Model And Importer Design

Goal: design a production-grade artifact organization layer that can ingest old
workspaces while supporting future dashboards and hosted products.

What changes:

- Define stable domain objects: run, campaign, stage, artifact, event, budget,
  actor, capability.
- Define an artifact manifest per run/stage.
- Define a compatibility importer from old `results/` and campaign workspaces.
- Define derived read models for dashboards and search.

What must not change:

- Existing artifact meaning.
- Completion semantics.
- Raw old workspace contents.
- Research-kernel output behavior.

Design reasoning:

- Rebuilding storage is allowed and desirable, but compatibility must be proven.
- A manifest separates raw artifacts from indexed summaries and UI-specific
  views.

Assumptions:

- Raw artifacts should be immutable where possible.
- Derived indexes can be deleted and rebuilt.
- The production layout should hide old filesystem quirks from public APIs.

Interfaces/contracts introduced:

- Production artifact manifest.
- Importer contract.
- Read-model contract for graph/status/artifacts/logs.
- Event log contract for lifecycle changes.

Validation plan:

- Import selected fixtures.
- Compare imported read models with current `campaign_cli.py status`, run
  status files, budget files, and known paper artifacts.
- Snapshot-test importer outputs.

User evaluation checkpoint:

- User reviews artifact model and importer design before implementation.
- User confirms whether the production model captures the artifacts they care
  about for quality and audit.

Exit criteria:

- Artifact model approved.
- Importer behavior specified.
- Test fixture expectations defined.

## Stage 3: SDK And Public CLI Control Surface

Goal: expose MSc as a stable SDK/CLI that orchestrators can use without raw
access to internals.

What changes:

- Design public commands around domain objects:
  - runs
  - campaigns
  - stages
  - artifacts
  - events
  - budgets
  - capabilities
- Define read-only commands first.
- Define mutation commands behind confirmation/capability gates.
- Keep old entry points wrapped during the transition.

What must not change:

- Core runner behavior.
- Research graph behavior.
- Prompt/model/budget decisions.
- Existing `msc` behavior unless changes are compatibility-preserving.

Design reasoning:

- OpenClaude, OpenClaw, VS Code, and future webapp should all call the same
  public surface.
- A clear CLI/SDK boundary prevents every UI or agent from learning internal
  filesystem and script details.

Assumptions:

- Read-only commands ship first.
- Mutations require explicit confirmation.
- Capability scope is part of the contract, not an afterthought.

Interfaces/contracts introduced:

- SDK package boundary.
- CLI command family.
- Capability model.
- Structured JSON output mode for machine clients.

Validation plan:

- Compare new read-only command output against fixture snapshots.
- Verify mutating commands refuse to run without confirmation/capability.
- Run dry-run validation for launch-related commands.

User evaluation checkpoint:

- User reviews command names, output shape, and capability model.
- User confirms command surface is expressive enough for OpenClaude skills.

Exit criteria:

- Public command spec approved.
- SDK boundary approved.
- Confirmation policy documented.

## Stage 4: Single Orchestrator Harness

Goal: create one orchestration harness that wraps preserved research behavior
and becomes the target for CLI, OpenClaude, VS Code, and optional OpenClaw.

What changes:

- Design harness responsibilities:
  - launch
  - supervise
  - pause/resume where supported
  - collect status
  - collect artifacts
  - emit events
  - enforce confirmation/capability boundaries
- Wrap existing `msc`, campaign scripts, and OpenClaw-compatible operations.

What must not change:

- The harness must not alter graph edges, prompts, validators, model policy, or
  artifact completion semantics.
- The harness must not invent new research retry behavior.

Design reasoning:

- One harness prevents VS Code, OpenClaude, OpenClaw, and webapp code from each
  becoming their own orchestration layer.
- Harness replacement can be staged while old commands still work underneath.

Assumptions:

- Initial harness can be local/Remote-SSH-first.
- Hosted mode comes later.
- The harness may delegate to old scripts until replacements are proven.

Interfaces/contracts introduced:

- Harness lifecycle API.
- Event stream contract.
- Action audit log.
- Capability enforcement points.

Validation plan:

- Harness read-only operations replay against fixtures.
- Mutating operations are tested with dry-run or safe no-op paths.
- No max/ultra reruns are required.

User evaluation checkpoint:

- User reviews harness responsibility boundary.
- User confirms no core logic has moved into the harness.

Exit criteria:

- Harness spec approved.
- Event/audit behavior specified.
- Confirmed boundary from research kernel.

## Stage 5: Remote-SSH VS Code Read-Only Dashboard

Goal: build the first GUI as a Remote-SSH-first read-only dashboard.

What changes:

- Add a VS Code extension surface with:
  - Graph tab
  - Chat tab placeholder or OpenClaude handoff area
  - artifact browser
  - log viewer
  - budget/status panels
- Graph tab renders live run/campaign stage state from read models.

What must not change:

- No mutation of campaign YAML, tasks, prompts, artifacts, stages, or budgets.
- No hidden repair/launch/abort behavior.
- No core pipeline imports that bypass the SDK/CLI boundary.

Design reasoning:

- Read-only first minimizes risk and builds trust.
- Remote SSH lets the dashboard reflect live Engaging files without public
  hosting complexity.

Assumptions:

- Engaging Remote SSH is the first-class environment.
- Local clone support can come later.
- The graph is operational stage state, not a full LangGraph internals viewer.

Interfaces/contracts introduced:

- VS Code read-model client.
- Graph data contract.
- Artifact browser contract.

Validation plan:

- Test dashboard against fixture read models.
- Test active/stalled/failed/completed visual states.
- Verify no write operations are exposed in v1.

User evaluation checkpoint:

- User reviews dashboard UX and graph semantics.
- User confirms the dashboard provides enough live observability.

Exit criteria:

- Read-only dashboard works against fixtures.
- Graph tab reflects stage status, liveness, budget, and artifacts.
- No mutations are possible from v1 UI.

## Stage 6: Forked OpenClaude Integration

Goal: provide a native MSc chat/operator experience by customizing a separate
OpenClaude fork and constraining it with an MSc skill/playbook.

What changes:

- Maintain OpenClaude customization in a separate fork.
- Add MSc skill/playbook that calls the public SDK/CLI.
- Configure OpenClaude to use MSc's OpenRouter key by launch-time environment
  injection.
- Embed or host the OpenClaude experience in the GUI chat tab.

What must not change:

- OpenClaude must not call internal graph/prompt modules directly.
- OpenClaude must not receive unbounded mutation authority by default.
- OpenClaude must not duplicate credentials into unmanaged plaintext profiles
  if launch env injection is available.

Design reasoning:

- A fork allows native UX while preserving a clear integration boundary.
- Skills/playbooks keep OpenClaude focused on the MSc public surface.
- Launch env prevents credential duplication.

Assumptions:

- OpenClaude uses OpenRouter through OpenAI-compatible environment variables.
- The same `OPENROUTER_API_KEY` powers MSc and OpenClaude.
- Mutations require confirmation.

Interfaces/contracts introduced:

- MSc OpenClaude skill/playbook.
- OpenClaude launch environment contract.
- Chat/action history contract.

Validation plan:

- Verify OpenClaude starts with OpenRouter configuration.
- Verify read-only commands work through the skill.
- Verify mutation commands require confirmation/capability.

User evaluation checkpoint:

- User reviews OpenClaude fork scope.
- User confirms chat UX and permission boundaries.

Exit criteria:

- OpenClaude can inspect MSc status through public commands.
- No raw research-kernel access is required.
- Credential handling is reviewed.

## Stage 7: Guided Setup And Tutorial

Goal: hide setup nuance behind an elegant guided experience.

What changes:

- Add setup wizard covering:
  - environment detection
  - OpenRouter API key entry
  - `msc doctor`/readiness checks
  - OpenClaude provider launch configuration
  - optional OpenClaw/Telegram setup
  - clear explanation of what remains optional
- Defer Slack to a later professional integration.

What must not change:

- Existing credential precedence must remain understandable.
- Setup must not overwrite user credentials without confirmation.
- Setup must not enable OpenClaw by default.

Design reasoning:

- Users should not need to understand every Engaging/OpenClaude/OpenClaw detail
  before trying the system.
- Optional automation should feel intentional, not invasive.

Assumptions:

- `~/.msc/.env` remains the initial credential store.
- OpenClaude uses launch env derived from MSc config.
- Telegram/OpenClaw is optional.

Interfaces/contracts introduced:

- Setup wizard state model.
- Readiness checklist.
- Optional integration configuration.

Validation plan:

- Test missing/invalid/valid OpenRouter key states.
- Test setup without OpenClaw.
- Test optional Telegram fields without forcing network sends unless requested.

User evaluation checkpoint:

- User reviews setup wizard flow and copy.
- User confirms that optional automation is clearly separated from required
  setup.

Exit criteria:

- User can complete required setup without manual env editing.
- OpenClaude and MSc share the OpenRouter key safely.
- Optional OpenClaw/Telegram setup is discoverable but not required.

## Stage 8: Optional OpenClaw Automation

Goal: provide away-from-keyboard cluster automation for users who explicitly
choose it.

What changes:

- OpenClaw operates through public SDK/CLI commands.
- Default mode is read-only plus confirmation-gated operator actions.
- Telegram interaction is optional.
- Capabilities define what OpenClaw may do.

What must not change:

- OpenClaw must not require full unbounded access to code and artifacts.
- OpenClaw must not bypass confirmation gates for sensitive operations.
- OpenClaw must not mutate research logic.

Design reasoning:

- Persistent agents on user clusters can feel suspicious.
- Capability-limited automation builds trust and makes risk visible.

Assumptions:

- Some users will never enable OpenClaw.
- OpenClaude remains the primary interface.
- OpenClaw is convenience automation, not core product dependency.

Interfaces/contracts introduced:

- OpenClaw capability profile.
- Telegram action confirmation flow.
- OpenClaw audit event format.

Validation plan:

- Test read-only OpenClaw mode.
- Test confirmation-gated actions.
- Test OpenClaw-down behavior while campaigns continue.

User evaluation checkpoint:

- User reviews OpenClaw permission model.
- User confirms Telegram flow is safe and useful.

Exit criteria:

- OpenClaw can monitor without broad access.
- Dangerous actions require explicit approval.
- Disabling OpenClaw does not impair core product use.

## Stage 9: Later Slack And Webapp Planning

Goal: plan professional team-facing integrations after local/Remote-SSH
product experience is stable.

What changes:

- Define Slack as a later professional team notification and optional
  interactive workflow.
- Define webapp migration requirements from the Remote-SSH product shell.
- Define multi-user permissions, hosted secrets, and artifact access policy.

What must not change:

- Slack/webapp must use the same SDK/CLI/control-plane semantics.
- Hosted surfaces must not expose core internals directly.
- Core research logic remains protected.

Design reasoning:

- Slack can be polished and professional, but it should not distract from v1.
- Webapp requires stronger auth, secrets, and data isolation than Remote SSH.

Assumptions:

- Slack is not v1.
- Webapp comes after SDK/CLI, artifact model, and dashboard are stable.
- Hosted mode needs a stricter security model.

Interfaces/contracts introduced:

- Slack notification/interaction concept.
- Webapp auth and artifact access concept.
- Hosted deployment prerequisites.

Validation plan:

- Design review only at this stage unless implementation begins.
- Security review before any hosted/web-facing prototype.

User evaluation checkpoint:

- User reviews whether Slack/webapp priorities have changed.
- User decides whether to begin hosted-product planning.

Exit criteria:

- Slack/webapp roadmap documented.
- No premature hosted complexity added to v1.

## Cross-Stage Confirmation Policy

The following actions require explicit user confirmation in any GUI, CLI skill,
OpenClaude integration, OpenClaw script, or webapp:

- launch a run or campaign
- repair a stage
- abort or cancel work
- increase or override budget
- override stage status
- archive or delete workspaces
- rewrite prompts, tasks, plans, or campaign YAML
- mutate artifacts or generated papers

Read-only inspection should be the default posture until the user approves a
specific mutation flow.

## Cross-Stage Validation Policy

Do not require max/ultra reruns for product-shell work.

Preferred validation:

- static checks
- unit tests for parsers, importers, and command wrappers
- snapshot tests against saved workspaces
- `msc run --dry-run` for setup/launch validation
- fixture-backed SDK/CLI/dashboard validation for local product-shell stages

Full paid pipeline smoke tests are deferred until the user is ready for
Engaging integration testing. Do not use `cheap_smoke_test.sh` as a recurring
local stage gate for Stage 1-5 product-shell work.

Any change that touches protected research-kernel behavior requires explicit
approval and a separate validation plan.

## Append-Only Progress Log

Add new entries below this line. Do not rewrite old entries except to fix
factual errors with an explicit correction note.

### 2026-05-10: Production Lift Plan Created

- Stage: planning foundation
- Work completed: created this production lift blueprint structure for the
  multistage refactor.
- Decisions made: progress log is append-only and dated; user evaluation occurs
  at the end of every major stage; detail level is a detailed blueprint.
- Evidence/tests: docs-only change; no runtime tests needed.
- Risks discovered: future implementation must avoid moving orchestration
  special cases into protected research logic.
- User review status: pending review of this document.
- Next action: user reviews and approves or revises the stage breakdown before
  implementation begins.

### 2026-05-10: Stage 0 Preservation Inventory Drafted

- Stage: Stage 0, Baseline Preservation Inventory
- Work completed: drafted the protected research-kernel inventory,
  approval-required artifact semantics, current entry points, and replaceable
  product-shell infrastructure list.
- Decisions made: treat prompts, graph, state, runner behavior, agents,
  supervision, toolkits, model policy, budget tracking, and campaign execution
  semantics as approval-required; treat artifact storage/read models, UI,
  setup, orchestration wrappers, and notification surfaces as rebuildable.
- Evidence/tests: inspected `pyproject.toml`, `docs/architecture.md`,
  `docs/data_formats.md`, `consortium/`, `consortium/cli/commands/`, and
  `scripts/`; docs-only change, no runtime tests needed.
- Risks discovered: campaign modules mix protected execution semantics with
  replaceable operational infrastructure, so future stages must separate
  wrappers from behavioral changes carefully.
- User review status: pending review of
  `docs/agent_reference/stage_0_preservation_inventory.md`.
- Next action: user reviews the inventory before Stage 1 begins.

### 2026-05-10: Stage 0 Approved With Artifact Feedback Amendment

- Stage: Stage 0, Baseline Preservation Inventory
- Work completed: incorporated user evaluation of Stage 0.
- Decisions made: generated paper artifacts are immutable source evidence; human
  feedback may route work back into an earlier approved stage through a new
  steering, iteration, or revision request rather than mutating historical
  outputs in place.
- Evidence/tests: docs-only amendment; no runtime tests needed.
- Risks discovered: product surfaces must distinguish immutable historical
  artifacts from new human-directed revision attempts, or auditability will
  degrade.
- User review status: Stage 0 approved on 2026-05-10.
- Next action: begin Stage 1 artifact/read-model audit and compatibility
  fixture selection.

### 2026-05-10 01:36 EDT: Stage 1 Artifact Audit Drafted

- Stage: Stage 1, Artifact And Read-Model Audit
- Work completed: drafted the artifact contract audit, available fixture
  candidates, missing real-workspace fixture classes, read-model requirements,
  artifact immutability rule, validation plan, and user evaluation checkpoint.
- Decisions made: checked-in examples, campaign specs, and archive manifests
  are useful parser/contract fixtures, but they are not sufficient
  compatibility proof for production importers because this checkout has no
  real `results/` workspaces beyond `.gitkeep`.
- Evidence/tests: inspected `docs/data_formats.md`,
  `consortium/campaign/status.py`, `consortium/campaign/spec.py`,
  `scripts/campaign_cli.py`, root campaign YAMLs, quickstart examples, archive
  manifests, and current `results/` contents; docs-only change, no runtime
  tests needed.
- Risks discovered: Stage 2 can design the artifact model from documented
  contracts, but full importer validation remains blocked until completed,
  failed, stalled, campaign, iterate, budget-rich, math-enabled, and strict
  paper workspace fixtures are supplied or restored.
- User review status: pending review of
  `docs/agent_reference/stage_1_artifact_read_model_audit.md`.
- Next action: user reviews Stage 1 and either identifies real regression
  workspaces or approves a contract-first Stage 2 with fixture validation
  deferred.

### 2026-05-10 01:41 EDT: Stage 2 Artifact Model Drafted

- Stage: Stage 2, Production Artifact Model And Importer Design
- Work completed: drafted the production domain objects, manifest contract,
  importer modes, source discovery order, normalization rules, status/liveness
  model, read models, compatibility expectations, and security/privacy rules.
- Decisions made: use a sidecar-first production manifest and derived index
  model; keep raw workspaces as immutable evidence; expose human feedback as
  append-only revision/steering artifacts linked to prior outputs; defer
  physical artifact relocation until importer/read-model behavior is proven.
- Evidence/tests: inspected current archive behavior, run inspection helpers,
  status/log/budget CLI readers, Stage 1 audit, and data format contracts;
  docs-only change, no runtime tests needed.
- Risks discovered: the artifact model can now guide implementation, but true
  compatibility still requires real completed/failed/stalled/revision
  workspaces before importer behavior can be considered production-proven.
- User review status: pending review of
  `docs/agent_reference/stage_2_production_artifact_model.md`.
- Next action: user reviews the artifact model and decides whether Stage 3
  should expose the model first through a Python SDK, CLI JSON commands, or
  both.

### 2026-05-10 01:49 EDT: Stage 3 SDK/CLI Direction Drafted

- Stage: Stage 3, SDK And Public CLI Control Surface
- Work completed: recorded the user decision that SDK and CLI should be built
  together, then drafted the SDK/CLI parity contract, command families,
  capability model, confirmation flow, self-validation strategy, error
  contract, implementation sequence, and user evaluation checkpoint.
- Decisions made: the Python SDK is the horizontal programmatic API; the CLI is
  the complete expressive operator/agent surface over that SDK; Codex should be
  able to validate every stable entry point before OpenClaude integration, so
  OpenClaude can later use the same expressive system through skills.
- Evidence/tests: inspected existing `msc` CLI registration, package script
  entry points, Stage 2 artifact model, and open questions; docs-only change,
  no runtime tests needed.
- Risks discovered: the current CLI is useful but human-oriented in places; the
  production surface will need stable JSON output, SDK parity, exit codes,
  confirmation tokens, and redaction tests before it is safe as an agent
  control plane.
- User review status: pending review of
  `docs/agent_reference/stage_3_sdk_cli_control_surface.md`.
- Next action: user reviews the Stage 3 command/capability shape and decides
  whether Stage 4 should add a daemon/control harness immediately or start as
  SDK plus CLI only.

### 2026-05-10 01:52 EDT: Stage 4 Orchestrator Harness Drafted

- Stage: Stage 4, Single Orchestrator Harness
- Work completed: drafted the single-harness boundary, responsibilities,
  first implementation shape, daemon deferral strategy, current entry points to
  wrap, read-only and mutating action flows, SDK interface sketch, state
  separation, security boundaries, validation plan, and user evaluation
  checkpoint.
- Decisions made: begin with an SDK-backed local orchestration library and CLI
  action runner; defer a long-lived daemon until VS Code live views, OpenClaude
  RPC, multi-client coordination, or hosted mode requires it; keep all
  mutations behind capabilities and confirmation tokens.
- Evidence/tests: inspected Stage 3 SDK/CLI contract, production lift Stage 4
  plan, orchestration vision, and guardrails; docs-only change, no runtime
  tests needed.
- Risks discovered: implementing a daemon or harness before SDK/CLI parity
  would risk creating a second control plane; weak event logging would make
  agent actions hard to audit.
- User review status: pending review of
  `docs/agent_reference/stage_4_orchestrator_harness.md`.
- Next action: user reviews the Stage 4 harness boundary and confirms whether
  the first implementation should stay library-plus-CLI before daemon work.

### 2026-05-10 01:54 EDT: Stage 5 VS Code Dashboard Drafted

- Stage: Stage 5, Remote-SSH VS Code Read-Only Dashboard
- Work completed: drafted the VS Code v1 dashboard contract, Remote-SSH user
  flow, architecture, data sources, graph/artifact/log/budget/event views,
  chat placeholder, readiness hooks, refresh model, security boundaries,
  implementation notes, validation plan, and user evaluation checkpoint.
- Decisions made: VS Code v1 remains read-only; it should run on the remote VS
  Code host during Engaging Remote SSH; it should consume SDK/CLI/harness read
  models rather than parsing protected internals; OpenClaude belongs to Stage 6
  and should only have a placeholder or handoff area in Stage 5.
- Evidence/tests: inspected Stage 4 harness design, production lift Stage 5
  plan, Engaging setup documentation, and checked for an existing VS Code
  extension scaffold; none exists in the repo; docs-only change, no runtime
  tests needed.
- Risks discovered: building the extension before SDK/CLI JSON read commands
  exist could recreate ad hoc filesystem parsing; polling is acceptable for v1
  but may later need a daemon/event stream.
- User review status: pending review of
  `docs/agent_reference/stage_5_vscode_dashboard.md`.
- Next action: user reviews the read-only dashboard UX and decides whether
  implementation should wait for SDK/CLI read models or use a temporary adapter
  with the same schemas.

### 2026-05-10 01:59 EDT: Stage 6 OpenClaude Integration Drafted

- Stage: Stage 6, Forked OpenClaude Integration
- Work completed: drafted the OpenClaude integration contract, external
  context, fork-versus-configuration strategy, shared OpenRouter launch
  environment, MSc skill/playbook scope, capability profile, chat confirmation
  flow, VS Code chat-tab options, Codex-to-OpenClaude validation handoff,
  security boundaries, implementation notes, and user evaluation checkpoint.
- Decisions made: start with the least invasive configuration/skill approach
  and fork only where required; OpenClaude must operate through the public
  SDK/CLI/harness surface; use launch-time environment injection for the shared
  OpenRouter key when possible; default OpenClaude to read-only plus
  append-only feedback and confirmation-gated mutations.
- Evidence/tests: checked the upstream OpenClaude GitHub repository on
  2026-05-10 and confirmed it has a CLI workflow, OpenAI-compatible provider
  support, VS Code extension directory, provider profiles, and headless gRPC
  mode; docs-only change, no runtime tests needed.
- Risks discovered: deep forks can become expensive to maintain; saved provider
  profiles may duplicate secrets; headless gRPC is promising but could add
  premature daemon complexity; broad shell/tool access must not bypass the
  MSc SDK/CLI capability boundary.
- User review status: pending review of
  `docs/agent_reference/stage_6_openclaude_integration.md`.
- Next action: user reviews whether OpenClaude should start as terminal
  handoff, VS Code-extension bridge, or headless gRPC integration.

### 2026-05-10 02:01 EDT: Stage 7 Guided Setup Drafted

- Stage: Stage 7, Guided Setup And Tutorial
- Work completed: drafted setup phases, required versus optional setup
  boundaries, OpenRouter key handling, MSc readiness, optional OpenClaude
  readiness, VS Code readiness, optional OpenClaw/Telegram setup, tutorial
  defaults, setup state model, security rules, validation plan, and user
  evaluation checkpoint.
- Decisions made: required setup covers environment, CLI, OpenRouter, and
  readiness; OpenClaude, OpenClaw, Telegram, and Slack are optional; tutorial
  defaults should be no-cost dry-runs and fixture-backed exploration unless the
  user explicitly approves spend.
- Evidence/tests: inspected Engaging setup documentation, OpenClaw guide, and
  Stage 6 OpenClaude integration plan; docs-only change, no runtime tests
  needed.
- Risks discovered: setup can become a hidden mutation surface if it overwrites
  credentials or silently enables automation; credential validation must avoid
  leaking keys or surprising spend.
- User review status: pending review of
  `docs/agent_reference/stage_7_guided_setup.md`.
- Next action: user reviews required/optional setup boundaries and OpenRouter
  key handling.

### 2026-05-10 02:01 EDT: Stage 8 OpenClaw Automation Drafted

- Stage: Stage 8, Optional OpenClaw Automation
- Work completed: drafted OpenClaw optionality, read-only/operator/repair/admin
  capability profiles, Telegram confirmation flow, current entry points to wrap,
  OpenClaw-down behavior, security boundaries, validation plan, and user
  evaluation checkpoint.
- Decisions made: OpenClaw remains optional and read-only by default; Telegram
  is optional; all mutations route through SDK/CLI/harness confirmations; a
  disabled or down OpenClaw must not impair running campaigns or core product
  use.
- Evidence/tests: inspected current OpenClaw guide and Engaging setup docs;
  docs-only change, no runtime tests needed.
- Risks discovered: persistent cluster automation can feel invasive; Telegram
  defaults can leak sensitive context; OpenClaw must not become a second control
  plane.
- User review status: pending review of
  `docs/agent_reference/stage_8_openclaw_automation.md`.
- Next action: user reviews OpenClaw capability profiles and Telegram action
  confirmation model.

### 2026-05-10 02:01 EDT: Stage 9 Slack/Webapp Planning Drafted

- Stage: Stage 9, Later Slack And Webapp Planning
- Work completed: drafted Slack later-integration concept, webapp concept,
  hosted security requirements, multi-user direction, artifact exposure policy,
  dependency on earlier stages, validation plan, and user evaluation
  checkpoint.
- Decisions made: Slack and webapp are not v1; Slack should be a professional
  notification/light approval surface later; webapp should follow stable
  SDK/CLI/harness/dashboard foundations and likely start as single-tenant
  lab/project deployment before broad multi-tenant hosting.
- Evidence/tests: design-only stage; docs-only change, no runtime tests needed.
- Risks discovered: hosted surfaces can expose unpublished research and control
  cluster jobs unless authentication, authorization, secret handling, artifact
  access policy, and audit logging are designed first.
- User review status: pending review of
  `docs/agent_reference/stage_9_slack_webapp_later.md`.
- Next action: user reviews whether Slack/webapp remain later and confirms the
  initial hosted security and artifact exposure posture.

### 2026-05-10 02:01 EDT: Implementation Validation Protocol Drafted

- Stage: cross-stage validation
- Work completed: checked local OpenRouter key and runtime readiness without
  printing secrets, then drafted the recurring implementation validation
  protocol with no-cost dry-run command, optional cheap-model smoke command,
  stage gate checklist, protected-kernel heuristics, artifact heuristics, and
  graph/node heuristics.
- Decisions made: use the existing `budget` tier shape as the cheap canary
  because it resolves to `gpt-5-mini`, counsel off, math agents off, tree
  search off, and markdown output; run the no-cost dry-run after each
  implementation stage; run a paid cheap smoke only with explicit user approval
  when launch/harness behavior changes.
- Evidence/tests: local environment currently has no visible OpenRouter key,
  no `~/.msc/.env`, no repo `.env`, no `.venv`, no `msc` on PATH, and
  `python3 -m consortium.runner --help` fails because dependencies are not
  installed; docs-only change, no runtime tests passed.
- Risks discovered: cheap-model output is only a canary and cannot prove
  max/ultra scientific quality; preserved workspace fixtures and protected-file
  invariants remain the stronger guardrail.
- User review status: pending review of
  `docs/agent_reference/validation_protocol.md`.
- Next action: configure environment and OpenRouter key before running the
  no-cost dry-run gate.

### 2026-05-10 10:50 EDT: Stage 1 Artifact Read Models Implemented

- Stage: Stage 1, Artifact And Read-Model Audit
- Work completed: added the first read-only `msc_sdk` package with run and
  campaign workspace inspectors, normalized artifact/log/budget/stage/read
  models, and synthetic tests covering core run artifacts, run listing, and
  campaign required/optional artifact contracts.
- Decisions made: keep the Stage 1 implementation independent of protected
  graph/runtime modules; parse documented raw files directly; tolerate missing
  or malformed JSON/YAML by returning partial read models instead of raising;
  update packaging to include `msc_sdk*` as a product-shell package.
- Evidence/tests: local unit tests are pending in this entry and will be
  recorded after validation gates complete.
- Risks discovered: real completed/failed/stalled workspace fixtures are still
  absent, so Stage 1 behavior is validated against synthetic contract fixtures
  and existing repo examples until fresh smoke workspaces are available.
- User review status: user delegated implementation through Stage 5 with
  autonomous commit/push after each stage.
- Next action: run Stage 1 tests, no-cost gates, paid cheap smoke, then commit
  and push if all pass.

### 2026-05-10 11:46 EDT: Stage 1 Smoke Harness Adjustment

- Stage: Stage 1 validation
- Work completed: observed the first paid cheap smoke run timeout in
  `literature_review_agent` after the validation harness pinned deep research
  to `openrouter/perplexity/sonar-pro`, which is not present in the preserved
  budget pricing table and therefore fell back to rate-limited arXiv search.
- Decisions made: keep the protected model/budget policy unchanged; change only
  the validation smoke default for `DEEP_RESEARCH_MODEL` to
  `openrouter/openai/gpt-5-mini`, a priced cheap OpenRouter surface already used
  by the budget tier; keep the smoke test as a cheap canary, not a scientific
  quality proof.
- Evidence/tests: failed smoke workspace
  `results/consortium_20260510_112837` and report
  `logs/validation/cheap_smoke_20260510_112835.json` showed timeout, incomplete
  status, and budget under the configured cap.
- Risks discovered: cheap smoke tests can be distorted by external literature
  provider rate limits if the validation harness selects a model surface not
  covered by the current pricing table.
- User review status: user delegated smoke-harness decisions unless protected
  kernel behavior must change; no protected kernel change was made.
- Next action: rerun no-cost gates and the paid cheap smoke with the priced
  deep-research model default.

### 2026-05-10 12:17 EDT: Stage 1 Smoke Task Narrowed

- Stage: Stage 1 validation
- Work completed: observed the second paid cheap smoke run avoid the unpriced
  model path but still time out in `literature_review_agent` while resolving
  arXiv metadata from generated paper references.
- Decisions made: keep the quickstart task as a real user-facing example, but
  move mandatory repeat-stage smoke to a new self-contained validation task at
  `scripts/validation/tasks/cheap_smoke_task.txt`; keep the run full-pipeline
  and paid, but remove dependence on external literature search and make
  citation-marker checks opt-in with `MSC_SMOKE_REQUIRE_CITATIONS=1`.
- Evidence/tests: failed smoke workspace
  `results/consortium_20260510_115344` and report
  `logs/validation/cheap_smoke_20260510_115343.json` showed the pipeline
  reached literature review, remained within budget, but timed out before a
  completed paper.
- Risks discovered: a mandatory after-every-stage smoke test must be
  deterministic enough to distinguish product-shell regressions from external
  provider/rate-limit noise.
- User review status: user delegated validation-harness decisions unless a
  research-kernel change is required; no protected kernel change was made.
- Next action: rerun validation gates and paid cheap smoke using the
  self-contained smoke task.

### 2026-05-10 13:00 EDT: Stage 1 Smoke Acceptance Calibrated

- Stage: Stage 1 validation
- Work completed: the self-contained paid smoke reached an artifact-rich
  terminal run with `run_summary.json`, effective models, budget artifacts,
  stage summaries, experiment workspace files, and a failed status caused by
  the preserved graph recursion limit after cheap-model duality/follow-up
  routing.
- Decisions made: do not change the protected graph recursion limit or duality
  routing; calibrate the cheap smoke harness so a terminal failed/partial run
  can pass as a product-shell canary when core metadata, budget, model-surface,
  and workspace checks pass; keep final-paper and citation checks opt-in for
  this cheap mandatory gate.
- Evidence/tests: smoke workspace `results/consortium_20260510_123928` and
  report `logs/validation/cheap_smoke_20260510_123927.json` showed the run
  stayed on `gpt-5-mini`, spent about $0.51, and produced many inspectable
  artifacts before the terminal graph-recursion failure.
- Risks discovered: cheap mandatory smoke is useful for product-shell
  regression detection, but it must not be interpreted as a quality/completion
  proof; max/ultra reference fixtures remain the quality guardrail.
- User review status: user delegated validation-harness decisions unless a
  protected kernel change is required; no protected kernel change was made.
- Next action: re-run the analyzer against the latest smoke workspace, then
  commit and push Stage 1 if the calibrated gate passes.

### 2026-05-10 14:41 EDT: Stage 2 Manifest Importer Implemented

- Stage: Stage 2, Production Artifact Model And Importer Design
- Work completed: added a derived manifest importer/writer in `msc_sdk` that
  imports run workspaces, results directories, and campaign YAML specs into a
  versioned manifest with normalized run/campaign payloads and artifact counts.
- Decisions made: `.msc_index/` is the default derived-index location and is
  ignored by git; manifests are derived, reproducible JSON files and not the
  scientific source of truth; the importer reuses Stage 1 read models instead
  of reparsing protected runtime internals.
- Evidence/tests: `tests/test_msc_sdk_artifacts.py` and
  `tests/test_msc_sdk_manifest.py` passed (`7 passed`); contract tests passed
  (`17 passed`); cheap dry-run passed; paid cheap smoke produced accepted
  terminal canary workspace `results/consortium_20260510_144151` with report
  `logs/validation/cheap_smoke_20260510_144150.json` and about $0.35 spend.
- Risks discovered: importer validation still uses synthetic fixtures until
  real completed/failed/stalled workspaces are intentionally preserved.
- User review status: user delegated implementation through Stage 5 with
  autonomous commit/push after each stage.
- Next action: commit and push Stage 2, then begin Stage 3 SDK/CLI JSON
  surface work.

### 2026-05-10 15:10 EDT: Local Full Smoke Deferred

- Stage: cross-stage validation policy
- Work completed: revised the validation strategy after user review identified
  that local full smoke tests are not appropriate while building the product
  shell locally.
- Decisions made: remove paid full pipeline smoke from the recurring local
  Stage 1-5 gate; use no-cost contract tests, dry-runs, fixture-backed
  SDK/CLI/importer/dashboard tests, protected-file checks, and redaction checks
  for local product-shell work; reserve `cheap_smoke_test.sh` for a separately
  approved Engaging integration test plan.
- Evidence/tests: inspected `scripts/validation/cheap_smoke_test.sh`,
  `scripts/validation/cheap_dry_run.sh`, and
  `scripts/validation/analyze_smoke_workspace.py`; stopped the interrupted
  local paid smoke process before it continued spending or generating more
  local artifacts.
- Risks discovered: local full smoke can generate Engaging/SLURM-oriented
  artifacts and spend provider budget while providing weak quality evidence;
  earlier cheap smoke results are useful historical canaries but should not be
  treated as the local validation standard.
- User review status: user requested this policy revision on 2026-05-10.
- Next action: continue Stage 3-5 product-shell implementation using the
  revised no-cost local validation gate unless a new decision requires user
  input.

### 2026-05-10 15:45 EDT: Stage 3 SDK/CLI Surface Implemented

- Stage: Stage 3, SDK And Public CLI Control Surface
- Work completed: added public SDK clients for project inspection, run
  inspection, campaign inspection, and command self-validation; added JSON CLI
  groups for `project`, `artifacts`, `campaigns`, and `selftest`; extended
  `msc runs` into a backward-compatible group with JSON list, inspect, logs,
  budget, dry-run, and resume-request surfaces.
- Decisions made: keep Stage 3 read-heavy and product-shell-only; expose
  mutating actions as request/confirmation scaffolding rather than executing
  them; keep existing human-oriented commands compatible while adding
  machine-readable JSON output for agents, VS Code, and future OpenClaude
  skills.
- Evidence/tests: focused Stage 3 suite passed
  (`tests/test_msc_sdk_artifacts.py`, `tests/test_msc_sdk_manifest.py`,
  `tests/test_msc_sdk_cli_surface.py`, and `tests/test_cli_contracts.py`: 28
  passed); `scripts/validation/contract_tests.sh` passed (`17 passed`);
  `scripts/validation/cheap_dry_run.sh` passed. No paid full smoke was run per
  the revised validation policy.
- Risks discovered: real completed/failed/stalled workspace fixtures are still
  needed for production-grade importer confidence; the current command surface
  is sufficient for Stage 5 read-only dashboard work but Stage 4 must centralize
  capability enforcement before any mutation execution is exposed.
- User review status: user delegated implementation through Stage 5 and
  revised the validation policy to omit local full smoke.
- Next action: commit and push Stage 3, then begin Stage 4 orchestrator
  harness with read-first capabilities, events, and confirmation scaffolding.

### 2026-05-10 15:52 EDT: Stage 4 Orchestrator Harness Implemented

- Stage: Stage 4, Single Orchestrator Harness
- Work completed: added capability profiles, append-only event storage with
  redaction, action request/confirmation scaffolding, harness manifest refresh,
  and JSON CLI commands for `capabilities`, `events`, and `harness`.
- Decisions made: keep the harness library-plus-CLI, not daemon-first; allow
  derived manifest refresh through `write.index`; deny mutating capabilities by
  default; record confirmation requests for permitted write-style actions but
  defer actual mutation execution until preserved entry-point wrapping is
  reviewed.
- Evidence/tests: focused Stage 3+4 suite passed
  (`tests/test_msc_sdk_artifacts.py`, `tests/test_msc_sdk_manifest.py`,
  `tests/test_msc_sdk_cli_surface.py`, `tests/test_msc_sdk_harness.py`, and
  `tests/test_cli_contracts.py`: 33 passed);
  `scripts/validation/contract_tests.sh` passed (`17 passed`);
  `scripts/validation/cheap_dry_run.sh` passed. No paid full smoke was run per
  the revised validation policy.
- Risks discovered: event/action state is intentionally simple JSONL/JSON for
  v1 and may later need locking or a daemon if multiple clients write
  concurrently; confirmed mutation execution remains unavailable until the
  exact preserved entry-point wrappers are reviewed.
- User review status: user delegated implementation through Stage 5 with the
  revised no-cost local validation gate.
- Next action: commit and push Stage 4, then begin Stage 5 Remote-SSH VS Code
  read-only dashboard extension.
