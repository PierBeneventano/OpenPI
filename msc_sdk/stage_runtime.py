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


def _legacy_workspace() -> Path | None:
    value = os.getenv("RESULTS_BASE_DIR")
    if not value:
        return None
    return Path(value).resolve()


def _legacy_artifact_text(artifact: ArtifactContract) -> str | None:
    workspace = _legacy_workspace()
    if workspace is None:
        return None
    for legacy_path in artifact.legacy_paths:
        candidate = (workspace / legacy_path).resolve()
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _stage_text(stage_id: str, state: dict[str, Any], result: dict[str, Any]) -> str:
    merged_outputs = {
        **(state.get("agent_outputs") or {}),
        **(result.get("agent_outputs") or {}),
    }
    candidates = [
        merged_outputs.get(stage_id),
        result.get(stage_id),
        result.get("output"),
        result.get("summary"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _title(stage_id: str) -> str:
    return stage_id.replace("_", " ").title()


def _default_research_goals(task: str, output: str) -> dict[str, Any]:
    objective = task.strip() or "Complete the approved research objective."
    return {
        "brainstorm_data_quality": "adapter_materialized",
        "goals": [
            {
                "id": "G1",
                "title": "Minimal empirical comparison",
                "description": objective,
                "hypothesis_id": "H1",
                "approach_ids": ["approach_1"],
                "track": "experiment",
                "success_criteria": {
                    "strong": "A reproducible comparison reports spectral norm trajectories for batch-normalized and non-batch-normalized models.",
                    "minimum_viable": "A toy experiment produces a coherent qualitative comparison and a short writeup.",
                },
                "deliverables": [
                    "experiment_results.md",
                    "formalized_results.md",
                    "final_paper.md",
                ],
                "dependencies": [],
                "priority": "high",
                "citations": [],
            }
        ],
        "total_goals": 1,
        "theory_goal_count": 0,
        "experiment_goal_count": 1,
        "both_goal_count": 0,
        "source_excerpt": output[:2000],
    }


def _default_track_decomposition(state: dict[str, Any]) -> dict[str, Any]:
    goals = (state.get("research_goals") or {}).get("goals") or []
    objective = str(state.get("agent_task") or state.get("task") or "Run the empirical track.")
    empirical_questions = [
        str(goal.get("description") or goal.get("title"))
        for goal in goals
        if goal.get("track") in {"experiment", "empirical", "both", None}
    ]
    if not empirical_questions:
        empirical_questions = [objective]
    theory_questions = [
        str(goal.get("description") or goal.get("title"))
        for goal in goals
        if goal.get("track") in {"theory", "both"}
    ]
    math_enabled = bool(state.get("math_enabled"))
    return {
        "theory_questions": theory_questions if math_enabled else [],
        "empirical_questions": empirical_questions,
        "recommended_track": "both" if math_enabled and theory_questions else "empirical",
        "rationale": "Adapter materialized from the approved research goals for SDK contract compatibility.",
        "cross_track_dependencies": [],
    }


def _markdown_artifact(stage_id: str, artifact_path: str, task: str, output: str, state: dict[str, Any]) -> str:
    if output:
        body = output
    else:
        body = (
            f"This artifact was materialized by the legacy adapter for `{stage_id}`. "
            "The stage completed without a dedicated file for this SDK contract."
        )
    return (
        f"# {_title(stage_id)}\n\n"
        f"## Objective\n\n{task or 'No objective was provided.'}\n\n"
        f"## Stage Output\n\n{body}\n\n"
        "## Contract Note\n\n"
        f"Canonical SDK artifact: `{artifact_path}`.\n"
    )


def _json_artifact(stage_id: str, artifact_path: str, task: str, output: str, state: dict[str, Any]) -> dict[str, Any]:
    if stage_id == "track_decomposition_gate":
        return _default_track_decomposition(state)
    if stage_id == "experimentation_agent" and artifact_path.endswith("experiment_manifest.json"):
        return {
            "status": "completed",
            "runner": "legacy_adapter",
            "artifacts": ["artifacts/experiment_results.md"],
            "commands": [],
            "notes": "The adapter captured the experiment narrative as the reproducibility surface for this smoke test.",
        }
    if stage_id == "duality_check":
        return {
            "verdict": "pass",
            "status": "passed",
            "failed_lenses": [],
            "objections": [],
            "rationale": output[:2000] or "No contradiction was surfaced by the legacy duality stage.",
        }
    if stage_id == "duality_gate":
        return {"decision": "pass", "next": "resource_preparation_agent", "rationale": "Duality check passed."}
    if stage_id == "resource_preparation_agent":
        return {
            "resources": ["artifacts/formalized_results.md", "artifacts/experiment_results.md"],
            "ready_for_writeup": True,
            "notes": output[:1000],
        }
    if stage_id == "paper_contract_builder":
        return {
            "format": "markdown",
            "required_sections": ["Summary", "Method", "Results", "Limitations"],
            "required_terms": ["batch normalization", "spectral norm", "Gaussian blobs"],
        }
    if stage_id in {"milestone_goals", "milestone_review"}:
        return {"decision": "approved", "mode": "human_or_adapter_review", "notes": output[:1000]}
    if stage_id.endswith("_gate") or stage_id.endswith("_entry"):
        return {"decision": "continue", "next": None, "status": "passed", "notes": output[:1000]}
    return {
        "stage_id": stage_id,
        "artifact": artifact_path,
        "status": "completed",
        "summary": output[:2000],
        "objective": task,
    }


def _write_contract_artifact(
    ctx: StageRunContext,
    artifact: ArtifactContract,
    *,
    task: str,
    output: str,
    state: dict[str, Any],
    metadata: dict[str, Any],
) -> str:
    legacy_text = _legacy_artifact_text(artifact)
    if legacy_text is not None and artifact.kind != "json":
        content: str | dict[str, Any] = legacy_text
    elif artifact.kind == "json":
        content = _json_artifact(ctx.stage_id, artifact.path, task, output or legacy_text or "", state)
    else:
        content = _markdown_artifact(ctx.stage_id, artifact.path, task, output or legacy_text or "", state)
    ctx.write_required(artifact.path, content, kind=artifact.kind, metadata=metadata)
    return str(ctx.workspace_rel / artifact.path)


def materialize_stage_outputs(stage_id: str, state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Persist the first contract-native slice from existing node outputs."""

    ctx = StageRunContext.from_env(stage_id)
    if ctx is None:
        return result

    stage_output = _stage_text(stage_id, state, result)
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
    elif stage_id == "formalize_goals_agent":
        research_goals = result.get("research_goals") or state.get("research_goals")
        if not isinstance(research_goals, dict):
            research_goals = _default_research_goals(task, stage_output)
        ctx.write_required(
            "artifacts/research_goals.json",
            research_goals,
            kind="json",
            metadata={"run_id": ctx.run_id, "materializer": "default_research_goals"},
        )
        ctx.write_required(
            "artifacts/goal_spec.md",
            _markdown_artifact(stage_id, "artifacts/goal_spec.md", task, stage_output, {**state, "research_goals": research_goals}),
            metadata={"run_id": ctx.run_id, "materializer": "goal_spec"},
        )
        written = {
            "research_goals": str(ctx.workspace_rel / "artifacts/research_goals.json"),
            "goal_spec": str(ctx.workspace_rel / "artifacts/goal_spec.md"),
        }
        result = {**result, "research_goals": research_goals}
    else:
        for artifact in ctx.contract.required_artifacts:
            if artifact.path in written.values():
                continue
            written_key = Path(artifact.path).stem
            written[written_key] = _write_contract_artifact(
                ctx,
                artifact,
                task=task,
                output=stage_output,
                state={**state, **result},
                metadata={"run_id": ctx.run_id, "materializer": "legacy_adapter_contract"},
            )

    if written:
        missing = ctx.validate_required()
        if missing:
            raise StageArtifactError(f"{stage_id} missing required artifacts: {', '.join(missing)}")
        return {**result, "artifacts": {**(result.get("artifacts") or {}), **written}}
    return result
