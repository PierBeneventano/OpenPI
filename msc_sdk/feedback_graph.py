"""Feedback-derived target research graph definition.

``docs/agent_reference/feedback.md`` is the preserved raw researcher snapshot.
This module is the SDK-owned structured representation of that graph shape:
the 29 top-level Mermaid nodes, iterate overlay, nested theory/experiment
subgraphs, routers, retry caps, feature-flagged routes, and state contracts.
"""

from __future__ import annotations

from .kernel.models import (
    FeatureFlagRoute,
    GraphTemplateSpec,
    RetryPolicy,
    RouteCondition,
    RouteSpec,
    RouterSpec,
    StateFieldContract,
    SubgraphSpec,
)


FEEDBACK_GRAPH_REFERENCE = "docs/agent_reference/feedback.md"
TARGET_RESEARCH_TEMPLATE_ID = "target_research"

FEEDBACK_MAIN_STAGE_IDS: tuple[str, ...] = (
    "persona_council",
    "literature_review_agent",
    "lit_review_gate",
    "brainstorm_agent",
    "brainstorm_artifact_gate",
    "formalize_goals_entry",
    "formalize_goals_agent",
    "research_plan_writeup_agent",
    "track_decomposition_gate",
    "milestone_goals",
    "theory_track",
    "experiment_track",
    "track_merge",
    "verify_completion",
    "formalize_results_agent",
    "duality_check",
    "duality_gate",
    "followup_lit_review",
    "resource_preparation_agent",
    "paper_contract_builder",
    "writeup_agent",
    "writeup_artifact_gate",
    "proofreading_entry",
    "proofreading_agent",
    "proofread_gate",
    "reviewer_agent",
    "review_gate",
    "milestone_review",
    "validation_gate",
)

FEEDBACK_ITERATE_STAGE_IDS: tuple[str, ...] = (
    "iterate_entry",
    "persona_council",
    "iterate_router",
)

FEEDBACK_THEORY_AGENT_STAGE_IDS: tuple[str, ...] = (
    "math_literature_agent",
    "math_proposer_agent",
    "math_prover_agent",
    "math_rigorous_verifier_agent",
    "math_empirical_verifier_agent",
    "proof_transcription_agent",
)

# The researcher-facing table names T1-T6. The SDK also models deterministic
# repair/review gates inside the theory branch so diagnostics can show the full
# control structure without flattening the six scientific agents.
FEEDBACK_THEORY_SUBGRAPH_STAGE_IDS: tuple[str, ...] = (
    "math_literature_agent",
    "math_proposer_agent",
    "goal_tag_validation_gate",
    "math_prover_agent",
    "math_rigorous_verifier_agent",
    "human_review_gate",
    "math_empirical_verifier_agent",
    "proof_transcription_agent",
    "theory_track_repair_gate",
)

FEEDBACK_EXPERIMENT_SUBGRAPH_STAGE_IDS: tuple[str, ...] = (
    "experiment_literature_agent",
    "experiment_design_agent",
    "experimentation_agent",
    "experiment_verification_agent",
    "experiment_transcription_agent",
)


def target_research_graph_template() -> GraphTemplateSpec:
    """Return the faithful SDK representation of the feedback target graph."""

    return GraphTemplateSpec(
        id=TARGET_RESEARCH_TEMPLATE_ID,
        entry_stage_id="persona_council",
        main_stage_ids=FEEDBACK_MAIN_STAGE_IDS,
        iterate_stage_ids=FEEDBACK_ITERATE_STAGE_IDS,
        subgraphs=_subgraphs(),
        routers=_routers(),
        feature_flags=_feature_flags(),
        state_fields=_state_fields(),
        metadata={
            "schema": "msc.graph_template.feedback.v1",
            "source": "feedback_graph",
            "rawFeedbackSnapshot": FEEDBACK_GRAPH_REFERENCE,
            "fidelityTarget": "feedback.md Mermaid graph and master node table",
            "topLevelNodeCount": 29,
            "dualityRequiredByDefault": True,
            "openClawRole": "optional_wrapper_only",
        },
    )


