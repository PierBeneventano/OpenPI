"""Read-only artifact and workspace inspection helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .budget import read_budget_workspace
from .read_models import (
    ArtifactReadModel,
    CampaignReadModel,
    LogReadModel,
    RunReadModel,
    StageReadModel,
)

FINAL_PAPER_CANDIDATES = (
    "paper_workspace/final_paper.pdf",
    "paper_workspace/final_paper.tex",
    "paper_workspace/final_paper.md",
    "final_paper.pdf",
    "final_paper.tex",
    "final_paper.md",
)

LOG_SUFFIXES = {".log", ".out", ".txt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _iso_mtime(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _kind(path: Path) -> str:
    if path.is_dir():
        return "directory"
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".md":
        return "markdown"
    if suffix == ".tex":
        return "tex"
    if suffix == ".pdf":
        return "pdf"
    if suffix in LOG_SUFFIXES:
        return "log"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix in {".csv", ".tsv"}:
        return "table"
    if suffix in {".py", ".sh", ".ipynb"}:
        return "code"
    return "file"


def _source_role(path: Path) -> str:
    name = path.name
    if name in {
        "experiment_metadata.json",
        "run_summary.json",
        "budget_state.json",
        "budget_ledger.jsonl",
        "run_status.json",
        "STATUS.txt",
        "effective_models.json",
    }:
        return "source"
    if "paper_workspace" in path.parts:
        return "source"
    if name.endswith(".manifest.json") or ".msc_index" in path.parts:
        return "derived"
    return "raw"


def _artifact_for(root: Path, path: Path, *, required: bool = False) -> ArtifactReadModel:
    exists = path.exists()
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return ArtifactReadModel(
        id=rel.as_posix(),
        path=rel.as_posix(),
        kind=_kind(path),
        exists=exists,
        size_bytes=_size(path) if exists and path.is_file() else None,
        modified_at=_iso_mtime(path) if exists else None,
        source_role=_source_role(path),
        required=required,
    )


def _discover_artifacts(root: Path) -> list[ArtifactReadModel]:
    if not root.is_dir():
        return []
    artifacts: list[ArtifactReadModel] = []
    for path in sorted(root.rglob("*")):
        if path.name.startswith(".") and path.name != ".progress_heartbeat":
            continue
        if any(part in {"__pycache__", ".git", ".venv", "node_modules"} for part in path.parts):
            continue
        artifacts.append(_artifact_for(root, path))
    return artifacts


def _discover_logs(root: Path) -> list[LogReadModel]:
    if not root.is_dir():
        return []
    logs: list[LogReadModel] = []
    candidates = []
    for logs_dir in (root / "logs", root):
        if logs_dir.is_dir():
            candidates.extend(
                path for path in logs_dir.iterdir()
                if path.is_file() and path.suffix.lower() in LOG_SUFFIXES
            )
    metadata = _read_json(root / "experiment_metadata.json")
    for value in (metadata.get("log_files") or {}).values():
        if isinstance(value, str):
            path = Path(value)
            if path.is_file():
                candidates.append(path)
    seen: set[str] = set()
    for path in sorted(candidates):
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        logs.append(
            LogReadModel(
                path=rel,
                size_bytes=_size(path) or 0,
                modified_at=_iso_mtime(path),
            )
        )
    return logs


def _find_final_paper(root: Path, summary: dict[str, Any]) -> str | None:
    final_paper = summary.get("final_paper")
    if isinstance(final_paper, str) and (root / final_paper).exists():
        return final_paper
    for candidate in FINAL_PAPER_CANDIDATES:
        if (root / candidate).exists():
            return candidate
    return None


def inspect_run_workspace(path: str | Path) -> RunReadModel:
    """Inspect a run workspace without mutating it."""
    root = Path(path).resolve()
    status = _read_json(root / "run_status.json")
    summary = _read_json(root / "run_summary.json")
    metadata = _read_json(root / "experiment_metadata.json")

    status_value = str(
        status.get("status")
        or summary.get("status")
        or ("completed" if _find_final_paper(root, summary) else "unknown")
    ).lower()

    return RunReadModel(
        run_id=root.name,
        workspace=str(root),
        status=status_value,
        current_stage=status.get("current_stage") or summary.get("current_stage"),
        task=summary.get("task") or metadata.get("task") or metadata.get("task_preview"),
        model=summary.get("model") or metadata.get("model"),
        final_paper=_find_final_paper(root, summary),
        started_at=status.get("started_at") or summary.get("started_at"),
        updated_at=status.get("updated_at") or status.get("last_activity_at"),
        budget=read_budget_workspace(root, summary),
        artifacts=_discover_artifacts(root),
        logs=_discover_logs(root),
        metadata={
            "summary_present": bool(summary),
            "metadata_present": bool(metadata),
            "status_present": bool(status),
        },
        provenance={
            "reader": "msc_sdk.artifacts.inspect_run_workspace",
            "source": str(root),
        },
    )


def list_run_workspaces(results_dir: str | Path, *, limit: int | None = None) -> list[RunReadModel]:
    """List run workspaces in a results directory, newest first."""
    root = Path(results_dir).resolve()
    if not root.is_dir():
        return []
    run_dirs = sorted(
        [path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if limit is not None:
        run_dirs = run_dirs[:limit]
    return [inspect_run_workspace(path) for path in run_dirs]


def _stage_workspace(spec: dict[str, Any], state: dict[str, Any], campaign_root: Path) -> str | None:
    for key in ("workspace", "stage_workspace", "workspace_dir"):
        value = state.get(key, spec.get(key))
        if isinstance(value, str) and value:
            return value
    stage_id = spec.get("id") or spec.get("name")
    workspace_root = spec.get("workspace_root")
    if isinstance(workspace_root, str):
        return workspace_root
    if stage_id:
        candidate = campaign_root / str(stage_id)
        if candidate.exists():
            return str(candidate)
    return None


def _artifact_contracts(
    campaign_root: Path,
    workspace: str | None,
    paths: list[str],
    *,
    required: bool,
) -> list[ArtifactReadModel]:
    if not paths:
        return []
    workspace_root = Path(workspace).resolve() if workspace else campaign_root
    artifacts: list[ArtifactReadModel] = []
    for rel in paths:
        target = workspace_root / rel
        artifacts.append(_artifact_for(workspace_root, target, required=required))
    return artifacts


def inspect_campaign(path: str | Path) -> CampaignReadModel:
    """Inspect a campaign YAML and nearby observed state without mutation."""
    campaign_path = Path(path).resolve()
    campaign_root = campaign_path.parent
    spec = _read_yaml(campaign_path)
    workspace_root_raw = spec.get("workspace_root")
    workspace_root = str((campaign_root / workspace_root_raw).resolve()) if isinstance(workspace_root_raw, str) and not Path(workspace_root_raw).is_absolute() else workspace_root_raw
    observed_root = Path(workspace_root).resolve() if isinstance(workspace_root, str) else campaign_root

    state = _read_json(observed_root / "campaign_state.json")
    status = _read_json(observed_root / "campaign_status.json")
    stages_state = state.get("stages") or status.get("stages") or {}

    stage_models: list[StageReadModel] = []
    for raw_stage in spec.get("stages") or []:
        if not isinstance(raw_stage, dict):
            continue
        stage_id = str(raw_stage.get("id") or raw_stage.get("name") or "stage")
        observed = stages_state.get(stage_id, {}) if isinstance(stages_state, dict) else {}
        if not isinstance(observed, dict):
            observed = {"status": str(observed)}
        workspace = _stage_workspace(raw_stage, observed, observed_root)
        success = raw_stage.get("success_artifacts") or {}
        required = success.get("required") or raw_stage.get("required_artifacts") or []
        optional = success.get("optional") or raw_stage.get("optional_artifacts") or []
        stage_root = Path(workspace).resolve() if workspace else observed_root
        stage_models.append(
            StageReadModel(
                stage_id=stage_id,
                status=str(observed.get("status") or raw_stage.get("status") or "unknown"),
                workspace=workspace,
                current_attempt=observed.get("attempt_id") or observed.get("current_attempt"),
                started_at=observed.get("started_at"),
                completed_at=observed.get("completed_at"),
                fail_reason=observed.get("fail_reason") or observed.get("status_reason"),
                required_artifacts=_artifact_contracts(
                    observed_root, workspace, [str(item) for item in required], required=True
                ),
                optional_artifacts=_artifact_contracts(
                    observed_root, workspace, [str(item) for item in optional], required=False
                ),
                budget=read_budget_workspace(stage_root),
                logs=_discover_logs(stage_root),
                metadata={
                    "launcher": raw_stage.get("launcher"),
                    "validators": raw_stage.get("validators") or success.get("validators") or {},
                },
            )
        )

    budget_state = _read_json(observed_root / "budget_state.json")
    budget_summary = {
        "total_usd": status.get("total_cost_usd") or state.get("total_cost_usd"),
        "campaign_limit_usd": spec.get("budget_usd"),
    }
    if budget_state:
        budget_summary.update(budget_state)
    stage_budget_totals = {
        stage.stage_id: stage.budget.total_usd
        for stage in stage_models
        if stage.budget.total_usd is not None
    }
    observed_stage_total = round(sum(stage_budget_totals.values()), 6) if stage_budget_totals else None

    return CampaignReadModel(
        campaign_id=campaign_path.stem,
        path=str(campaign_path),
        name=spec.get("name") or state.get("campaign_name") or status.get("campaign_name"),
        workspace_root=str(workspace_root) if workspace_root else None,
        status=str(status.get("status") or state.get("status") or "unknown"),
        budget=read_budget_workspace(
            observed_root,
            budget_summary,
            metadata={
                "stage_budget_totals": stage_budget_totals,
                "observed_stage_total_usd": observed_stage_total,
            },
        ),
        stages=stage_models,
        metadata={
            "planning_enabled": bool((spec.get("planning") or {}).get("enabled")),
            "stage_count": len(stage_models),
        },
        provenance={
            "reader": "msc_sdk.artifacts.inspect_campaign",
            "source": str(campaign_path),
        },
    )
