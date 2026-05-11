"""Contract-native artifact runtime helpers for campaign-attached runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .stage_contracts import ArtifactContract, StageContract, get_stage_contract


class StageArtifactError(RuntimeError):
    """Raised when a stage artifact cannot be written or validated."""


@dataclass(frozen=True)
class StageRunContext:
    """Write and validate one stage's canonical campaign artifacts."""

    root: Path
    campaign_id: str
    run_id: str
    stage_id: str
    contract: StageContract

    @property
    def workspace(self) -> Path:
        return self.root / self.workspace_rel

    @property
    def workspace_rel(self) -> Path:
        return Path("results") / self.campaign_id / "runs" / self.run_id / self.stage_id

    @classmethod
    def from_env(cls, stage_id: str) -> "StageRunContext | None":
        campaign_id = os.getenv("MSC_CAMPAIGN_ID")
        run_id = os.getenv("MSC_CAMPAIGN_RUN_ID")
        root = os.getenv("MSC_CAMPAIGN_ROOT") or os.getenv("CONSORTIUM_PROJECT_ROOT")
        if not campaign_id or not run_id or not root:
            return None
        try:
            contract = get_stage_contract(stage_id)
        except KeyError:
            return None
        return cls(
            root=Path(root).resolve(),
            campaign_id=campaign_id,
            run_id=run_id,
            stage_id=stage_id,
            contract=contract,
        )

    def artifact_path(self, artifact_path: str) -> Path:
        rel = Path(artifact_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise StageArtifactError(f"Unsafe artifact path for {self.stage_id}: {artifact_path}")
        target = (self.workspace / rel).resolve()
        workspace = self.workspace.resolve()
        if target != workspace and workspace not in target.parents:
            raise StageArtifactError(f"Artifact path escapes stage workspace: {artifact_path}")
        return target

    def artifact_contract(self, artifact_path: str) -> ArtifactContract:
        for artifact in [*self.contract.required_artifacts, *self.contract.optional_artifacts]:
            if artifact.path == artifact_path:
                return artifact
        raise StageArtifactError(f"{artifact_path} is not declared by stage {self.stage_id}")

    def write_artifact(
        self,
        artifact_path: str,
        content: str | bytes | dict[str, Any] | list[Any],
        *,
        kind: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        contract = self.artifact_contract(artifact_path)
        target = self.artifact_path(artifact_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(content, bytes):
            target.write_bytes(content)
        elif isinstance(content, (dict, list)):
            target.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target.write_text(str(content), encoding="utf-8")

        self._record_artifact(
            artifact_path=artifact_path,
            kind=kind or contract.kind,
            required=contract.required,
            metadata=metadata or {},
        )
        return target

    def write_required(
        self,
        artifact_path: str,
        content: str | bytes | dict[str, Any] | list[Any],
        *,
        kind: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        contract = self.artifact_contract(artifact_path)
        if not contract.required:
            raise StageArtifactError(f"{artifact_path} is not a required artifact for {self.stage_id}")
        return self.write_artifact(artifact_path, content, kind=kind, metadata=metadata)

    def write_optional(
        self,
        artifact_path: str,
        content: str | bytes | dict[str, Any] | list[Any],
        *,
        kind: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        contract = self.artifact_contract(artifact_path)
        if contract.required:
            raise StageArtifactError(f"{artifact_path} is not an optional artifact for {self.stage_id}")
        return self.write_artifact(artifact_path, content, kind=kind, metadata=metadata)

    def validate_required(self) -> list[str]:
        missing: list[str] = []
        for artifact in self.contract.required_artifacts:
            target = self.artifact_path(artifact.path)
            if not target.exists():
                missing.append(artifact.path)
            elif target.is_file() and target.stat().st_size == 0:
                missing.append(f"{artifact.path} is empty")
        return missing

    def _record_artifact(
        self,
        *,
        artifact_path: str,
        kind: str,
        required: bool,
        metadata: dict[str, Any],
    ) -> None:
        try:
            from .campaign_store import CampaignStore

            CampaignStore(self.root).record_artifact(
                self.campaign_id,
                stage_id=self.stage_id,
                artifact_path=artifact_path,
                workspace=str(self.workspace_rel),
                kind=kind,
                required=required,
                producer_node_id=self.stage_id,
                run_id=self.run_id,
                metadata=metadata,
            )
        except Exception as exc:  # pragma: no cover - best-effort runtime bridge
            raise StageArtifactError(f"Failed to record artifact {artifact_path}: {exc}") from exc


def default_lit_review_feasibility(task: str, output: str) -> dict[str, Any]:
    return {
        "decision": "proceed",
        "feasible": True,
        "novelty_risk": "unknown",
        "requires_human_review": True,
        "task": task,
        "rationale": (
            "The literature review stage completed and produced a narrative matrix. "
            "A researcher should review feasibility before expensive downstream work."
        ),
        "evidence_excerpt": output[:2000],
    }


def default_approach_menu(output: str) -> dict[str, Any]:
    return {
        "hypotheses_addressed": ["H1"],
        "approaches": [
            {
                "id": "approach_1",
                "title": "Primary research path",
                "type": "mixed",
                "hypothesis_ids": ["H1"],
                "priority_rank": 1,
                "summary": "Derived from the brainstorm stage output.",
            }
        ],
        "requires_human_review": True,
        "source_excerpt": output[:2000],
    }


def materialize_stage_outputs(stage_id: str, state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Persist the first contract-native slice from existing node outputs."""

    ctx = StageRunContext.from_env(stage_id)
    if ctx is None:
        return result

    merged_outputs = {
        **(state.get("agent_outputs") or {}),
        **(result.get("agent_outputs") or {}),
    }
    stage_output = str(merged_outputs.get(stage_id) or result.get(stage_id) or "")
    task = str(state.get("agent_task") or state.get("task") or "")

    written: dict[str, str] = {}
    if stage_id in {"literature_review_agent", "brainstorm_agent"} and not stage_output.strip():
        raise StageArtifactError(f"{stage_id} produced no text for required artifacts")

    if stage_id == "literature_review_agent":
        ctx.write_required(
            "artifacts/literature_matrix.md",
            stage_output,
            metadata={"run_id": ctx.run_id, "materializer": "agent_output"},
        )
        ctx.write_required(
            "artifacts/lit_review_feasibility.json",
            default_lit_review_feasibility(task, stage_output),
            kind="json",
            metadata={"run_id": ctx.run_id, "materializer": "default_feasibility"},
        )
        written = {
            "literature_matrix": str(ctx.workspace_rel / "artifacts/literature_matrix.md"),
            "lit_review_feasibility": str(ctx.workspace_rel / "artifacts/lit_review_feasibility.json"),
        }
    elif stage_id == "brainstorm_agent":
        if not any(section in stage_output for section in ("Executive Summary", "Per-Hypothesis Approach Menu")):
            stage_output = (
                "# Brainstorm\n\n"
                "## Executive Summary\n\n"
                f"{stage_output}\n\n"
                "## Per-Hypothesis Approach Menu\n\n"
                "- H1: See `approach_1` in the structured approach menu.\n\n"
                "## Recommended Priority Ordering\n\n"
                "1. approach_1\n\n"
                "## Open Questions and Decision Points\n\n"
                "- Researcher review is required before expensive downstream work.\n"
            )
        ctx.write_required(
            "artifacts/brainstorm.md",
            stage_output,
            metadata={"run_id": ctx.run_id, "materializer": "agent_output"},
        )
        ctx.write_required(
            "artifacts/approach_menu.json",
            default_approach_menu(stage_output),
            kind="json",
            metadata={"run_id": ctx.run_id, "materializer": "default_approach_menu"},
        )
        written = {
            "brainstorm": str(ctx.workspace_rel / "artifacts/brainstorm.md"),
            "approach_menu": str(ctx.workspace_rel / "artifacts/approach_menu.json"),
        }

    if written:
        missing = ctx.validate_required()
        if missing:
            raise StageArtifactError(f"{stage_id} missing required artifacts: {', '.join(missing)}")
        return {**result, "artifacts": {**(result.get("artifacts") or {}), **written}}
    return result