def router_for_stage(stage_id: str) -> RouterSpec | None:
    return target_research_graph_template().router_map().get(stage_id)


def state_field_contracts_for_stage(stage_id: str) -> tuple[StateFieldContract, ...]:
    return target_research_graph_template().state_fields_for_stage(stage_id)


def subgraph_for_stage(stage_id: str) -> SubgraphSpec | None:
    for subgraph in target_research_graph_template().subgraphs:
        if stage_id in subgraph.stage_ids or stage_id == subgraph.id:
            return subgraph
    return None


def _subgraphs() -> tuple[SubgraphSpec, ...]:
    return (
        SubgraphSpec(
            id="theory_track",
            entry_stage_id="math_literature_agent",
            stage_ids=FEEDBACK_THEORY_SUBGRAPH_STAGE_IDS,
            exit_stage_id="track_merge",
            routes=(
                RouteSpec(target="math_proposer_agent"),
                RouteSpec(target="goal_tag_validation_gate"),
                RouteSpec(target="math_prover_agent"),
                RouteSpec(target="math_rigorous_verifier_agent"),
                RouteSpec(target="human_review_gate"),
                RouteSpec(target="math_empirical_verifier_agent"),
                RouteSpec(target="proof_transcription_agent"),
                RouteSpec(target="theory_track_repair_gate"),
                RouteSpec(target="track_merge", kind="join", condition="theory_complete"),
            ),
            metadata={
                "label": "Theory Track",
                "feedbackAgentStageIds": list(FEEDBACK_THEORY_AGENT_STAGE_IDS),
                "adapter": "sdk_native",
                "collapsedByDefault": True,
            },
        ),
        SubgraphSpec(
            id="experiment_track",
            entry_stage_id="experiment_literature_agent",
            stage_ids=FEEDBACK_EXPERIMENT_SUBGRAPH_STAGE_IDS,
            exit_stage_id="track_merge",
            routes=(
                RouteSpec(target="experiment_design_agent"),
                RouteSpec(target="experimentation_agent"),
                RouteSpec(target="experiment_verification_agent"),
                RouteSpec(target="experiment_transcription_agent"),
                RouteSpec(target="track_merge", kind="join", condition="experiment_complete"),
            ),
            metadata={
                "label": "Experiment Track",
                "feedbackAgentStageIds": list(FEEDBACK_EXPERIMENT_SUBGRAPH_STAGE_IDS),
                "adapter": "sdk_native",
                "collapsedByDefault": True,
            },
        ),
    )


