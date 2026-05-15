"""Tests for the Stage 3 SDK/CLI machine-readable control surface."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from consortium.cli.main import cli
from msc_sdk.campaigns import CampaignClient
from msc_sdk.runs import RunClient
from msc_sdk.validation import ValidationClient


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["--no-banner", *args], catch_exceptions=False)


def _make_run(results_dir: Path, name: str = "consortium_20260510_demo") -> Path:
    run_dir = results_dir / name
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "run_status.json").write_text(
        json.dumps({"status": "completed", "current_stage": "reviewer"})
    )
    (run_dir / "run_summary.json").write_text(
        json.dumps({"task": "Synthetic smoke task", "model": "openrouter/openai/gpt-5-mini"})
    )
    (run_dir / "budget_ledger.jsonl").write_text(
        json.dumps({"model_id": "openrouter/openai/gpt-5-mini", "total_usd": 0.12}) + "\n"
    )
    (run_dir / "logs" / "run.log").write_text("stage complete\n")
    return run_dir


def _make_campaign(tmp_path: Path) -> Path:
    CampaignClient(tmp_path).create(
        title="Demo Campaign",
        objective="Exercise local campaign graph reads.",
        template="consortium_scaffold",
        budget=1,
    )
    return Path("demo-campaign")


def test_run_client_and_cli_list_inspect_budget_logs_match(tmp_path: Path):
    runner = CliRunner()
    results_dir = tmp_path / "results"
    run_dir = _make_run(results_dir)

    sdk_run = RunClient(results_dir).inspect(run_dir.name).to_dict()

    list_result = _invoke(
        runner, ["runs", "list", "--results-dir", str(results_dir), "--json"]
    )
    inspect_result = _invoke(
        runner, ["runs", "inspect", run_dir.name, "--results-dir", str(results_dir), "--json"]
    )
    budget_result = _invoke(
        runner, ["runs", "budget", run_dir.name, "--results-dir", str(results_dir), "--json"]
    )
    logs_result = _invoke(
        runner, ["runs", "logs", run_dir.name, "--results-dir", str(results_dir), "--json"]
    )

    assert list_result.exit_code == 0
    assert inspect_result.exit_code == 0
    assert budget_result.exit_code == 0
    assert logs_result.exit_code == 0
    assert json.loads(list_result.output)["runs"][0]["run_id"] == sdk_run["run_id"]
    assert json.loads(inspect_result.output)["status"] == "completed"
    assert json.loads(budget_result.output)["budget"]["total_usd"] == 0.12
    assert json.loads(logs_result.output)["logs"][0]["path"] == "logs/run.log"


def test_artifacts_and_campaigns_cli_emit_dashboard_json(tmp_path: Path):
    runner = CliRunner()
    results_dir = tmp_path / "results"
    _make_run(results_dir)
    campaign = _make_campaign(tmp_path)

    artifacts_result = _invoke(runner, ["artifacts", "inspect", str(results_dir), "--json"])
    graph_result = _invoke(
        runner, ["campaigns", "--root", str(tmp_path), "graph", str(campaign), "--json"]
    )

    assert artifacts_result.exit_code == 0
    assert json.loads(artifacts_result.output)["source_kind"] == "results_dir"
    assert graph_result.exit_code == 0
    graph = json.loads(graph_result.output)
    assert graph["nodes"][0]["id"] == "persona_council"
    assert graph["edges"][0]["source"] == "persona_council"
    assert graph["edges"][0]["target"] == "literature_review_agent"
    assert graph["edges"][0]["kind"] == "stage_order"
    assert any(edge["kind"] == "loop" for edge in graph["edges"])


def test_campaign_client_and_selftest_expose_parity_contract(tmp_path: Path):
    campaign = _make_campaign(tmp_path)
    graph = CampaignClient(tmp_path).graph(str(campaign))
    validation = ValidationClient()
    commands = validation.commands()["commands"]
    agent_contract = validation.operation_contract("read_only")

    assert graph["nodes"][0]["id"] == "persona_council"
    assert any(command["operation"] == "runs.inspect" for command in commands)
    assert any(command["operation"] == "campaigns.graph" for command in commands)
    assert any(command["operation"] == "campaigns.create" for command in commands)
    assert any(command["operation"] == "campaigns.explain_node" for command in commands)
    assert agent_contract["surface"] == "msc_cli_sdk_v1"
    assert all(not operation["mutates"] for operation in agent_contract["operations"])
    assert "campaign_events" in agent_contract["read_model_sources"]


def test_campaign_list_uses_store_only_and_ignores_template_placeholder(tmp_path: Path):
    (tmp_path / "campaign_template.yaml").write_text(
        "name: My Research Campaign\nstages: []\n", encoding="utf-8"
    )
    assert CampaignClient(tmp_path).list() == []


def test_local_first_campaign_cli_create_events_export_and_approve(tmp_path: Path):
    runner = CliRunner()

    create = _invoke(
        runner,
        [
            "campaigns",
            "--root",
            str(tmp_path),
            "create",
            "--title",
            "CLI Campaign",
            "--objective",
            "Exercise the local-first campaign store.",
            "--template",
            "consortium_scaffold",
            "--budget",
            "1",
            "--json",
        ],
    )
    assert create.exit_code == 0
    created = json.loads(create.output)["campaign"]
    assert created["campaign_id"] == "cli-campaign"

    graph = _invoke(
        runner, ["campaigns", "--root", str(tmp_path), "graph", "cli-campaign", "--json"]
    )
    assert graph.exit_code == 0
    assert json.loads(graph.output)["nodes"][0]["id"] == "persona_council"

    events = _invoke(
        runner, ["campaigns", "--root", str(tmp_path), "events", "cli-campaign", "--json"]
    )
    assert events.exit_code == 0
    assert any(event["type"] == "CampaignCreated" for event in json.loads(events.output)["events"])

    approve = _invoke(
        runner,
        [
            "campaigns",
            "--root",
            str(tmp_path),
            "approve-graph",
            "cli-campaign",
            "--graph-version",
            "1",
            "--json",
        ],
    )
    assert approve.exit_code == 0
    assert json.loads(approve.output)["state"] == "approved"

    export = _invoke(
        runner, ["campaigns", "--root", str(tmp_path), "export", "cli-campaign", "--json"]
    )
    assert export.exit_code == 0
    bundle_path = Path(json.loads(export.output)["bundle_path"])
    assert (bundle_path / "campaign.json").exists()
    assert (bundle_path / "graph.json").exists()


def test_campaign_cli_steering_surfaces_are_json_and_audited(tmp_path: Path):
    runner = CliRunner()
    _make_campaign(tmp_path)

    explain = _invoke(
        runner,
        ["campaigns", "--root", str(tmp_path), "explain-node", "demo-campaign", "lit_review_gate", "--json"],
    )
    assert explain.exit_code == 0
    assert json.loads(explain.output)["contract"]["kind"] in {"gate", "router"}

    proposal = _invoke(
        runner,
        [
            "campaigns",
            "--root",
            str(tmp_path),
            "rewrite-stage",
            "demo-campaign",
            "writeup_agent",
            "--instruction",
            "Tighten the claims and rewrite the limitations section.",
            "--json",
        ],
    )
    assert proposal.exit_code == 0
    proposal_data = json.loads(proposal.output)
    assert proposal_data["approval"]["status"] == "pending"

    approve = _invoke(
        runner,
        [
            "campaigns",
            "--root",
            str(tmp_path),
            "approve",
            proposal_data["approval"]["id"],
            "--json",
        ],
    )
    assert approve.exit_code == 0
    assert json.loads(approve.output)["status"] == "approved"

    pause = _invoke(
        runner,
        ["campaigns", "--root", str(tmp_path), "pause", "demo-campaign", "--reason", "manual check", "--json"],
    )
    assert pause.exit_code == 0
    assert json.loads(pause.output)["status"] == "paused"

    feedback = _invoke(
        runner,
        [
            "campaigns",
            "--root",
            str(tmp_path),
            "feedback",
            "demo-campaign",
            "--node",
            "writeup_agent",
            "--text",
            "Please clarify the theorem statement before rerunning.",
            "--json",
        ],
    )
    assert feedback.exit_code == 0
    feedback_data = json.loads(feedback.output)
    assert feedback_data["message_id"]
    assert feedback_data["event"]["type"] == "InstructionSent"
    assert feedback_data["event"]["payload"]["type"] == "feedback"
    assert feedback_data["event"]["payload"]["metadata"]["node_id"] == "writeup_agent"

    context = _invoke(
        runner,
        [
            "campaigns",
            "--root",
            str(tmp_path),
            "context",
            "link",
            "demo-campaign",
            "--scope",
            "artifact",
            "--node",
            "writeup_agent",
            "--artifact-path",
            "artifacts/paper.md",
            "--note",
            "This paper draft needs a sharper contribution statement.",
            "--json",
        ],
    )
    assert context.exit_code == 0
    context_data = json.loads(context.output)
    assert context_data["link_id"]

    context_list = _invoke(
        runner,
        ["campaigns", "--root", str(tmp_path), "context", "list", "demo-campaign", "--json"],
    )
    assert context_list.exit_code == 0
    assert json.loads(context_list.output)["active_links"][0]["target"]["artifact_path"] == "artifacts/paper.md"

    summary = _invoke(
        runner,
        ["campaigns", "--root", str(tmp_path), "summarize-artifacts", "demo-campaign", "--json"],
    )
    assert summary.exit_code == 0
    assert json.loads(summary.output)["total"] > 0


def test_selftest_commands_cli_json():
    runner = CliRunner()

    result = _invoke(runner, ["selftest", "commands", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert any(command["cli"].startswith("msc project inspect") for command in data["commands"])
