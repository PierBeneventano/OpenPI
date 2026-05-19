"""Tests for the Stage 6 OpenClaude configuration-first integration."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from consortium.cli.core.env_manager import save_env_file
from consortium.cli.main import cli
from msc_sdk.campaign_store import CampaignStore
from msc_sdk.openclaude import openclaude_context_pack, openclaude_env_contract, openclaude_launch_plan


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
    assert plan["operation_contract"]["confirmation_required_for_mutations"] is False
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
    assert data["context_pack"]["schema"] == "msc.openclaude.context_pack.v1"
    assert data["msc_cli"]["shell_prefix"]
    assert data["context_pack"]["msc_cli"]["shell_prefix"] == data["msc_cli"]["shell_prefix"]
    assert data["model_options"]["custom_model_allowed"] is True
    assert "hard_stops" in data["guardrails"]
    assert any(
        operation["operation"] == "campaigns.workspace"
        for operation in data["operation_contract"]["operations"]
    )
    execution_guidance = "\n".join(data["researcher_workflows"]["execution"])
    steering_guidance = "\n".join(data["researcher_workflows"]["feedback_and_steering"])
    assert "campaigns rewind harness-demo" in steering_guidance
    assert "missing SDK capability" in steering_guidance
    assert "msc campaigns start" in execution_guidance
    assert "separate run launcher" in execution_guidance
    researcher_guidance = "\n".join(data["researcher_workflows"]["researcher_questions"])
    assert "run workspaces" in researcher_guidance
    assert "run --campaign-id harness-demo" not in execution_guidance
    assert "run_status.json" in data["guardrails"]["do_not_use_as_truth"]
    assert "not-a-real-openrouter-key" not in result.output


def test_openclaude_context_pack_and_models_cli(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (repo / "consortium").mkdir()
    store = CampaignStore(repo)
    store.create_campaign(
        title="Context CLI Demo",
        objective="Expose compact context through CLI.",
        template="literature_only",
        budget=1,
    )
    monkeypatch.chdir(repo)

    context = _invoke(runner, ["openclaude", "context-pack", "context-cli-demo", "--json"])
    models = _invoke(runner, ["openclaude", "models", "--json"])

    assert context.exit_code == 0
    assert json.loads(context.output)["schema"] == "msc.openclaude.context_pack.v1"
    assert models.exit_code == 0
    assert json.loads(models.output)["custom_model_allowed"] is True


def test_openclaude_context_pack_prefers_linked_deliverables(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = CampaignStore(repo)
    store.create_campaign(
        title="Context Demo",
        objective="Use linked artifacts as durable chat context.",
        template="literature_only",
        budget=1,
    )
    run_id = "id_context_demo"
    workspace = repo / "results" / "context-demo" / "runs" / run_id / "literature_review_agent"
    artifact = workspace / "artifacts" / "literature_matrix.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Matrix\n", encoding="utf-8")
    store.record_artifact(
        "context-demo",
        stage_id="literature_review_agent",
        artifact_path="artifacts/literature_matrix.md",
        workspace=str(workspace.relative_to(repo)),
        kind="md",
        required=True,
        run_id=run_id,
    )
    store.link_context(
        "context-demo",
        target_scope="artifact",
        artifact_path="artifacts/literature_matrix.md",
        node_id="literature_review_agent",
        note="I do not trust the comparison criteria yet.",
    )

    pack = openclaude_context_pack("context-demo", project_root=repo)

    assert pack["schema"] == "msc.openclaude.context_pack.v1"
    assert pack["aim"]["objective"] == "Use linked artifacts as durable chat context."
    assert "graph" in pack["map"]
    assert "claims" in pack["evidence"]
    assert "pending" in pack["decisions"]
    assert pack["active_context_links"][0]["target"]["artifact_path"] == "artifacts/literature_matrix.md"
    assert pack["recent_feedback"][0]["target"]["scope"] == "artifact"
    assert pack["selected_artifacts"][0]["path"] == "artifacts/literature_matrix.md"
    assert "prompt" in pack["context_policy"]["excluded_by_default"]


def test_openclaude_skill_preserves_kernel_guardrails():
    skill = Path("integrations/openclaude/MSC_SKILL.md").read_text()

    assert "Use public `msc` commands" in skill
    assert "msc_cli.shell_prefix" in skill
    assert "Do not directly edit" in skill
    assert "consortium/prompts/" in skill
    assert "confirmation" in skill.lower()
    assert "msc openclaude campaign-harness <campaign> --json" in skill
    assert "campaign execution" in skill
