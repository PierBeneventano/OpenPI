# SDK-Native $0.50 E2E Report - 2026-05-19

## Scope

Campaign: `sdk-native-fifty-cent-e2e-20260519`

Objective:

> Investigate, in a toy setting, whether batch normalization changes the
> spectral norm growth of a 2-layer MLP trained on synthetic Gaussian blobs.
> Produce a minimal empirical comparison and a short markdown writeup with
> limitations.

Configuration:

- Template: `target_research`
- Tier: `lean`
- Budget cap: `$0.50`
- Output: `markdown`
- Disabled: math, counsel
- Human gates: enabled
- Driver: Codex acting as OpenClaude-style SDK coworker and human reviewer

## Result

The campaign completed end to end through public SDK/CLI operations:

- graph approved,
- SDK-native execution started,
- planning checkpoint reviewed and approved,
- empirical track, formalized results, and duality completed,
- pre-writeup checkpoint reviewed and approved,
- resource prep, paper contract, writeup, proofreading, review, and validation completed.

Final read model:

- Campaign status: `completed`
- Execution status: `completed`
- Current stage: `validation_gate`
- Pending decisions: `0`
- Duality: `passed`
- Deliverable/evidence artifacts visible: `35`
- Actionable missing required artifacts: `0`
- Legacy attempts: `0`
- Model policy violations: `0`
- Final projected spend: `$0.0011`
- Remaining budget: `$0.4989`

## Human Decisions

Planning checkpoint feedback:

> Planning checkpoint approved for fifty-cent SDK-native E2E. Scope is
> acceptable: lean empirical smoke path, math disabled, markdown output,
> preserve smoke-test limitations.

Pre-writeup feedback:

> Pre-writeup checkpoint approved for fifty-cent SDK-native E2E. Duality passed
> and claim is explicitly smoke-test-only; proceed to markdown writeup and
> review.

## Artifact Quality

The final paper is coherent enough for a product smoke test. It states the toy
setup, reports final spectral norms, and clearly limits the result to product
validation rather than science.

Final reported values:

- With batch normalization: `1.14`
- Without batch normalization: `1.69`

## Product Finding

During the run, resumed execution initially showed lower spend than the previous
segment because the SDK-native budget ledger was segment-local. The SDK now
hydrates the ledger from prior `BudgetSpent` events for the same execution, and
workspace projection sums spend events cumulatively.
