"""Production artifact manifest and compatibility importer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .artifacts import inspect_campaign, inspect_run_workspace, list_run_workspaces
from .read_models import CampaignReadModel, RunReadModel

MANIFEST_VERSION = "0.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"msc_{digest}"


@dataclass(frozen=True)
class ManifestImportResult:
    """Result of importing raw MSc artifacts into a derived manifest."""

    manifest_version: str
    manifest_id: str
    generated_at: str
    source_path: str
    source_kind: Literal["run", "results_dir", "campaign", "unknown"]
    runs: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    artifact_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_to_manifest(run: RunReadModel) -> dict[str, Any]:
    data = run.to_dict()
    data["manifest_role"] = "run"
    data["artifact_ids"] = [artifact.id for artifact in run.artifacts]
    data["log_paths"] = [log.path for log in run.logs]
    return data


def _campaign_to_manifest(campaign: CampaignReadModel) -> dict[str, Any]:
    data = campaign.to_dict()
    data["manifest_role"] = "campaign"
    data["stage_ids"] = [stage.stage_id for stage in campaign.stages]
    return data


def import_manifest(path: str | Path) -> ManifestImportResult:
    """Import a run, results directory, or campaign YAML into a derived manifest."""
    source = Path(path).resolve()
    warnings: list[str] = []
    runs: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    source_kind: Literal["run", "results_dir", "campaign", "unknown"] = "unknown"

    if source.is_file() and source.suffix.lower() in {".yaml", ".yml"}:
        source_kind = "campaign"
        campaign = inspect_campaign(source)
        campaigns.append(_campaign_to_manifest(campaign))
    elif source.is_dir() and source.name == "results":
        source_kind = "results_dir"
        run_models = list_run_workspaces(source)
        runs.extend(_run_to_manifest(run) for run in run_models)
        if not run_models:
            warnings.append("No run workspaces found in results directory.")
    elif source.is_dir():
        if any((source / name).exists() for name in ("run_status.json", "run_summary.json", "experiment_metadata.json")):
            source_kind = "run"
            runs.append(_run_to_manifest(inspect_run_workspace(source)))
        elif (source / "campaign_state.json").exists() or (source / "campaign_status.json").exists():
            source_kind = "results_dir"
            run_models = list_run_workspaces(source)
            runs.extend(_run_to_manifest(run) for run in run_models)
            warnings.append("Directory looks campaign-like but no campaign YAML was provided.")
        else:
            warnings.append("Source path is a directory but no known MSc artifacts were detected.")
    else:
        warnings.append("Source path does not exist.")

    artifact_count = sum(len(run.get("artifacts", [])) for run in runs)
    artifact_count += sum(
        len(stage.get("required_artifacts", [])) + len(stage.get("optional_artifacts", []))
        for campaign in campaigns
        for stage in campaign.get("stages", [])
    )

    return ManifestImportResult(
        manifest_version=MANIFEST_VERSION,
        manifest_id=_stable_id(str(source), source_kind),
        generated_at=_now(),
        source_path=str(source),
        source_kind=source_kind,
        runs=runs,
        campaigns=campaigns,
        artifact_count=artifact_count,
        warnings=warnings,
    )


def write_manifest(path: str | Path, out_dir: str | Path = ".msc_index") -> Path:
    """Write a derived manifest for *path* and return the manifest path."""
    manifest = import_manifest(path)
    out_root = Path(out_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    target = out_root / f"{manifest.manifest_id}.manifest.json"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n")
    tmp.replace(target)
    return target


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Read a previously written derived manifest."""
    manifest_path = Path(path)
    return json.loads(manifest_path.read_text())
