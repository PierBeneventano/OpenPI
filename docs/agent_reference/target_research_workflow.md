# Target Research Workflow

This document describes the product-facing SDK representation of the researcher
feedback snapshot in [`feedback.md`](feedback.md). The snapshot remains raw and
unedited; for V0 graph shape it is authoritative. The SDK should grow to encode
that graph faithfully, rather than simplifying the graph to fit older SDK
limitations.

## Workflow

```text
campaign goal
  -> persona_council
  -> literature_review_agent
  -> lit_review_gate
  -> brainstorm_agent
  -> brainstorm_artifact_gate
  -> formalize_goals_entry
  -> formalize_goals_agent
  -> research_plan_writeup_agent
  -> track_decomposition_gate
  -> milestone_goals
  -> theory_track and/or experiment_track
  -> track_merge
  -> verify_completion
  -> formalize_results_agent
  -> duality_check
  -> duality_gate
  -> followup_lit_review on failed duality
  -> resource_preparation_agent
  -> paper_contract_builder
  -> writeup_agent
  -> writeup_artifact_gate
  -> proofreading_entry
  -> proofreading_agent
  -> proofread_gate
  -> reviewer_agent
  -> review_gate
  -> milestone_review
  -> validation_gate
```

The canonical SDK source is `msc_sdk.feedback_graph`. It encodes all 29
top-level nodes from the feedback Mermaid graph, the `iterate_entry ->
persona_council -> iterate_router` overlay, the T1-T6 theory agents, the E1-E5
experiment agents, router labels, retry caps, fan-out/fan-in, feature flags,
and state read/write contracts.

Revision is not a separate product island. Iterate mode prepends
`iterate_entry`, reuses `persona_council`, and classifies the revision through
`iterate_router` as `writing_only`, `needs_research`, or `needs_full_rethink`.
`iterate_start_stage_override` can jump to any registered SDK node for surgical
reruns.

`enable_duality_check=False` may collapse
`formalize_results_agent -> duality_check -> duality_gate -> resource_preparation_agent`
into `formalize_results_agent -> resource_preparation_agent`, but the default
scientific product posture keeps duality required. `math_enabled=False` skips
the theory track and routes only to the experiment track.

## Product Concepts

- Persona councils challenge research framing and produce visible verdicts.
- Model councils are a high-stakes execution posture for selected stages.
- Deterministic gates validate artifacts, route the graph, and expose reasons.
- Duality check is required before paper/writeup generation.
- OpenClaude is the local coworker that helps the researcher interpret state
  and call typed SDK operations.
- OpenClaw is optional wrapper infrastructure and not a separate authority.

## Default Tiers

```text
scaffold: zero-spend graph/artifact planning
lean: single-model exploratory execution
standard: persona council plus single-model specialist stages
serious: persona council, duality check, selected model councils
ultra: empirical-grounding persona and broader model-council use
```

## Required Human Decisions

The product should pause for failed gates, scientific direction changes,
expensive work, repair, reroute, rewind, and budget increases. Cheap bounded
mechanical retries may remain automatic when the stage policy says they do not
change the scientific direction.
