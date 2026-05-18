"""SDK-native campaign execution.

This module is the production path away from legacy LangGraph control truth.
The legacy runner may still exist as an adapter, but this executor treats the
SDK graph, kernel events, artifact contracts, and campaign read models as the
authoritative product surface.
"""

from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from pathlib import Path
from typing import Any

from .campaign_store import CampaignStore, file_checksum, json_dumps, now_iso, path_is_inside
from .kernel import (
    ArtifactRecord,
    BudgetPolicy,
    CheckpointStore,
    DecisionQueue,
    DecisionRecord,
    EventRecord,
    EvidenceLink,
    GraphSpec,
    ModelPolicy,
    RunCheckpoint,
    RunSpec,
    StageSpec,
    build_kernel_native_research_kernel,
)
from .kernel.models import stable_id
from .research_tiers import TARGET_MODEL_DEFAULTS, tier_policy
from .stage_contracts import compile_kernel_graph


NATIVE_RUNTIME = "sdk_native"


class StoreBackedKernelEventBus:
    """Persist kernel events into ``CampaignStore`` as product truth."""

    def __init__(self, store: CampaignStore, *, actor: str = NATIVE_RUNTIME) -> None:
        self.store = store
        self.actor = actor

    def emit(self, event_type: str, *, run: RunSpec, payload: dict[str, Any]) -> EventRecord:
        payload = self._product_payload(event_type, run=run, payload=payload)
        with self.store.connect() as conn:
            event = self.store._append_event(
                conn,
                campaign_id=run.campaign_id,
                event_type=event_type,
                actor=self.actor,
                payload=payload,
            )
            self._apply_projection_side_effects(conn, event_type=event_type, run=run, payload=payload)
        return EventRecord(
            id=str(event["id"]),
            type=event_type,
            run_id=run.id,
            campaign_id=run.campaign_id,
            created_at=str(event["created_at"]),
            payload=event["payload"],
        )

    def _product_payload(self, event_type: str, *, run: RunSpec, payload: dict[str, Any]) -> dict[str, Any]:
        if event_type != "ArtifactIndexed":
            return payload
        artifact = dict(payload.get("artifact") or {})
        if not artifact:
            return payload
        absolute_path = Path(str(artifact.get("absolute_path") or ""))
        artifact_path = str(artifact.get("path") or "")
        workspace = None
        if absolute_path and artifact_path:
            stage_workspace = _stage_workspace_from_artifact(absolute_path, artifact_path)
            workspace = str(stage_workspace)
            if path_is_inside(stage_workspace, self.store.root):
                workspace = str(stage_workspace.resolve().relative_to(self.store.root.resolve()))
        return {
            **payload,
            "artifact_id": f"{run.campaign_id}:{run.id}:{artifact.get('stage_id')}:{artifact_path}",
            "stage_id": artifact.get("stage_id") or payload.get("stage_id"),
            "path": artifact_path,
            "kind": artifact.get("kind"),
            "required": artifact.get("required"),
            "producer_node_id": artifact.get("stage_id") or payload.get("stage_id"),
            "workspace": workspace,
            "run_id": run.id,
            "size_bytes": artifact.get("size_bytes"),
            "checksum": artifact.get("checksum"),
            "source_role": NATIVE_RUNTIME,
            "metadata": {
                "runtime": NATIVE_RUNTIME,
                "run_id": run.id,
                "source_role": NATIVE_RUNTIME,
                "audience": artifact.get("role") or "deliverable",
            },
        }

    def _apply_projection_side_effects(
        self,
        conn: sqlite3.Connection,
        *,
        event_type: str,
        run: RunSpec,
        payload: dict[str, Any],
    ) -> None:
        if event_type in {"CampaignExecutionStarted", "CampaignExecutionResumed"}:
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("running", now_iso(), run.campaign_id))
            if event_type == "CampaignExecutionResumed":
                for stage_id in payload.get("completed_stage_ids") or []:
                    self._set_node_status(
                        conn,
                        campaign_id=run.campaign_id,
                        node_id=str(stage_id),
                        status="completed",
                    )
            return
        if event_type == "StageStarted":
            self._set_node_status(conn, campaign_id=run.campaign_id, node_id=str(payload.get("stage_id")), status="running")
            return
        if event_type == "ArtifactIndexed":
            self._record_artifact_row(conn, run=run, payload=payload)
            return
        if event_type == "StageCompleted":
            self._set_node_status(conn, campaign_id=run.campaign_id, node_id=str(payload.get("stage_id")), status="completed")
            return
        if event_type == "HumanDecisionRequired":
            stage_id = str(payload.get("stage_id") or payload.get("target_id") or "")
            self._set_node_status(conn, campaign_id=run.campaign_id, node_id=stage_id, status="human_decision_required")
            self._record_kernel_decision(conn, run=run, payload=payload)
            conn.execute(
                "UPDATE campaigns SET status=?, updated_at=? WHERE id=?",
                ("human_decision_required", now_iso(), run.campaign_id),
            )
            return
        if event_type == "CampaignExecutionCompleted":
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("completed", now_iso(), run.campaign_id))
            return
        if event_type == "CampaignExecutionFailed":
            status = str(payload.get("status") or "failed")
            conn.execute(
                "UPDATE campaigns SET status=?, updated_at=? WHERE id=?",
                ("human_decision_required" if status == "human_decision_required" else "failed", now_iso(), run.campaign_id),
            )

    def _set_node_status(
        self,
        conn: sqlite3.Connection,
        *,
        campaign_id: str,
        node_id: str,
        status: str,
    ) -> None:
        if not node_id:
            return
        snapshot = conn.execute(
            "SELECT * FROM graph_snapshots WHERE campaign_id=? ORDER BY version DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        if snapshot is not None:
            graph = json.loads(snapshot["graph_json"])
            for node in graph.get("nodes", []):
                if node.get("id") == node_id:
                    node["status"] = status
                    break
            conn.execute(
                "UPDATE graph_snapshots SET graph_json=? WHERE id=?",
                (json_dumps(graph), snapshot["id"]),
            )
            conn.execute(
                "UPDATE graph_nodes SET status=? WHERE campaign_id=? AND graph_version=? AND node_id=?",
                (status, campaign_id, snapshot["version"], node_id),
            )
        self.store._append_event(
            conn,
            campaign_id=campaign_id,
            event_type="GraphNodeStatusChanged",
            actor=self.actor,
            payload={"node_id": node_id, "status": status},
        )

    def _record_artifact_row(self, conn: sqlite3.Connection, *, run: RunSpec, payload: dict[str, Any]) -> None:
        artifact = dict(payload.get("artifact") or {})
        stage_id = str(payload.get("stage_id") or artifact.get("stage_id") or "")
        artifact_path = str(artifact.get("path") or payload.get("path") or "")
        absolute_path = Path(str(artifact.get("absolute_path") or ""))
        if not stage_id or not artifact_path or not absolute_path.exists():
            return
        stage_workspace = _stage_workspace_from_artifact(absolute_path, artifact_path)
        workspace = str(stage_workspace)
        if path_is_inside(stage_workspace, self.store.root):
            workspace = str(stage_workspace.resolve().relative_to(self.store.root.resolve()))
        role = str(artifact.get("role") or "deliverable")
        required = bool(artifact.get("required", True))
        artifact_id = f"{run.campaign_id}:{run.id}:{stage_id}:{artifact_path}"
        metadata = {
            "workspace": workspace,
            "source_role": NATIVE_RUNTIME,
            "audience": role,
            "run_id": run.id,
            "runtime": NATIVE_RUNTIME,
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO artifacts
            (id, campaign_id, stage_id, path, kind, required, status, producer_node_id,
             size_bytes, checksum, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                run.campaign_id,
                stage_id,
                artifact_path,
                str(artifact.get("kind") or Path(artifact_path).suffix.replace(".", "") or "artifact"),
                1 if required else 0,
                "existing",
                stage_id,
                absolute_path.stat().st_size,
                str(artifact.get("checksum") or file_checksum(absolute_path)),
                json_dumps(metadata),
            ),
        )

    def _record_kernel_decision(self, conn: sqlite3.Connection, *, run: RunSpec, payload: dict[str, Any]) -> None:
        decision_id = str(payload.get("decision_id") or "")
        if not decision_id:
            return
        existing = conn.execute("SELECT id FROM approvals WHERE id=?", (decision_id,)).fetchone()
        if existing is not None:
            return
        stage_id = str(payload.get("stage_id") or "")
        reason = str(payload.get("reason") or "human_decision_required")
        metadata = {
            "runtime": NATIVE_RUNTIME,
            "kernel_decision_id": decision_id,
            "run_id": run.id,
            "stage_id": stage_id,
            "reason": reason,
            "safe_next_actions": list(payload.get("safe_next_actions") or []),
            "evidence": payload.get("evidence") or [],
            "failed_lenses": payload.get("failed_lenses") or [],
            "objections": payload.get("objections") or [],
        }
        self.store._create_approval(
            conn,
            campaign_id=run.campaign_id,
            target_type="kernel_decision",
            target_id=stage_id,
            actor=self.actor,
            metadata=metadata,
            approval_id=decision_id,
        )


class NativeCampaignExecutor:
    """Run and resume campaigns through the SDK-native kernel."""

    def __init__(self, store: CampaignStore) -> None:
        self.store = store

    def start(
        self,
        campaign_ref: str | Path,
        *,
        tier: str | None = None,
        budget: float | None = None,
        output_format: str | None = None,
        math_enabled: bool = False,
        counsel_enabled: bool = False,
        human_gates: bool = True,
        force_duality_fail: bool = False,
        actor: str = "user",
    ) -> dict[str, Any]:
        campaign_id = self.store.resolve_ref(campaign_ref)
        graph = self.store.graph(campaign_id)
        if graph.get("state") != "approved":
            raise RuntimeError("Campaign graph must be approved before SDK-native start.")
        campaign = self.store.get_campaign(campaign_id)
        graph_version = int(graph.get("version") or 1)
        metadata = {
            "runtime": NATIVE_RUNTIME,
            "tier": tier or campaign.get("tier") or "lean",
            "budget": float(budget if budget is not None else campaign.get("budget_cap_usd") or 0),
            "output_format": output_format or campaign.get("output_format") or "markdown",
            "math_enabled": bool(math_enabled),
            "counsel_enabled": bool(counsel_enabled),
            "human_gates": bool(human_gates),
            "force_duality_fail": bool(force_duality_fail),
        }
        run_id = stable_id(campaign_id, NATIVE_RUNTIME, str(graph_version), now_iso())
        self.store.append_event(
            campaign_id,
            "CampaignExecutionPrepared",
            actor=actor,
            payload={
                "execution_id": run_id,
                "run_id": run_id,
                "runtime": NATIVE_RUNTIME,
                "graph_version": graph_version,
                "metadata": metadata,
            },
        )
        return self._run_segment(
            campaign_id,
            run_id=run_id,
            metadata=metadata,
            checkpoint=None,
        )

    def continue_(
        self,
        campaign_ref: str | Path,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        campaign_id = self.store.resolve_ref(campaign_ref)
        pending = [
            decision for decision in self.store.workspace_read_model(campaign_id)["pending_decisions"]
            if decision.get("target_type") == "kernel_decision"
        ]
        if pending:
            raise RuntimeError("A SDK-native decision is still pending approval.")
        checkpoint = self._latest_checkpoint(campaign_id)
        run_id = checkpoint.run_id
        metadata = self._run_metadata(campaign_id, run_id)
        metadata.setdefault("runtime", NATIVE_RUNTIME)
        self.store.append_event(
            campaign_id,
            "CampaignContinueRequested",
            actor=actor,
            payload={"run_id": run_id, "runtime": NATIVE_RUNTIME, "checkpoint_id": checkpoint.id},
        )
        return self._run_segment(
            campaign_id,
            run_id=run_id,
            metadata=metadata,
            checkpoint=checkpoint,
        )

    def _run_segment(
        self,
        campaign_id: str,
        *,
        run_id: str,
        metadata: dict[str, Any],
        checkpoint: RunCheckpoint | None,
    ) -> dict[str, Any]:
        campaign = self.store.get_campaign(campaign_id)
        graph = compile_kernel_graph(
            graph_id=f"{campaign_id}:target_research:native",
            template="target_research",
            budget=float(metadata.get("budget") or campaign.get("budget_cap_usd") or 0),
        )
        graph = _apply_native_policy(
            graph,
            math_enabled=bool(metadata.get("math_enabled")),
            human_gates=bool(metadata.get("human_gates", True)),
            tier=str(metadata.get("tier") or campaign.get("tier") or "lean"),
            counsel_enabled=bool(metadata.get("counsel_enabled")),
        )
        run = RunSpec(
            id=run_id,
            campaign_id=campaign_id,
            objective=str(campaign.get("objective") or ""),
            graph=graph,
            workspace=self.store.root / "results" / campaign_id / "runs" / run_id,
            budget=BudgetPolicy(max_usd=float(metadata.get("budget") or 0), spend_allowed=True),
            metadata=dict(metadata),
        )
        kernel = build_kernel_native_research_kernel(graph)
        kernel.event_bus = StoreBackedKernelEventBus(self.store)
        kernel.decision_queue = self._approved_decision_queue(campaign_id, run_id)
        kernel.checkpoint_store = CheckpointStore()
        outcomes = kernel.run(run, checkpoint=checkpoint)
        self.store.refresh_artifact_files(campaign_id)
        self.store.write_snapshot(campaign_id)
        self.store.export_bundle(campaign_id, actor=NATIVE_RUNTIME)
        return {
            "ok": True,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "runtime": NATIVE_RUNTIME,
            "outcomes": [
                {
                    "stage_id": outcome.stage_id,
                    "status": outcome.status,
                    "scheduled_stage_ids": list(outcome.scheduled_stage_ids),
                    "route_conditions": list(outcome.route_conditions),
                }
                for outcome in outcomes
            ],
            "workspace": self.store.workspace_read_model(campaign_id),
        }

    def _approved_decision_queue(self, campaign_id: str, run_id: str) -> DecisionQueue:
        queue = DecisionQueue()
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE campaign_id=? AND status=?",
                (campaign_id, "approved"),
            ).fetchall()
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if metadata.get("runtime") != NATIVE_RUNTIME:
                continue
            if metadata.get("run_id") != run_id:
                continue
            decision_id = str(metadata.get("kernel_decision_id") or row["id"])
            queue.decisions[decision_id] = DecisionRecord(
                id=decision_id,
                run_id=run_id,
                campaign_id=campaign_id,
                stage_id=str(metadata.get("stage_id") or row["target_id"]),
                reason=str(metadata.get("reason") or "human_decision_required"),
                status="approved",
                safe_next_actions=tuple(metadata.get("safe_next_actions") or []),
                created_at=str(row["created_at"]),
                decided_at=str(row["decided_at"] or now_iso()),
                actor=str(row["actor"] or "user"),
                metadata=metadata,
            )
        return queue

    def _latest_checkpoint(self, campaign_id: str) -> RunCheckpoint:
        checkpoints = [
            event for event in self.store.events(campaign_id)["events"]
            if event["type"] == "CampaignExecutionCheckpointed"
        ]
        if not checkpoints:
            raise RuntimeError("No SDK-native checkpoint is available to continue.")
        payload = checkpoints[-1]["payload"]
        return _checkpoint_from_payload(payload)

    def _run_metadata(self, campaign_id: str, run_id: str) -> dict[str, Any]:
        for event in reversed(self.store.events(campaign_id)["events"]):
            if event["type"] not in {"CampaignExecutionStarted", "CampaignExecutionResumed", "CampaignExecutionPrepared"}:
                continue
            payload = event["payload"]
            if str(payload.get("run_id") or payload.get("execution_id")) != run_id:
                continue
            return dict(payload.get("metadata") or {})
        return {}


def _apply_native_policy(
    graph: GraphSpec,
    *,
    math_enabled: bool,
    human_gates: bool,
    tier: str,
    counsel_enabled: bool,
) -> GraphSpec:
    tier_config = tier_policy(tier)
    stages: list[StageSpec] = []
    for stage in graph.stages:
        routes = list(stage.routes)
        if not math_enabled:
            if stage.id == "milestone_goals":
                routes = [route for route in routes if route.target != "theory_track"]
            elif stage.id == "theory_track":
                routes = []
        pause_before = False
        pause_after = False
        if human_gates:
            pause_after = stage.id == "milestone_goals"
            pause_before = stage.id == "resource_preparation_agent"
        model_policy = _model_policy_for_stage(
            stage,
            tier=tier_config.id,
            counsel_enabled=counsel_enabled,
        )
        stages.append(
            replace(
                stage,
                routes=tuple(routes),
                pause_before=pause_before,
                pause_after=pause_after,
                model_policy=model_policy,
                adapter_id=f"{NATIVE_RUNTIME}.{stage.id}",
                metadata={
                    **dict(stage.metadata),
                    "adapter": NATIVE_RUNTIME,
                    "native_runtime": True,
                    "tier": tier_config.id,
                    "execution_model_policy": {
                        "allowed_model_ids": list(model_policy.allowed_model_ids),
                        "max_input_tokens": model_policy.max_input_tokens,
                        "max_output_tokens": model_policy.max_output_tokens,
                        "structured_output_required": model_policy.structured_output_required,
                    },
                },
            )
        )
    native = GraphSpec(id=graph.id, stages=tuple(stages), entry_stage_id=graph.entry_stage_id)
    native.validate()
    return native


def _model_policy_for_stage(
    stage: StageSpec,
    *,
    tier: str,
    counsel_enabled: bool,
) -> ModelPolicy:
    if tier == "scaffold":
        return ModelPolicy()

    if tier == "lean":
        return ModelPolicy(
            allowed_model_ids=("deepseek-chat",),
            max_input_tokens=16_000,
            max_output_tokens=2_000,
        )

    if stage.id == "persona_council":
        persona = TARGET_MODEL_DEFAULTS["persona"]
        model_ids = (
            str(persona["practical_compass"]),
            str(persona["rigor_novelty"]),
            str(persona["narrative_architect"]),
        )
        if tier == "ultra":
            model_ids = (*model_ids, str(persona["empirical_grounding"]))
        return ModelPolicy(allowed_model_ids=_unique_models(model_ids), max_input_tokens=32_000, max_output_tokens=4_000)

    if stage.id == "duality_check":
        return ModelPolicy(
            allowed_model_ids=(str(TARGET_MODEL_DEFAULTS["duality_check"]),),
            max_input_tokens=32_000,
            max_output_tokens=4_000,
            structured_output_required=True,
        )

    if counsel_enabled and stage.council_policy.kind == "model_council":
        council = TARGET_MODEL_DEFAULTS["model_council"]
        return ModelPolicy(
            allowed_model_ids=tuple(str(model_id) for model_id in council["members"]),
            max_input_tokens=32_000,
            max_output_tokens=4_000,
        )

    if tier in {"serious", "ultra"}:
        return ModelPolicy(allowed_model_ids=("claude-sonnet-4-6",), max_input_tokens=32_000, max_output_tokens=4_000)

    return ModelPolicy(allowed_model_ids=("deepseek-chat",), max_input_tokens=16_000, max_output_tokens=2_000)


def _unique_models(model_ids: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for model_id in model_ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        ordered.append(model_id)
    return tuple(ordered)


def _checkpoint_from_payload(payload: dict[str, Any]) -> RunCheckpoint:
    return RunCheckpoint(
        id=str(payload["id"]),
        run_id=str(payload["run_id"]),
        campaign_id=str(payload["campaign_id"]),
        blocked_stage_id=str(payload["blocked_stage_id"]),
        reason=str(payload["reason"]),
        queue=tuple(str(item) for item in payload.get("queue") or []),
        completed_stage_ids=tuple(str(item) for item in payload.get("completed_stage_ids") or []),
        available_artifacts=tuple(_artifact_record_from_payload(item) for item in payload.get("available_artifacts") or []),
        visit_counts={str(key): int(value) for key, value in dict(payload.get("visit_counts") or {}).items()},
        created_at=str(payload.get("created_at") or now_iso()),
    )


def _artifact_record_from_payload(payload: dict[str, Any]) -> ArtifactRecord:
    return ArtifactRecord(
        id=str(payload["id"]),
        stage_id=str(payload["stage_id"]),
        path=str(payload["path"]),
        absolute_path=Path(str(payload["absolute_path"])),
        kind=str(payload["kind"]),
        role=str(payload.get("role") or "deliverable"),  # type: ignore[arg-type]
        required=bool(payload.get("required")),
        schema_id=payload.get("schema_id"),
        claim_ids=tuple(str(item) for item in payload.get("claim_ids") or []),
        evidence_links=tuple(EvidenceLink(**item) for item in payload.get("evidence_links") or []),
        size_bytes=int(payload.get("size_bytes") or 0),
        checksum=str(payload.get("checksum") or ""),
    )


def _stage_workspace_from_artifact(absolute_path: Path, artifact_path: str) -> Path:
    workspace = absolute_path.resolve()
    for _part in Path(artifact_path).parts:
        workspace = workspace.parent
    return workspace