def _routers() -> tuple[RouterSpec, ...]:
    return (
        RouterSpec(
            id="iterate_persona_exit_router",
            source_stage_id="persona_council",
            branches=(
                RouteCondition(
                    label="iterate_start_stage_override",
                    expression="iterate_mode and iterate_start_stage_override in registered_nodes",
                    description="Surgical revision jump to any registered SDK node.",
                    metadata={"overlay": "iterate"},
                ),
                RouteCondition(
                    label="default_iterate_classification",
                    target="iterate_router",
                    expression="iterate_mode and no override",
                    metadata={"overlay": "iterate"},
                ),
            ),
        ),
        RouterSpec(
            id="_iterate_route_selector",
            source_stage_id="iterate_router",
            branches=(
                RouteCondition(label="writing_only", target="resource_preparation_agent"),
                RouteCondition(label="needs_research", target="literature_review_agent"),
                RouteCondition(label="needs_full_rethink", target="brainstorm_agent"),
            ),
        ),
        RouterSpec(
            id="lit_review_gate_router",
            source_stage_id="lit_review_gate",
            branches=(
                RouteCondition(
                    label="infeasible",
                    target="persona_council",
                    retry=RetryPolicy(max_attempts=2, counter="lit_review_attempts"),
                    description="Loop to framing if literature feasibility fails.",
                ),
                RouteCondition(label="feasible", target="brainstorm_agent"),
            ),
        ),
        RouterSpec(
            id="_critical_failure_check:brainstorm",
            source_stage_id="brainstorm_agent",
            branches=(
                RouteCondition(label="critical_failure", terminal=True, expression="critical_failure is set"),
                RouteCondition(label="continue", target="brainstorm_artifact_gate"),
            ),
        ),
        RouterSpec(
            id="brainstorm_artifact_gate_router",
            source_stage_id="brainstorm_artifact_gate",
            branches=(
                RouteCondition(
                    label="missing_artifacts",
                    target="brainstorm_agent",
                    retry=RetryPolicy(max_attempts=2, counter="brainstorm_artifact_retries"),
                    metadata={"sourceCondition": "missing_or_invalid_artifacts"},
                ),
                RouteCondition(label="halt", terminal=True, expression="critical_failure is set"),
                RouteCondition(label="advance", target="formalize_goals_entry", metadata={"sourceCondition": "valid"}),
            ),
        ),
        RouterSpec(
            id="_critical_failure_check:formalize_goals",
            source_stage_id="formalize_goals_entry",
            branches=(
                RouteCondition(label="critical_failure", terminal=True, expression="critical_failure is set"),
                RouteCondition(label="continue", target="formalize_goals_agent"),
            ),
        ),
        RouterSpec(
            id="track_router",
            source_stage_id="milestone_goals",
            branches=(
                RouteCondition(
                    label="math_enabled_and_theory_questions",
                    target="theory_track",
                    expression="math_enabled and theory_questions present",
                    feature_flag="math_enabled",
                    metadata={"fanout": True},
                ),
                RouteCondition(
                    label="empirical_questions_or_default",
                    target="experiment_track",
                    expression="empirical_questions present or theory disabled",
                    metadata={"fanout": True},
                ),
            ),
            metadata={"fanout": True, "join": "track_merge"},
        ),
        RouterSpec(
            id="verify_completion_router",
            source_stage_id="verify_completion",
            branches=(
                RouteCondition(
                    label="pass",
                    target="formalize_results_agent",
                    expression="goals_met / goals_total >= 0.8",
                    metadata={"sourceCondition": "complete"},
                ),
                RouteCondition(
                    label="incomplete",
                    target="formalize_goals_agent",
                    expression="0.3 <= goals_met / goals_total < 0.8",
                    retry=RetryPolicy(max_attempts=3, counter="verify_rework_attempts"),
                    metadata={"sourceCondition": "needs_goal_refinement"},
                ),
                RouteCondition(
                    label="rethink",
                    target="brainstorm_agent",
                    expression="goals_met / goals_total < 0.3",
                    metadata={"sourceCondition": "needs_fundamental_rethink"},
                ),
            ),
        ),
        RouterSpec(
            id="duality_gate_router",
            source_stage_id="duality_gate",
            branches=(
                RouteCondition(
                    label="pass",
                    target="resource_preparation_agent",
                    expression="duality_check_result.both_passed is true",
                ),
                RouteCondition(
                    label="failed",
                    target="followup_lit_review",
                    expression="duality_check_result.both_passed is false",
                    retry=RetryPolicy(max_attempts=2, counter="duality_rework_attempts"),
                    metadata={"sourceCondition": "needs_followup_lit_review"},
                ),
            ),
        ),
        RouterSpec(
            id="writeup_artifact_gate_router",
            source_stage_id="writeup_artifact_gate",
            branches=(
                RouteCondition(
                    label="missing_artifacts",
                    target="writeup_agent",
                    metadata={"sourceCondition": "missing_or_invalid_paper_artifacts"},
                ),
                RouteCondition(label="advance", target="proofreading_entry", metadata={"sourceCondition": "valid"}),
            ),
        ),
        RouterSpec(
            id="proofread_gate_router",
            source_stage_id="proofread_gate",
            branches=(
                RouteCondition(
                    label="quality_below_threshold",
                    target="proofreading_agent",
                    metadata={"sourceCondition": "copyedit_incomplete"},
                ),
                RouteCondition(label="advance", target="reviewer_agent", metadata={"sourceCondition": "ready_for_review"}),
            ),
        ),
        RouterSpec(
            id="review_gate_router",
            source_stage_id="review_gate",
            branches=(
                RouteCondition(
                    label="score_below_min_review_score",
                    target="reviewer_agent",
                    metadata={"sourceCondition": "review_needs_retry"},
                ),
                RouteCondition(label="advance", target="milestone_review", metadata={"sourceCondition": "review_accepted"}),
            ),
        ),
        RouterSpec(
            id="validation_router",
            source_stage_id="validation_gate",
            branches=(
                RouteCondition(
                    label="missing_paper_artifacts",
                    target="writeup_agent",
                    retry=RetryPolicy(max_attempts=3, counter="validation_retry_count"),
                    metadata={"sourceCondition": "paper_artifact_failure"},
                ),
                RouteCondition(
                    label="missing_experiment_outputs",
                    target="experiment_track",
                    retry=RetryPolicy(max_attempts=3, counter="validation_retry_count"),
                    metadata={"sourceCondition": "experiment_artifact_failure"},
                ),
                RouteCondition(
                    label="missing_theory_outputs",
                    target="theory_track",
                    retry=RetryPolicy(max_attempts=3, counter="validation_retry_count"),
                    metadata={"sourceCondition": "theory_artifact_failure"},
                ),
                RouteCondition(label="finished", terminal=True, expression="finished is true"),
            ),
        ),
    )


