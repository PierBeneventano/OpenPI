from __future__ import annotations

import json
from pathlib import Path

from msc_sdk.campaign_projection import CampaignEventProjector
from msc_sdk.campaign_store import CampaignStore, artifact_row_to_dict
from msc_sdk.campaigns import CampaignClient
from msc_sdk.stage_runtime import StageRunContext, materialize_stage_outputs


def test_campaign_store_creates_sqlite_jsonl_snapshot_and_declared_outputs(tmp_path: Path):
    store = CampaignStore(tmp_path)

    campaign = store.create_campaign(
        title="Consortium Graph Demo",
        objective="Visualize the full graph without spending money.",
        template="consortium_scaffold",
        budget=1,
        tier="budget",
        output_format="markdown",
    )

    assert (tmp_path / ".msc" / "campaigns.db").exists()
    assert (tmp_path / ".msc" / "events" / "campaigns.jsonl").exists()
    assert (tmp_path / ".msc" / "snapshots" / "consortium-graph-demo.graph.json").exists()
    assert (tmp_path / "campaigns" / "consortium-graph-demo" / "campaign.json").exists()
    assert (tmp_path / "campaigns" / "consortium-graph-demo" / "graph.json").exists()
    assert (tmp_path / "campaigns" / "consortium-graph-demo" / "artifacts.json").exists()
    assert (tmp_path / "campaigns" / "consortium-graph-demo" / "events.jsonl").exists()
    assert not (tmp_path / "campaigns" / "consortium-graph-demo" / "campaign.yaml").exists()
    assert (tmp_path / "campaigns" / "consortium-graph-demo" / "README.md").exists()
    assert campaign["campaign_id"] == "consortium-graph-demo"

    graph = store.graph("consortium-graph-demo")
    assert graph["nodes"][0]["id"] == "persona_council"
    assert graph["nodes"][-1]["id"] == "validation_gate"
    assert graph["state"] == "planned"
    assert graph["metadata"]["source"] == "kernel_graph_projection"
    assert "control" in graph["modes"]
    assert any(edge["kind"] == "loop" for edge in graph["edges"])

    artifact = store.artifacts("consortium-graph-demo", "persona_council")["stages"][0]["required_artifacts"][0]
    assert artifact["path"] == "artifacts/persona_debate.md"
    assert artifact["required"]
    assert not artifact["exists"]
    assert artifact["status"] == "declared"

    events = store.events("consortium-graph-demo")["events"]
    assert any(event["type"] == "CampaignCreated" for event in events)
    assert any(event["type"] == "ArtifactDeclared" for event in events)


def test_campaign_graph_creation_does_not_depend_on_stage_contract_nodes(tmp_path: Path):
    from msc_sdk import stage_contracts

    assert not hasattr(stage_contracts.StageContract, "to_node")
    store = CampaignStore(tmp_path)

    created = store.create_campaign(
        title="Kernel Graph Demo",
        objective="Project the campaign graph from kernel specs.",
        template="literature_only",
        budget=1,
    )
    graph = store.graph(created["campaign_id"])

    assert graph["metadata"]["source"] == "kernel_graph_projection"
    assert [node["id"] for node in graph["nodes"]][:2] == [
        "persona_council",
        "literature_review_agent",
    ]


def test_campaign_store_redacts_events_and_survives_reopen(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Secret Demo",
        objective="Ensure secret payloads do not leak.",
        template="blank",
        budget=1,
    )

    with store.connect() as conn:
        store._append_event(
            conn,
            campaign_id="secret-demo",
            event_type="ObjectiveUpdated",
            actor="user",
            payload={"openrouter_api_key": "sk-test", "safe": "visible"},
        )

    reopened = CampaignStore(tmp_path)
    events = reopened.events("secret-demo")["events"]
    payload = events[-1]["payload"]
    assert payload["openrouter_api_key"] == "[REDACTED]"
    assert payload["safe"] == "visible"


