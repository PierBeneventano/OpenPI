"""Tests for the Stage 3 SDK/CLI machine-readable control surface."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
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
    campaign = tmp_path / "demo_campaign.yaml"
    workspace = tmp_path / "campaign_workspace"
    workspace.mkdir()
    campaign.write_text(
        yaml.safe_dump(
            {
                "name": "Demo Campaign",
                "workspace_root": str(workspace),
                "budget_usd": 2,
                "stages": [
                    {
                        "id": "stage_a",
                        "success_artifacts": {"required": ["paper_workspace/final_paper.md"]},
                    },
                    {"id": "stage_b"},
                ],
            }
        )
    )
    return campaign


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
        runner, ["campaigns", "--root", str(tmp_path), "graph", campaign.name, "--json"]
    )

    assert artifacts_result.exit_code == 0
    assert json.loads(artifacts_result.output)["source_kind"] == "results_dir"
    assert graph_result.exit_code == 0
    graph = json.loads(graph_result.output)
    assert graph["nodes"][0]["id"] == "stage_a"
    assert graph["edges"] == [{"source": "stage_a", "target": "stage_b", "kind": "stage_order"}]


def test_campaign_client_and_selftest_expose_parity_contract(tmp_path: Path):
    campaign = _make_campaign(tmp_path)
    graph = CampaignClient(tmp_path).graph(campaign.name)
    commands = ValidationClient().commands()["commands"]

    assert graph["nodes"][0]["id"] == "stage_a"
    assert any(command["operation"] == "runs.inspect" for command in commands)
    assert any(command["operation"] == "campaigns.graph" for command in commands)


def test_selftest_commands_cli_json():
    runner = CliRunner()

    result = _invoke(runner, ["selftest", "commands", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert any(command["cli"].startswith("msc project inspect") for command in data["commands"])
