"""Execution engine for the first-principles research kernel."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import (
    ArtifactRecord,
    ArtifactSpec,
    BudgetExceededError,
    BudgetLedger,
    CheckpointStore,
    DecisionQueue,
    EvidenceLink,
    InputSpec,
    InMemoryEventBus,
    ModelPolicyError,
    ModelRegistry,
    RunCheckpoint,
    RunSpec,
    RuntimeContext,
    SchemaRegistry,
    SchemaValidationError,
    StageOutcome,
    StageSpec,
    ToolPolicyError,
    ToolRegistry,
    ValidationResult,
    ValidatorRegistry,
    artifact_record_for_file,
)


StageHandler = Callable[[RuntimeContext], dict[str, Any] | None]


class ResearchKernel:
    """Small deterministic runtime for explicit research stages.

    The kernel owns the invariant that a stage is complete only when required
    artifacts exist and all declared validators pass. Stage handlers may write
    artifacts through ``RuntimeContext.write_artifact`` or directly produce
    files at declared artifact paths; the kernel indexes both.
    """

    def __init__(
        self,
        *,
        validators: ValidatorRegistry | None = None,
        event_bus: InMemoryEventBus | None = None,
        budget_ledger: BudgetLedger | None = None,
        decision_queue: DecisionQueue | None = None,
        tool_registry: ToolRegistry | None = None,
        schema_registry: SchemaRegistry | None = None,
        model_registry: ModelRegistry | None = None,
        checkpoint_store: CheckpointStore | None = None,
        max_stage_executions: int = 100,
    ) -> None:
        self.validators = validators or ValidatorRegistry()
        self.event_bus = event_bus or InMemoryEventBus()
        self.budget_ledger = budget_ledger or BudgetLedger()
        self.decision_queue = decision_queue or DecisionQueue()
        self.tool_registry = tool_registry or ToolRegistry()
        self.schema_registry = schema_registry or SchemaRegistry()
        self.model_registry = model_registry or ModelRegistry()
        self.checkpoint_store = checkpoint_store or CheckpointStore()
        self.max_stage_executions = max_stage_executions
        self._runs: dict[str, RunSpec] = {}

    def run(
        self,
        run: RunSpec,
        handlers: dict[str, StageHandler],
        *,
        checkpoint: RunCheckpoint | None = None,
    ) -> list[StageOutcome]:
        run.graph.validate()
        self._runs[run.id] = run
        self._validate_handlers(run, handlers)
        self._validate_validators(run)
        self._validate_tools(run)
        self._validate_schemas(run)
        self._validate_models(run)
        run.workspace.mkdir(parents=True, exist_ok=True)
        if checkpoint is None:
            self.event_bus.emit(
                "RunStarted",
                run=run,
                payload={
                    "objective": run.objective,
                    "graph_id": run.graph.id,
                    "workspace": str(run.workspace),
                },
            )
        else:
            self.event_bus.emit(
                "RunResumed",
                run=run,
                payload={
                    "checkpoint_id": checkpoint.id,
                    "blocked_stage_id": checkpoint.blocked_stage_id,
                    "reason": checkpoint.reason,
                    "queue": list(checkpoint.queue),
                },
            )

        outcomes: list[StageOutcome] = []
        stages = run.graph.stage_map()
        queue: list[str] = list(checkpoint.queue) if checkpoint is not None else [run.graph.entry_stage_id]
        completed_order: list[str] = list(checkpoint.completed_stage_ids) if checkpoint is not None else []
        completed: set[str] = set(completed_order)
        available_artifacts: dict[tuple[str, str], ArtifactRecord] = {
            (artifact.stage_id, artifact.path): artifact
            for artifact in (checkpoint.available_artifacts if checkpoint is not None else ())
        }
        visit_counts: dict[str, int] = dict(checkpoint.visit_counts) if checkpoint is not None else {}

        while queue:
            if len(outcomes) >= self.max_stage_executions:
                self.event_bus.emit(
                    "RunFailed",
                    run=run,
                    payload={
                        "reason": "max_stage_executions_exceeded",
                        "max_stage_executions": self.max_stage_executions,
                    },
                )
                return outcomes

            stage_id = queue.pop(0)
            stage = stages[stage_id]
            input_artifacts, missing_inputs = self._resolve_inputs(
                run=run,
                stage=stage,
                available_artifacts=available_artifacts,
            )
            if missing_inputs:
                result = ValidationResult(
                    validator_id="stage_inputs",
                    passed=False,
                    message="required stage inputs are missing",
                    details={"missing": missing_inputs},
                )
                self.event_bus.emit(
                    "StageInputMissing",
                    run=run,
                    payload={
                        "stage_id": stage.id,
                        "missing": missing_inputs,
                    },
                )
                self._request_decision(
                    run=run,
                    stage_id=stage.id,
                    reason="stage_inputs_missing",
                    safe_next_actions=["rerun-upstream", "rewrite-stage", "skip-stage", "abort"],
                    metadata={"validation": [result.__dict__]},
                )
                outcomes.append(
                    StageOutcome(
                        stage_id=stage.id,
                        validation=(result,),
                        status="human_decision_required",
                    )
                )
                self._checkpoint(
                    run=run,
                    blocked_stage_id=stage.id,
                    reason="stage_inputs_missing",
                    queue=[stage.id, *queue],
                    completed_stage_ids=completed_order,
                    available_artifacts=available_artifacts,
                    visit_counts=visit_counts,
                )
                self.event_bus.emit(
                    "RunFailed",
                    run=run,
                    payload={"stage_id": stage.id, "status": "human_decision_required"},
                )
                return outcomes
            visit_counts[stage_id] = visit_counts.get(stage_id, 0) + 1
            context = RuntimeContext(
                run=run,
                stage=stage,
                event_bus=self.event_bus,
                validator_registry=self.validators,
                budget_ledger=self.budget_ledger,
                decision_queue=self.decision_queue,
                tool_registry=self.tool_registry,
                schema_registry=self.schema_registry,
                model_registry=self.model_registry,
                input_artifacts=input_artifacts,
            )
            outcome = self.run_stage(context, handlers[stage_id])
            outcomes.append(outcome)
            if outcome.status != "completed":
                checkpoint_queue = [stage_id, *queue]
                checkpoint_completed_order = list(completed_order)
                checkpoint_artifacts = dict(available_artifacts)
                if stage.pause_after and all(result.passed for result in outcome.validation):
                    checkpoint_queue = [
                        next_stage_id for next_stage_id in (outcome.next_stage_id,)
                        if next_stage_id is not None
                    ] + queue
                    if stage_id not in completed:
                        checkpoint_completed_order.append(stage_id)
                    for artifact_record in outcome.artifacts:
                        checkpoint_artifacts[(artifact_record.stage_id, artifact_record.path)] = artifact_record
                self._checkpoint(
                    run=run,
                    blocked_stage_id=stage_id,
                    reason=outcome.status,
                    queue=checkpoint_queue,
                    completed_stage_ids=checkpoint_completed_order,
                    available_artifacts=checkpoint_artifacts,
                    visit_counts=visit_counts,
                )
                self.event_bus.emit(
                    "RunFailed",
                    run=run,
                    payload={"stage_id": stage_id, "status": outcome.status},
                )
                return outcomes
            completed.add(stage_id)
            completed_order.append(stage_id)
            for artifact_record in outcome.artifacts:
                available_artifacts[(artifact_record.stage_id, artifact_record.path)] = artifact_record

            scheduled, blocked = self._schedule_next(
                run=run,
                stage=stage,
                outcome=outcome,
                queue=queue,
                completed=completed,
                visit_counts=visit_counts,
            )
            if blocked:
                self._checkpoint(
                    run=run,
                    blocked_stage_id=stage_id,
                    reason="route_blocked",
                    queue=queue,
                    completed_stage_ids=completed_order,
                    available_artifacts=available_artifacts,
                    visit_counts=visit_counts,
                )
                self.event_bus.emit(
                    "RunFailed",
                    run=run,
                    payload={"stage_id": stage_id, "status": "human_decision_required"},
                )
                outcomes[-1] = StageOutcome(
                    stage_id=outcome.stage_id,
                    artifacts=outcome.artifacts,
                    validation=outcome.validation,
                    status="human_decision_required",
                    next_stage_id=outcome.next_stage_id,
                    route_conditions=outcome.route_conditions,
                    scheduled_stage_ids=scheduled,
                )
                return outcomes
            if scheduled:
                outcomes[-1] = StageOutcome(
                    stage_id=outcome.stage_id,
                    artifacts=outcome.artifacts,
                    validation=outcome.validation,
                    status=outcome.status,
                    next_stage_id=scheduled[0],
                    route_conditions=outcome.route_conditions,
                    scheduled_stage_ids=scheduled,
                )

        self.event_bus.emit(
            "RunCompleted",
            run=run,
            payload={"completed_stage_ids": completed_order},
        )
        return outcomes

    def run_stage(self, context: RuntimeContext, handler: StageHandler) -> StageOutcome:
        stage = context.stage
        run = context.run
        if stage.pause_before and not self._has_approved_decision(run.id, stage.id, "pause_before_stage"):
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="pause_before_stage",
                safe_next_actions=["approve", "rewrite-stage", "skip-stage", "abort"],
            )
            return StageOutcome(stage_id=stage.id, status="human_decision_required")

        self.event_bus.emit("StageStarted", run=run, payload={"stage_id": stage.id})

        try:
            handler_result = handler(context)
        except BudgetExceededError as exc:
            result = ValidationResult(
                validator_id="budget_policy",
                passed=False,
                message=str(exc),
            )
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="budget_policy_failed",
                safe_next_actions=["approve-budget-increase", "rewrite-stage", "rerun-stage", "abort"],
                metadata={"validation": [result.__dict__]},
            )
            return StageOutcome(
                stage_id=stage.id,
                validation=(result,),
                status="human_decision_required",
                route_conditions=self._route_conditions(None),
            )
        except ToolPolicyError as exc:
            result = ValidationResult(
                validator_id="tool_policy",
                passed=False,
                message=str(exc),
            )
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="tool_policy_failed",
                safe_next_actions=["rewrite-stage", "rerun-stage", "approve-tool-access", "abort"],
                metadata={"validation": [result.__dict__]},
            )
            return StageOutcome(
                stage_id=stage.id,
                validation=(result,),
                status="human_decision_required",
                route_conditions=self._route_conditions(None),
            )
        except ModelPolicyError as exc:
            result = ValidationResult(
                validator_id="model_policy",
                passed=False,
                message=str(exc),
            )
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="model_policy_failed",
                safe_next_actions=["rewrite-stage", "rerun-stage", "approve-model-access", "abort"],
                metadata={"validation": [result.__dict__]},
            )
            return StageOutcome(
                stage_id=stage.id,
                validation=(result,),
                status="human_decision_required",
                route_conditions=self._route_conditions(None),
            )
        except SchemaValidationError as exc:
            return self._schema_failure_outcome(run=run, stage=stage, exc=exc)
        except Exception as exc:
            result = ValidationResult(
                validator_id="stage_handler",
                passed=False,
                message=f"{type(exc).__name__}: {exc}",
            )
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="stage_handler_failed",
                safe_next_actions=["rewrite-stage", "rerun-stage", "abort"],
                metadata={"validation": [result.__dict__]},
            )
            return StageOutcome(
                stage_id=stage.id,
                validation=(result,),
                status="human_decision_required",
                route_conditions=self._route_conditions(None),
            )

        try:
            artifacts = self._index_declared_artifacts(context)
        except SchemaValidationError as exc:
            return self._schema_failure_outcome(run=run, stage=stage, exc=exc)
        validation = self._validate_completion(context, artifacts)
        route_conditions = self._route_conditions(handler_result)
        failed = [result for result in validation if not result.passed]
        if failed:
            self.event_bus.emit(
                "ValidationFailed",
                run=run,
                payload={
                    "stage_id": stage.id,
                    "validation": [result.__dict__ for result in validation],
                    "safe_next_actions": ["rewrite-stage", "rerun-stage", "abort"],
                },
            )
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="stage_validation_failed",
                safe_next_actions=["rewrite-stage", "rerun-stage", "abort"],
                metadata={"validation": [result.__dict__ for result in failed]},
            )
            return StageOutcome(
                stage_id=stage.id,
                artifacts=artifacts,
                validation=tuple(validation),
                status="human_decision_required",
                route_conditions=route_conditions,
            )

        self.event_bus.emit(
            "ValidationPassed",
            run=run,
            payload={"stage_id": stage.id, "validation": [result.__dict__ for result in validation]},
        )
        if stage.pause_after and not self._has_approved_decision(run.id, stage.id, "pause_after_stage"):
            self._request_decision(
                run=run,
                stage_id=stage.id,
                reason="pause_after_stage",
                safe_next_actions=["approve", "rewrite-stage", "rerun-stage", "abort"],
                metadata={"validation": [result.__dict__ for result in validation]},
            )
            return StageOutcome(
                stage_id=stage.id,
                artifacts=artifacts,
                validation=tuple(validation),
                status="human_decision_required",
                next_stage_id=self._next_stage_id(stage),
                route_conditions=route_conditions,
            )

        self.event_bus.emit(
            "StageCompleted",
            run=run,
            payload={"stage_id": stage.id, "artifacts": [artifact.to_dict() for artifact in artifacts]},
        )
        return StageOutcome(
            stage_id=stage.id,
            artifacts=artifacts,
            validation=tuple(validation),
            status="completed",
            next_stage_id=self._next_stage_id(stage),
            route_conditions=route_conditions,
        )

    def _index_declared_artifacts(self, context: RuntimeContext) -> tuple[ArtifactRecord, ...]:
        records: list[ArtifactRecord] = []
        for artifact in context.stage.outputs:
            target = context.artifact_path(artifact)
            if not target.exists() or not target.is_file():
                continue
            context.validate_artifact_file(artifact, target)
            record = artifact_record_for_file(context.stage.id, artifact, target)
            records.append(record)
            self.event_bus.emit(
                "ArtifactIndexed",
                run=context.run,
                payload={"stage_id": context.stage.id, "artifact": record.to_dict()},
            )
        return tuple(records)

    def _validate_completion(self, context: RuntimeContext, artifacts: tuple[ArtifactRecord, ...]) -> list[ValidationResult]:
        present_required = {
            artifact.path for artifact in artifacts
            if artifact.required and artifact.size_bytes > 0
        }
        missing = [
            artifact.path for artifact in context.stage.required_outputs
            if artifact.path not in present_required
        ]
        results = [
            ValidationResult(
                validator_id="required_artifacts_exist",
                passed=not missing,
                message="all required artifacts exist" if not missing else "missing required artifacts",
                details={"missing": missing},
            )
        ]
        for validator_id in context.stage.validator_ids:
            results.append(self.validators.run(validator_id, context, context.stage, artifacts))
        return results

    def _validate_handlers(self, run: RunSpec, handlers: dict[str, StageHandler]) -> None:
        missing = [stage.id for stage in run.graph.stages if stage.id not in handlers]
        if missing:
            raise KeyError(f"Missing stage handlers: {', '.join(missing)}")

    def _validate_validators(self, run: RunSpec) -> None:
        validator_ids: list[str] = []
        for stage in run.graph.stages:
            validator_ids.extend(stage.validator_ids)
        self.validators.require(validator_ids)

    def _validate_tools(self, run: RunSpec) -> None:
        tool_ids: list[str] = []
        for stage in run.graph.stages:
            tool_ids.extend(stage.tool_ids)
        self.tool_registry.require(tool_ids)

    def _validate_schemas(self, run: RunSpec) -> None:
        schema_ids: list[str] = []
        for stage in run.graph.stages:
            schema_ids.extend(
                artifact.schema_id
                for artifact in stage.outputs
                if artifact.schema_id is not None
            )
        self.schema_registry.require(schema_ids)

    def _validate_models(self, run: RunSpec) -> None:
        model_ids: list[str] = []
        for stage in run.graph.stages:
            model_ids.extend(stage.model_policy.allowed_model_ids)
        self.model_registry.require(model_ids)

    def _resolve_inputs(
        self,
        *,
        run: RunSpec,
        stage: StageSpec,
        available_artifacts: dict[tuple[str, str], ArtifactRecord],
    ) -> tuple[tuple[ArtifactRecord, ...], list[dict[str, Any]]]:
        resolved: list[ArtifactRecord] = []
        missing: list[dict[str, Any]] = []
        for input_spec in stage.input_specs:
            matches = self._match_input(input_spec, available_artifacts)
            if not matches:
                if input_spec.required:
                    missing.append(
                        {
                            "path": input_spec.path,
                            "source_stage_id": input_spec.source_stage_id,
                            "reason": "not_available",
                        }
                    )
                continue
            if len(matches) > 1:
                missing.append(
                    {
                        "path": input_spec.path,
                        "source_stage_id": input_spec.source_stage_id,
                        "reason": "ambiguous",
                        "matches": [
                            {"stage_id": match.stage_id, "path": match.path}
                            for match in matches
                        ],
                    }
                )
                continue
            record = matches[0]
            if input_spec.schema_id is not None and record.schema_id != input_spec.schema_id:
                missing.append(
                    {
                        "path": input_spec.path,
                        "source_stage_id": input_spec.source_stage_id,
                        "reason": "schema_mismatch",
                        "expected_schema_id": input_spec.schema_id,
                        "actual_schema_id": record.schema_id,
                    }
                )
                continue
            resolved.append(record)
            self.event_bus.emit(
                "StageInputResolved",
                run=run,
                payload={
                    "stage_id": stage.id,
                    "input": input_spec.to_dict(),
                    "artifact": record.to_dict(),
                },
            )
        return tuple(resolved), missing

    @staticmethod
    def _match_input(
        input_spec: InputSpec,
        available_artifacts: dict[tuple[str, str], ArtifactRecord],
    ) -> list[ArtifactRecord]:
        if input_spec.source_stage_id is not None:
            record = available_artifacts.get((input_spec.source_stage_id, input_spec.path))
            return [record] if record is not None else []
        return [
            record for (_stage_id, path), record in available_artifacts.items()
            if path == input_spec.path
        ]

    def _schema_failure_outcome(
        self,
        *,
        run: RunSpec,
        stage: StageSpec,
        exc: SchemaValidationError,
    ) -> StageOutcome:
        result = exc.result
        self._request_decision(
            run=run,
            stage_id=stage.id,
            reason="artifact_schema_failed",
            safe_next_actions=["rewrite-stage", "rerun-stage", "revise-schema", "abort"],
            metadata={"validation": [result.__dict__]},
        )
        return StageOutcome(
            stage_id=stage.id,
            validation=(result,),
            status="human_decision_required",
            route_conditions=self._route_conditions(None),
        )

    @staticmethod
    def _next_stage_id(stage: StageSpec) -> str | None:
        next_routes = [
            route for route in stage.routes
            if route.kind == "next" and route.condition == "always"
        ]
        return next_routes[0].target if next_routes else None

    @staticmethod
    def _route_conditions(handler_result: dict[str, Any] | None) -> tuple[str, ...]:
        if not handler_result:
            return ("always",)
        values = handler_result.get("route_conditions")
        if values is None:
            values = handler_result.get("routes")
        if values is None:
            value = handler_result.get("route_condition") or handler_result.get("route")
            values = [value] if value else ["always"]
        if isinstance(values, str):
            values = [values]
        conditions = tuple(str(value) for value in values if value)
        return conditions or ("always",)

    def _schedule_next(
        self,
        *,
        run: RunSpec,
        stage: StageSpec,
        outcome: StageOutcome,
        queue: list[str],
        completed: set[str],
        visit_counts: dict[str, int],
    ) -> tuple[tuple[str, ...], bool]:
        scheduled: list[str] = []
        blocked = False
        for route in stage.routes:
            if route.condition != "always" and route.condition not in outcome.route_conditions:
                continue
            if route.kind == "failure":
                continue
            if route.kind == "loop":
                max_visits = route.max_visits or stage.failure.max_retries + 1
                if visit_counts.get(route.target, 0) >= max_visits:
                    self.event_bus.emit(
                        "LoopLimitReached",
                        run=run,
                        payload={
                            "source_stage_id": stage.id,
                            "target_stage_id": route.target,
                            "condition": route.condition,
                            "max_visits": max_visits,
                        },
                    )
                    self._request_decision(
                        run=run,
                        stage_id=stage.id,
                        reason="loop_limit_reached",
                        safe_next_actions=["rewrite-stage", "rerun-stage", "approve", "abort"],
                    )
                    blocked = True
                    continue
                self._select_route(run, stage.id, route.target, route.kind, route.condition)
                self._enqueue(queue, route.target, scheduled, allow_completed=True)
                continue
            if route.kind == "join":
                self._select_route(run, stage.id, route.target, route.kind, route.condition)
                required_sources = self._join_sources(run, route.target)
                waiting_for = sorted(source for source in required_sources if source not in completed)
                if waiting_for:
                    self.event_bus.emit(
                        "JoinWaiting",
                        run=run,
                        payload={
                            "join_stage_id": route.target,
                            "completed_source_id": stage.id,
                            "waiting_for_stage_ids": waiting_for,
                        },
                    )
                    continue
                self._enqueue(queue, route.target, scheduled, allow_completed=False, completed=completed)
                continue
            self._select_route(run, stage.id, route.target, route.kind, route.condition)
            self._enqueue(queue, route.target, scheduled, allow_completed=False, completed=completed)
        return tuple(scheduled), blocked

    def _select_route(self, run: RunSpec, source: str, target: str, kind: str, condition: str) -> None:
        self.event_bus.emit(
            "RouteSelected",
            run=run,
            payload={
                "source_stage_id": source,
                "target_stage_id": target,
                "kind": kind,
                "condition": condition,
            },
        )

    def _request_decision(
        self,
        *,
        run: RunSpec,
        stage_id: str,
        reason: str,
        safe_next_actions: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decision = self.decision_queue.request(
            run=run,
            stage_id=stage_id,
            reason=reason,
            safe_next_actions=safe_next_actions,
            metadata=metadata,
        )
        self.event_bus.emit(
            "HumanDecisionRequired",
            run=run,
            payload={
                "decision_id": decision.id,
                "stage_id": stage_id,
                "reason": reason,
                "safe_next_actions": list(decision.safe_next_actions),
                **(metadata or {}),
            },
        )

    def decide(self, decision_id: str, *, approved: bool, actor: str = "user") -> dict[str, Any]:
        decision = self.decision_queue.decide(decision_id, approved=approved, actor=actor)
        try:
            run = self._runs[decision.run_id]
        except KeyError as exc:
            raise KeyError(f"Run not found for decision: {decision.run_id}") from exc
        self.event_bus.emit(
            "ApprovalDecided",
            run=run,
            payload={
                "decision_id": decision.id,
                "stage_id": decision.stage_id,
                "status": decision.status,
                "actor": actor,
            },
        )
        return decision.to_dict()

    def _has_approved_decision(self, run_id: str, stage_id: str, reason: str) -> bool:
        return any(
            decision.run_id == run_id
            and decision.stage_id == stage_id
            and decision.reason == reason
            and decision.status == "approved"
            for decision in self.decision_queue.decisions.values()
        )

    def resume(self, decision_id: str, handlers: dict[str, StageHandler]) -> list[StageOutcome]:
        decision = self.decision_queue.decisions.get(decision_id)
        if decision is None:
            raise KeyError(f"Decision not found: {decision_id}")
        if decision.status != "approved":
            raise ValueError(f"Decision must be approved before resume: {decision_id}")
        try:
            run = self._runs[decision.run_id]
        except KeyError as exc:
            raise KeyError(f"Run not found for decision: {decision.run_id}") from exc
        checkpoint = self.checkpoint_store.latest(decision.run_id)
        if decision.reason == "pause_after_stage":
            artifacts = [
                artifact for artifact in checkpoint.available_artifacts
                if artifact.stage_id == decision.stage_id
            ]
            self.event_bus.emit(
                "StageCompleted",
                run=run,
                payload={
                    "stage_id": decision.stage_id,
                    "artifacts": [artifact.to_dict() for artifact in artifacts],
                },
            )
        return self.run(run, handlers, checkpoint=checkpoint)

    def _checkpoint(
        self,
        *,
        run: RunSpec,
        blocked_stage_id: str,
        reason: str,
        queue: list[str],
        completed_stage_ids: list[str],
        available_artifacts: dict[tuple[str, str], ArtifactRecord],
        visit_counts: dict[str, int],
    ) -> RunCheckpoint:
        checkpoint = self.checkpoint_store.save(
            run=run,
            blocked_stage_id=blocked_stage_id,
            reason=reason,
            queue=queue,
            completed_stage_ids=completed_stage_ids,
            available_artifacts=available_artifacts.values(),
            visit_counts=visit_counts,
        )
        self.event_bus.emit(
            "RunCheckpointed",
            run=run,
            payload=checkpoint.to_dict(),
        )
        return checkpoint

    @staticmethod
    def _enqueue(
        queue: list[str],
        target: str,
        scheduled: list[str],
        *,
        allow_completed: bool,
        completed: set[str] | None = None,
    ) -> None:
        if target in queue:
            return
        if not allow_completed and completed is not None and target in completed:
            return
        queue.append(target)
        scheduled.append(target)

    @staticmethod
    def _join_sources(run: RunSpec, target: str) -> set[str]:
        return {
            stage.id
            for stage in run.graph.stages
            for route in stage.routes
            if route.kind == "join" and route.target == target
        }


def non_empty_artifact(path: str) -> Callable[[RuntimeContext, StageSpec, tuple[ArtifactRecord, ...]], ValidationResult]:
    """Build a validator that requires a specific declared artifact to be non-empty."""

    def validate(context: RuntimeContext, stage: StageSpec, artifacts: tuple[ArtifactRecord, ...]) -> ValidationResult:
        matches = [artifact for artifact in artifacts if artifact.path == path]
        passed = bool(matches and matches[0].size_bytes > 0)
        return ValidationResult(
            validator_id=f"non_empty:{path}",
            passed=passed,
            message=f"{path} is non-empty" if passed else f"{path} is missing or empty",
            details={"path": path},
        )

    return validate


def artifact(
    path: str,
    kind: str = "markdown",
    *,
    required: bool = True,
    role: str = "deliverable",
    schema_id: str | None = None,
    claim_ids: tuple[str, ...] = (),
    evidence_links: tuple[EvidenceLink, ...] = (),
) -> ArtifactSpec:
    return ArtifactSpec(  # type: ignore[arg-type]
        path=path,
        kind=kind,
        required=required,
        role=role,
        schema_id=schema_id,
        claim_ids=claim_ids,
        evidence_links=evidence_links,
    )