def test_bundle_export_import_and_client_reads_store_only(tmp_path: Path):
    client = CampaignClient(tmp_path)
    created = client.create(
        title="Bundle Demo",
        objective="Exercise bundle import and export.",
        template="literature_only",
        budget=1,
    )
    assert created["campaign_id"] == "bundle-demo"

    exported = client.export_bundle("bundle-demo")
    bundle_path = Path(exported["bundle_path"])
    assert (bundle_path / "campaign.json").exists()
    assert (bundle_path / "graph.json").exists()
    assert (bundle_path / "artifacts.json").exists()

    imported_root = tmp_path / "imported"
    imported = CampaignClient(imported_root).import_bundle(bundle_path)
    assert imported["campaign_id"] == "bundle-demo"
    store_graph = CampaignClient(imported_root).graph("bundle-demo")
    assert store_graph["nodes"][0]["id"] == "persona_council"

    (tmp_path / "ignored_campaign.yaml").write_text("name: Ignored\nstages: []\n", encoding="utf-8")
    list_rows = client.list()
    assert [row["id"] for row in list_rows] == ["bundle-demo"]
    assert all(row.get("source") == "sqlite" for row in list_rows)


def test_campaign_delete_removes_records_bundle_results_snapshots_and_events(tmp_path: Path):
    client = CampaignClient(tmp_path)
    created = client.create(
        title="Delete Demo",
        objective="Exercise destructive campaign cleanup.",
        template="literature_only",
        budget=1,
    )
    campaign_id = created["campaign_id"]
    results_file = tmp_path / "results" / campaign_id / "literature_review_agent" / "artifacts" / "literature_matrix.md"
    results_file.parent.mkdir(parents=True)
    results_file.write_text("# Matrix\n", encoding="utf-8")
    chat_file = tmp_path / ".msc" / "openclaude_chats" / "delete-demo-faa3429ebb.json"
    chat_file.parent.mkdir(parents=True)
    chat_file.write_text("{}", encoding="utf-8")

    deleted = client.delete(campaign_id)

    assert deleted["ok"]
    assert not (tmp_path / "campaigns" / campaign_id).exists()
    assert not (tmp_path / "results" / campaign_id).exists()
    assert not (tmp_path / ".msc" / "snapshots" / f"{campaign_id}.graph.json").exists()
    assert not chat_file.exists()
    assert client.list() == []
    assert campaign_id not in (tmp_path / ".msc" / "events" / "campaigns.jsonl").read_text(encoding="utf-8")


