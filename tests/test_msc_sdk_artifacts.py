from __future__ import annotations

import json
from pathlib import Path

import yaml

from msc_sdk.artifacts import inspect_campaign, inspect_run_workspace, list_run_workspaces


def test_inspect_run_workspace_reads_core_artifacts(tmp_path: Path):
    run = tmp_path / "results" / "consortium_20260510_123456"
    paper = run / "paper_workspace"
    logs = run / "logs"
    paper.mkdir(parents=True)
    logs.mkdir()
    (run / "run_status.json").write_text(
        json.dumps({"status": "completed", "current_stage": "writeup_agent"})
    )
    (run / "experiment_metadata.json").write_text(
        json.dumps({"model": "gpt-5-mini", "task_preview": "A task"})
    )
    (run / "run_summary.json").write_text(
        json.dumps({"task": "A task", "final_paper": "paper_workspace/final_paper.md"})
    )
    (run / "budget_state.json").write_text(json.dumps({"total_usd": 1.25, "budget_usd": 5}))
    (run / "budget_ledger.jsonl").write_text(
        json.dumps({"model_id": "openrouter/openai/gpt-5-mini", "cost_usd": 0.25}) + "\n"
    )
    (paper / "final_paper.md").write_text("# Paper\n\nBody")
    (logs / "consortium.out").write_text("INFO started\n")

    model = inspect_run_workspace(run)

    assert model.run_id == "consortium_20260510_123456"
    assert model.status == "completed"
    assert model.current_stage == "writeup_agent"
    assert model.task == "A task"
    assert model.model == "gpt-5-mini"
    assert model.final_paper == "paper_workspace/final_paper.md"
    assert model.budget.total_usd == 1.25
    assert model.budget.by_model == {"openrouter/openai/gpt-5-mini": 0.25}
    assert any(a.path == "paper_workspace/final_paper.md" for a in model.artifacts)
    assert any(log.path == "logs/consortium.out" for log in model.logs)
    assert model.to_dict()["budget"]["total_usd"] == 1.25


def test_list_run_workspaces_sorts_newest_first(tmp_path: Path):
    results = tmp_path / "results"
    old = results / "old"
    new = results / "new"
    old.mkdir(parents=True)
    new.mkdir()
    (old / "run_summary.json").write_text("{}")
    (new / "run_summary.json").write_text("{}")

    runs = list_run_workspaces(results)

    assert [run.run_id for run in runs] == ["new", "old"]


def test_inspect_campaign_preserves_required_artifact_contract(tmp_path: Path):
    campaign = tmp_path / "campaign_demo.yaml"
    workspace = tmp_path / "results" / "demo" / "stage_a"
    workspace.mkdir(parents=True)
    (workspace / "paper_workspace").mkdir()
    (workspace / "paper_workspace" / "final_paper.md").write_text("# Done")
    (tmp_path / "results" / "demo" / "campaign_state.json").write_text(
        json.dumps(
            {
                "campaign_name": "Demo",
                "status": "running",
                "stages": {
                    "stage_a": {
                        "status": "completed",
                        "workspace": str(workspace),
                        "attempt_id": "attempt-1",
                    }
                },
            }
        )
    )
    campaign.write_text(
        yaml.safe_dump(
            {
                "name": "Demo",
                "workspace_root": "results/demo",
                "budget_usd": 12,
                "stages": [
                    {
                        "id": "stage_a",
                        "success_artifacts": {
                            "required": ["paper_workspace/final_paper.md"],
                            "optional": ["paper_workspace/review_verdict.json"],
                        },
                    }
                ],
            }
        )
    )

    model = inspect_campaign(campaign)

    assert model.name == "Demo"
    assert model.status == "running"
    assert model.budget.limit_usd == 12
    assert len(model.stages) == 1
    stage = model.stages[0]
    assert stage.stage_id == "stage_a"
    assert stage.status == "completed"
    assert stage.current_attempt == "attempt-1"
    assert stage.required_artifacts[0].path == "paper_workspace/final_paper.md"
    assert stage.required_artifacts[0].exists is True
    assert stage.optional_artifacts[0].exists is False
    assert model.to_dict()["stages"][0]["required_artifacts"][0]["required"] is True
