from __future__ import annotations

from msc_sdk.feedback_graph import (
    FEEDBACK_EXPERIMENT_SUBGRAPH_STAGE_IDS,
    FEEDBACK_MAIN_STAGE_IDS,
    FEEDBACK_THEORY_AGENT_STAGE_IDS,
    target_research_graph_template,
)
from msc_sdk.stage_contracts import compile_kernel_graph, project_kernel_graph


def _router_labels(router_id: str) -> set[str]:
    template = target_research_graph_template()
    router = next(router for router in template.routers if router.id == router_id)
    return {branch.label for branch in router.branches}


def _router(router_id: str):
    template = target_research_graph_template()
    return next(router for router in template.routers if router.id == router_id)


def test_target_research_contains_all_feedback_top_level_nodes():
    template = target_research_graph_template()

    assert len(template.main_stage_ids) == 29
    assert template.main_stage_ids == FEEDBACK_MAIN_STAGE_IDS
    assert "followup_lit_review" in template.main_stage_ids
    assert template.entry_stage_id == "persona_council"


def test_iterate_overlay_contains_entry_router_and_feedback_routes():
    template = target_research_graph_template()

    assert template.iterate_stage_ids == ("iterate_entry", "persona_council", "iterate_router")
    assert {
        "writing_only",
        "needs_research",
        "needs_full_rethink",
    } <= _router_labels("_iterate_route_selector")
    assert "iterate_start_stage_override" in _router_labels("iterate_persona_exit_router")


def test_theory_and_experiment_subgraphs_are_nested_specs():
    template = target_research_graph_template()
    subgraphs = {subgraph.id: subgraph for subgraph in template.subgraphs}

    assert set(FEEDBACK_THEORY_AGENT_STAGE_IDS) <= set(subgraphs["theory_track"].stage_ids)
    assert subgraphs["theory_track"].metadata["feedbackAgentStageIds"] == list(FEEDBACK_THEORY_AGENT_STAGE_IDS)
    assert subgraphs["experiment_track"].stage_ids == FEEDBACK_EXPERIMENT_SUBGRAPH_STAGE_IDS
    assert subgraphs["theory_track"].metadata["collapsedByDefault"] is True
    assert subgraphs["experiment_track"].metadata["collapsedByDefault"] is True


def test_router_retry_caps_and_feedback_labels_are_preserved():
    lit_router = _router("lit_review_gate_router")
    brainstorm_router = _router("brainstorm_artifact_gate_router")
    verify_router = _router("verify_completion_router")
    duality_router = _router("duality_gate_router")
    validation_router = _router("validation_router")

    assert _retry(lit_router, "infeasible") == ("lit_review_attempts", 2)
    assert _retry(brainstorm_router, "missing_artifacts") == ("brainstorm_artifact_retries", 2)
    assert _retry(verify_router, "incomplete") == ("verify_rework_attempts", 3)
    assert _retry(duality_router, "failed") == ("duality_rework_attempts", 2)
    assert _retry(validation_router, "missing_paper_artifacts") == ("validation_retry_count", 3)
    assert {"pass", "incomplete", "rethink"} == _router_labels("verify_completion_router")
    assert {"pass", "failed"} == _router_labels("duality_gate_router")


def test_feature_flags_capture_duality_collapse_math_skip_and_iterate_override():
    template = target_research_graph_template()
    flags = {flag.flag: flag for flag in template.feature_flags}

    assert flags["enable_duality_check"].enabled_target == "duality_check"
    assert flags["enable_duality_check"].disabled_target == "resource_preparation_agent"
    assert flags["math_enabled"].enabled_target == "theory_track"
    assert flags["math_enabled"].disabled_target == "experiment_track"
    assert "iterate_start_stage_override" in flags


def test_projection_exposes_rich_feedback_graph_metadata():
    graph = compile_kernel_graph(graph_id="demo", template="target_research", budget=1)
    projected = project_kernel_graph(
        graph=graph,
        campaign_id="demo",
        title="Demo",
        template="target_research",
        tier="standard",
    )

    assert projected["metadata"]["sdkGraphAuthority"] == "feedback_graph"
    assert projected["metadata"]["topLevelNodeCount"] == 29
    assert len(projected["metadata"]["subgraphs"]) == 2
    assert projected["metadata"]["featureFlags"]
    assert projected["metadata"]["stateFields"]

    node_ids = {node["id"] for node in projected["nodes"]}
    assert "followup_lit_review" in node_ids

    duality_gate = next(node for node in projected["nodes"] if node["id"] == "duality_gate")
    assert duality_gate["metadata"]["routerSpec"]["id"] == "duality_gate_router"
    assert duality_gate["metadata"]["stateReads"] == ["duality_check_result", "duality_rework_attempts"]

    theory_track = next(node for node in projected["nodes"] if node["id"] == "theory_track")
    assert theory_track["metadata"]["subgraphSpec"]["id"] == "theory_track"
    assert theory_track["metadata"]["subgraphSpec"]["metadata"]["collapsedByDefault"] is True


def _retry(router, label: str) -> tuple[str | None, int | None]:
    branch = next(branch for branch in router.branches if branch.label == label)
    assert branch.retry is not None
    return branch.retry.counter, branch.retry.max_attempts
