from __future__ import annotations

import json
from pathlib import Path

import yaml

from msc_sdk.manifest import import_manifest, read_manifest, write_manifest


def _make_run(root: Path, name: str) -> Path:
    run = root / name
    paper = run / "paper_workspace"
    paper.mkdir(parents=True)
    (run / "run_status.json").write_text(json.dumps({"status": "completed"}))
    (run / "run_summary.json").write_text(
        json.dumps({"task": name, "final_paper": "paper_workspace/final_paper.md"})
    )
    (run / "effective_models.json").write_text(json.dumps({"main_model": "gpt-5-mini"}))
    (paper / "final_paper.md").write_text("# Smoke")
    return run


def test_import_manifest_from_single_run(tmp_path: Path):
    run = _make_run(tmp_path, "consortium_20260510_000001")

    manifest = import_manifest(run)

    assert manifest.source_kind == "run"
    assert manifest.manifest_version == "0.1"
    assert manifest.runs[0]["run_id"] == "consortium_20260510_000001"
    assert manifest.artifact_count >= 4
    assert manifest.warnings == []


def test_import_manifest_from_results_dir(tmp_path: Path):
    results = tmp_path / "results"
    _make_run(results, "older")
    _make_run(results, "newer")

    manifest = import_manifest(results)

    assert manifest.source_kind == "results_dir"
    assert {run["run_id"] for run in manifest.runs} == {"older", "newer"}
    assert manifest.campaigns == []


def test_import_manifest_from_campaign_yaml(tmp_path: Path):
    campaign = tmp_path / "campaign_demo.yaml"
    stage_workspace = tmp_path / "results" / "demo" / "stage_a"
    stage_workspace.mkdir(parents=True)
    (stage_workspace / "artifact.md").write_text("done")
    campaign.write_text(
        yaml.safe_dump(
            {
                "name": "Demo",
                "workspace_root": "results/demo",
                "stages": [
                    {
                        "id": "stage_a",
                        "success_artifacts": {
                            "required": ["artifact.md"],
                            "optional": ["optional.json"],
                        },
                    }
                ],
            }
        )
    )

    manifest = import_manifest(campaign)

    assert manifest.source_kind == "campaign"
    assert manifest.campaigns[0]["name"] == "Demo"
    assert manifest.campaigns[0]["stages"][0]["stage_id"] == "stage_a"
    assert manifest.artifact_count == 2


def test_write_manifest_round_trip(tmp_path: Path):
    run = _make_run(tmp_path, "consortium_20260510_000001")
    out_dir = tmp_path / ".msc_index"

    manifest_path = write_manifest(run, out_dir)
    payload = read_manifest(manifest_path)

    assert manifest_path.parent == out_dir
    assert payload["source_kind"] == "run"
    assert payload["runs"][0]["run_id"] == "consortium_20260510_000001"
