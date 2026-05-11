from __future__ import annotations

import json
from pathlib import Path

from msc_sdk.campaign_store import CampaignStore
from msc_sdk.campaigns import CampaignClient
from msc_sdk.stage_runtime import StageRunContext, materialize_stage_outputs


def test_campaign_store_creates_sqlite_jsonl_snapshot_and_scaffold(tmp_path: Path):
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
    assert "control" in graph["modes"]
    assert any(edge["kind"] == "loop" for edge in graph["edges"])

    artifact = tmp_path / "results" / "consortium-graph-demo" / "persona_council" / "artifacts" / "persona_debate.md"
    assert artifact.exists()

    events = store.events("consortium-graph-demo")["events"]
    assert any(event["type"] == "CampaignCreated" for event in events)
    assert any(event["type"] == "ArtifactDeclared" for event in events)


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


def test_event_jsonl_can_replay_into_fresh_db(tmp_path: Path):
    store = CampaignStore(tmp_path)
    store.create_campaign(title="Replay Demo", objective="Replay events.", template="blank", budget=1)

    db_path = tmp_path / ".msc" / "campaigns.db"
    db_path.unlink()

    replayed = CampaignStore(tmp_path).replay_jsonl()
    assert replayed >= 1

    lines = (tmp_path / ".msc" / "events" / "campaigns.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "CampaignCreated"


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
