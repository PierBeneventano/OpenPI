"""Stable read models for product-shell consumers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
    logs: list[LogReadModel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
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
