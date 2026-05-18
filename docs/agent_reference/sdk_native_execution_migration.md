# SDK-Native Execution Migration

## Product Boundary

Campaign execution now has a product-native path:

```text
CampaignGoal -> ResearchGraphTemplate -> GraphSpec -> StageSpec -> RuntimeContext -> EventRecord -> ReadModel
```

The SDK-native executor is started with:

```bash
msc campaigns start <campaign> --tier lean --budget 25 --output-format markdown --no-math --no-counsel --json
msc campaigns continue <campaign> --json
```

`msc run` remains available as a legacy adapter/diagnostic launcher, but it is no
longer the preferred product authority for campaign control flow, gates,
current stage, artifact completion, or human decisions.

## Implemented V0 Path

The first SDK-native path is intentionally lean:

- `target_research`
- `lean`
- markdown output
- math disabled by default
- counsel disabled by default
- deterministic toy experiment artifacts
- required duality check
- human pause after graph/planning milestone
- human pause before writeup/resource preparation

This path can complete the toy batch-normalization spectral-norm smoke campaign
without LangGraph status files or raw runner supervision.

## HITL Semantics

Human-in-the-loop pauses are represented as campaign approvals with
`target_type=kernel_decision`.

Important pause reasons:

- `pause_after_stage`: used for the planning milestone.
- `pause_before_stage`: used before writeup/resource preparation.
- `duality_failed`: used when the duality gate fails and must not be silently
  routed into autonomous follow-up work.

OpenClaude and UI clients should read `workspace.pending_decisions` and approve
with:

```bash
msc campaigns approve <approval-id> --json
```

Then continue with:

```bash
msc campaigns continue <campaign> --json
```

## Duality Gate

The duality check is a core scientific gate. A failed duality check emits:

- `DualityCheckStarted`
- `DualityCheckCompleted`
- `HumanDecisionRequired`
- a pending `kernel_decision`
- safe actions:
  - `revise-goals`
  - `rerun-literature`
  - `rerun-experiment-track`
  - `reroute`
  - `stop-campaign`

Writeup artifacts are not produced after failed duality until the researcher or
OpenClaude resolves the decision through the SDK.

## Legacy Compatibility

Legacy tables such as `runs` still exist as compatibility indexes. Raw process
details remain diagnostics. Product behavior should be implemented against
campaign events, graph specs, artifact contracts, approvals, and workspace read
models.

