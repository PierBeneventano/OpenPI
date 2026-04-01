"""
Tests for consortium/runner.py — utility functions (no real API calls).
"""

import json
import os
import pytest
from datetime import datetime
from unittest.mock import patch


class TestValidateApiKeys:
    def test_no_error_when_openrouter_key_set(self, monkeypatch):
        from consortium.runner import _validate_api_keys
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        errors = _validate_api_keys("claude-opus-4-6")
        assert errors == []

    def test_error_when_openrouter_key_missing(self, monkeypatch):
        from consortium.runner import _validate_api_keys
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        errors = _validate_api_keys("claude-opus-4-6")
        assert len(errors) == 1
        assert "OPENROUTER_API_KEY" in errors[0]

    def test_no_error_for_gpt_when_openrouter_set(self, monkeypatch):
        from consortium.runner import _validate_api_keys
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        errors = _validate_api_keys("gpt-5")
        assert errors == []

    def test_error_for_gpt_when_openrouter_missing(self, monkeypatch):
        from consortium.runner import _validate_api_keys
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        errors = _validate_api_keys("gpt-5")
        assert len(errors) == 1
        assert "OPENROUTER_API_KEY" in errors[0]

    def test_gemini_uses_openrouter(self, monkeypatch):
        from consortium.runner import _validate_api_keys
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        errors = _validate_api_keys("gemini-2.5-pro")
        assert len(errors) == 1
        assert "OPENROUTER_API_KEY" in errors[0]


class TestListRuns:
    def test_no_results_dir(self, tmp_path, monkeypatch, capsys):
        from consortium.runner import _list_runs
        monkeypatch.chdir(tmp_path)
        _list_runs(str(tmp_path / "nonexistent"))
        out = capsys.readouterr().out
        assert "No results" in out

    def test_empty_results_dir(self, tmp_path, capsys):
        from consortium.runner import _list_runs
        (tmp_path / "results").mkdir()
        _list_runs(str(tmp_path / "results"))
        out = capsys.readouterr().out
        assert "No past runs" in out

    def test_lists_workspace_with_budget_state(self, tmp_path, capsys):
        from consortium.runner import _list_runs
        ws = tmp_path / "consortium_20260101_120000"
        ws.mkdir()
        (ws / "budget_state.json").write_text(json.dumps({"total_usd": 5.42}))
        (ws / "STATUS.txt").write_text("COMPLETE")
        summary = {"task": "Test research task about neural networks"}
        (ws / "run_summary.json").write_text(json.dumps(summary))
        _list_runs(str(tmp_path))
        out = capsys.readouterr().out
        assert "consortium_20260101_120000" in out
        assert "$5.42" in out
        assert "COMPLETE" in out


class TestWriteExperimentMetadata:
    def test_writes_metadata_json(self, tmp_path):
        from consortium.runner import _write_experiment_metadata

        class FakeArgs:
            enable_math_agents = False
            enable_counsel = False
            output_format = "latex"
            enforce_paper_artifacts = False
            min_review_score = 8

        _write_experiment_metadata(str(tmp_path), FakeArgs(), "claude-opus-4-6", "Test task")
        meta_path = tmp_path / "experiment_metadata.json"
        assert meta_path.exists()
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["model"] == "claude-opus-4-6"
        assert "git_commit" in meta
        assert "python_version" in meta
        assert meta["cli_args"]["enable_math_agents"] is False

    def test_task_preview_truncated(self, tmp_path):
        from consortium.runner import _write_experiment_metadata

        class FakeArgs:
            enable_math_agents = False
            enable_counsel = False
            output_format = "markdown"
            enforce_paper_artifacts = False
            min_review_score = 8

        long_task = "x" * 500
        _write_experiment_metadata(str(tmp_path), FakeArgs(), "gpt-5", long_task)
        with open(tmp_path / "experiment_metadata.json") as f:
            meta = json.load(f)
        assert len(meta["task_preview"]) <= 200


class TestWriteRunSummary:
    def test_writes_summary_json(self, tmp_path):
        from consortium.runner import _write_run_summary

        start = datetime(2026, 1, 1, 12, 0, 0)
        _write_run_summary(
            workspace_dir=str(tmp_path),
            task="Test task",
            model_name="claude-opus-4-6",
            start_time=start,
            stages_completed=["ideation_agent", "literature_review_agent"],
        )
        summary_path = tmp_path / "run_summary.json"
        assert summary_path.exists()
        with open(summary_path) as f:
            summary = json.load(f)
        assert summary["task"] == "Test task"
        assert summary["model"] == "claude-opus-4-6"
        assert "ideation_agent" in summary["stages_completed"]
        assert summary["duration_seconds"] >= 0

    def test_reads_budget_state_for_cost(self, tmp_path):
        from consortium.runner import _write_run_summary
        (tmp_path / "budget_state.json").write_text(json.dumps({"total_usd": 12.34}))
        _write_run_summary(str(tmp_path), "task", "gpt-5", datetime.now(), [])
        with open(tmp_path / "run_summary.json") as f:
            summary = json.load(f)
        assert abs(summary["total_cost_usd"] - 12.34) < 1e-6
