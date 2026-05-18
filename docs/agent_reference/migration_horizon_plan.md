# Migration Horizon Plan

This is the long-horizon implementation map for migrating the product to the
current north star. It folds the researcher intent snapshot in
[`feedback.md`](feedback.md) into the SDK-first product architecture by treating
the snapshot as the authoritative V0 graph-shape reference and representing it
through product-facing SDK abstractions.

## Target State

The product source of truth is:

```text
CampaignGoal -> ResearchGraphTemplate -> GraphSpec -> StageSpec
  -> RuntimeContext -> EventRecord -> ReadModel
```

The researcher sees a campaign, a graph, decisions, deliverables, OpenClaude
steering, and diagnostics on request. Internal process/session IDs may remain
for compatibility, but they are not the product center.

## Cutover Order

1. Keep `feedback.md` raw and use
   [`target_research_workflow.md`](target_research_workflow.md) plus
   `msc_sdk.feedback_graph` as the faithful SDK representation of the feedback
   graph.
2. Make campaign-execution events primary while projecting legacy `Run*` events
   as compatibility aliases.
3. Compile the target workflow template into `GraphSpec`/`StageSpec` with all
   29 feedback top-level nodes, nested theory/experiment subgraphs, router
   labels, retry caps, fan-out/fan-in, council policy, tier policy, validators,
   pause policy, artifact contracts, state read/write contracts, feature flags,
   and revision routes.
4. Route council and duality behavior through `RuntimeContext.run_council(...)`.
5. Block writeup/resource preparation until the required duality gate is
   complete.
6. Require human decisions for failed gates, scientific direction changes,
   repair, reroute, rewind, budget increases, and expensive work.
7. Make campaign event projection the public read authority for UI, CLI,
   OpenClaude, and optional OpenClaw wrappers.
8. Hide raw engine artifacts by default; expose them through diagnostics.
9. Replace legacy adapters stage by stage only after the product read model is
   stable.

## Acceptance Checklist

- A new campaign defaults to the target research workflow.
- The graph exposes councils, duality, tier/model posture, validators, pause
  policy, safe routes, router labels, retry caps, feature flags, subgraphs, and
  state field contracts.
- `target_research` includes `followup_lit_review`, iterate-mode entry/router
  nodes, T1-T6 theory agents, and E1-E5 experiment agents in the SDK graph
  template.
- The SDK emits campaign-execution, council, and duality events.
- `RunStarted` / `RunExited` remain readable compatibility events.
- The workspace read model includes council summaries, duality status, pending
  decisions, safe next actions, deliverables, diagnostics, and model/tier
  policy.
- OpenClaude can inspect, steer, request evidence, propose repair, and change
  tier/model policy only through typed SDK/CLI operations.
- OpenClaw remains read-only or wrapper-only.
- Writeup cannot proceed before duality has passed or a human has chosen a safe
  recovery route.

## Verification

Run these focused checks during the migration:

```bash
pytest tests/test_research_kernel.py tests/test_stage_contracts.py -q
pytest tests/test_campaign_store.py tests/test_agent_operation_contract.py -q
pytest tests/test_openclaude_integration.py tests/test_openclaw_optional.py -q
npm test --prefix extensions/vscode-msc
```
