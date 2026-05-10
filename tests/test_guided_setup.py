"""Tests for the Stage 7 guided setup state."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from consortium.cli.core.env_manager import save_env_file
from consortium.cli.main import cli
from msc_sdk.setup_state import build_setup_state, tutorial_plan


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["--no-banner", *args], catch_exceptions=False)


def test_setup_state_model_contains_no_secret_values(tmp_path: Path):
    state = build_setup_state(
        project_root=Path.cwd(),
        config_dir=tmp_path / "cfg",
        credential_source="config-dir",
        openrouter_configured=True,
        results_dir=tmp_path / "results",
        openclaude_available=False,
        openclaude_launch_ready=False,
        telegram_enabled=False,
    ).to_dict()

    assert state["openrouter_configured"] is True
    assert state["credential_source"] == "config-dir"
    assert "OPENROUTER_API_KEY" not in json.dumps(state)


def test_project_setup_state_cli_reports_configured_key_without_printing_it(
    tmp_path: Path,
    monkeypatch,
):
    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    save_env_file({"OPENROUTER_API_KEY": "not-a-real-openrouter-key"}, str(config_dir))
    monkeypatch.chdir(tmp_path)

    result = _invoke(
        runner,
        ["--config-dir", str(config_dir), "project", "setup-state", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["setup"]["openrouter_configured"] is True
    assert data["setup"]["credential_source"] == "config-dir"
    assert "not-a-real-openrouter-key" not in result.output


def test_tutorial_plan_is_no_cost_and_no_paid_pipeline():
    plan = tutorial_plan()

    assert plan["spends_budget"] is False
    assert plan["runs_paid_pipeline"] is False
    assert any(step["command"] == "scripts/validation/cheap_dry_run.sh" for step in plan["steps"])


def test_project_tutorial_plan_cli_json():
    runner = CliRunner()

    result = _invoke(runner, ["project", "tutorial-plan", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["spends_budget"] is False
    assert data["runs_paid_pipeline"] is False
