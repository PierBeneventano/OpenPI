from __future__ import annotations

from pathlib import Path
import ast

from consortium.stage_contracts.historical import HISTORICAL_RUNTIME_NODE_IDS
from msc_sdk.stage_contracts import build_contract_graph, contracts_by_id, validate_contract_coverage


GRAPH_SOURCE = Path(__file__).resolve().parents[1] / "consortium" / "graph.py"


def _graph_list(name: str) -> list[str]:
    tree = ast.parse(GRAPH_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        return [item.value for item in node.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    raise AssertionError(f"{name} not found in consortium/graph.py")


def test_historical_runtime_nodes_have_stage_contracts():
    runtime_nodes = {
        *_graph_list("V2_PRE_TRACK_STAGES"),
        *_graph_list("MATH_PIPELINE_STAGES"),
        *_graph_list("EXPERIMENT_PIPELINE_STAGES"),
        *_graph_list("V2_POST_TRACK_STAGES"),
        "lit_review_gate",
        "brainstorm_artifact_gate",
        "track_decomposition_gate",
        "milestone_goals",
        "theory_track",
        "experiment_track",
        "track_merge",
        "verify_completion",
        "duality_check",
        "duality_gate",
        "followup_lit_review",
        "paper_contract_builder",
        "writeup_artifact_gate",
        "proofreading_entry",
        "proofread_gate",
        "review_gate",
        "milestone_review",
        "validation_gate",
        "goal_tag_validation_gate",
        "human_review_gate",
        "theory_track_repair_gate",
        "iterate_entry",
        "iterate_router",
    }

    coverage = validate_contract_coverage(runtime_nodes)

    assert coverage["ok"], coverage["missing"]
    assert runtime_nodes <= set(HISTORICAL_RUNTIME_NODE_IDS)


def test_contracts_include_product_semantics():
    contracts = contracts_by_id()
    for node_id in [
        "persona_council",
        "literature_review_agent",
        "track_decomposition_gate",
        "theory_track",
        "experiment_track",
        "writeup_agent",
        "validation_gate",
    ]:
        contract = contracts[node_id]
        assert contract.purpose
        assert contract.failure_policy
        assert contract.tool_families or contract.kind in {"gate", "control", "approval", "router"}
        assert contract.allowed_routes or node_id == "validation_gate"


def test_contract_graph_projects_control_nodes_loops_and_artifacts():
    graph = build_contract_graph(
        campaign_id="demo",
        title="Demo",
        template="consortium_scaffold",
        tier="budget",
        budget=1,
    )
    node_ids = {node["id"] for node in graph["nodes"]}
    edge_kinds = {edge["kind"] for edge in graph["edges"]}

    assert graph["metadata"]["source"] == "stage_contract_registry"
    assert graph["modes"] == ["pipeline", "control", "runtime"]
    assert "lit_review_gate" in node_ids
    assert "track_decomposition_gate" in node_ids
    assert "theory_track" in node_ids
    assert "experiment_track" in node_ids
    assert "validation_gate" in node_ids
    assert "iterate_entry" not in node_ids
    assert "loop" in edge_kinds
    assert "fanout" in edge_kinds
    assert "fanin" in edge_kinds

    writeup = next(node for node in graph["nodes"] if node["id"] == "writeup_agent")
    assert "artifacts/final_paper.md" in writeup["outputs"]
    assert writeup["metadata"]["purpose"]
    assert writeup["metadata"]["humanPausePolicy"]


def test_scaffold_template_is_zero_spend_safe_before_runtime(tmp_path: Path):
    from msc_sdk.campaign_store import CampaignStore

    store = CampaignStore(tmp_path)
    created = store.create_campaign(
        title="Zero Spend Scaffold",
        objective="Render the graph and demo artifacts without launching a run.",
        template="consortium_scaffold",
        budget=1,
    )

    graph = store.graph(created["campaign_id"])
    assert graph["state"] == "planned"
    assert all(node["status"] == "planned" for node in graph["nodes"])
    assert (tmp_path / "results" / "zero-spend-scaffold" / "writeup_agent" / "artifacts" / "final_paper.md").exists()
