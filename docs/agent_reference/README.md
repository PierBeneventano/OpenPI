# Agent Reference

This directory is the canonical working reference for reengineering
PoggioAI/MSc into a product while preserving the research engine that made the
prototype valuable.

The old proof-of-concept staging notes have been pruned. The current reference
set is intentionally smaller and more product-forward:

- [Target Product Model](target_product_model.md): signed-off north star for
  the campaign-as-research-attempt product model.
- [Researcher Feedback Snapshot](feedback.md): raw, intentionally unedited
  snapshot of the originating researcher's current target-engine intent. Treat
  it as product/research intent, not as a binding implementation map.
- [Core SDK Implementation Plan](core_sdk_implementation_plan.md): plan for
  implementing the SDK around the target model.
- [Migration Horizon Plan](migration_horizon_plan.md): long-horizon cutover map
  from legacy execution to the SDK/event/read-model product.
- [Target Research Workflow](target_research_workflow.md): faithful SDK
  representation of the feedback graph with product-facing abstractions.
- [OpenClaude Coworking Contract](openclaude_coworking_contract.md): safe
  operation boundary for the local AI coworker.
- [Product Boundary Audit](product_boundary_audit.md): migration guardrails for
  product terms crossing into legacy runtime surfaces.
- [Extension UI Implementation Plan](extension_ui_implementation_plan.md): plan
  for making the VS Code extension follow the target researcher experience.
- [V1 Product Requirements](v1_product_requirements.md): target user, product
  promise, required workflows, OpenClaude steering, artifact expectations, and
  acceptance criteria.
- [Current Failure Points](current_failure_points.md): architectural blockers
  that prevent the V1 requirements from being implemented cleanly.
- [Reengineering Storyboard](reengineering_storyboard.md): staged overhaul plan
  for moving from prototype internals to an elegant local-first product.
- [Research Engine Invariants](../research_engine_invariants.md): what must be
  preserved in any redesign.
- [Architecture Overview](../architecture.md): current runtime architecture and
  product-facing local campaign architecture.
- [Local-First Campaign Workspace](../local_first_campaign_workspace.md): current
  state of the campaign store, bundle, event, and VS Code migration.

## Working Rule

The full historical research behavior is preserved for V1, and the graph shape
described in [feedback.md](feedback.md) is the authoritative V0 target. The
legacy LangGraph implementation remains an adapter; the SDK graph template is
the product contract that must be able to express the feedback graph directly:
persona/model councils, explicit gates, duality checking, revision loops,
subgraphs, router labels, retry caps, and human steerability.

The work is to make the engine inspectable, steerable, auditable, and
product-grade:

```text
preserve research behavior
extract explicit contracts
connect runtime to campaign state
expose expressive SDK/CLI controls
make OpenClaude the natural-language steering harness
make VS Code the local research cockpit
```

Lean or cheaper engine variants are later optimizations, not V1 goals.
