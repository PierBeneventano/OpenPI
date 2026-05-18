# SDK-Native $5 E2E Report - 2026-05-18

## Scope

Objective:

> Investigate, in a toy setting, whether batch normalization changes spectral
> norm growth in a 2-layer MLP trained on synthetic Gaussian blobs. Produce a
> minimal empirical comparison and a short markdown writeup with limitations.

Configuration:

- Template: `target_research`
- Tier: `lean`
- Budget cap: `$5`
- Output: `markdown`
- Disabled: math, counsel
- Human gates: enabled
- Driver: Codex acting as OpenClaude-style SDK coworker and human reviewer

## Result

The fixed campaign `sdk-native-five-dollar-e2e-fixed-20260518-162922` completed
end to end through public SDK/CLI operations:

- graph approved,
- SDK-native execution started,
- planning checkpoint inspected and approved,
- experiment/formalized-results/duality completed,
- pre-writeup checkpoint inspected and approved,
- writeup/review/validation completed.

Final read model:

- Campaign status: `completed`
- Execution status: `completed`
- Current stage: `validation_gate`
- Pending decisions: `0`
- Duality: `passed`
- Deliverables/evidence visible: `35`
- Actionable missing required artifacts: `0`
- Old run events present: `false`
- False campaign failure events during HITL pauses: `false`

## Human Decisions

Planning checkpoint feedback:

> Planning checkpoint approved for fixed five-dollar SDK-native E2E. Continue,
> preserving smoke-test limitations.

Pre-writeup feedback:

> Pre-writeup gate approved. Duality passed; writeup may proceed as a smoke-test
> deliverable, not scientific evidence.

## Artifact Quality

The final paper is coherent enough for a product smoke test. It clearly states
the setup, the deterministic toy spectral-norm comparison, and the limitation
that the result is not scientific evidence.

Representative final claim:

- With batch normalization final spectral norm: `1.14`
- Without batch normalization final spectral norm: `1.69`

The artifact quality is still scaffold-level. It validates campaign mechanics,
not research quality.

## Bugs Found And Fixed

1. HITL pauses emitted `CampaignExecutionFailed`, which made checkpointed human
   decisions look like execution failures. Pauses now rely on
   `HumanDecisionRequired`, `ApprovalRequested`, and
   `CampaignExecutionCheckpointed`; true failures remain `CampaignExecutionFailed`.

2. Completed campaigns reported skipped/disabled stages such as `theory_track`
   and `followup_lit_review` as missing required artifacts. Artifact summaries
   now expose these as `skipped` with `declared_missing_required`, while keeping
   actionable `missing_required` at `0`.

3. `campaigns create --json` emitted the full inspect graph, which is too large
   for OpenClaude/control-loop use. It now returns a concise creation receipt.

## Remaining Product Notes

- The later Research IR migration adds SDK-native model-policy checks and tiny
  budget charges through `RuntimeContext` so spend appears in campaign events
  before model/tool invocation.
- Optional PDF/TEX editorial artifacts remain declared but unproduced in markdown
  runs. This is acceptable, but the UI should keep them visually secondary.
- The experiment still writes an optional "failure report" saying no failure was
  observed. This is not a blocker, but the adapter should eventually avoid
  producing failure-named artifacts on success.
