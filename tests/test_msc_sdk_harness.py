"""Tests for the Stage 4 orchestrator harness scaffolding."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from consortium.cli.main import cli
from msc_sdk.capabilities import CapabilityClient
from msc_sdk.events import EventStore
from msc_sdk.harness import OrchestratorHarness


def _invoke(runner: CliRunner, args: list[str]):
    return runner.invoke(cli, ["--no-banner", *args], catch_exceptions=False)


def _make_run(root: Path) -> Path:
    run_dir = root / "results" / "consortium_20260510_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "run_status.json").write_text(json.dumps({"status": "completed"}))
    (run_dir / "run_summary.json").write_text(json.dumps({"task": "Harness fixture"}))
    return run_dir


def test_capability_client_defaults_are_read_first():
    current = CapabilityClient().current()
    denied = CapabilityClient().check("mutate.launch")

    assert "read.project" in current["capabilities"]
    assert "write.index" in current["capabilities"]
    assert current["mutations_allowed"] is False
    assert denied.ok is False


def test_event_store_appends_and_redacts_secrets(tmp_path: Path):
    store = EventStore(tmp_path / ".msc")

    event = store.append(
        kind="observation",
        summary="Checked setup.",
        details={"OPENROUTER_API_KEY": "fake-secret-value", "nested": {"token": "abc"}},
    )

    loaded = store.show(event.id)
    assert loaded is not None
    assert loaded.details["OPENROUTER_API_KEY"] == "[REDACTED]"
    assert loaded.details["nested"]["token"] == "[REDACTED]"
    assert store.list()[0].id == event.id


def test_harness_refresh_manifest_and_request_action_are_audited(tmp_path: Path):
    run_dir = _make_run(tmp_path)
    harness = OrchestratorHarness(root=tmp_path, state_dir=tmp_path / ".msc", index_dir=tmp_path / ".msc_index")

    refreshed = harness.refresh_manifest(run_dir, actor="codex")
    denied = harness.request_action(
        operation="launch_run",
        target="demo",
        capability="mutate.launch",
        actor="codex",
    )

    assert refreshed["ok"] is True
    assert Path(refreshed["manifest_path"]).exists()
    assert denied["ok"] is False
    assert denied["error_category"] == "capability_denied"
    assert [event["kind"] for event in harness.list_events()["events"]] == [
        "artifact_indexed",
        "capability_denied",
    ]


def test_harness_confirmation_scaffold_defers_execution(tmp_path: Path):
    harness = OrchestratorHarness(
        root=tmp_path,
        state_dir=tmp_path / ".msc",
        profile="openclaude_v1",
    )

    request_result = harness.request_action(
        operation="append_feedback",
        target="run:demo",
        capability="write.feedback",
        actor="user",
        details={"feedback": "Try a narrower theorem."},
    )
    request = request_result["request"]
    denied = harness.execute_action(request["id"], "wrong-token")
    deferred = harness.execute_action(request["id"], request["confirmation_token"])

    assert request_result["ok"] is True
    assert denied["error_category"] == "confirmation_required"
    assert deferred["error_category"] == "unavailable"
    assert "does not execute mutations yet" in deferred["message"]


def test_stage4_cli_surfaces_emit_json(tmp_path: Path):
    runner = CliRunner()
    run_dir = _make_run(tmp_path)

    caps = _invoke(runner, ["capabilities", "current", "--json"])
    refresh = _invoke(
        runner,
        [
            "harness",
            "--root",
            str(tmp_path),
            "--state-dir",
            str(tmp_path / ".msc"),
            "--index-dir",
            str(tmp_path / ".msc_index"),
            "refresh-manifest",
            str(run_dir),
            "--json",
        ],
    )
    events = _invoke(
        runner,
        ["events", "--state-dir", str(tmp_path / ".msc"), "list", "--json"],
    )

    assert caps.exit_code == 0
    assert "read.project" in json.loads(caps.output)["capabilities"]
    assert refresh.exit_code == 0
    assert json.loads(refresh.output)["ok"] is True
    assert events.exit_code == 0
    assert json.loads(events.output)["events"][0]["kind"] == "artifact_indexed"