def test_event_jsonl_can_replay_into_fresh_db(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(title="Replay Demo", objective="Replay events.", template="blank", budget=1)

    db_path = tmp_path / ".msc" / "campaigns.db"
    db_path.unlink()

    replayed = CampaignStore(tmp_path).replay_jsonl()
    assert replayed >= 1

    lines = (tmp_path / ".msc" / "events" / "campaigns.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "CampaignCreated"


def test_campaign_read_views_rebuild_from_events_without_cache_tables(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Event Truth Demo",
        objective="Rebuild product views from events.",
        template="consortium_scaffold",
        budget=1,
    )
    store.approve_graph("event-truth-demo", 1)

    with store.connect() as conn:
        conn.execute("DELETE FROM graph_snapshots")
        conn.execute("DELETE FROM graph_nodes")
        conn.execute("DELETE FROM graph_edges")
        conn.execute("DELETE FROM artifacts")

    graph = store.graph("event-truth-demo")
    assert graph["state"] == "approved"
    assert graph["metadata"]["read_source"] == "campaign_events"
    assert graph["nodes"][0]["id"] == "persona_council"
    assert all(node["status"] == "approved" for node in graph["nodes"])

    artifacts = store.artifacts("event-truth-demo", "persona_council")["stages"][0]["required_artifacts"]
    persona = next(artifact for artifact in artifacts if artifact["path"] == "artifacts/persona_debate.md")
    assert not persona["exists"]
    assert persona["status"] == "declared"
    assert persona["workspace"] == str(Path("results") / "event-truth-demo" / "persona_council")

    model = store.inspect_dict("event-truth-demo")
    assert model["status"] == "approved"
    assert model["metadata"]["source"] == "campaign_events"
    assert model["provenance"]["source"] == "campaign_events"


def test_campaign_workspace_model_uses_campaign_execution_and_deliverables(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Workspace Demo",
        objective="Expose one campaign workspace read model.",
        template="literature_only",
        budget=1,
    )
    run = store.record_run_started("workspace-demo", command=["msc", "run"], pid=321)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "workspace-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    ctx = StageRunContext.from_env("literature_review_agent")
    assert ctx is not None
    ctx.write_required("artifacts/literature_matrix.md", "# Matrix")
    store.record_instruction(
        "workspace-demo",
        text="Tighten the literature criteria before continuing.",
        instruction_type="revision",
        direction="to_campaign",
        metadata={"node_id": "literature_review_agent"},
    )

    workspace = store.workspace_read_model("workspace-demo")

    assert workspace["schema"] == "msc.campaign.workspace.v1"
    assert workspace["campaign"]["objective"] == "Expose one campaign workspace read model."
    assert workspace["execution"]["status"] == "running"
    assert workspace["execution"]["latest_attempt"]["execution_id"] == run["run_id"]
    assert workspace["safe_next_actions"] == ["pause-campaign", "record-feedback"]
    assert workspace["graph"]["metadata"]["source"] == "kernel_graph_projection"
    assert [artifact["path"] for artifact in workspace["deliverables"]] == ["artifacts/literature_matrix.md"]
    assert all(artifact["audience"] in {"deliverable", "evidence"} for artifact in workspace["deliverables"])
    assert workspace["feedback"][0]["target"]["node_id"] == "literature_review_agent"
    assert workspace["feedback"][0]["type"] == "revision"

    events = [event["type"] for event in store.events("workspace-demo")["events"]]
    assert not any(event_type.startswith("Run") for event_type in events)
    assert "CampaignExecutionStarted" in events
    assert "HumanFeedbackRecorded" in events


def test_campaign_context_links_are_events_and_workspace_memory(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Context Memory Demo",
        objective="Attach artifact concerns to the campaign.",
        template="literature_only",
        budget=1,
    )

    linked = store.link_context(
        "context-memory-demo",
        target_scope="artifact",
        node_id="literature_review_agent",
        artifact_path="artifacts/literature_matrix.md",
        note="The literature comparison criteria look too broad.",
    )
    store.update_context_link("context-memory-demo", linked["link_id"], status="resolved", note="Criteria revised.")

    workspace = store.workspace_read_model("context-memory-demo")
    links = workspace["context"]["links"]

    assert links[0]["id"] == linked["link_id"]
    assert links[0]["status"] == "resolved"
    assert links[0]["target"]["scope"] == "artifact"
    assert links[0]["target"]["artifact_path"] == "artifacts/literature_matrix.md"
    assert workspace["context"]["active_links"] == []
    assert workspace["feedback"][0]["type"] == "context_note"
    assert workspace["feedback"][0]["target"]["scope"] == "artifact"

    event_types = [event["type"] for event in store.events("context-memory-demo")["events"]]
    assert "ContextLinked" in event_types
    assert "ContextLinkUpdated" in event_types


def test_failure_recovery_decision_is_researcher_readable(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Failure UX Demo",
        objective="Expose runtime failures as recovery decisions.",
        template="literature_only",
        budget=1,
    )
    run = store.record_run_started("failure-ux-demo", command=["msc", "run"], pid=42)
    store.record_run_exited(
        "failure-ux-demo",
        run["run_id"],
        exit_code=1,
        metadata={"error": "Recursion limit of 25 reached without hitting a stop condition."},
    )

    workspace = store.workspace_read_model("failure-ux-demo")
    decision = workspace["pending_decisions"][0]

    assert workspace["execution"]["status"] == "human_decision_required"
    assert workspace["execution"]["current_stage_id"] != run["run_id"]
    assert decision["target_type"] == "failure_recovery"
    assert decision["target_label"] == "latest failed execution"
    assert decision["title"] == "Campaign execution reached graph transition limit"
    assert "used more graph transitions than the runtime allowed" in decision["summary"]
    assert decision["reason"] == "Recursion limit of 25 reached without hitting a stop condition."


def test_dry_run_passed_is_not_projected_as_execution_failure(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Dry Run Demo",
        objective="Validate autostart dry-run projection.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("dry-run-demo", command=["msc", "run", "--dry-run"], pid=43)
    result = store.record_run_exited("dry-run-demo", run["run_id"], exit_code=0, status="dry_run_passed")

    workspace = store.workspace_read_model("dry-run-demo")
    events = [event["type"] for event in store.events("dry-run-demo")["events"]]

    assert result["approval"] is None
    assert "CampaignExecutionCompleted" in events
    assert "CampaignExecutionFailed" not in events
    assert workspace["campaign"]["status"] == "approved"
    assert workspace["execution"]["status"] == "dry_run_passed"
    assert workspace["pending_decisions"] == []
    assert workspace["safe_next_actions"] == ["review-deliverables", "record-feedback", "rerun-stage"]


def test_spurious_dry_run_failure_recovery_is_hidden(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Spurious Dry Run Bug Demo",
        objective="Ignore old dry-run success events that were mislabeled as failures.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("spurious-dry-run-bug-demo", command=["msc", "run", "--dry-run"], pid=44)
    run_id = run["run_id"]
    with store.connect() as conn:
        conn.execute(
            "UPDATE runs SET status=?, exited_at=?, exit_code=? WHERE id=?",
            ("dry_run_passed", "2026-05-18T00:00:00Z", 0, run_id),
        )
        store._append_event(
            conn,
            campaign_id="spurious-dry-run-bug-demo",
            event_type="CampaignExecutionFailed",
            actor="runner",
            payload={"execution_id": run_id, "run_id": run_id, "status": "dry_run_passed", "exit_code": 0},
        )
        store._create_approval(
            conn,
            campaign_id="spurious-dry-run-bug-demo",
            target_type="failure_recovery",
            target_id=run_id,
            actor="runner",
            metadata={"run_id": run_id, "exit_code": 0, "reason": None},
        )

    workspace = store.workspace_read_model("spurious-dry-run-bug-demo")

    assert workspace["campaign"]["status"] == "approved"
    assert workspace["execution"]["status"] == "dry_run_passed"
    assert workspace["pending_decisions"] == []
    assert workspace["safe_next_actions"] == ["review-deliverables", "record-feedback", "rerun-stage"]


def test_campaign_event_projector_is_independent_of_store(tmp_path: Path):
    events = [
        {
            "id": "evt_1",
            "campaign_id": "projector-demo",
            "type": "CampaignCreated",
            "actor": "user",
            "created_at": "2026-01-01T00:00:00Z",
            "payload": {
                "title": "Projector Demo",
                "objective": "Project without a store instance.",
                "workspace_root": "results/projector-demo",
                "budget": 1,
                "tier": "budget",
                "output_format": "markdown",
            },
        },
        {
            "id": "evt_2",
            "campaign_id": "projector-demo",
            "type": "GraphProjected",
            "actor": "system",
            "created_at": "2026-01-01T00:00:01Z",
            "payload": {
                "graph": {
                    "campaign": "projector-demo",
                    "version": 1,
                    "state": "planned",
                    "nodes": [{"id": "stage_one", "status": "planned"}],
                    "edges": [],
                    "metadata": {},
                }
            },
        },
        {
            "id": "evt_3",
            "campaign_id": "projector-demo",
            "type": "ArtifactDeclared",
            "actor": "system",
            "created_at": "2026-01-01T00:00:02Z",
            "payload": {
                "artifact_id": "projector-demo:stage_one:artifacts/result.md",
                "stage_id": "stage_one",
                "path": "artifacts/result.md",
                "kind": "md",
                "required": True,
                "workspace": "results/projector-demo/stage_one",
            },
        },
        {
            "id": "evt_4",
            "campaign_id": "projector-demo",
            "type": "GraphNodeStatusChanged",
            "actor": "runner",
            "created_at": "2026-01-01T00:00:03Z",
            "payload": {"node_id": "stage_one", "status": "completed"},
        },
    ]

    projection = CampaignEventProjector(tmp_path).project("projector-demo", events)

    assert projection["campaign"]["title"] == "Projector Demo"
    assert projection["graph"]["nodes"][0]["status"] == "completed"
    assert projection["graph"]["metadata"]["read_source"] == "campaign_events"
    assert projection["artifact_rows"][0]["path"] == "artifacts/result.md"


def test_stage_run_context_writes_run_versioned_contract_artifact(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Runtime Demo",
        objective="Write native artifacts.",
        template="literature_only",
        budget=1,
    )
    run = store.record_run_started("runtime-demo", command=["msc", "run"], pid=123)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "runtime-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    ctx = StageRunContext.from_env("persona_council")
    assert ctx is not None
    target = ctx.write_required("artifacts/research_proposal.md", "# Proposal")

    assert target == tmp_path / "results" / "runtime-demo" / "runs" / run["run_id"] / "persona_council" / "artifacts" / "research_proposal.md"
    artifacts = store.artifacts("runtime-demo", "persona_council")["stages"][0]["required_artifacts"]
    proposal = next(artifact for artifact in artifacts if artifact["path"] == "artifacts/research_proposal.md")
    assert proposal["exists"]
    assert proposal["source_role"] == "contract_runtime"
    assert proposal["workspace"] == str(Path("results") / "runtime-demo" / "runs" / run["run_id"] / "persona_council")


def test_materialize_brainstorm_outputs_creates_required_contract_artifacts(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Brainstorm Runtime Demo",
        objective="Materialize brainstorm artifacts.",
        template="literature_only",
        budget=1,
    )
    run = store.record_run_started("brainstorm-runtime-demo", command=["msc", "run"], pid=456)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "brainstorm-runtime-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    result = materialize_stage_outputs(
        "brainstorm_agent",
        {"task": "Find a useful toy research direction."},
        {"agent_outputs": {"brainstorm_agent": "Test low-rank fine-tuning updates."}},
    )

    assert "brainstorm" in result["artifacts"]
    root = tmp_path / "results" / "brainstorm-runtime-demo" / "runs" / run["run_id"] / "brainstorm_agent"
    assert (root / "artifacts" / "brainstorm.md").exists()
    assert (root / "artifacts" / "approach_menu.json").exists()
    artifacts = store.artifacts("brainstorm-runtime-demo", "brainstorm_agent")["stages"][0]["required_artifacts"]
    assert {artifact["path"] for artifact in artifacts if artifact["exists"]} >= {
        "artifacts/brainstorm.md",
        "artifacts/approach_menu.json",
    }


def test_materialize_formalize_goals_creates_sdk_goal_contract(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Goal Runtime Demo",
        objective="Materialize formalized goals.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("goal-runtime-demo", command=["msc", "run"], pid=457)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "goal-runtime-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    result = materialize_stage_outputs(
        "formalize_goals_agent",
        {"task": "Compare batch normalization against a baseline in a toy MLP."},
        {"agent_outputs": {"formalize_goals_agent": "Goal: run a small empirical comparison."}},
    )

    assert result["research_goals"]["goals"][0]["track"] == "experiment"
    root = tmp_path / "results" / "goal-runtime-demo" / "runs" / run["run_id"] / "formalize_goals_agent"
    assert (root / "artifacts" / "research_goals.json").exists()
    assert (root / "artifacts" / "goal_spec.md").exists()
    completion = store.update_node_status(
        "goal-runtime-demo",
        "formalize_goals_agent",
        "completed",
        payload={"run_id": run["run_id"]},
    )
    assert completion["status"] == "completed"
    assert completion["completion"]["complete"]


def test_materialize_control_gate_creates_required_json_artifact(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Gate Runtime Demo",
        objective="Materialize gate artifacts.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("gate-runtime-demo", command=["msc", "run"], pid=458)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "gate-runtime-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    materialize_stage_outputs(
        "track_decomposition_gate",
        {
            "task": "Run a minimal empirical study.",
            "research_goals": {
                "goals": [{"id": "G1", "description": "Empirical comparison", "track": "experiment"}]
            },
            "math_enabled": False,
        },
        {},
    )

    root = tmp_path / "results" / "gate-runtime-demo" / "runs" / run["run_id"] / "track_decomposition_gate"
    decomposition = json.loads((root / "artifacts" / "track_decomposition.json").read_text())
    assert decomposition["recommended_track"] == "empirical"
    completion = store.update_node_status(
        "gate-runtime-demo",
        "track_decomposition_gate",
        "completed",
        payload={"run_id": run["run_id"]},
    )
    assert completion["status"] == "completed"


def test_materializer_skips_existing_required_artifacts(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Skip Existing Demo",
        objective="Do not overwrite native node artifacts.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("skip-existing-demo", command=["msc", "run"], pid=459)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "skip-existing-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    ctx = StageRunContext.from_env("persona_council")
    assert ctx is not None
    ctx.write_required("artifacts/persona_debate.md", "# Native Debate")
    ctx.write_required("artifacts/research_proposal.md", "# Native Proposal")

    materialize_stage_outputs(
        "persona_council",
        {"task": "Keep native content."},
        {"agent_outputs": {"persona_council": "adapter fallback should not overwrite"}},
    )

    root = tmp_path / "results" / "skip-existing-demo" / "runs" / run["run_id"] / "persona_council"
    assert (root / "artifacts" / "persona_debate.md").read_text() == "# Native Debate"
    assert (root / "artifacts" / "research_proposal.md").read_text() == "# Native Proposal"


def test_experiment_track_materializer_writes_diagnostic_summary_for_adapter(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Experiment Summary Demo",
        objective="Bridge SDK experiment artifacts back to an adapter track merge.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("experiment-summary-demo", command=["msc", "run"], pid=460)
    adapter_workspace = tmp_path / "results" / "adapter-run"
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "experiment-summary-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])
    monkeypatch.setenv("RESULTS_BASE_DIR", str(adapter_workspace))

    materialize_stage_outputs(
        "experiment_track",
        {"task": "Compare spectral norm growth."},
        {"agent_outputs": {"experiment_track": "Empirical track completed."}},
    )

    summary = json.loads((adapter_workspace / "paper_workspace" / "experiment_track_summary.json").read_text())
    assert summary["passed"] == ["G1"]
    assert summary["metrics"]["without_batch_norm"][-1] > summary["metrics"]["with_batch_norm"][-1]
    assert (adapter_workspace / "paper_workspace" / "experiment_report.tex").exists()


def test_experimentation_materializer_writes_concrete_toy_results(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Experiment Results Demo",
        objective="Materialize concrete toy experiment results.",
        template="target_research",
        budget=1,
    )
    run = store.record_run_started("experiment-results-demo", command=["msc", "run"], pid=461)
    adapter_workspace = tmp_path / "results" / "adapter-run"
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "experiment-results-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])
    monkeypatch.setenv("RESULTS_BASE_DIR", str(adapter_workspace))

    materialize_stage_outputs(
        "experimentation_agent",
        {"task": "Compare spectral norm growth."},
        {"agent_outputs": {"experimentation_agent": "Pseudo-code from adapter model."}},
    )

    root = tmp_path / "results" / "experiment-results-demo" / "runs" / run["run_id"] / "experimentation_agent"
    results_md = (root / "artifacts" / "experiment_results.md").read_text()
    adapter_results = json.loads((adapter_workspace / "paper_workspace" / "experiment_results.json").read_text())
    assert "Without BN" in results_md
    assert adapter_results["without_batch_norm"][-1] > adapter_results["with_batch_norm"][-1]


def test_stage_completion_is_derived_from_required_artifacts(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Completion Runtime Demo",
        objective="Do not let verbal completion outrun artifacts.",
        template="literature_only",
        budget=1,
    )
    run = store.record_run_started("completion-runtime-demo", command=["msc", "run"], pid=789)
    run_id = run["run_id"]

    incomplete = store.update_node_status(
        "completion-runtime-demo",
        "literature_review_agent",
        "completed",
        payload={"run_id": run_id},
    )

    assert incomplete["status"] == "human_decision_required"
    assert incomplete["completion"]["missing_required_artifacts"] == [
        "artifacts/literature_matrix.md",
        "artifacts/lit_review_feasibility.json",
    ]
    assert incomplete["approval"]["target_type"] == "stage_completion"

    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "completion-runtime-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run_id)
    materialize_stage_outputs(
        "literature_review_agent",
        {"task": "Survey a narrow topic."},
        {"agent_outputs": {"literature_review_agent": "A cited matrix with enough detail."}},
    )

    complete = store.update_node_status(
        "completion-runtime-demo",
        "literature_review_agent",
        "completed",
        payload={"run_id": run_id},
    )

    assert complete["status"] == "completed"
    assert complete["completion"]["complete"]
    events = store.events("completion-runtime-demo")["events"]
    assert any(event["type"] == "StageCompletionEvaluated" for event in events)
    assert any(event["type"] == "ValidationPassed" for event in events)


def test_run_scoped_artifacts_are_collapsed_to_researcher_artifact(tmp_path: Path, monkeypatch):
    store = CampaignStore(tmp_path)
    store.create_campaign(
        title="Run Scoped Artifact Demo",
        objective="Prefer concrete run artifacts over declarations.",
        template="literature_only",
        budget=1,
    )
    run = store.record_run_started("run-scoped-artifact-demo", command=["msc", "run"], pid=790)
    monkeypatch.setenv("MSC_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("MSC_CAMPAIGN_ID", "run-scoped-artifact-demo")
    monkeypatch.setenv("MSC_CAMPAIGN_RUN_ID", run["run_id"])

    ctx = StageRunContext.from_env("literature_review_agent")
    assert ctx is not None
    ctx.write_required("artifacts/literature_matrix.md", "# Matrix")

    artifacts = store.artifacts("run-scoped-artifact-demo", "literature_review_agent")["stages"][0]["required_artifacts"]
    matrix_rows = [artifact for artifact in artifacts if artifact["path"] == "artifacts/literature_matrix.md"]
    assert len(matrix_rows) == 1
    assert matrix_rows[0]["exists"]
    assert matrix_rows[0]["metadata"]["run_id"] == run["run_id"]
    assert matrix_rows[0]["audience"] == "deliverable"


def test_scaffold_files_project_as_prompts_not_deliverables(tmp_path: Path):
    workspace = tmp_path / "results" / "demo" / "literature_review_agent"
    artifact = workspace / "artifacts" / "literature_matrix.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "\n".join(
            [
                "# Literature Review",
                "",
                "Campaign: Demo",
                "Stage: literature_review_agent",
                "Kind: agent",
                "Contract:",
                "- Tool families: paper_search, llm",
                "- Human pause policy: after_literature_feasibility",
                "- Failure policy: stop_and_await_human_feedback",
                "Research objective:",
                "Test.",
            ]
        ),
        encoding="utf-8",
    )

    row = artifact_row_to_dict(
        {
            "id": "demo:literature_review_agent:artifacts/literature_matrix.md",
            "campaign_id": "demo",
            "stage_id": "literature_review_agent",
            "path": "artifacts/literature_matrix.md",
            "kind": "md",
            "required": 1,
            "status": "existing",
            "size_bytes": artifact.stat().st_size,
            "metadata_json": json.dumps({"workspace": "results/demo/literature_review_agent"}),
        },
        tmp_path,
    )

    assert row["exists"]
    assert row["status"] == "scaffold_prompt"
    assert row["source_role"] == "scaffold_prompt"
    assert row["audience"] == "prompt"
