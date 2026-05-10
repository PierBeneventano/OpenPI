# Open Questions For Product Scope

These questions should guide future updates to this directory.

## Preservation Expectations

1. Answered and approved on 2026-05-10: see
   [`stage_0_preservation_inventory.md`](stage_0_preservation_inventory.md)
   for the protected-module inventory.
2. Answered: prompts are part of the protected research behavior and should stay
   the same while rebuilding the product harness.
3. Answered: layers beyond the data/artifact layer can be rebuilt as product
   shell layers, including UI, control plane, adapter, execution substrate,
   notifications, and derived indexes. They must preserve research-kernel
   behavior and artifact semantics.
4. Partially answered on 2026-05-10: see
   [`stage_1_artifact_read_model_audit.md`](stage_1_artifact_read_model_audit.md).
   Checked-in campaign specs and archive manifests are fixture candidates, but
   the local `results/` directory does not contain real run workspaces. The
   canonical completed/failed/stalled workspace fixtures still need to be
   provided, restored, or explicitly deferred.
5. Answered in draft: for product-shell changes, use the recurring validation
   gate in [`validation_protocol.md`](validation_protocol.md): static checks,
   protected-file checks, unit tests, snapshot tests, importer replay,
   no-cost dry-runs, fixture-backed SDK/CLI/dashboard checks, and redaction
   checks. Full paid smoke tests are deferred until a separate Engaging
   integration testing plan is approved.
6. Answered: max-mode or ultra-mode behavior should be treated as frozen unless
   the user approves a separate kernel-change validation plan and has budget to
   rerun a reference experiment.
7. Answered: anything touching functional research logic should not be touched.
   Artifact storage, reading, and organization can be ripped out and rebuilt
   into production-grade infrastructure as long as result semantics and quality
   are preserved.

## VS Code And Operator Experience

1. Answered in draft on 2026-05-10: the first VS Code extension should assume
   Remote SSH into Engaging as the primary v1 workflow, while keeping local
   clone support possible later. See
   [`stage_5_vscode_dashboard.md`](stage_5_vscode_dashboard.md).
2. Answered: VS Code v1 should be a read-only dashboard first.
3. Should the extension call existing scripts directly at first, or should we
   introduce a small `msc-control` daemon immediately?
4. Answered in draft: the first GUI workflows are graph/status, artifact
   browsing, logs, budget/status panels, events, and final paper/review preview.
   OpenClaude chat is a Stage 6 handoff/placeholder, not required in Stage 5.
5. Answered: mutation actions such as launch, repair, abort, budget increase,
   stage status override, archive, prompt/task rewrite, and artifact mutation
   require confirmation.
6. Should WhatsApp and VS Code share a single message/action history?
7. Should the VS Code GUI be allowed to edit campaign YAML and task files after
   read-only v1, or should editing stay inside the orchestrator harness?

## SDK, CLI, And Orchestrator Harness

1. Answered on 2026-05-10: build the Python SDK and CLI together, with the SDK
   as the horizontal programmatic API and the CLI as the full expressive
   operator/agent interface. See
   [`stage_3_sdk_cli_control_surface.md`](stage_3_sdk_cli_control_surface.md).
2. Answered in draft: the public CLI should cover projects, artifacts, runs,
   campaigns, events, capabilities, and self-validation, with JSON output and
   SDK parity for external orchestrators such as OpenClaude and OpenClaw.
3. Answered in draft on 2026-05-10: begin with an SDK-backed local
   orchestration library plus CLI action runner. Add a daemon later only if VS
   Code live views, OpenClaude RPC, multi-client coordination, or hosted mode
   needs it. See
   [`stage_4_orchestrator_harness.md`](stage_4_orchestrator_harness.md).
4. Answered in draft: permissions/capabilities should be represented as
   explicit read/write/mutate capabilities enforced by the single orchestrator
   harness, with read-only defaults and confirmation-gated mutation profiles.
5. Should the SDK support local-only, Engaging Remote SSH, and hosted modes from
   the beginning, or should Engaging Remote SSH be the first-class target?

## OpenClaw And Control Plane

1. Answered: OpenClaw should be optional bonus automation for users who choose
   to run a script on Engaging and interact via Telegram while away.
2. Answered: OpenClaw should not be assumed to have full access to all code and
   artifacts by default; expose a constrained SDK/CLI surface instead.
3. Should OpenClaw actions be represented as auditable structured commands
   before execution?
4. Answered in draft: the control plane should be local/Remote-SSH first.
   Any daemon should be loopback-local by default; nonlocal/tunnel access
   requires authentication and explicit approval.
5. Answered in draft: if OpenClaw or a future daemon is down, running campaigns
   should continue. Users and agents should still inspect status through CLI
   file readers and current entry points.

## OpenClaude Integration

1. Answered in draft on 2026-05-10: start with the least invasive
   configuration/skill integration and fork OpenClaude only where embedding,
   capability constraints, launch behavior, or validation requires it. See
   [`stage_6_openclaude_integration.md`](stage_6_openclaude_integration.md).
2. Answered in draft: OpenClaude should use the same OpenRouter key through
   launch-time OpenAI-compatible environment variables rather than requiring
   duplicate unmanaged key entry by default.
3. Answered in draft: OpenClaude v1 receives read capabilities plus append-only
   feedback; mutating actions require the Stage 3/4 confirmation flow.
4. Should the first chat tab launch OpenClaude in an integrated terminal, bridge
   to OpenClaude's VS Code extension, or wait for headless gRPC integration?
5. Should append-only feedback be enabled in the first OpenClaude integration,
   or should v1 be strictly read-only until the revision request flow is built?

## Setup And Tutorial

1. Answered in draft on 2026-05-10: setup should be guided and staged, with
   OpenRouter/API readiness required and OpenClaude, OpenClaw, Telegram, and
   Slack clearly optional. See
   [`stage_7_guided_setup.md`](stage_7_guided_setup.md).
2. Answered in draft: setup should prefer user-private credential storage,
   preserve understandable credential precedence, and avoid overwriting
   existing credentials without confirmation.
3. Answered in draft: tutorial paths should default to no-cost dry-runs and
   fixture-backed exploration; full paid smoke runs should not appear in local
   tutorial paths and require a separate Engaging integration-test decision.

## OpenClaw Optional Automation

1. Answered in draft on 2026-05-10: OpenClaw remains optional and read-only by
   default, with operator/repair/admin capabilities requiring explicit opt-in
   and confirmation. See
   [`stage_8_openclaw_automation.md`](stage_8_openclaw_automation.md).
2. Answered in draft: Telegram is optional and should use concise,
   redacted notifications plus confirmation-token action flows.
3. Answered in draft: if OpenClaw is down, campaigns continue and users can
   still inspect through VS Code, OpenClaude, CLI, and current entry points.

## Future Webapp Scope

1. Answered in draft on 2026-05-10: webapp planning is later and should follow
   artifact model, SDK/CLI, harness, VS Code dashboard, setup, and OpenClaude
   stabilization. See
   [`stage_9_slack_webapp_later.md`](stage_9_slack_webapp_later.md).
2. Pending: decide future billing/key model: bring-your-own OpenRouter key,
   lab-managed key, or product-managed billing.
3. Answered in draft: single-tenant lab/project deployment should likely come
   before broad multi-tenant SaaS.
4. Partially answered in draft: artifact exposure needs explicit classification
   for public/shareable, lab-private, project-private, sensitive logs/traces,
   and secret-adjacent data.
5. Pending: decide which parts of the Engaging-first workflow must survive when
   moving to a hosted webapp.
