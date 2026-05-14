"""Tests for the Stage 6 OpenClaude configuration-first integration."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from consortium.cli.core.env_manager import save_env_file
from consortium.cli.main import cli
from msc_sdk.campaign_store import CampaignStore
from msc_sdk.openclaude import openclaude_env_contract, openclaude_launch_plan


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["--no-banner", *args], catch_exceptions=False)


def test_openclaude_readiness_uses_config_key_without_printing_secret(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    save_env_file({"OPENROUTER_API_KEY": "not-a-real-openrouter-key"}, str(config_dir))
    monkeypatch.chdir(tmp_path)

    result = _invoke(
        runner,
        ["--config-dir", str(config_dir), "openclaude", "readiness", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["openrouter_configured"] is True
    assert data["openrouter_source"] == "config-dir"
    assert "not-a-real-openrouter-key" not in result.output


def test_openclaude_env_contract_is_redacted():
    env = openclaude_env_contract(openrouter_configured=True, model="openai/gpt-5-mini")

    assert env["CLAUDE_CODE_USE_OPENAI"] == "1"
    assert env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["OPENAI_MODEL"] == "openai/gpt-5-mini"
    assert env["OPENAI_API_KEY"] == "[REDACTED]"


def test_openclaude_launch_plan_is_non_executing_and_scoped(tmp_path: Path):
    plan = openclaude_launch_plan(
        project_root=tmp_path,
        openrouter_configured=True,
        extra_args=["--help"],
    )

    assert plan["ok"] is True
    assert plan["command"][-1] == "--help"
    assert "--append-system-prompt-file" in plan["command"]
    assert "--add-dir" in plan["command"]
    assert plan["cwd"] == str(tmp_path)
    assert plan["capability_profile"] == "openclaude_v1"
    assert plan["env"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert plan["operation_contract"]["surface"] == "msc_cli_sdk_v1"
    assert plan["operation_contract"]["confirmation_required_for_mutations"] is True
    assert any(
        operation["operation"] == "campaigns.approve"
        for operation in plan["operation_contract"]["operations"]
    )


def test_openclaude_cli_launch_defaults_to_plan_only(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    save_env_file({"OPENROUTER_API_KEY": "not-a-real-openrouter-key"}, str(config_dir))
    monkeypatch.chdir(tmp_path)

    result = _invoke(
        runner,
        ["--config-dir", str(config_dir), "openclaude", "launch", "--json", "--", "--help"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["execute"] is False
    assert data["command"][-1] == "--help"
    assert "--append-system-prompt-file" in data["command"]
    assert "--add-dir" in data["command"]
    assert data["env"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert "not-a-real-openrouter-key" not in result.output


def test_openclaude_campaign_harness_exposes_workspace_and_guardrails(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    save_env_file({"OPENROUTER_API_KEY": "not-a-real-openrouter-key"}, str(config_dir))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (repo / "consortium").mkdir()
    (repo / "results").mkdir()
    store = CampaignStore(repo)
    store.create_campaign(
        title="Harness Demo",
        objective="Let OpenClaude inspect and steer the campaign.",
        template="literature_only",
        budget=1,
    )
    monkeypatch.chdir(repo)

    result = _invoke(
        runner,
        ["--config-dir", str(config_dir), "openclaude", "campaign-harness", "harness-demo", "--json"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema"] == "msc.openclaude.campaign_harness.v1"
    assert data["workspace"]["campaign"]["id"] == "harness-demo"
    assert data["workspace"]["execution"]["status"] == "not_started"
    assert data["readiness"]["openrouter_configured"] is True
    assert data["operation_contract"]["profile"] == "openclaude_v1"
    assert any(
        operation["operation"] == "campaigns.workspace"
        for operation in data["operation_contract"]["operations"]
    )
    assert "run_status.json" in data["guardrails"]["do_not_use_as_truth"]
    assert "not-a-real-openrouter-key" not in result.output


def test_openclaude_skill_preserves_kernel_guardrails():
    skill = Path("integrations/openclaude/MSC_SKILL.md").read_text()

    assert "Use public `msc` commands" in skill
    assert "Do not directly edit" in skill
    assert "consortium/prompts/" in skill
    assert "confirmation" in skill.lower()
    assert "msc openclaude campaign-harness <campaign> --json" in skill
    assert "campaign execution" in skill
