from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from consortium.cli.main import cli
from msc_sdk.campaigns import CampaignClient


OBJECTIVE = (
    "Investigate, in a toy setting, whether batch normalization changes the spectral norm "
    "growth of a 2-layer MLP trained on synthetic Gaussian blobs. Produce a minimal "
    "empirical comparison and a short markdown writeup."
)


def _approve_all(client: CampaignClient, workspace: dict) -> None:
    for decision in workspace["pending_decisions"]:
        client.approve(decision["id"])


def test_sdk_native_campaign_completes_lean_smoke_path(tmp_path: Path):
    client = CampaignClient(tmp_path)
    created = client.create(
        title="Native Lean Smoke",
        objective=OBJECTIVE,
        template="target_research",
        tier="lean",
        budget=25,
        output_format="markdown",
    )
    campaign_id = created["campaign_id"]
    client.approve_graph(campaign_id, 1)

    first = client.start(
        campaign_id,
        tier="lean",
        budget=25,
        output_format="markdown",
        math_enabled=False,
        counsel_enabled=False,
    )

    assert first["runtime"] == "sdk_native"
    assert first["workspace"]["execution"]["status"] == "human_decision_required"
    assert first["workspace"]["execution"]["current_stage_id"] == "milestone_goals"
    assert first["workspace"]["pending_decisions"][0]["target_type"] == "kernel_decision"
    assert first["workspace"]["pending_decisions"][0]["reason"] == "pause_after_stage"
    assert "approve" in first["workspace"]["safe_next_actions"]
    with client.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    _approve_all(client, first["workspace"])
    second = client.continue_execution(campaign_id)

    assert second["workspace"]["execution"]["status"] == "human_decision_required"
    assert second["workspace"]["execution"]["current_stage_id"] == "resource_preparation_agent"
    assert second["workspace"]["duality"]["status"] == "passed"
    assert second["workspace"]["pending_decisions"][0]["reason"] == "pause_before_stage"

    _approve_all(client, second["workspace"])
    completed = client.continue_execution(campaign_id)
    workspace = completed["workspace"]

    assert workspace["execution"]["status"] == "completed"
    assert workspace["campaign"]["status"] == "completed"
    assert workspace["pending_decisions"] == []
    assert workspace["duality"]["status"] == "passed"
    assert workspace["aim"]["objective"] == OBJECTIVE
    assert workspace["map"]["current_stage_id"] == workspace["execution"]["current_stage_id"]
    assert workspace["evidence"]["claims"][0]["id"] == "C1"
    assert workspace["evidence"]["limitations"]
    assert workspace["decisions"]["gate_verdicts"][0]["gate_id"] == "duality_gate"
    assert workspace["diagnostics"]["legacy_attempts"] == []
    assert any(
        artifact["stage_id"] == "writeup_agent"
        and artifact["path"] == "artifacts/final_paper.md"
        and artifact["exists"]
        for artifact in workspace["deliverables"]
    )
    artifact_summary = client.summarize_artifacts(campaign_id)
    budget = client.inspect_budget(campaign_id)
    assert budget["spent_usd"] > 0
    assert budget["remaining_usd"] < 25
    assert artifact_summary["missing_required"] == 0
    assert artifact_summary["by_stage"]["writeup_agent"]["missing_required"] == 0
    assert artifact_summary["by_stage"]["theory_track"]["skipped"]
    assert artifact_summary["by_stage"]["followup_lit_review"]["skipped"]
    event_types = [event["type"] for event in client.events(campaign_id)["events"]]
    council_start = next(event for event in client.events(campaign_id)["events"] if event["type"] == "CouncilStarted")
    assert council_start["payload"]["model_ids"] == ["deepseek-chat"]
    assert not any(event_type.startswith("Run") for event_type in event_types)
    assert "CampaignExecutionFailed" not in event_types
    assert "CampaignExecutionPrepared" in event_types
    assert "CampaignExecutionCompleted" in event_types
    assert "DualityCheckCompleted" in event_types


def test_sdk_native_duality_failure_blocks_writeup_with_safe_actions(tmp_path: Path):
    client = CampaignClient(tmp_path)
    campaign_id = client.create(
        title="Native Duality Failure",
        objective=OBJECTIVE,
        template="target_research",
        tier="lean",
        budget=25,
        output_format="markdown",
    )["campaign_id"]
    client.approve_graph(campaign_id, 1)

    first = client.start(campaign_id, force_duality_fail=True)
    _approve_all(client, first["workspace"])
    blocked = client.continue_execution(campaign_id)
    workspace = blocked["workspace"]

    assert workspace["execution"]["status"] == "human_decision_required"
    assert workspace["execution"]["current_stage_id"] == "duality_gate"
    assert workspace["duality"]["status"] == "failed"
    assert workspace["decisions"]["gate_verdicts"][0]["passed"] is False
    assert workspace["evidence"]["objections"][0]["claim_id"] == "C1"
    decision = workspace["pending_decisions"][0]
    assert decision["target_type"] == "kernel_decision"
    assert decision["reason"] == "duality_failed"
    assert decision["safe_next_actions"] == [
        "revise-goals",
        "rerun-literature",
        "rerun-experiment-track",
        "reroute",
        "stop-campaign",
    ]
    assert not any(artifact["path"] == "artifacts/final_paper.md" for artifact in workspace["deliverables"])


def test_campaigns_start_and_continue_cli_use_sdk_native_executor(tmp_path: Path):
    runner = CliRunner()
    create = runner.invoke(
        cli,
        [
            "--no-banner",
            "campaigns",
            "--root",
            str(tmp_path),
            "create",
            "--title",
            "Native CLI Smoke",
            "--objective",
            OBJECTIVE,
            "--template",
            "target_research",
            "--tier",
            "lean",
            "--budget",
            "25",
            "--json",
        ],
        catch_exceptions=False,
    )
    assert create.exit_code == 0
    client = CampaignClient(tmp_path)
    campaign_id = client.list()[0]["id"]
    client.approve_graph(campaign_id, 1)

    start = runner.invoke(
        cli,
        [
            "--no-banner",
            "campaigns",
            "--root",
            str(tmp_path),
            "start",
            campaign_id,
            "--tier",
            "lean",
            "--budget",
            "25",
            "--no-math",
            "--no-counsel",
            "--json",
        ],
        catch_exceptions=False,
    )

    assert start.exit_code == 0
    workspace = client.workspace(campaign_id)
    assert workspace["execution"]["status"] == "human_decision_required"
    _approve_all(client, workspace)

    continued = runner.invoke(
        cli,
        [
            "--no-banner",
            "campaigns",
            "--root",
            str(tmp_path),
            "continue",
            campaign_id,
            "--json",
        ],
        catch_exceptions=False,
    )

    assert continued.exit_code == 0
    assert client.workspace(campaign_id)["execution"]["current_stage_id"] == "resource_preparation_agent"
