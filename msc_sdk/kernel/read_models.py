"""Read-model projection for kernel event streams."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import EventRecord


TERMINAL_RUN_EVENTS = {
    "CampaignExecutionCompleted",
    "CampaignExecutionFailed",
}
TERMINAL_STAGE_EVENTS = {"StageCompleted", "HumanDecisionRequired"}


@dataclass
class KernelArtifactReadModel:
    id: str
    stage_id: str
    path: str
    kind: str
    role: str
    required: bool
    size_bytes: int
    checksum: str
    absolute_path: str
    schema_id: str | None = None
    claim_ids: list[str] = field(default_factory=list)
    evidence_links: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KernelStageReadModel:
    stage_id: str
    status: str = "planned"
    artifacts: list[KernelArtifactReadModel] = field(default_factory=list)
    validation: list[dict[str, Any]] = field(default_factory=list)
    safe_next_actions: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    pending_decision_id: str | None = None
    decision_status: str | None = None
    budget_spent_usd: float = 0.0
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def deliverables(self) -> list[KernelArtifactReadModel]:
        return [artifact for artifact in self.artifacts if artifact.role == "deliverable"]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["deliverables"] = [artifact.to_dict() for artifact in self.deliverables]
        return data


@dataclass
class KernelRunReadModel:
    run_id: str
    campaign_id: str
    status: str = "planned"
    objective: str = ""
    graph_id: str = ""
    workspace: str = ""
    budget_spent_usd: float = 0.0
    stages: dict[str, KernelStageReadModel] = field(default_factory=dict)
    completed_stage_ids: list[str] = field(default_factory=list)
    councils: list[dict[str, Any]] = field(default_factory=list)
    duality_status: dict[str, Any] = field(default_factory=dict)

    def stage_list(self) -> list[KernelStageReadModel]:
        return list(self.stages.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "campaign_id": self.campaign_id,
            "status": self.status,
            "objective": self.objective,
            "graph_id": self.graph_id,
            "workspace": self.workspace,
            "budget_spent_usd": self.budget_spent_usd,
            "completed_stage_ids": list(self.completed_stage_ids),
            "councils": list(self.councils),
            "duality_status": dict(self.duality_status),
            "stages": [stage.to_dict() for stage in self.stage_list()],
        }


def project_run(events: Iterable[EventRecord]) -> KernelRunReadModel:
    """Project kernel events into a current run view.

    This is the read-side contract that product surfaces should use instead of
    inferring truth from files, logs, subprocess state, or free-form messages.
    """

    ordered = list(events)
    if not ordered:
        raise ValueError("Cannot project an empty event stream")

    first = ordered[0]
    model = KernelRunReadModel(run_id=first.run_id, campaign_id=first.campaign_id)

    for event in ordered:
        if event.run_id != model.run_id:
            raise ValueError("Event stream contains multiple run ids")
        payload = event.payload
        event_type = str(event.type)
        stage_id = str(payload.get("stage_id") or "")

        if event_type == "CampaignExecutionStarted":
            model.status = "running"
            model.objective = str(payload.get("objective") or "")
            model.graph_id = str(payload.get("graph_id") or "")
            model.workspace = str(payload.get("workspace") or "")
        elif event_type == "CampaignExecutionResumed":
            model.status = "running"
        elif event_type == "CampaignExecutionCompleted":
            model.status = "completed"
            model.completed_stage_ids = [
                str(stage) for stage in payload.get("completed_stage_ids") or []
            ]
        elif event_type == "CampaignExecutionFailed":
            model.status = "human_decision_required"
        elif event_type == "StageStarted" and stage_id:
            stage = _stage(model, stage_id)
            stage.status = "running"
            stage.started_at = event.created_at
        elif event_type == "BudgetSpent" and stage_id:
            stage = _stage(model, stage_id)
            stage.budget_spent_usd = float(payload.get("stage_total_usd") or stage.budget_spent_usd)
            model.budget_spent_usd = float(payload.get("run_total_usd") or model.budget_spent_usd)
        elif event_type == "BudgetExceeded" and stage_id:
            stage = _stage(model, stage_id)
            stage.status = "human_decision_required"
            stage.failure_reason = "budget_policy_failed"
            stage.safe_next_actions = ["approve-budget-increase", "rewrite-stage", "rerun-stage", "abort"]
        elif event_type in {"ArtifactWritten", "ArtifactIndexed"} and stage_id:
            artifact = _artifact_from_payload(payload.get("artifact") or {})
            if artifact is not None:
                stage = _stage(model, stage_id)
                _upsert_artifact(stage, artifact)
        elif event_type in {"ValidationPassed", "ValidationFailed"} and stage_id:
            stage = _stage(model, stage_id)
            stage.validation = list(payload.get("validation") or [])
            if event_type == "ValidationFailed":
                stage.status = "human_decision_required"
                stage.safe_next_actions = [str(action) for action in payload.get("safe_next_actions") or []]
        elif event_type == "HumanDecisionRequired" and stage_id:
            model.status = "human_decision_required"
            stage = _stage(model, stage_id)
            stage.status = "human_decision_required"
            stage.pending_decision_id = str(payload.get("decision_id") or "") or None
            stage.decision_status = "pending"
            stage.failure_reason = str(payload.get("reason") or "")
            stage.safe_next_actions = [str(action) for action in payload.get("safe_next_actions") or []]
        elif event_type == "ApprovalDecided" and stage_id:
            stage = _stage(model, stage_id)
            stage.pending_decision_id = str(payload.get("decision_id") or stage.pending_decision_id or "") or None
            stage.decision_status = str(payload.get("status") or "")
        elif event_type in {"CouncilStarted", "CouncilVerdictRecorded", "CouncilSynthesisRecorded"}:
            model.councils.append({"type": event_type, "created_at": event.created_at, **payload})
        elif event_type == "DualityCheckStarted":
            model.duality_status = {
                **model.duality_status,
                "status": "running",
                "stage_id": stage_id or payload.get("stage_id"),
                "started_at": event.created_at,
            }
        elif event_type == "DualityCheckCompleted":
            model.duality_status = {
                **model.duality_status,
                "status": "passed" if payload.get("passed") else "failed",
                "stage_id": stage_id or payload.get("stage_id"),
                "completed_at": event.created_at,
                "verdict": payload.get("verdict"),
                "metadata": dict(payload.get("metadata") or {}),
            }
        elif event_type == "StageCompleted" and stage_id:
            stage = _stage(model, stage_id)
            stage.status = "completed"
            stage.completed_at = event.created_at

    return model


def read_jsonl_events(path: str | Path) -> list[EventRecord]:
    events: list[EventRecord] = []
    source = Path(path)
    if not source.exists():
        return events
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        events.append(
            EventRecord(
                id=str(data["id"]),
                type=str(data["type"]),
                run_id=str(data["run_id"]),
                campaign_id=str(data["campaign_id"]),
                created_at=str(data["created_at"]),
                payload=dict(data.get("payload") or {}),
            )
        )
    return events


def _stage(model: KernelRunReadModel, stage_id: str) -> KernelStageReadModel:
    stage = model.stages.get(stage_id)
    if stage is None:
        stage = KernelStageReadModel(stage_id=stage_id)
        model.stages[stage_id] = stage
    return stage


def _artifact_from_payload(payload: dict[str, Any]) -> KernelArtifactReadModel | None:
    if not payload:
        return None
    return KernelArtifactReadModel(
        id=str(payload.get("id") or ""),
        stage_id=str(payload.get("stage_id") or ""),
        path=str(payload.get("path") or ""),
        kind=str(payload.get("kind") or ""),
        role=str(payload.get("role") or ""),
        required=bool(payload.get("required")),
        schema_id=str(payload.get("schema_id") or "") or None,
        claim_ids=[str(claim_id) for claim_id in payload.get("claim_ids") or []],
        evidence_links=[dict(link) for link in payload.get("evidence_links") or []],
        size_bytes=int(payload.get("size_bytes") or 0),
        checksum=str(payload.get("checksum") or ""),
        absolute_path=str(payload.get("absolute_path") or ""),
    )


def _upsert_artifact(stage: KernelStageReadModel, artifact: KernelArtifactReadModel) -> None:
    for index, existing in enumerate(stage.artifacts):
        if existing.path == artifact.path:
            stage.artifacts[index] = artifact
            return
    stage.artifacts.append(artifact)
