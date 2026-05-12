"""Tests for the Stage 8 optional OpenClaw safety surface."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from consortium.cli.main import cli
from msc_sdk.openclaw import OPENCLAW_PROFILES, openclaw_launch_plan, read_openclaw_config


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["--no-banner", *args], catch_exceptions=False)


def test_openclaw_profiles_default_to_read_only():
    read_only = OPENCLAW_PROFILES["read_only"]

    assert read_only["default"] is True
    assert read_only["mutations_allowed"] is False
    assert "mutate.launch" not in read_only["capabilities"]


def test_openclaw_config_redacts_tokens(tmp_path: Path, monkeypatch):
    config = tmp_path / "openclaw.json"
    config.write_text(
        json.dumps(
            {
                "gateway": {"port": 18789, "auth": {"token": "secret-token"}},
                "channels": {"telegram": {"enabled": True, "bot_token": "secret-bot"}},
            }
        )
    )
    monkeypatch.setenv("OPENCLAW_CONFIG", str(config))

    data = read_openclaw_config()

    assert data["gateway"]["auth"]["token"] == "[REDACTED]"
    assert data["channels"]["telegram"]["bot_token"] == "[REDACTED]"


def test_openclaw_readiness_cli_json_uses_env_config(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps({"gateway": {"port": 18789}}))
    monkeypatch.setenv("OPENCLAW_CONFIG", str(config))

    result = _invoke(runner, ["openclaw", "readiness", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["configured"] is True
    assert data["default_profile"] == "read_only"


def test_openclaw_launch_plan_is_dry_run(tmp_path: Path):
    script = tmp_path / "launch.sh"
    script.write_text("#!/usr/bin/env bash\n")

    plan = openclaw_launch_plan(script)

    assert plan["ok"] is True
    assert plan["executes"] is False
    assert plan["command"] == ["bash", str(script)]
    assert plan["confirmation_required_for_mutations"] is True
    assert plan["operation_contract"]["profile"] == "openclaw_read_only"
    assert all(not operation["mutates"] for operation in plan["operation_contract"]["operations"])


def test_openclaw_status_json_redacts_config(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps({"gateway": {"port": 9, "auth": {"token": "secret-token"}}}))
    monkeypatch.setenv("OPENCLAW_CONFIG", str(config))

    result = _invoke(runner, ["openclaw", "status", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["configured"] is True
    assert data["running"] is False
    assert data["config"]["gateway"]["auth"]["token"] == "[REDACTED]"
