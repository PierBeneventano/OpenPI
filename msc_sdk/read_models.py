"""Stable read models for product-shell consumers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .kernel.read_models import KernelRunReadModel


@dataclass(frozen=True)
class ArtifactReadModel:
    """Read-only description of a raw or derived artifact."""

    id: str
    path: str
    kind: str
    exists: bool
    size_bytes: int | None = None
    modified_at: str | None = None
    source_role: str = "raw"
    required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogReadModel:
    """Read-only description of a log file."""

    path: str
    size_bytes: int
    modified_at: str | None = None
    stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BudgetReadModel:
    """Normalized budget state derived from raw budget artifacts."""

    total_usd: float | None = None
    limit_usd: float | None = None
    ledger_path: str | None = None
    state_path: str | None = None
    by_model: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunReadModel:
    """Read-only summary of a single run workspace."""

    run_id: str
    workspace: str
    status: str
    current_stage: str | None = None
    task: str | None = None
    model: str | None = None
    final_paper: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    budget: BudgetReadModel = field(default_factory=BudgetReadModel)
    artifacts: list[ArtifactReadModel] = field(default_factory=list)
    logs: list[LogReadModel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget"] = self.budget.to_dict()
        data["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        data["logs"] = [log.to_dict() for log in self.logs]
        return data


@dataclass(frozen=True)
class StageReadModel:
    """Read-only campaign stage summary."""

    stage_id: str
    status: str = "unknown"
    workspace: str | None = None
    current_attempt: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    fail_reason: str | None = None
    required_artifacts: list[ArtifactReadModel] = field(default_factory=list)
    optional_artifacts: list[ArtifactReadModel] = field(default_factory=list)
    budget: BudgetReadModel = field(default_factory=BudgetReadModel)
    logs: list[LogReadModel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget"] = self.budget.to_dict()
        data["required_artifacts"] = [
            artifact.to_dict() for artifact in self.required_artifacts
        ]
        data["optional_artifacts"] = [
            artifact.to_dict() for artifact in self.optional_artifacts
        ]
        data["logs"] = [log.to_dict() for log in self.logs]
        return data


@dataclass(frozen=True)
class CampaignReadModel:
    """Read-only summary of a campaign spec plus observed state."""

    campaign_id: str
    path: str
    name: str | None = None
    workspace_root: str | None = None
    status: str = "unknown"
    budget: BudgetReadModel = field(default_factory=BudgetReadModel)
    stages: list[StageReadModel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget"] = self.budget.to_dict()
        data["stages"] = [stage.to_dict() for stage in self.stages]
        return data


def campaign_model_from_kernel_run(kernel_run: KernelRunReadModel) -> CampaignReadModel:
    """Project kernel event state into the stable product-shell campaign view."""

    stages: list[StageReadModel] = []
    for kernel_stage in kernel_run.stage_list():
        required_artifacts: list[ArtifactReadModel] = []
        optional_artifacts: list[ArtifactReadModel] = []
        for artifact in kernel_stage.artifacts:
            product_artifact = ArtifactReadModel(
                id=artifact.id,
                path=artifact.path,
                kind=artifact.kind,
                exists=artifact.size_bytes > 0,
                size_bytes=artifact.size_bytes,
                source_role=artifact.role,
                required=artifact.required,
                metadata={
                    "source": "kernel_events",
                    "run_id": kernel_run.run_id,
                    "stage_id": artifact.stage_id,
                    "absolute_path": artifact.absolute_path,
                    "checksum": artifact.checksum,
                    "schema_id": artifact.schema_id,
                    "claim_ids": list(artifact.claim_ids),
                    "evidence_links": list(artifact.evidence_links),
                },
            )
            if artifact.required:
                required_artifacts.append(product_artifact)
            else:
                optional_artifacts.append(product_artifact)
        stages.append(
            StageReadModel(
                stage_id=kernel_stage.stage_id,
                status=kernel_stage.status,
                started_at=kernel_stage.started_at,
                completed_at=kernel_stage.completed_at,
                fail_reason=kernel_stage.failure_reason,
                required_artifacts=required_artifacts,
                optional_artifacts=optional_artifacts,
                budget=BudgetReadModel(total_usd=kernel_stage.budget_spent_usd),
                metadata={
                    "source": "kernel_events",
                    "validation": list(kernel_stage.validation),
                    "safe_next_actions": list(kernel_stage.safe_next_actions),
                    "pending_decision_id": kernel_stage.pending_decision_id,
                    "decision_status": kernel_stage.decision_status,
                },
            )
        )

    return CampaignReadModel(
        campaign_id=kernel_run.campaign_id,
        path=kernel_run.workspace,
        workspace_root=kernel_run.workspace,
        status=kernel_run.status,
        budget=BudgetReadModel(total_usd=kernel_run.budget_spent_usd),
        stages=stages,
        metadata={
            "source": "kernel_events",
            "run_id": kernel_run.run_id,
            "objective": kernel_run.objective,
            "graph_id": kernel_run.graph_id,
            "completed_stage_ids": list(kernel_run.completed_stage_ids),
            "councils": list(kernel_run.councils),
            "duality_status": dict(kernel_run.duality_status),
        },
        provenance={"source": "kernel_events"},
    )
