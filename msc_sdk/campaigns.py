"""Campaign-level SDK helpers backed by the local campaign store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .campaign_store import CampaignStore
from .research_tiers import TARGET_RESEARCH_TEMPLATE
from .read_models import ArtifactReadModel, BudgetReadModel, CampaignReadModel, LogReadModel, StageReadModel


class CampaignClient:
    """Inspect and mutate repo-local campaign bundles through SQLite-backed APIs."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)
        self.store = CampaignStore(self.root)

    def list(self) -> list[dict[str, Any]]:
        return self.store.list_campaigns()

    def inspect(self, ref: str | Path) -> CampaignReadModel:
        return campaign_model_from_dict(self.store.inspect_dict(ref))

    def status(self, ref: str | Path) -> dict[str, Any]:
        campaign = self.inspect(ref)
        return {
            "campaign": campaign.campaign_id,
            "status": campaign.status,
            "budget": campaign.budget.to_dict(),
            "stages": [stage.to_dict() for stage in campaign.stages],
        }

    def workspace(self, ref: str | Path) -> dict[str, Any]:
        return self.store.workspace_read_model(ref)

    def graph(self, ref: str | Path) -> dict[str, Any]:
        return self.store.graph(ref)

    def artifacts(self, ref: str | Path, stage_id: str | None = None) -> dict[str, Any]:
        return self.store.artifacts(ref, stage_id)

    def create(
        self,
        *,
        title: str,
        objective: str,
        template: str = TARGET_RESEARCH_TEMPLATE,
        budget: float = 1.0,
        tier: str = "standard",
        output_format: str = "markdown",
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.create_campaign(
            title=title,
            objective=objective,
            template=template,
            budget=budget,
            tier=tier,
            output_format=output_format,
            actor=actor,
        )

    def delete(self, campaign_ref: str | Path, *, actor: str = "user", delete_files: bool = True) -> dict[str, Any]:
        return self.store.delete_campaign(campaign_ref, actor=actor, delete_files=delete_files)

    def import_bundle(self, bundle_path: str | Path, *, actor: str = "user") -> dict[str, Any]:
        return self.store.import_bundle(bundle_path, actor=actor)

    def export_bundle(self, campaign_ref: str | Path, *, actor: str = "user") -> dict[str, Any]:
        campaign_id = self.store.resolve_ref(campaign_ref)
        return self.store.export_bundle(campaign_id, actor=actor)

    def events(self, campaign_ref: str | Path, *, limit: int | None = None) -> dict[str, Any]:
        return self.store.events(campaign_ref, limit=limit)

    def feedback(
        self,
        campaign_ref: str | Path,
        *,
        text: str,
        node_id: str | None = None,
        artifact_id: str | None = None,
        artifact_path: str | None = None,
        decision_id: str | None = None,
        run_id: str | None = None,
        feedback_type: str = "feedback",
        actor: str = "user",
    ) -> dict[str, Any]:
        metadata = {key: value for key, value in {
            "node_id": node_id,
            "artifact_id": artifact_id,
            "artifact_path": artifact_path,
            "decision_id": decision_id,
        }.items() if value}
        return self.store.record_instruction(
            campaign_ref,
            text=text,
            instruction_type=feedback_type,
            run_id=run_id,
            direction="to_campaign",
            actor=actor,
            metadata=metadata,
        )

    def link_context(
        self,
        campaign_ref: str | Path,
        *,
        note: str,
        target_scope: str = "campaign",
        node_id: str | None = None,
        artifact_id: str | None = None,
        artifact_path: str | None = None,
        decision_id: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.link_context(
            campaign_ref,
            note=note,
            target_scope=target_scope,
            node_id=node_id,
            artifact_id=artifact_id,
            artifact_path=artifact_path,
            decision_id=decision_id,
            actor=actor,
        )

    def list_context_links(self, campaign_ref: str | Path) -> dict[str, Any]:
        return self.store.list_context_links(campaign_ref)

    def update_context_link(
        self,
        campaign_ref: str | Path,
        link_id: str,
        *,
        status: str,
        note: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.update_context_link(campaign_ref, link_id, status=status, note=note, actor=actor)

    def approve_graph(self, campaign_ref: str | Path, graph_version: int, *, actor: str = "user") -> dict[str, Any]:
        campaign_id = self.store.resolve_ref(campaign_ref)
        return self.store.approve_graph(campaign_id, graph_version, actor=actor)

    def explain_node(self, campaign_ref: str | Path, node_id: str) -> dict[str, Any]:
        return self.store.explain_node(campaign_ref, node_id)

    def propose_graph_change(
        self,
        campaign_ref: str | Path,
        *,
        change_type: str,
        instruction: str,
        node_id: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.propose_graph_change(
            campaign_ref,
            change_type=change_type,
            instruction=instruction,
            node_id=node_id,
            actor=actor,
        )

    def approve(self, approval_id: str, *, actor: str = "user") -> dict[str, Any]:
        return self.store.decide_approval(approval_id, approved=True, actor=actor)

    def reject(self, approval_id: str, *, actor: str = "user") -> dict[str, Any]:
        return self.store.decide_approval(approval_id, approved=False, actor=actor)

    def pause(self, campaign_ref: str | Path, *, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.store.pause(campaign_ref, reason=reason, actor=actor)

    def resume(self, campaign_ref: str | Path, *, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.store.resume(campaign_ref, reason=reason, actor=actor)

    def stop(self, campaign_ref: str | Path, *, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.store.stop(campaign_ref, reason=reason, actor=actor)

    def reroute(self, campaign_ref: str | Path, *, from_node: str, to_node: str, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.store.reroute(campaign_ref, from_node=from_node, to_node=to_node, reason=reason, actor=actor)

    def rewrite_stage(self, campaign_ref: str | Path, node_id: str, *, instruction: str, actor: str = "user") -> dict[str, Any]:
        return self.store.rewrite_stage(campaign_ref, node_id, instruction=instruction, actor=actor)

    def rerun_stage(self, campaign_ref: str | Path, node_id: str, *, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.store.rerun_stage(campaign_ref, node_id, reason=reason, actor=actor)

    def rewind(
        self,
        campaign_ref: str | Path,
        node_id: str,
        *,
        reason: str = "",
        decision_id: str | None = None,
        run_id: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.rewind(
            campaign_ref,
            node_id,
            reason=reason,
            decision_id=decision_id,
            run_id=run_id,
            actor=actor,
        )

    def summarize_artifacts(self, campaign_ref: str | Path) -> dict[str, Any]:
        return self.store.summarize_artifacts(campaign_ref)

    def request_evidence(
        self,
        campaign_ref: str | Path,
        *,
        question: str,
        node_id: str | None = None,
        artifact_path: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.request_evidence(
            campaign_ref,
            question=question,
            node_id=node_id,
            artifact_path=artifact_path,
            actor=actor,
        )

    def inspect_budget(self, campaign_ref: str | Path) -> dict[str, Any]:
        return self.store.inspect_budget(campaign_ref)

    def diagnose_execution(self, campaign_ref: str | Path) -> dict[str, Any]:
        return self.store.diagnose_execution(campaign_ref)

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
        return self.store.start_native_execution(
            campaign_ref,
            tier=tier,
            budget=budget,
            output_format=output_format,
            math_enabled=math_enabled,
            counsel_enabled=counsel_enabled,
            human_gates=human_gates,
            force_duality_fail=force_duality_fail,
            actor=actor,
        )

    def continue_execution(self, campaign_ref: str | Path, *, actor: str = "user") -> dict[str, Any]:
        return self.store.continue_native_execution(campaign_ref, actor=actor)

    def propose_repair(
        self,
        campaign_ref: str | Path,
        *,
        node_id: str | None = None,
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.propose_repair(campaign_ref, node_id=node_id, reason=reason, actor=actor)

    def change_tier_model(
        self,
        campaign_ref: str | Path,
        *,
        tier: str | None = None,
        model: str | None = None,
        node_id: str | None = None,
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.change_tier_model(
            campaign_ref,
            tier=tier,
            model=model,
            node_id=node_id,
            reason=reason,
            actor=actor,
        )

def campaign_model_from_dict(data: dict[str, Any]) -> CampaignReadModel:
    return CampaignReadModel(
        campaign_id=data["campaign_id"],
        path=data["path"],
        name=data.get("name"),
        workspace_root=data.get("workspace_root"),
        status=data.get("status", "unknown"),
        budget=budget_model(data.get("budget") or {}),
        stages=[stage_model(stage) for stage in data.get("stages") or []],
        metadata=data.get("metadata") or {},
        provenance=data.get("provenance") or {},
    )


def stage_model(data: dict[str, Any]) -> StageReadModel:
    return StageReadModel(
        stage_id=data["stage_id"],
        status=data.get("status", "unknown"),
        workspace=data.get("workspace"),
        current_attempt=data.get("current_attempt"),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        fail_reason=data.get("fail_reason"),
        required_artifacts=[artifact_model(item) for item in data.get("required_artifacts") or []],
        optional_artifacts=[artifact_model(item) for item in data.get("optional_artifacts") or []],
        budget=budget_model(data.get("budget") or {}),
        logs=[log_model(item) for item in data.get("logs") or []],
        metadata=data.get("metadata") or {},
    )


def artifact_model(data: dict[str, Any]) -> ArtifactReadModel:
    return ArtifactReadModel(
        id=data["id"],
        path=data.get("path") or "",
        kind=data.get("kind") or data.get("type") or "artifact",
        exists=bool(data.get("exists")),
        size_bytes=data.get("size_bytes"),
        modified_at=data.get("modified_at"),
        source_role=data.get("source_role", "raw"),
        required=bool(data.get("required")),
        metadata=data.get("metadata") or {},
    )


def budget_model(data: dict[str, Any]) -> BudgetReadModel:
    return BudgetReadModel(
        total_usd=data.get("total_usd"),
        limit_usd=data.get("limit_usd") or data.get("campaign_limit_usd"),
        ledger_path=data.get("ledger_path"),
        state_path=data.get("state_path"),
        by_model=data.get("by_model") or {},
        metadata=data.get("metadata") or {},
    )


def log_model(data: dict[str, Any]) -> LogReadModel:
    return LogReadModel(
        path=data.get("path", ""),
        size_bytes=int(data.get("size_bytes") or 0),
        modified_at=data.get("modified_at"),
        stage=data.get("stage"),
    )
