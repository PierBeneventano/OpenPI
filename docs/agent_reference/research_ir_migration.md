# Research IR Migration

This note records the product boundary after migrating the SDK from
stage/artifact completion toward a research-native IR.

## Product Truth

SDK-native campaign execution is the product path. The public state model is
made from campaign events and exposed through five research aisles:

- `aim`: objective, tier, budget, output format, and model posture.
- `map`: graph, current stage, routes, gates, and skipped paths.
- `evidence`: claims, evidence records, objections, limitations, and visible
  deliverables/evidence artifacts.
- `decisions`: pending decisions, gate verdicts, objections, feedback, and safe
  next actions.
- `diagnostics`: raw events, raw/diagnostic artifacts, model policy violations,
  completion evaluations, and legacy runtime attempts.

Legacy live-run attempts are diagnostics only. They may appear under
`diagnostics.legacy_attempts`, but they cannot mark product execution as
running, completed, failed, or gate-passed.

## First-Class Research Objects

The kernel now records:

- `ClaimRecorded`
- `EvidenceRecorded`
- `ObjectionRecorded`
- `GateVerdictRecorded`
- `StageCompletionEvaluated`
- `StageRetryScheduled`
- `StageTimeoutReached`
- `ModelPolicyViolation`

Gates must produce structured verdicts. Contradictory verdict payloads, such as
`passed=false` with `verdict=pass`, become human decisions instead of silent
progress.

## Completion And Policy

`StageSpec` owns completion, gate, retry, timeout, and model policy. Artifact
existence is necessary but not sufficient for scientific completion.

Markdown writeup completion requires the markdown paper and gate decision.
PDF/TEX outputs stay optional unless the campaign explicitly requests a PDF or
LaTeX output path.

Model use is checked through `RuntimeContext.use_model(...)`, with budget charge
attempted before the invocation. Disallowed models emit `ModelPolicyViolation`
and pause for human approval.

## OpenClaude Boundary

OpenClaude should prefer the five aisle fields from
`msc campaigns workspace <campaign> --json` and the context pack. Raw files,
status JSON, logs, prompts, process IDs, and legacy runtime attempts are
diagnostics only.
