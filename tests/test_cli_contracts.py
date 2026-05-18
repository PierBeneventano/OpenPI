"""Regression tests for the msc CLI contract surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import yaml
from click.testing import CliRunner
import pytest

from consortium.cli.core.config_manager import load_config, load_explicit_config
from consortium.cli.core.env_manager import build_runtime_env, get_runtime_env_sources
from consortium.cli.main import cli
from consortium.cli.core.env_manager import save_env_file


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["--no-banner", *args], catch_exceptions=False)


def _fake_completed_process(returncode: int = 0):
    return SimpleNamespace(returncode=returncode, stdout="", stderr="")


def test_campaign_init_writes_valid_spec_and_task_file(tmp_path, monkeypatch):
    from consortium.campaign.spec import load_spec

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(tmp_path / "cfg"),
            "campaign",
            "init",
            "--name",
            "Muon Project",
            "--task",
            "Investigate normalization dynamics in transformers.",
            "--budget",
            "123",
        ],
    )

    assert result.exit_code == 0

    campaign_file = tmp_path / "muon_project_campaign.yaml"
    task_file = tmp_path / "automation_tasks" / "generated" / "muon_project_discovery_task.txt"
    assert campaign_file.exists()
    assert task_file.exists()

    spec = load_spec(str(campaign_file))
    assert spec.planning is not None
    assert spec.planning.base_task_file.endswith("muon_project_discovery_task.txt")
    assert spec.budget_usd == 123
    assert "Investigate normalization dynamics" in task_file.read_text()


def test_load_spec_accepts_legacy_inline_planning_task(tmp_path):
    from consortium.campaign.spec import load_spec

    campaign_file = tmp_path / "legacy_campaign.yaml"
    campaign_file.write_text(
        "name: legacy\n"
        "workspace_root: results/legacy\n"
        "planning:\n"
        "  enabled: true\n"
        "  base_task: Legacy inline task\n"
        "stages: []\n"
        "budget:\n"
        "  usd_limit: 77\n"
    )

    with pytest.deprecated_call():
        spec = load_spec(str(campaign_file))

    assert spec.planning is not None
    assert spec.budget_usd == 77
    assert spec.planning.base_task_file.endswith("legacy_campaign_discovery_task.txt")
    assert Path(spec.planning.base_task_file).exists()
    assert "Legacy inline task" in Path(spec.planning.base_task_file).read_text()


def test_campaign_start_uses_config_dir_env_without_repo_root_dotenv(tmp_path, monkeypatch):
    import consortium.cli.commands.campaign as campaign_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(config_dir))

    campaign_file = tmp_path / "demo_campaign.yaml"
    campaign_file.write_text("name: demo\nstages: []\nplanning:\n  enabled: false\n")

    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _fake_completed_process(0)

    monkeypatch.setattr(campaign_cmd, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(campaign_cmd, "find_script_path", lambda name: tmp_path / "scripts" / name)
    monkeypatch.setattr(campaign_cmd.subprocess, "run", fake_run)

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "campaign",
            "start",
            str(campaign_file),
        ],
    )

    assert result.exit_code == 0
    assert captured["args"][0][1] == str(tmp_path / "scripts" / "campaign_heartbeat.py")
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["env"]["OPENROUTER_API_KEY"] == "sk-or-test"


def test_campaign_status_accepts_dict_stage_payloads(tmp_path, monkeypatch):
    import consortium.cli.commands.campaign as campaign_cmd

    runner = CliRunner()
    campaign_file = tmp_path / "demo_campaign.yaml"
    campaign_file.write_text("name: demo\nstages: []\nplanning:\n  enabled: false\n")

    payload = {
        "campaign_name": "Audit Demo",
        "stages": {
            "discovery_plan": {
                "status": "pending",
                "duration": "-",
            }
        },
        "budget": {
            "campaign_lifetime_usd": 0.0,
            "campaign_limit_usd": 42.0,
        },
    }

    monkeypatch.setattr(campaign_cmd, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(campaign_cmd, "find_script_path", lambda name: tmp_path / "scripts" / name)
    monkeypatch.setattr(
        campaign_cmd.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )

    result = _invoke(
        runner,
        [
            "campaign",
            "status",
            str(campaign_file),
        ],
    )

    assert result.exit_code == 0
    assert "Audit Demo" in result.output
    assert "discovery_plan" in result.output


def test_run_rejects_openai_only_key(tmp_path, monkeypatch):
    import consortium.cli.commands.run as run_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENAI_API_KEY": "sk-test-openai-only"}, str(config_dir))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_cmd, "find_project_root", lambda: None)

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "run",
            "--dry-run",
            "Test task",
        ],
    )

    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is required" in result.output


def test_run_uses_persisted_config_overrides(tmp_path, monkeypatch):
    import consortium.cli.commands.run as run_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(config_dir))

    captured: dict[str, object] = {}

    def fake_run(argv, env=None, **kwargs):
        captured["argv"] = argv
        captured["env"] = env
        return _fake_completed_process(0)

    monkeypatch.setattr(run_cmd.subprocess, "run", fake_run)

    for key, value in [
        ("tier", "budget"),
        ("model", "gpt-5"),
        ("budget_usd", "55"),
        ("output_format", "markdown"),
        ("enable_counsel", "true"),
        ("enable_math_agents", "true"),
        ("enable_tree_search", "true"),
        ("mode", "hpc"),
    ]:
        result = _invoke(
            runner,
            ["--config-dir", str(config_dir), "config", "set", key, value],
        )
        assert result.exit_code == 0

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "--quiet",
            "run",
            "--dry-run",
            "Test task",
        ],
    )

    assert result.exit_code == 0
    argv = captured["argv"]
    assert argv[:3] == [sys.executable, "-m", "consortium.runner"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gpt-5"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "markdown"
    assert "--enable-counsel" in argv
    assert "--enable-math-agents" in argv
    assert "--enable-tree-search" in argv
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "hpc"

    llm_cfg = yaml.safe_load((tmp_path / ".llm_config.yaml").read_text())
    assert llm_cfg["main_agents"]["model"] == "gpt-5"
    assert llm_cfg["budget"]["usd_limit"] == 55
    assert llm_cfg["counsel"]["enabled"] is True


def test_run_explicit_tier_beats_setup_style_defaults(tmp_path, monkeypatch):
    import consortium.cli.commands.run as run_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(config_dir))

    captured: dict[str, object] = {}

    def fake_run(argv, env=None, **kwargs):
        captured["argv"] = argv
        captured["env"] = env
        return _fake_completed_process(0)

    monkeypatch.setattr(run_cmd.subprocess, "run", fake_run)

    for key, value in [
        ("tier", "medium"),
        ("model", "claude-sonnet-4-6"),
        ("budget_usd", "200"),
        ("output_format", "latex"),
        ("enable_counsel", "false"),
        ("enable_math_agents", "true"),
        ("enable_tree_search", "false"),
    ]:
        result = _invoke(
            runner,
            ["--config-dir", str(config_dir), "config", "set", key, value],
        )
        assert result.exit_code == 0

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "--quiet",
            "run",
            "--tier",
            "budget",
            "--dry-run",
            "Test task",
        ],
    )

    assert result.exit_code == 0
    argv = captured["argv"]
    assert argv[:3] == [sys.executable, "-m", "consortium.runner"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gpt-5-mini"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "markdown"
    assert "--enable-counsel" not in argv
    assert "--enable-math-agents" not in argv
    assert "--enable-tree-search" not in argv
    assert "--no-counsel" in argv

    llm_cfg = yaml.safe_load((tmp_path / ".llm_config.yaml").read_text())
    assert llm_cfg["main_agents"]["model"] == "gpt-5-mini"
    assert llm_cfg["budget"]["usd_limit"] == 35
    assert llm_cfg["counsel"]["enabled"] is False


def test_run_accepts_product_tier_names_before_legacy_runtime(tmp_path, monkeypatch):
    import consortium.cli.commands.run as run_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(config_dir))

    captured: dict[str, object] = {}

    def fake_run(argv, env=None, **kwargs):
        captured["argv"] = argv
        captured["env"] = env
        return _fake_completed_process(0)

    monkeypatch.setattr(run_cmd.subprocess, "run", fake_run)

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "--quiet",
            "run",
            "--tier",
            "standard",
            "--dry-run",
            "Test task",
        ],
    )

    assert result.exit_code == 0
    argv = captured["argv"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--dry-run" in argv


def test_run_rejects_invalid_model_before_spawning_runner(tmp_path, monkeypatch):
    import consortium.cli.commands.run as run_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(config_dir))

    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return _fake_completed_process(0)

    monkeypatch.setattr(run_cmd.subprocess, "run", fake_run)

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "run",
            "--tier",
            "standard",
            "--model",
            "moonshotai/kimi-k2",
            "--dry-run",
            "Test task",
        ],
    )

    assert result.exit_code == 2
    assert "Unknown model 'moonshotai/kimi-k2'" in result.output
    assert called is False


def test_load_config_derives_tier_defaults_without_persisting_them(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("tier: budget\npreset: budget\nmode: hpc\n")
    monkeypatch.chdir(tmp_path)

    cfg = load_config(str(config_dir))
    explicit_cfg = load_explicit_config(str(config_dir))

    assert cfg["tier"] == "budget"
    assert cfg["preset"] == "budget"
    assert cfg["model"] == "gpt-5-mini"
    assert cfg["budget_usd"] == 35
    assert cfg["output_format"] == "markdown"
    assert cfg["mode"] == "hpc"
    assert explicit_cfg == {"tier": "budget", "preset": "budget", "mode": "hpc"}


def test_run_backs_up_stale_llm_config_before_regeneration(tmp_path, monkeypatch):
    import consortium.cli.commands.run as run_cmd

    runner = CliRunner()
    config_dir = tmp_path / "cfg"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(config_dir))

    _invoke(runner, ["--config-dir", str(config_dir), "config", "set", "tier", "budget"])
    (tmp_path / ".llm_config.yaml").write_text("main_agents:\n  model: stale\n")

    monkeypatch.setattr(run_cmd.subprocess, "run", lambda *args, **kwargs: _fake_completed_process(0))

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(config_dir),
            "--quiet",
            "run",
            "--dry-run",
            "Test task",
        ],
    )

    assert result.exit_code == 0
    backup_path = tmp_path / ".llm_config.yaml.bak"
    assert backup_path.exists()
    assert "model: stale" in backup_path.read_text()
    assert "Auto-generated by msc" in (tmp_path / ".llm_config.yaml").read_text()


def test_doctor_rejects_openai_only_and_accepts_openrouter(tmp_path, monkeypatch):
    import consortium.cli.commands.doctor as doctor_cmd

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    fake_info = SimpleNamespace(
        missing=[],
        python_version="3.11.0",
        has_git=True,
        has_pdflatex=False,
        has_playwright=False,
        has_slurm=False,
    )

    monkeypatch.setattr(doctor_cmd, "detect", lambda: fake_info)
    monkeypatch.setattr(doctor_cmd, "detect_consortium", lambda: (True, "/tmp/consortium"))
    monkeypatch.setattr(doctor_cmd, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        doctor_cmd,
        "find_script_path",
        lambda name: tmp_path / "scripts" / name,
    )
    monkeypatch.setattr(
        doctor_cmd.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "git" else None,
    )

    openai_cfg = tmp_path / "cfg-openai"
    save_env_file({"OPENAI_API_KEY": "sk-test-openai-only"}, str(openai_cfg))
    result = _invoke(runner, ["--config-dir", str(openai_cfg), "doctor"])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not configured" in result.output

    openrouter_cfg = tmp_path / "cfg-openrouter"
    save_env_file({"OPENROUTER_API_KEY": "sk-or-test"}, str(openrouter_cfg))
    result = _invoke(runner, ["--config-dir", str(openrouter_cfg), "doctor"])
    assert result.exit_code == 0
    assert "All checks passed" in result.output
    assert "config-dir" in result.output


def test_runtime_env_ignores_repo_env_outside_repo_root(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    workspace = tmp_path / "work"
    repo_root.mkdir()
    workspace.mkdir()
    (repo_root / ".env").write_text('OPENROUTER_API_KEY="sk-or-from-repo"\n')
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    env = build_runtime_env(repo_root=repo_root)
    sources = get_runtime_env_sources(repo_root=repo_root)
    assert env.get("OPENROUTER_API_KEY") is None
    assert "OPENROUTER_API_KEY" not in sources

    env = build_runtime_env(repo_root=repo_root, allow_repo_env=True)
    sources = get_runtime_env_sources(repo_root=repo_root, allow_repo_env=True)
    assert env["OPENROUTER_API_KEY"] == "sk-or-from-repo"
    assert sources["OPENROUTER_API_KEY"] == "repo-env"


def test_ultra_llm_config_propagates_persona_council_details():
    from consortium.cli.core.llm_config_generator import tier_to_llm_config
    from consortium.cli.core.presets import TIERS

    cfg = tier_to_llm_config(TIERS["ultra"])

    assert cfg["persona_council"]["max_post_vote_retries"] == 3
    assert len(cfg["persona_council"]["personas"]) == len(TIERS["ultra"].persona_council_specs or ())
    assert cfg["persona_council"]["personas"][0]["persona"] == "practical_compass"
    assert "model_kwargs" not in cfg["persona_council"]["personas"][0]


def test_budget_llm_config_persona_defaults_stay_inside_budget_tier():
    from consortium.cli.core.llm_config_generator import tier_to_llm_config
    from consortium.cli.core.presets import TIERS

    cfg = tier_to_llm_config(TIERS["budget"])

    assert cfg["main_agents"]["model"] == "gpt-5-mini"
    assert cfg["summary_model"]["model"] == "gpt-5-mini"
    persona_models = {spec["model"] for spec in cfg["persona_council"]["personas"]}
    assert persona_models == {"gpt-5-mini"}


def test_live_smoke_llm_config_forces_cheap_single_model_surface():
    from consortium.cli.core.llm_config_generator import tier_to_llm_config
    from consortium.cli.core.presets import TIER_ORDER, TIERS

    cfg = tier_to_llm_config(TIERS["live-smoke"])

    assert TIER_ORDER[0] == "live-smoke"
    assert cfg["main_agents"]["model"] == "deepseek-chat"
    assert cfg["summary_model"]["model"] == "deepseek-chat"
    assert cfg["budget"]["usd_limit"] == 2
    assert cfg["counsel"]["enabled"] is False
    assert cfg["counsel"]["models"] == []
    assert cfg["persona_council"]["max_debate_rounds"] == 1
    assert cfg["persona_council"]["max_post_vote_retries"] == 0
    assert {spec["model"] for spec in cfg["persona_council"]["personas"]} == {"deepseek-chat"}
    assert set(cfg["run_experiment_tool"].values()) == {"deepseek-chat"}


def test_product_tier_names_resolve_to_runtime_tiers():
    from consortium.cli.core.presets import TIER_CHOICES, resolve_tier_name

    assert "standard" in TIER_CHOICES
    assert "serious" in TIER_CHOICES
    assert resolve_tier_name("scaffold") == "live-smoke"
    assert resolve_tier_name("lean") == "budget"
    assert resolve_tier_name("standard") == "medium"
    assert resolve_tier_name("serious") == "pro"
    assert resolve_tier_name("ultra") == "ultra"


def test_status_reports_stalled_run_and_reads_ledger_cost(tmp_path, monkeypatch):
    import consortium.cli.core.run_inspector as inspector

    runner = CliRunner()
    results_dir = tmp_path / "results"
    run_dir = results_dir / "consortium_20260409_010203_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "run_status.json").write_text(
        json.dumps({"status": "running", "current_stage": "persona_council", "pid": 12345})
    )
    (run_dir / ".progress_heartbeat").write_text(json.dumps({"pid": 12345, "stage": "persona_council"}))
    (run_dir / "budget_ledger.jsonl").write_text(
        json.dumps({"total_usd": 1.23, "model_id": "openrouter/openai/gpt-5-mini"}) + "\n"
    )
    stale_ts = 1_700_000_000
    os.utime(run_dir / ".progress_heartbeat", (stale_ts, stale_ts))
    os.utime(run_dir / "budget_ledger.jsonl", (stale_ts, stale_ts))
    monkeypatch.setattr(inspector, "_is_pid_running", lambda pid: True)

    result = _invoke(runner, ["status", "--results-dir", str(results_dir)])

    assert result.exit_code == 0
    assert "stalled" in result.output
    assert "$1.23" in result.output


def test_logs_reads_workspace_log_file(tmp_path):
    runner = CliRunner()
    results_dir = tmp_path / "results"
    run_dir = results_dir / "consortium_20260409_010203_demo"
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "consortium_20260409.out").write_text("INFO pipeline started\n")

    result = _invoke(runner, ["logs", run_dir.name, "--results-dir", str(results_dir)])

    assert result.exit_code == 0
    assert "pipeline started" in result.output


def test_status_does_not_report_dead_pid_as_active(tmp_path, monkeypatch):
    import consortium.cli.core.run_inspector as inspector

    runner = CliRunner()
    results_dir = tmp_path / "results"
    run_dir = results_dir / "consortium_20260409_010203_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "run_status.json").write_text(
        json.dumps({"status": "running", "current_stage": "persona_council", "pid": 12345})
    )
    (run_dir / ".progress_heartbeat").write_text(json.dumps({"pid": 12345, "stage": "persona_council"}))
    monkeypatch.setattr(inspector, "_is_pid_running", lambda pid: False)

    result = _invoke(runner, ["status", "--results-dir", str(results_dir)])

    assert result.exit_code == 0
    assert "active" not in result.output
    assert "partial" in result.output


def test_resume_uses_discovered_results_dir_and_module_runner(tmp_path, monkeypatch):
    import consortium.cli.commands.resume as resume_cmd

    runner = CliRunner()
    workspace = tmp_path / "workspace"
    results_dir = tmp_path / "results"
    run_dir = results_dir / "consortium_20260409_010203_demo"
    workspace.mkdir()
    run_dir.mkdir(parents=True)
    monkeypatch.chdir(workspace)

    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_completed_process(0)

    monkeypatch.setattr(resume_cmd.subprocess, "run", fake_run)

    result = _invoke(
        runner,
        [
            "--config-dir",
            str(tmp_path / "cfg"),
            "resume",
            run_dir.name,
        ],
    )

    assert result.exit_code == 0
    argv = captured["argv"]
    assert argv[:3] == [sys.executable, "-m", "consortium.runner"]
    assert argv[argv.index("--resume") + 1] == str(run_dir)