def _feature_flags() -> tuple[FeatureFlagRoute, ...]:
    return (
        FeatureFlagRoute(
            flag="enable_duality_check",
            enabled_target="duality_check",
            disabled_target="resource_preparation_agent",
            description="When false, formalize_results_agent routes directly to resource_preparation_agent.",
            metadata={"sourceStageId": "formalize_results_agent", "collapsedSegment": ["duality_check", "duality_gate"]},
        ),
        FeatureFlagRoute(
            flag="math_enabled",
            enabled_target="theory_track",
            disabled_target="experiment_track",
            description="When false, track_router skips the theory subgraph and routes only to experiment_track.",
            metadata={"sourceStageId": "milestone_goals", "router": "track_router"},
        ),
        FeatureFlagRoute(
            flag="enable_milestone_gates",
            enabled_target="milestone_goals",
            disabled_target=None,
            description="Controls whether milestone approval gates pause for human review.",
        ),
        FeatureFlagRoute(
            flag="enable_ensemble_review",
            enabled_target="reviewer_agent",
            disabled_target="reviewer_agent",
            description="Switches reviewer_agent from single-reviewer to ensemble-review posture.",
        ),
        FeatureFlagRoute(
            flag="iterate_start_stage_override",
            enabled_target=None,
            disabled_target="iterate_router",
            description="Allows iterate mode to jump to any registered SDK node after persona_council.",
            metadata={"overlay": "iterate"},
        ),
    )


