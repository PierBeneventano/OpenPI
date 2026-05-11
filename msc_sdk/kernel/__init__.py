"""First-principles research runtime kernel.

This package is intentionally independent from the historical LangGraph
implementation. It defines the domain objects and execution boundary that new
product features should target: run specs, stage specs, artifacts, validators,
events, and deterministic runtime context.
"""

from .engine import ResearchKernel
from .models import (
    ArtifactRecord,
    ArtifactSpec,
    BudgetExceededError,
    BudgetLedger,
    BudgetPolicy,
    EventRecord,
    FailurePolicy,
    GraphSpec,
    InMemoryEventBus,
    JsonlEventBus,
    RouteSpec,
    RunSpec,
    RuntimeContext,
    SpendRecord,
    StageOutcome,
    StageSpec,
    ValidationResult,
    ValidatorRegistry,
)
from .read_models import (
    KernelArtifactReadModel,
    KernelRunReadModel,
    KernelStageReadModel,
    project_run,
    read_jsonl_events,
)

__all__ = [
    "ArtifactRecord",
    "ArtifactSpec",
    "BudgetExceededError",
    "BudgetLedger",
    "BudgetPolicy",
    "EventRecord",
    "FailurePolicy",
    "GraphSpec",
    "InMemoryEventBus",
    "JsonlEventBus",
    "ResearchKernel",
    "RouteSpec",
    "RunSpec",
    "RuntimeContext",
    "SpendRecord",
    "StageOutcome",
    "StageSpec",
    "ValidationResult",
    "ValidatorRegistry",
    "KernelArtifactReadModel",
    "KernelRunReadModel",
    "KernelStageReadModel",
    "project_run",
    "read_jsonl_events",
]
