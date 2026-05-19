# Product Boundary Audit

This note captures the failure mode found during the north-star migration:
new product concepts can look correct in the SDK/UI while still crossing into
non-campaign runtime surfaces that speak older names.

## Boundary Rule

Every product-to-runtime crossing needs an explicit SDK-native adapter and a
regression test. UI, OpenClaude, and campaign read models must not call separate
run CLIs, status files, or run tables by assumption.

Critical boundaries:

- Product tiers `scaffold`, `lean`, `standard`, `serious`, `ultra` must resolve
  through SDK-owned tier/model policy.
- Product templates should default to `target_research`.
- `target_research` graph shape must come from `msc_sdk.feedback_graph`, not
  scattered stage lists in docs, UI, or tier policy.
- Optional model overrides must be validated before SDK-native execution.
- Failed runtime commands must surface as typed campaign errors.
- OpenClaude should report the credential source it actually uses; explicit
  config-dir credentials take precedence for OpenClaude launch/readiness.

## Regression Expectations

The following behaviors should stay covered:

```text
Create campaign -> SDK-native start/continue -> campaign execution events
Product tier standard -> SDK tier/model policy
Invalid model override -> rejected before execution
OpenClaude config-dir key -> reports config-dir without leaking the secret
target_research -> 29 feedback top-level nodes, followup_lit_review, iterate overlay, subgraphs, router retry caps
```

When adding a new product concept, add its adapter test before wiring it into
the cockpit or OpenClaude harness.
