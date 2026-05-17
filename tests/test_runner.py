"""
Tests for consortium/runner.py — utility functions (no real API calls).
"""

import json
import os
import subprocess
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
        assert "partial" in out or "completed" in out


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

    def test_handles_missing_git_context_without_failing(self, tmp_path):
        from consortium.runner import _write_experiment_metadata

        class FakeArgs:
            enable_math_agents = False
            enable_counsel = False
            output_format = "latex"
            enforce_paper_artifacts = False
            min_review_score = 8

        with patch(
            "consortium.runner.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(128, ["git"]),
        ):
            _write_experiment_metadata(
                str(tmp_path),
                FakeArgs(),
                "claude-opus-4-6",
                "Test task",
                project_root=str(tmp_path),
            )

        meta = json.loads((tmp_path / "experiment_metadata.json").read_text())
        assert meta["project_root"] == str(tmp_path)
        assert meta["git_commit"] is None
        assert meta["git_dirty"] is None

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

    def test_records_effective_counsel_configuration(self, tmp_path):
        from consortium.runner import _write_experiment_metadata

        class FakeArgs:
            enable_math_agents = True
            enable_counsel = True
            output_format = "latex"
            enforce_paper_artifacts = True
            min_review_score = 8

        _write_experiment_metadata(
            str(tmp_path),
            FakeArgs(),
            "claude-opus-4-6",
            "Muon iterate replay",
            counsel_settings={
                "enabled": True,
                "effective_model_specs": [
                    {"model": "claude-opus-4-6", "reasoning_effort": "high"},
                    {"model": "gpt-5.4", "reasoning_effort": "high", "verbosity": "high"},
                    {"model": "gemini-3.1-pro-preview", "thinking_budget": 131072},
                    {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
                ],
                "effective_model_names": [
                    "claude-opus-4-6",
                    "gpt-5.4",
                    "gemini-3.1-pro-preview",
                    "claude-sonnet-4-6",
                ],
                "max_debate_rounds": 5,
                "synthesis_model": "claude-opus-4-6",
            },
            persona_settings={
                "debate_rounds": 5,
                "synthesis_model": "claude-opus-4-6",
                "max_post_vote_retries": 1,
            },
        )

        with open(tmp_path / "experiment_metadata.json") as f:
            meta = json.load(f)

        assert meta["counsel"]["enabled"] is True
        assert meta["counsel"]["max_debate_rounds"] == 5
        assert meta["counsel"]["model_names"] == [
            "claude-opus-4-6",
            "gpt-5.4",
            "gemini-3.1-pro-preview",
            "claude-sonnet-4-6",
        ]
        assert meta["counsel"]["synthesis_model"] == "claude-opus-4-6"
        assert meta["persona_council"]["debate_rounds"] == 5
        assert meta["persona_council"]["synthesis_model"] == "claude-opus-4-6"

    def test_records_effective_models_and_credential_sources(self, tmp_path):
        from consortium.runner import _write_experiment_metadata

        class FakeArgs:
            enable_math_agents = True
            enable_counsel = False
            output_format = "markdown"
            enforce_paper_artifacts = False
            min_review_score = 8

        _write_experiment_metadata(
            str(tmp_path),
            FakeArgs(),
            "gpt-5-mini",
            "Budget task",
            effective_models={"main_model": "gpt-5-mini"},
            model_provenance={"main_model": "tier"},
            credential_sources={"OPENROUTER_API_KEY": "config-dir"},
            log_paths={"stdout": "/tmp/stdout.log", "stderr": "/tmp/stderr.log"},
        )

        meta = json.loads((tmp_path / "experiment_metadata.json").read_text())
        assert meta["effective_models"]["main_model"] == "gpt-5-mini"
        assert meta["model_provenance"]["main_model"] == "tier"
        assert meta["credential_sources"]["OPENROUTER_API_KEY"] == "config-dir"
        assert meta["log_files"]["stdout"] == "/tmp/stdout.log"


class TestLangGraphRecursionLimit:
    def test_derives_limit_from_pipeline_size(self):
        from consortium.runner import _resolve_langgraph_recursion_limit

        class FakeArgs:
            recursion_limit = None
            followup_max_iterations = 1
            max_rebuttal_iterations = 0
            max_validation_retries = 1

        limit = _resolve_langgraph_recursion_limit(FakeArgs(), [f"stage_{idx}" for idx in range(34)])

        assert limit >= 136

    def test_explicit_limit_wins(self):
        from consortium.runner import _resolve_langgraph_recursion_limit

        class FakeArgs:
            recursion_limit = 42
            followup_max_iterations = 99
            max_rebuttal_iterations = 99
            max_validation_retries = 99

        assert _resolve_langgraph_recursion_limit(FakeArgs(), ["a", "b"]) == 42


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
        assert summary["status"] == "completed"
        assert summary["completed"] is True

    def test_reads_budget_state_for_cost(self, tmp_path):
        from consortium.runner import _write_run_summary
        (tmp_path / "budget_state.json").write_text(json.dumps({"total_usd": 12.34}))
        _write_run_summary(str(tmp_path), "task", "gpt-5", datetime.now(), [])
        with open(tmp_path / "run_summary.json") as f:
            summary = json.load(f)
        assert abs(summary["total_cost_usd"] - 12.34) < 1e-6

    def test_prefers_paper_workspace_final_paper(self, tmp_path):
        from consortium.runner import _write_run_summary

        pw = tmp_path / "paper_workspace"
        pw.mkdir()
        (pw / "final_paper.pdf").write_bytes(b"%PDF-1.4\nstub")

        _write_run_summary(
            workspace_dir=str(tmp_path),
            task="Muon task",
            model_name="claude-opus-4-6",
            start_time=datetime.now(),
            stages_completed=["writeup_agent", "proofreading_agent", "reviewer_agent"],
        )

        with open(tmp_path / "run_summary.json") as f:
            summary = json.load(f)

        assert summary["final_paper"] == "paper_workspace/final_paper.pdf"
        assert summary["stages_completed"] == [
            "writeup_agent",
            "proofreading_agent",
            "reviewer_agent",
        ]

    def test_records_failed_status_and_reason(self, tmp_path):
        from consortium.runner import _write_run_summary

        _write_run_summary(
            workspace_dir=str(tmp_path),
            task="Failed task",
            model_name="gpt-5-mini",
            start_time=datetime.now(),
            stages_completed=["persona_council"],
            status="failed",
            status_reason="boom",
            current_stage="persona_council",
        )

        summary = json.loads((tmp_path / "run_summary.json").read_text())
        assert summary["status"] == "failed"
        assert summary["failed"] is True
        assert summary["status_reason"] == "boom"
        assert summary["current_stage"] == "persona_council"
