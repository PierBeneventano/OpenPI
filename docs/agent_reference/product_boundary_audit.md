# Product Boundary Audit

This note captures the failure mode found during the north-star migration:
new product concepts can look correct in the SDK/UI while still crossing into a
legacy runtime surface that speaks older names.

## Boundary Rule

Every product-to-runtime crossing needs an explicit adapter and a regression
test. Do not let UI, OpenClaude, or campaign read models call a legacy CLI,
status file, model registry, or run table by assumption.

Critical adapters:

- Product tiers `scaffold`, `lean`, `standard`, `serious`, `ultra` must resolve
  to the legacy runtime tiers accepted by `msc run`.
- Product templates should default to `target_research`, while old templates
  remain compatibility aliases.
- `target_research` graph shape must come from `msc_sdk.feedback_graph`, not
  scattered stage lists in docs, UI, tier policy, or legacy runtime shortcuts.
- Optional model overrides must be validated before spawning the runner.
- Dry-run success must project as `dry_run_passed` / approved, even when older
  compatibility events used run-centric names.
- Failed runtime commands must surface as dashboard errors, not only as process
  log lines.
- OpenClaude should report the credential source it actually uses; explicit
  config-dir credentials take precedence for OpenClaude launch/readiness.

## Regression Expectations

The following behaviors should stay covered:

```text
Create campaign -> auto-start dry validation -> approved / dry_run_passed
Product tier standard -> accepted by msc run -> legacy runtime medium
Invalid model override -> rejected before runner spawn
Legacy dry-run failure bug event -> hidden from pending decisions
OpenClaude config-dir key -> reports config-dir without leaking the secret
target_research -> 29 feedback top-level nodes, followup_lit_review, iterate overlay, subgraphs, router retry caps
```

When adding a new product concept, add its adapter test before wiring it into
the cockpit or OpenClaude harness.
