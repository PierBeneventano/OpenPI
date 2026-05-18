# Legacy Pruning Boundary

## What Was Removed

This pruning pass removes old execution truth from the product surface:

- old run events are no longer emitted or projected by SDK-owned code,
- runner HTTP milestone approval is removed from the public CLI/SDK contract,
- PID liveness and raw process status are no longer workspace truth,
- campaign projection no longer falls back to SQLite rows, graph snapshots, or
  runtime workspace scans,
- target graph nodes now expose `sdk_native` adapter posture by default.

The current product truth is:

```text
CampaignExecution* events -> CampaignEventProjector -> workspace/read models
```

## Remaining Quarantine

`msc run`, `consortium.graph`, and `msc_sdk.stage_runtime` still exist as an
archived adapter path for old experiments and diagnostics. They are not allowed
to define campaign state, graph state, human gates, artifact completion, or
OpenClaude control behavior.

Safe use:

- compare historical outputs,
- diagnose an old run,
- mine prompts or artifact examples for a future native adapter.

Unsafe use:

- decide campaign status from status JSON,
- approve gates through runner-owned HTTP endpoints,
- infer artifact completion from workspace scans,
- add new product behavior to the archived runner path.

## Next Deletion Trigger

Delete the archived adapter path once SDK-native adapters cover provider-backed
literature search, experiment execution, writeup/review, and artifact validation
at the quality level needed for researcher-facing use. If a future feature needs
logic from the old path, extract the useful idea into a small SDK-native adapter
first; do not optimize the archived path.
