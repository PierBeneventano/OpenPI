"""Domain models for the first-principles research kernel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Protocol


ArtifactRole = Literal["deliverable", "evidence", "diagnostic", "log", "prompt", "system_state"]
StageKind = Literal["agent", "tool", "validator", "router", "approval", "control"]
EventType = Literal[
    "RunStarted",
    "StageStarted",
    "RouteSelected",
    "JoinWaiting",
    "LoopLimitReached",
    "BudgetSpent",
    "BudgetExceeded",
    "ToolInvoked",
    "ToolDenied",
    "ArtifactWritten",
    "ArtifactIndexed",
    "ValidationPassed",
    "ValidationFailed",
    "StageCompleted",
    "HumanDecisionRequired",
    "ApprovalDecided",
    "RunCompleted",
    "RunFailed",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"id_{digest}"


@dataclass(frozen=True)
class BudgetPolicy:
    max_usd: float = 0.0
    spend_allowed: bool = False
    approval_required_above_usd: float | None = None


@dataclass(frozen=True)
class FailurePolicy:
    mode: Literal["stop_for_human", "retry_then_human"] = "stop_for_human"
    max_retries: int = 0


class BudgetExceededError(RuntimeError):
    """Raised when a stage attempts to exceed explicit budget policy."""


class ToolPolicyError(RuntimeError):
    """Raised when a stage attempts to use an undeclared tool."""


ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    id: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(
        self,
        tool_id: str,
        handler: ToolHandler,
        *,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if tool_id in self._tools:
            raise ValueError(f"Tool already registered: {tool_id}")
        self._tools[tool_id] = (
            ToolSpec(id=tool_id, description=description, metadata=metadata or {}),
            handler,
        )

    def require(self, tool_ids: Iterable[str]) -> None:
        missing = [tool_id for tool_id in tool_ids if tool_id not in self._tools]
        if missing:
            raise KeyError(f"Missing tools: {', '.join(missing)}")

    def invoke(self, tool_id: str, **kwargs: Any) -> Any:
        try:
            _spec, handler = self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Missing tool: {tool_id}") from exc
        return handler(**kwargs)

    def spec(self, tool_id: str) -> ToolSpec:
        try:
            spec, _handler = self._tools[tool_id]
        except KeyError as exc:
            raise KeyError(f"Missing tool: {tool_id}") from exc
        return spec


@dataclass(frozen=True)
class SpendRecord:
    stage_id: str
    amount_usd: float
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BudgetLedger:
    def __init__(self) -> None:
        self.records: list[SpendRecord] = []

    @property
    def total_usd(self) -> float:
        return round(sum(record.amount_usd for record in self.records), 8)

    def stage_total_usd(self, stage_id: str) -> float:
        return round(
            sum(record.amount_usd for record in self.records if record.stage_id == stage_id),
            8,
        )

    def charge(
        self,
        *,
        run: "RunSpec",
        stage: "StageSpec",
        amount_usd: float,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SpendRecord:
        if amount_usd < 0:
            raise ValueError("Budget charge cannot be negative")
        if amount_usd == 0:
            return SpendRecord(stage_id=stage.id, amount_usd=0, reason=reason, metadata=metadata or {})
        if not run.budget.spend_allowed:
            raise BudgetExceededError("Run budget policy does not allow spend")
        if not stage.budget.spend_allowed:
            raise BudgetExceededError(f"Stage {stage.id} budget policy does not allow spend")
        projected_run_total = self.total_usd + amount_usd
        if run.budget.max_usd and projected_run_total > run.budget.max_usd:
            raise BudgetExceededError(
                f"Run budget cap exceeded: {projected_run_total:.4f} > {run.budget.max_usd:.4f}"
            )
        projected_stage_total = self.stage_total_usd(stage.id) + amount_usd
        if stage.budget.max_usd and projected_stage_total > stage.budget.max_usd:
            raise BudgetExceededError(
                f"Stage budget cap exceeded for {stage.id}: {projected_stage_total:.4f} > {stage.budget.max_usd:.4f}"
            )
        record = SpendRecord(
            stage_id=stage.id,
            amount_usd=round(float(amount_usd), 8),
            reason=reason,
            metadata=metadata or {},
        )
        self.records.append(record)
        return record


@dataclass(frozen=True)
class DecisionRecord:
    id: str
    run_id: str
    campaign_id: str
    stage_id: str
    reason: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    safe_next_actions: tuple[str, ...] = ()
    created_at: str = field(default_factory=now_iso)
    decided_at: str | None = None
    actor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DecisionQueue:
    def __init__(self) -> None:
        self.decisions: dict[str, DecisionRecord] = {}

    def request(
        self,
        *,
        run: "RunSpec",
        stage_id: str,
        reason: str,
        safe_next_actions: Iterable[str],
        metadata: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        created_at = now_iso()
        decision = DecisionRecord(
            id=stable_id(run.id, stage_id, reason, created_at),
            run_id=run.id,
            campaign_id=run.campaign_id,
            stage_id=stage_id,
            reason=reason,
            safe_next_actions=tuple(str(action) for action in safe_next_actions),
            created_at=created_at,
            metadata=metadata or {},
        )
        self.decisions[decision.id] = decision
        return decision

    def decide(
        self,
        decision_id: str,
        *,
        approved: bool,
        actor: str = "user",
    ) -> DecisionRecord:
        current = self.decisions.get(decision_id)
        if current is None:
            raise KeyError(f"Decision not found: {decision_id}")
        if current.status != "pending":
            raise ValueError(f"Decision is already {current.status}: {decision_id}")
        decided = DecisionRecord(
            id=current.id,
            run_id=current.run_id,
            campaign_id=current.campaign_id,
            stage_id=current.stage_id,
            reason=current.reason,
            status="approved" if approved else "rejected",
            safe_next_actions=current.safe_next_actions,
            created_at=current.created_at,
            decided_at=now_iso(),
            actor=actor,
            metadata=current.metadata,
        )
        self.decisions[decision_id] = decided
        return decided

    def pending(self, *, run_id: str | None = None) -> list[DecisionRecord]:
        rows = [decision for decision in self.decisions.values() if decision.status == "pending"]
        if run_id is not None:
            rows = [decision for decision in rows if decision.run_id == run_id]
        return sorted(rows, key=lambda decision: decision.created_at)


@dataclass(frozen=True)
class ArtifactSpec:
    path: str
    kind: str
    role: ArtifactRole = "deliverable"
    required: bool = True
    schema_id: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        rel = Path(self.path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Artifact path must be safe and relative: {self.path}")


@dataclass(frozen=True)
class RouteSpec:
    target: str
    condition: str = "always"
    kind: Literal["next", "branch", "join", "loop", "failure"] = "next"
    max_visits: int | None = None


@dataclass(frozen=True)
class StageSpec:
    id: str
    title: str
    kind: StageKind
    purpose: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[ArtifactSpec, ...] = ()
    validator_ids: tuple[str, ...] = ()
    tool_ids: tuple[str, ...] = ()
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    failure: FailurePolicy = field(default_factory=FailurePolicy)
    routes: tuple[RouteSpec, ...] = ()
    pause_before: bool = False
    pause_after: bool = False

    @property
    def required_outputs(self) -> tuple[ArtifactSpec, ...]:
        return tuple(output for output in self.outputs if output.required)


@dataclass(frozen=True)
class GraphSpec:
    id: str
    stages: tuple[StageSpec, ...]
    entry_stage_id: str

    def stage_map(self) -> dict[str, StageSpec]:
        return {stage.id: stage for stage in self.stages}

    def validate(self) -> None:
        stages = self.stage_map()
        if self.entry_stage_id not in stages:
            raise ValueError(f"Entry stage is not in graph: {self.entry_stage_id}")
        for stage in self.stages:
            for route in stage.routes:
                if route.target not in stages:
                    raise ValueError(f"Stage {stage.id} routes to unknown stage {route.target}")

    def ordered_stage_ids(self) -> list[str]:
        """Return a deterministic happy-path order.

        Branch schedulers can be layered on later. This intentionally supports
        only condition=always/next for the initial kernel so hidden routing
        logic cannot sneak back in.
        """

        self.validate()
        order: list[str] = []
        seen: set[str] = set()
        current = self.entry_stage_id
        stages = self.stage_map()
        while current and current not in seen:
            seen.add(current)
            order.append(current)
            next_routes = [
                route for route in stages[current].routes
                if route.kind == "next" and route.condition == "always"
            ]
            current = next_routes[0].target if next_routes else ""
        return order


@dataclass(frozen=True)
class RunSpec:
    id: str
    campaign_id: str
    objective: str
    graph: GraphSpec
    workspace: Path
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    stage_id: str
    path: str
    absolute_path: Path
    kind: str
    role: ArtifactRole
    required: bool
    size_bytes: int
    checksum: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["absolute_path"] = str(self.absolute_path)
        return data


@dataclass(frozen=True)
class ValidationResult:
    validator_id: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageOutcome:
    stage_id: str
    artifacts: tuple[ArtifactRecord, ...] = ()
    validation: tuple[ValidationResult, ...] = ()
    status: Literal["completed", "human_decision_required", "failed"] = "completed"
    next_stage_id: str | None = None
    route_conditions: tuple[str, ...] = ("always",)
    scheduled_stage_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventRecord:
    id: str
    type: EventType | str
    run_id: str
    campaign_id: str
    created_at: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus(Protocol):
    def emit(self, event_type: EventType | str, *, run: RunSpec, payload: dict[str, Any]) -> EventRecord:
        ...


class InMemoryEventBus:
    def __init__(self) -> None:
        self.events: list[EventRecord] = []

    def emit(self, event_type: EventType | str, *, run: RunSpec, payload: dict[str, Any]) -> EventRecord:
        created_at = now_iso()
        event = EventRecord(
            id=stable_id(run.id, event_type, created_at, json.dumps(payload, sort_keys=True, default=str)),
            type=event_type,
            run_id=run.id,
            campaign_id=run.campaign_id,
            created_at=created_at,
            payload=payload,
        )
        self.events.append(event)
        return event


class JsonlEventBus:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def emit(self, event_type: EventType | str, *, run: RunSpec, payload: dict[str, Any]) -> EventRecord:
        created_at = now_iso()
        event = EventRecord(
            id=stable_id(run.id, event_type, created_at, json.dumps(payload, sort_keys=True, default=str)),
            type=event_type,
            run_id=run.id,
            campaign_id=run.campaign_id,
            created_at=created_at,
            payload=payload,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True, default=str) + "\n")
        return event


Validator = Callable[["RuntimeContext", StageSpec, tuple[ArtifactRecord, ...]], ValidationResult]


class ValidatorRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, validator_id: str, validator: Validator) -> None:
        if validator_id in self._validators:
            raise ValueError(f"Validator already registered: {validator_id}")
        self._validators[validator_id] = validator

    def require(self, validator_ids: Iterable[str]) -> None:
        missing = [validator_id for validator_id in validator_ids if validator_id not in self._validators]
        if missing:
            raise KeyError(f"Missing validators: {', '.join(missing)}")

    def run(self, validator_id: str, context: "RuntimeContext", stage: StageSpec, artifacts: tuple[ArtifactRecord, ...]) -> ValidationResult:
        try:
            validator = self._validators[validator_id]
        except KeyError as exc:
            raise KeyError(f"Missing validator: {validator_id}") from exc
        return validator(context, stage, artifacts)


@dataclass(frozen=True)
class RuntimeContext:
    run: RunSpec
    stage: StageSpec
    event_bus: EventBus
    validator_registry: ValidatorRegistry
    budget_ledger: BudgetLedger
    decision_queue: DecisionQueue
    tool_registry: ToolRegistry

    @property
    def stage_workspace(self) -> Path:
        return self.run.workspace / self.stage.id

    def artifact_path(self, artifact: ArtifactSpec) -> Path:
        target = (self.stage_workspace / artifact.path).resolve()
        workspace = self.stage_workspace.resolve()
        if target != workspace and workspace not in target.parents:
            raise ValueError(f"Artifact escapes stage workspace: {artifact.path}")
        return target

    def write_artifact(self, artifact: ArtifactSpec, content: str | bytes | dict[str, Any] | list[Any]) -> ArtifactRecord:
        target = self.artifact_path(artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        elif isinstance(content, (dict, list)):
            target.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            target.write_text(str(content), encoding="utf-8")
        record = artifact_record_for_file(self.stage.id, artifact, target)
        self.event_bus.emit(
            "ArtifactWritten",
            run=self.run,
            payload={"stage_id": self.stage.id, "artifact": record.to_dict()},
        )
        return record

    def charge_budget(
        self,
        amount_usd: float,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SpendRecord:
        try:
            record = self.budget_ledger.charge(
                run=self.run,
                stage=self.stage,
                amount_usd=amount_usd,
                reason=reason,
                metadata=metadata,
            )
        except BudgetExceededError as exc:
            self.event_bus.emit(
                "BudgetExceeded",
                run=self.run,
                payload={
                    "stage_id": self.stage.id,
                    "amount_usd": amount_usd,
                    "reason": reason,
                    "message": str(exc),
                    "run_total_usd": self.budget_ledger.total_usd,
                    "stage_total_usd": self.budget_ledger.stage_total_usd(self.stage.id),
                },
            )
            raise
        self.event_bus.emit(
            "BudgetSpent",
            run=self.run,
            payload={
                "stage_id": self.stage.id,
                "spend": record.to_dict(),
                "run_total_usd": self.budget_ledger.total_usd,
                "stage_total_usd": self.budget_ledger.stage_total_usd(self.stage.id),
            },
        )
        return record

    def use_tool(self, tool_id: str, **kwargs: Any) -> Any:
        if tool_id not in self.stage.tool_ids:
            self.event_bus.emit(
                "ToolDenied",
                run=self.run,
                payload={
                    "stage_id": self.stage.id,
                    "tool_id": tool_id,
                    "reason": "tool_not_declared_for_stage",
                },
            )
            raise ToolPolicyError(f"Tool {tool_id} is not declared for stage {self.stage.id}")
        result = self.tool_registry.invoke(tool_id, **kwargs)
        self.event_bus.emit(
            "ToolInvoked",
            run=self.run,
            payload={
                "stage_id": self.stage.id,
                "tool_id": tool_id,
                "arguments": kwargs,
            },
        )
        return result


def artifact_record_for_file(stage_id: str, artifact: ArtifactSpec, path: Path) -> ArtifactRecord:
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return ArtifactRecord(
        id=stable_id(stage_id, artifact.path, checksum),
        stage_id=stage_id,
        path=artifact.path,
        absolute_path=path,
        kind=artifact.kind,
        role=artifact.role,
        required=artifact.required,
        size_bytes=path.stat().st_size,
        checksum=checksum,
    )