def _state_fields() -> tuple[StateFieldContract, ...]:
    fields: list[StateFieldContract] = []

    def read(stage_id: str, *names: str) -> None:
        fields.extend(StateFieldContract(field=name, direction="read", stage_id=stage_id) for name in names)

    def write(stage_id: str, *names: str, merge: str | None = None) -> None:
        for name in names:
            field_merge = merge
            if field_merge is None and name == "agent_outputs":
                field_merge = "dict_merge"
            if field_merge is None and name in {
                "brainstorm_history",
                "milestone_reports",
                "human_feedback_history",
                "verify_completion_history",
                "validation_results",
            }:
                field_merge = "append"
            fields.append(StateFieldContract(field=name, direction="write", stage_id=stage_id, merge=field_merge))

    read("persona_council", "task", "agent_task")
    write("persona_council", "research_proposal", "agent_outputs")
    read("literature_review_agent", "agent_task", "research_proposal")
    write("literature_review_agent", "agent_outputs", "lit_review_feasibility")
    read("lit_review_gate", "lit_review_feasibility", "lit_review_attempts")
    write("lit_review_gate", "current_agent", "agent_task", "lit_review_attempts")
    read("brainstorm_agent", "agent_task", "research_proposal", "lit_review_feasibility")
    write("brainstorm_agent", "brainstorm_output", "brainstorm_history", "agent_outputs")
    read("brainstorm_artifact_gate", "brainstorm_output", "critical_failure", "brainstorm_artifact_retries")
    write("brainstorm_artifact_gate", "current_agent", "brainstorm_artifact_retries")
    read("formalize_goals_entry", "brainstorm_output", "critical_failure")
    write("formalize_goals_entry", "agent_task")
    read("formalize_goals_agent", "agent_task", "brainstorm_output", "research_proposal")
    write("formalize_goals_agent", "research_goals", "agent_outputs")
    read("research_plan_writeup_agent", "research_goals", "track_decomposition")
    write("research_plan_writeup_agent", "agent_outputs", merge="dict_merge")
    read("track_decomposition_gate", "research_goals", "math_enabled")
    write("track_decomposition_gate", "track_decomposition")
    read("milestone_goals", "track_decomposition", "milestone_timeout")
    write("milestone_goals", "milestone_reports", "human_feedback_history")
    read("theory_track", "agent_task", "research_goals", "track_decomposition")
    write("theory_track", "theory_track_status", "theory_track_summary", "agent_outputs")
    read("experiment_track", "agent_task", "research_goals", "track_decomposition")
    write("experiment_track", "experiment_track_status", "agent_outputs")
    read("track_merge", "theory_track_summary", "experiment_track_status")
    write("track_merge", "agent_outputs", merge="dict_merge")
    read("verify_completion", "research_goals", "theory_track_summary", "experiment_track_status", "verify_completion_history")
    write("verify_completion", "verify_completion_result", "verify_completion_history", "verify_rework_attempts")
    read("formalize_results_agent", "verify_completion_result", "theory_track_summary", "experiment_track_status", "research_goals")
    write("formalize_results_agent", "formalized_results", "agent_outputs")
    read("duality_check", "formalized_results")
    write("duality_check", "duality_check_result")
    read("duality_gate", "duality_check_result", "duality_rework_attempts")
    write("duality_gate", "current_agent", "duality_rework_attempts")
    read("followup_lit_review", "agent_task", "duality_check_result", "agent_outputs")
    write("followup_lit_review", "agent_outputs", merge="dict_merge")
    read("resource_preparation_agent", "formalized_results", "theory_track_summary", "experiment_track_status", "research_goals")
    write("resource_preparation_agent", "agent_outputs", merge="dict_merge")
    read("paper_contract_builder", "formalized_results")
    write("paper_contract_builder", "agent_outputs", merge="dict_merge")
    read("writeup_agent", "formalized_results", "agent_outputs")
    write("writeup_agent", "agent_outputs", merge="dict_merge")
    read("writeup_artifact_gate", "require_pdf")
    write("writeup_artifact_gate", "current_agent")
    read("proofreading_entry", "agent_outputs")
    write("proofreading_entry", "agent_task")
    read("proofreading_agent", "agent_task")
    write("proofreading_agent", "agent_outputs", merge="dict_merge")
    read("proofread_gate", "agent_outputs")
    write("proofread_gate", "current_agent")
    read("reviewer_agent", "agent_outputs")
    write("reviewer_agent", "agent_outputs", merge="dict_merge")
    read("review_gate", "agent_outputs", "min_review_score")
    write("review_gate", "current_agent")
    read("milestone_review", "agent_outputs")
    write("milestone_review", "milestone_reports", "human_feedback_history")
    read("validation_gate", "artifacts", "enforce_paper_artifacts", "require_pdf", "require_experiment_plan", "validation_retry_count")
    write("validation_gate", "finished", "validation_results", "validation_retry_count")
    read("iterate_entry", "iterate_prior_paper_path", "iterate_feedback_path", "iterate_binding_constraints")
    write("iterate_entry", "agent_task")
    read("iterate_router", "research_proposal")
    write("iterate_router", "iterate_route", "agent_task")
    return tuple(fields)
