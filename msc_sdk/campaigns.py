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
        # Best-effort reconcile of any detached SLURM runs before we read.
        # This catches the case where the orchestrator (and the heartbeat)
        # both died between extension polls — without it, the UI would keep
        # showing a "running" status for a run that no longer exists.
        # Failures are deliberately swallowed: reconciliation is opportunistic
        # and the workspace read is the load-bearing operation.
        self._reconcile_detached_runs(ref)
        return self.store.workspace_read_model(ref)

    def _reconcile_detached_runs(self, ref: str | Path) -> None:
        import os, subprocess, sys
        try:
            campaign_id = self.store.resolve_ref(ref)
        except FileNotFoundError:
            return
        if os.environ.get("MSC_SKIP_RECONCILE") == "1":
            return
        try:
            # Python import overhead alone for `consortium.cli.main` is ~1.5s on
            # a cold cache; add an squeue call per open run on top. 20s is
            # comfortable; if even that isn't enough the reconcile just runs
            # next time workspace() is called.
            subprocess.run(
                [sys.executable, "-m", "consortium.cli.main", "hpc",
                 "--root", str(self.root), "reconcile", campaign_id, "--json"],
                capture_output=True, timeout=20, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

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

    def propose_repair(
        self,
        campaign_ref: str | Path,
        *,
        node_id: str | None = None,
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.propose_repair(campaign_ref, node_id=node_id, reason=reason, actor=actor)

    def update_budget_cap(
        self,
        campaign_ref: str | Path,
        new_cap_usd: float,
        *,
        actor: str = "user",
        reason: str = "",
    ) -> dict[str, Any]:
        return self.store.update_budget_cap(
            campaign_ref, new_cap_usd, actor=actor, reason=reason
        )

    def reset_node_statuses(
        self,
        campaign_ref: str | Path,
        *,
        new_status: str = "pending",
    ) -> dict[str, Any]:
        return self.store.reset_graph_node_statuses(campaign_ref, new_status=new_status)

    def get_council_deadlock(self, campaign_ref: str | Path) -> dict[str, Any] | None:
        """Return the latest persona_council_deadlock blob or None.

        The blob is the structured payload `_open_deadlock_decision` writes
        into campaign metadata when the council exhausts its safety cap.
        Shape: ``{decision_id, node_id, verdicts, rationales, proposal_path,
        attempts, opened_at, status}`` where ``status`` ∈
        ``{"open","accepted_as_is","edited","aborted"}``.
        """
        meta = self.store.get_campaign_metadata(campaign_ref) or {}
        blob = meta.get("persona_council_deadlock")
        return blob if isinstance(blob, dict) else None

    def resolve_council_deadlock(
        self,
        campaign_ref: str | Path,
        *,
        mode: str,
        actor: str = "user",
        reason: str = "",
    ) -> dict[str, Any]:
        """Mark a persona_council deadlock as resolved by a human.

        ``mode`` must be ``"accept_as_is"`` (use the latest synthesized draft
        verbatim) or ``"edited"`` (the user has already edited the file at
        ``proposal_path``; runner reads the file on resume). Either way:

        1. The deadlock blob's ``status`` flips to ``mode`` (still present
           in metadata) so the runner's ``_check_resolved_deadlock`` honors it
           on the next ``hpc submit``.
        2. The underlying approval row gets ``decide_approval(approved=True)``
           for the audit trail.
        3. A ``PersonaCouncilResolved`` event lands so the run-history drawer
           shows the boundary.

        Returns ``{ok, campaign_id, decision_id, mode, proposal_path}``.
        Raises if no open deadlock exists (idempotency guard).
        """
        if mode not in ("accept_as_is", "edited"):
            raise ValueError(f"mode must be 'accept_as_is' or 'edited', got {mode!r}")
        campaign_id = self.store.resolve_ref(campaign_ref)
        blob = self.get_council_deadlock(campaign_id)
        if not blob:
            raise FileNotFoundError(
                f"No persona_council_deadlock blob found for {campaign_id}; "
                f"nothing to resolve."
            )
        if blob.get("status") != "open":
            raise ValueError(
                f"persona_council_deadlock for {campaign_id} is already "
                f"resolved (status={blob.get('status')!r})."
            )
        # Flip the blob's status. We leave the blob in metadata so the
        # runner's pre-check can find it; it gets cleared after consumption.
        from datetime import datetime, timezone

        resolved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        new_blob = {**blob, "status": mode, "resolved_at": resolved_at,
                    "resolved_by": actor, "resolution_reason": reason or None}
        self.store.update_campaign_metadata(
            campaign_id, {"persona_council_deadlock": new_blob}, actor=actor
        )
        # Auto-approve the underlying decision for the audit chain.
        decision_id = blob.get("decision_id")
        if decision_id:
            try:
                self.store.decide_approval(decision_id, approved=True, actor=actor)
            except FileNotFoundError:
                pass  # approval row missing — best-effort
        # Standalone audit event.
        event = self.store.append_event(
            campaign_id, "PersonaCouncilResolved", actor=actor,
            payload={
                "decision_id": decision_id, "mode": mode,
                "proposal_path": blob.get("proposal_path"),
                "reason": reason or None,
            },
        )
        return {
            "ok": True, "campaign_id": campaign_id,
            "decision_id": decision_id, "mode": mode,
            "proposal_path": blob.get("proposal_path"),
            "event_id": event["id"],
        }

    def update_metadata(
        self,
        campaign_ref: str | Path,
        updates: dict[str, Any],
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.store.update_campaign_metadata(campaign_ref, updates, actor=actor)

    def metadata(self, campaign_ref: str | Path) -> dict[str, Any]:
        return self.store.get_campaign_metadata(campaign_ref)

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
