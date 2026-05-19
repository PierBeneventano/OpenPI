"""Local-first campaign store backed by project-local SQLite.

The store is intentionally local and boring: SQLite is the operational index,
JSON bundles are explicit import/export artifacts, and normal research outputs
remain visible in the repo.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .campaign_projection import EVENT_PROJECTION_SOURCE, CampaignEventProjector, artifact_audience
from .events import redact
from .research_tiers import TARGET_RESEARCH_TEMPLATE
from .stage_contracts import compile_kernel_graph, contracts_by_id, project_kernel_graph, template_names


SCHEMA_VERSION = 1
EVENT_LOG = Path(".msc") / "events" / "campaigns.jsonl"
DB_PATH = Path(".msc") / "campaigns.db"

TEMPLATE_NAMES = template_names()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or "campaign"


def stable_id(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"evt_{digest}"


def safe_chat_name(value: str) -> str:
    raw = str(value or "campaign")
    base = Path(raw).name
    basename = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-") or "campaign"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{basename[:70]}-{digest}"


def path_is_inside(path_value: Path, root: Path) -> bool:
    try:
        path_value.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class CampaignStore:
    """Project-local campaign control/index plane."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.state_dir = self.root / ".msc"
        self.db_path = self.state_dir / "campaigns.db"
        self.event_log_path = self.state_dir / "events" / "campaigns.jsonl"
        self.snapshot_dir = self.state_dir / "snapshots"

    def connect(self) -> sqlite3.Connection:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self.ensure_schema(conn)
        return conn

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '1');
            CREATE TABLE IF NOT EXISTS campaigns (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              objective TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              workspace_root TEXT NOT NULL,
              budget_cap_usd REAL,
              tier TEXT,
              output_format TEXT,
              bundle_path TEXT
            );
            CREATE TABLE IF NOT EXISTS campaign_events (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL,
              type TEXT NOT NULL,
              actor TEXT NOT NULL,
              created_at TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS graph_snapshots (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              state TEXT NOT NULL,
              created_at TEXT NOT NULL,
              graph_json TEXT NOT NULL,
              UNIQUE(campaign_id, version),
              FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS graph_nodes (
              campaign_id TEXT NOT NULL,
              graph_version INTEGER NOT NULL,
              node_id TEXT NOT NULL,
              type TEXT NOT NULL,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              budget_policy_json TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              PRIMARY KEY(campaign_id, graph_version, node_id)
            );
            CREATE TABLE IF NOT EXISTS graph_edges (
              campaign_id TEXT NOT NULL,
              graph_version INTEGER NOT NULL,
              source TEXT NOT NULL,
              target TEXT NOT NULL,
              kind TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              PRIMARY KEY(campaign_id, graph_version, source, target, kind)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL,
              stage_id TEXT,
              path TEXT NOT NULL,
              kind TEXT NOT NULL,
              required INTEGER NOT NULL,
              status TEXT NOT NULL,
              producer_node_id TEXT,
              size_bytes INTEGER,
              checksum TEXT,
              metadata_json TEXT NOT NULL,
              FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL,
              status TEXT NOT NULL,
              command_json TEXT NOT NULL,
              pid INTEGER,
              started_at TEXT,
              exited_at TEXT,
              exit_code INTEGER,
              budget_usd REAL,
              metadata_json TEXT NOT NULL,
              FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS steering_messages (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL,
              run_id TEXT,
              direction TEXT NOT NULL,
              text TEXT NOT NULL,
              type TEXT NOT NULL,
              created_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY,
              campaign_id TEXT NOT NULL,
              target_type TEXT NOT NULL,
              target_id TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              decided_at TEXT,
              actor TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
        if "bundle_path" not in columns:
            conn.execute("ALTER TABLE campaigns ADD COLUMN bundle_path TEXT")
            if "source_yaml_path" in columns:
                conn.execute("UPDATE campaigns SET bundle_path=source_yaml_path WHERE bundle_path IS NULL")
        conn.commit()

    def create_campaign(
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
        title = title.strip()
        objective = objective.strip()
        if not title:
            raise ValueError("Campaign title is required.")
        if not objective:
            raise ValueError("Research objective is required.")
        template = template if template in TEMPLATE_NAMES else TARGET_RESEARCH_TEMPLATE
        campaign_id = self._unique_campaign_id(slugify(title))
        created_at = now_iso()
        workspace_root = str(Path("results") / campaign_id)
        graph = build_graph_ir(campaign_id, title, template, tier, budget)
        bundle_dir = self.root / "campaigns" / campaign_id

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO campaigns
                (id, title, objective, status, created_at, updated_at, workspace_root,
                 budget_cap_usd, tier, output_format, bundle_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    title,
                    objective,
                    "draft",
                    created_at,
                    created_at,
                    workspace_root,
                    float(budget),
                    tier,
                    output_format,
                    str(bundle_dir),
                ),
            )
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="CampaignCreated",
                actor=actor,
                payload={
                    "title": title,
                    "objective": objective,
                    "template": template,
                    "workspace_root": workspace_root,
                    "budget": budget,
                    "tier": tier,
                    "output_format": output_format,
                },
            )
            self._write_graph(conn, campaign_id, graph)
            self._declare_graph_artifacts(conn, campaign_id, graph)

        self.write_snapshot(campaign_id)
        self.export_bundle(campaign_id, actor="system")
        return self.inspect_dict(campaign_id)

    def import_bundle(self, bundle_path: str | Path, *, actor: str = "user") -> dict[str, Any]:
        source = Path(bundle_path)
        if not source.is_absolute():
            source = self.root / source
        source = source.resolve()
        campaign_json = source / "campaign.json"
        graph_json = source / "graph.json"
        artifacts_json = source / "artifacts.json"
        if not campaign_json.exists() or not graph_json.exists():
            raise FileNotFoundError("Campaign bundle must contain campaign.json and graph.json.")
        data = json.loads(campaign_json.read_text(encoding="utf-8"))
        graph = json.loads(graph_json.read_text(encoding="utf-8"))
        artifacts = json.loads(artifacts_json.read_text(encoding="utf-8")) if artifacts_json.exists() else {"stages": []}
        original_id = str(data.get("id") or data.get("campaign_id") or source.name)
        campaign_id = self._unique_campaign_id(slugify(original_id))
        title = str(data.get("title") or data.get("name") or campaign_id)
        objective = str(data.get("objective") or "")
        workspace_root = str(data.get("workspace_root") or Path("results") / campaign_id)
        budget = float(data.get("budget_cap_usd") or data.get("budget") or 0)
        tier = str(data.get("tier") or "budget")
        output_format = str(data.get("output_format") or "markdown")
        created_at = now_iso()
        graph = normalize_imported_graph(graph, campaign_id, original_id=original_id)

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO campaigns
                (id, title, objective, status, created_at, updated_at, workspace_root,
                 budget_cap_usd, tier, output_format, bundle_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (campaign_id, title, objective, "imported", created_at, created_at, workspace_root, budget, tier, output_format, str(source)),
            )
            self._write_graph(conn, campaign_id, graph)
            if artifacts.get("stages"):
                self._import_bundle_artifacts(conn, campaign_id, artifacts, original_id=original_id)
            else:
                self._declare_graph_artifacts(conn, campaign_id, graph)
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="CampaignImported",
                actor=actor,
                payload={"bundle_path": str(source), "original_id": original_id},
            )
        self.write_snapshot(campaign_id)
        self.export_bundle(campaign_id, actor="system")
        return self.inspect_dict(campaign_id)

    def export_bundle(self, campaign_id: str, *, actor: str = "user") -> dict[str, Any]:
        campaign = self.get_campaign(campaign_id)
        graph = self.graph(campaign_id)
        artifacts = self.artifacts(campaign_id)
        bundle_dir = self.root / "campaigns" / campaign_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        campaign_doc = {
            "schema": "msc.campaign.bundle.v1",
            "id": campaign_id,
            "title": campaign["title"],
            "objective": campaign["objective"],
            "status": campaign["status"],
            "workspace_root": campaign["workspace_root"],
            "budget_cap_usd": campaign["budget_cap_usd"],
            "tier": campaign["tier"],
            "output_format": campaign["output_format"],
            "created_at": campaign["created_at"],
            "updated_at": campaign["updated_at"],
        }
        (bundle_dir / "campaign.json").write_text(json.dumps(campaign_doc, indent=2, sort_keys=True), encoding="utf-8")
        (bundle_dir / "graph.json").write_text(json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8")
        (bundle_dir / "artifacts.json").write_text(json.dumps(artifacts, indent=2, sort_keys=True), encoding="utf-8")
        self.write_readme(campaign_id)
        with self.connect() as conn:
            conn.execute("UPDATE campaigns SET bundle_path=?, updated_at=? WHERE id=?", (str(bundle_dir), now_iso(), campaign_id))
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="CampaignExported",
                actor=actor,
                payload={"bundle_path": str(bundle_dir)},
            )
        events = self.events(campaign_id)["events"]
        (bundle_dir / "events.jsonl").write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        return {"ok": True, "campaign_id": campaign_id, "bundle_path": str(bundle_dir)}

    def approve_graph(self, campaign_id: str, graph_version: int, *, actor: str = "user") -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM graph_snapshots WHERE campaign_id=? AND version=?",
                (campaign_id, graph_version),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(f"Graph version not found: {campaign_id}@{graph_version}")
            conn.execute(
                "UPDATE graph_snapshots SET state=? WHERE campaign_id=? AND version=?",
                ("approved", campaign_id, graph_version),
            )
            conn.execute("UPDATE graph_nodes SET status=? WHERE campaign_id=? AND graph_version=?", ("approved", campaign_id, graph_version))
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("approved", now_iso(), campaign_id))
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="GraphApproved",
                actor=actor,
                payload={"graph_version": graph_version},
            )
        self.write_snapshot(campaign_id)
        return {"ok": True, "campaign_id": campaign_id, "graph_version": graph_version, "state": "approved"}

    def append_event(
        self,
        campaign_ref: str | Path,
        event_type: str,
        *,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        with self.connect() as conn:
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
            )
            conn.execute("UPDATE campaigns SET updated_at=? WHERE id=?", (now_iso(), campaign_id))
            return event

    def explain_node(self, campaign_ref: str | Path, node_id: str) -> dict[str, Any]:
        graph = self.graph(campaign_ref)
        node = next((item for item in graph.get("nodes", []) if item.get("id") == node_id), None)
        if node is None:
            raise FileNotFoundError(f"Node not found in campaign graph: {node_id}")
        artifacts = self.artifacts(graph["campaign"], stage_id=node_id)
        metadata = node.get("metadata") or {}
        return {
            "ok": True,
            "campaign": graph["campaign"],
            "graph_version": graph.get("version"),
            "node": node,
            "contract": {
                "id": node["id"],
                "kind": metadata.get("kind") or node.get("type"),
                "purpose": metadata.get("purpose"),
                "validators": metadata.get("validators") or [],
                "tool_families": metadata.get("toolFamilies") or [],
                "human_pause_policy": metadata.get("humanPausePolicy") or [],
                "failure_policy": metadata.get("failurePolicy"),
                "council_policy": metadata.get("councilPolicy") or {},
                "tier_policy": metadata.get("tierPolicy") or {},
                "model_policy": metadata.get("modelPolicy") or {},
                "duality_required": bool(metadata.get("dualityRequired")),
                "requires_duality_pass": bool(metadata.get("requiresDualityPass")),
                "allowed_routes": metadata.get("allowedRoutes") or [],
                "legacy_runtime_mapping": metadata.get("legacyRuntimeMapping") or {},
            },
            "artifacts": artifacts,
        }

    def propose_graph_change(
        self,
        campaign_ref: str | Path,
        *,
        change_type: str,
        instruction: str,
        node_id: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        target_id = node_id or campaign_id
        payload = {
            "change_type": change_type,
            "instruction": instruction,
            "node_id": node_id,
            "policy": "proposal_only",
        }
        with self.connect() as conn:
            proposal = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="GraphChangeProposed",
                actor=actor,
                payload=payload,
            )
            approval = self._create_approval(
                conn,
                campaign_id=campaign_id,
                target_type="graph_change",
                target_id=target_id,
                actor=actor,
                metadata={"proposal_event_id": proposal["id"], **payload},
            )
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("pending_approval", now_iso(), campaign_id))
        return {"ok": True, "campaign_id": campaign_id, "proposal": proposal, "approval": approval}

    def decide_approval(self, approval_id: str, *, approved: bool, actor: str = "user") -> dict[str, Any]:
        status = "approved" if approved else "rejected"
        decided_at = now_iso()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                raise FileNotFoundError(f"Approval not found: {approval_id}")
            conn.execute(
                "UPDATE approvals SET status=?, decided_at=?, actor=? WHERE id=?",
                (status, decided_at, actor, approval_id),
            )
            event = self._append_event(
                conn,
                campaign_id=row["campaign_id"],
                event_type="ApprovalDecided",
                actor=actor,
                payload={
                    "approval_id": approval_id,
                    "target_type": row["target_type"],
                    "target_id": row["target_id"],
                    "status": status,
                },
            )
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("approved" if approved else "draft", now_iso(), row["campaign_id"]))
        return {"ok": True, "approval_id": approval_id, "status": status, "event": event}

    def pause(self, campaign_ref: str | Path, *, actor: str = "user", reason: str = "") -> dict[str, Any]:
        return self._campaign_state_event(campaign_ref, "paused", "CampaignPaused", actor=actor, payload={"reason": reason})

    def resume(self, campaign_ref: str | Path, *, actor: str = "user", reason: str = "") -> dict[str, Any]:
        return self._campaign_state_event(campaign_ref, "approved", "CampaignResumed", actor=actor, payload={"reason": reason})

    def stop(self, campaign_ref: str | Path, *, actor: str = "user", reason: str = "") -> dict[str, Any]:
        return self._campaign_state_event(campaign_ref, "stopped", "CampaignStopped", actor=actor, payload={"reason": reason})

    def reroute(self, campaign_ref: str | Path, *, from_node: str, to_node: str, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.propose_graph_change(
            campaign_ref,
            change_type="reroute",
            instruction=reason or f"Reroute from {from_node} to {to_node}.",
            node_id=from_node,
            actor=actor,
        ) | {"from": from_node, "to": to_node}

    def rewrite_stage(self, campaign_ref: str | Path, node_id: str, *, instruction: str, actor: str = "user") -> dict[str, Any]:
        return self.propose_graph_change(
            campaign_ref,
            change_type="rewrite_stage_instruction",
            instruction=instruction,
            node_id=node_id,
            actor=actor,
        )

    def rerun_stage(self, campaign_ref: str | Path, node_id: str, *, reason: str = "", actor: str = "user") -> dict[str, Any]:
        return self.propose_graph_change(
            campaign_ref,
            change_type="rerun_stage",
            instruction=reason or f"Rerun stage {node_id}.",
            node_id=node_id,
            actor=actor,
        )

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
        instruction = reason or f"Rewind campaign execution to stage {node_id}."
        proposal = self.propose_graph_change(
            campaign_ref,
            change_type="rewind_to_stage",
            instruction=instruction,
            node_id=node_id,
            actor=actor,
        )
        campaign_id = proposal["campaign_id"]
        with self.connect() as conn:
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="CampaignRewindRequested",
                actor=actor,
                payload={
                    "node_id": node_id,
                    "reason": instruction,
                    "decision_id": decision_id,
                    "run_id": run_id,
                    "approval_id": proposal["approval"]["id"],
                    "proposal_event_id": proposal["proposal"]["id"],
                },
            )
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("human_decision_required", now_iso(), campaign_id))
        return {
            **proposal,
            "node_id": node_id,
            "decision_id": decision_id,
            "run_id": run_id,
            "rewind_event": event,
        }

    def summarize_artifacts(self, campaign_ref: str | Path) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        stages = self.artifacts(campaign_id)["stages"]
        rows = [artifact for stage in stages for artifact in [*stage["required_artifacts"], *stage["optional_artifacts"]]]
        required = [artifact for artifact in rows if artifact["required"]]
        missing_required = [artifact for artifact in required if not artifact["exists"]]
        existing = [artifact for artifact in rows if artifact["exists"]]
        by_stage = {
            stage["stage_id"]: {
                "required": len(stage["required_artifacts"]),
                "optional": len(stage["optional_artifacts"]),
                "missing_required": len([item for item in stage["required_artifacts"] if not item["exists"]]),
                "existing": len([item for item in [*stage["required_artifacts"], *stage["optional_artifacts"]] if item["exists"]]),
            }
            for stage in stages
        }
        return {
            "ok": True,
            "campaign": campaign_id,
            "total": len(rows),
            "required": len(required),
            "optional": len(rows) - len(required),
            "existing": len(existing),
            "missing_required": len(missing_required),
            "by_stage": by_stage,
            "missing_required_artifacts": missing_required,
        }

    def request_evidence(
        self,
        campaign_ref: str | Path,
        *,
        question: str,
        node_id: str | None = None,
        artifact_path: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        event = self.append_event(
            campaign_id,
            "EvidenceRequested",
            actor=actor,
            payload={
                "question": question,
                "node_id": node_id,
                "artifact_path": artifact_path,
                "safe_next_actions": ["summarize-artifacts", "rerun-stage", "rewrite-stage"],
            },
        )
        return {"ok": True, "campaign_id": campaign_id, "event": event}

    def inspect_budget(self, campaign_ref: str | Path) -> dict[str, Any]:
        workspace = self.workspace_read_model(campaign_ref)
        return {
            "ok": True,
            "campaign": workspace["campaign"]["id"],
            "budget_cap_usd": workspace["campaign"].get("budget_cap_usd"),
            "tier": workspace["campaign"].get("tier"),
            "execution": {
                "status": workspace["execution"]["status"],
                "attempts": workspace["execution"]["attempts"],
            },
        }

    def diagnose_execution(self, campaign_ref: str | Path) -> dict[str, Any]:
        workspace = self.workspace_read_model(campaign_ref)
        latest = workspace["execution"].get("latest_attempt") or {}
        status = workspace["execution"]["status"]
        return {
            "ok": True,
            "campaign": workspace["campaign"]["id"],
            "status": status,
            "current_stage_id": workspace["execution"].get("current_stage_id"),
            "latest_attempt": latest,
            "pending_decisions": workspace["pending_decisions"],
            "safe_next_actions": workspace["safe_next_actions"],
            "diagnosis": "human_decision_required" if workspace["pending_decisions"] else status,
        }

    def propose_repair(
        self,
        campaign_ref: str | Path,
        *,
        node_id: str | None = None,
        reason: str = "",
        actor: str = "user",
    ) -> dict[str, Any]:
        return self.propose_graph_change(
            campaign_ref,
            change_type="repair_stage_or_execution",
            instruction=reason or "Prepare a bounded repair proposal.",
            node_id=node_id,
            actor=actor,
        )

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
        bits = []
        if tier:
            bits.append(f"tier={tier}")
        if model:
            bits.append(f"model={model}")
        instruction = reason or "Change model/tier policy: " + ", ".join(bits or ["unspecified"])
        return self.propose_graph_change(
            campaign_ref,
            change_type="model_tier_policy",
            instruction=instruction,
            node_id=node_id,
            actor=actor,
        )

    _WORKSPACE_PAYLOAD_HEAVY_FIELDS = {
        "CouncilMemberCompleted": ("output",),
        "CouncilSynthesisRecorded": ("synthesis",),
        "CouncilStarted": ("prompt",),
        "DualityCheckStarted": ("prompt",),
    }
    _WORKSPACE_PREVIEW_CHARS = 200

    @classmethod
    def _compact_event_for_workspace(cls, event: dict[str, Any]) -> dict[str, Any]:
        # The workspace read model is consumed by UIs (e.g. the VSCode extension)
        # that only render identifiers, timestamps and event type. Council member
        # outputs and synthesis text can each be tens of KB, blowing past the
        # extension's stdout maxBuffer. Trim them here while preserving the
        # event-stream shape; full text remains available via
        # `campaigns events <slug> --json`.
        heavy_fields = cls._WORKSPACE_PAYLOAD_HEAVY_FIELDS.get(event.get("type"))
        if not heavy_fields:
            return event
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return event
        new_payload = dict(payload)
        trimmed = False
        for field in heavy_fields:
            if field not in new_payload:
                continue
            raw = new_payload.pop(field)
            text = "" if raw is None else str(raw)
            new_payload[f"{field}_length"] = len(text)
            new_payload[f"{field}_preview"] = text[: cls._WORKSPACE_PREVIEW_CHARS]
            new_payload[f"{field}_truncated"] = len(text) > cls._WORKSPACE_PREVIEW_CHARS
            trimmed = True
        if not trimmed:
            return event
        new_event = dict(event)
        new_event["payload"] = new_payload
        return new_event

    def workspace_read_model(self, campaign_ref: str | Path) -> dict[str, Any]:
        """Return the product-facing campaign workspace model.

        This is the canonical read surface for UIs and steering layers. It
        keeps legacy run/process details available as diagnostics, but the main
        shape is campaign execution, decisions, graph, feedback, and
        deliverables.
        """

        campaign_id = self.resolve_ref(campaign_ref)
        projection = self._project_campaign(campaign_id)
        campaign = projection["campaign"]
        graph = self.graph(campaign_id)
        artifacts = self.artifacts(campaign_id)
        events = projection["events"]
        flat_artifacts = [
            artifact
            for stage in artifacts["stages"]
            for artifact in [*stage["required_artifacts"], *stage["optional_artifacts"]]
        ]
        decisions = self._decision_read_models(campaign_id)
        pending_decisions = [decision for decision in decisions if decision["status"] == "pending"]
        feedback = self._feedback_read_models(events)
        context_links = self._context_link_read_models(events)
        execution = self._campaign_execution_read_model(
            campaign=campaign,
            graph=graph,
            events=events,
            pending_decisions=pending_decisions,
        )
        deliverables = [
            artifact for artifact in flat_artifacts
            if artifact["exists"] and artifact.get("audience") in {"deliverable", "evidence"}
        ]
        planned_outputs = [
            artifact for artifact in flat_artifacts
            if not artifact["exists"] and artifact.get("audience") in {"deliverable", "evidence"}
        ]
        diagnostics = [
            artifact for artifact in flat_artifacts
            if artifact.get("audience") in {"diagnostic", "log", "prompt", "system_state"}
        ]
        safe_next_actions = self._workspace_safe_next_actions(
            execution_status=execution["status"],
            pending_decisions=pending_decisions,
            graph=graph,
        )
        return {
            "ok": True,
            "schema": "msc.campaign.workspace.v1",
            "campaign": {
                "id": campaign["id"],
                "title": campaign["title"],
                "objective": campaign["objective"],
                "status": campaign["status"],
                "created_at": campaign["created_at"],
                "updated_at": campaign["updated_at"],
                "workspace_root": campaign["workspace_root"],
                "budget_cap_usd": campaign["budget_cap_usd"],
                "tier": campaign["tier"],
                "output_format": campaign["output_format"],
                "bundle_path": campaign["bundle_path"],
            },
            "execution": execution,
            "councils": self._council_read_models(events),
            "duality": self._duality_read_model(events),
            "tier_policy": graph.get("metadata", {}).get("tierPolicy"),
            "model_policy": graph.get("metadata", {}).get("modelPolicy"),
            "safe_next_actions": safe_next_actions,
            "graph": graph,
            "decisions": decisions,
            "pending_decisions": pending_decisions,
            "feedback": feedback,
            "context": {
                "links": context_links,
                "active_links": [link for link in context_links if link["status"] == "active"],
            },
            "deliverables": deliverables,
            "planned_outputs": planned_outputs,
            "diagnostics": {
                "artifacts": diagnostics,
                "events": [self._compact_event_for_workspace(event) for event in events],
                "legacy_attempts": execution["attempts"],
            },
            "provenance": {
                "source": EVENT_PROJECTION_SOURCE,
                "reader": "msc_sdk.campaign_store.CampaignStore.workspace_read_model",
            },
        }

    @staticmethod
    def _council_read_models(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["type"] not in {"CouncilStarted", "CouncilMemberCompleted", "CouncilSynthesisRecorded", "CouncilVerdictRecorded"}:
                continue
            payload = event["payload"]
            council_id = str(payload.get("council_id") or event["id"])
            row = rows.setdefault(
                council_id,
                {
                    "id": council_id,
                    "stage_id": payload.get("stage_id"),
                    "kind": payload.get("kind"),
                    "status": "running",
                    "members": [],
                    "synthesis": None,
                    "verdict": None,
                    "passed": None,
                    "started_at": event["created_at"],
                    "updated_at": event["created_at"],
                },
            )
            row["updated_at"] = event["created_at"]
            if event["type"] == "CouncilMemberCompleted":
                # Drop full LLM output (often tens of KB) from the workspace
                # read model. Full text is reachable via `campaigns events`.
                output_text = "" if payload.get("output") is None else str(payload.get("output"))
                row["members"].append({
                    "model_id": payload.get("model_id"),
                    "output_length": len(output_text),
                    "output_preview": output_text[:200],
                    "output_truncated": len(output_text) > 200,
                })
            elif event["type"] == "CouncilSynthesisRecorded":
                synthesis_text = "" if payload.get("synthesis") is None else str(payload.get("synthesis"))
                row["synthesis_length"] = len(synthesis_text)
                row["synthesis_preview"] = synthesis_text[:200]
                row["synthesis_truncated"] = len(synthesis_text) > 200
                row["synthesis"] = None
            elif event["type"] == "CouncilVerdictRecorded":
                row["status"] = "completed"
                row["verdict"] = payload.get("verdict")
                row["passed"] = payload.get("passed")
        return sorted(rows.values(), key=lambda row: str(row.get("updated_at") or ""), reverse=True)

    @staticmethod
    def _duality_read_model(events: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "not_started", "required": True}
        for event in events:
            if event["type"] == "DualityCheckStarted":
                result.update({"status": "running", "stage_id": event["payload"].get("stage_id"), "started_at": event["created_at"]})
            elif event["type"] == "DualityCheckCompleted":
                result.update(
                    {
                        "status": "passed" if event["payload"].get("passed") else "failed",
                        "stage_id": event["payload"].get("stage_id"),
                        "completed_at": event["created_at"],
                        "verdict": event["payload"].get("verdict"),
                        "metadata": dict(event["payload"].get("metadata") or {}),
                    }
                )
        return result

    def _decision_read_models(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE campaign_id=? ORDER BY created_at DESC",
                (campaign_id,),
            ).fetchall()
        dry_run_success_run_ids = self._dry_run_success_run_ids(campaign_id)
        decisions: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            target_type = str(row["target_type"])
            if (
                target_type == "failure_recovery"
                and row["status"] == "pending"
                and str(row["target_id"]) in dry_run_success_run_ids
                and metadata.get("exit_code") == 0
            ):
                continue
            reason = str(metadata.get("reason") or metadata.get("error") or metadata.get("requested_status") or target_type)
            decisions.append(
                {
                    "id": row["id"],
                    "campaign_id": row["campaign_id"],
                    "target_type": target_type,
                    "target_id": row["target_id"],
                    "target_label": self._decision_target_label(target_type, str(row["target_id"]), metadata),
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "decided_at": row["decided_at"],
                    "actor": row["actor"],
                    "title": self._decision_title(target_type, reason),
                    "reason": reason,
                    "summary": self._decision_summary(target_type, reason),
                    "safe_next_actions": list(metadata.get("safe_next_actions") or self._safe_actions_for_decision(target_type)),
                    "evidence": metadata.get("evidence") or metadata.get("missing_required_artifacts") or [],
                    "metadata": metadata,
                }
            )
        return decisions

    def _dry_run_success_run_ids(self, campaign_id: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM runs WHERE campaign_id=? AND status=? AND exit_code=0",
                (campaign_id, "dry_run_passed"),
            ).fetchall()
        return {str(row["id"]) for row in rows}

    @staticmethod
    def _decision_title(target_type: str, reason: str) -> str:
        if target_type == "failure_recovery":
            if "recursion" in reason.lower() or "GRAPH_RECURSION_LIMIT" in reason:
                return "Campaign execution reached graph transition limit"
            return "Campaign execution needs recovery"
        if target_type == "stage_failure":
            return "Stage needs recovery"
        if target_type == "stage_completion":
            return "Stage output needs review"
        if target_type == "graph_change":
            return "Graph change needs review"
        return target_type.replace("_", " ").title()

    @staticmethod
    def _decision_summary(target_type: str, reason: str) -> str:
        if target_type == "failure_recovery":
            if "recursion" in reason.lower() or "GRAPH_RECURSION_LIMIT" in reason:
                return (
                    "The campaign used more graph transitions than the runtime allowed. "
                    "This may be a real loop, or a full research pass with feedback cycles "
                    "running under too small a transition budget. Use OpenClaude to inspect "
                    "the stage history, then rerun, rewind, or repair through SDK commands."
                )
            return "The latest campaign execution failed and needs a recovery choice before continuing."
        if target_type == "stage_failure":
            return "A stage failed and needs repair, rewrite, rerun, or abort guidance."
        if target_type == "stage_completion":
            return "A stage produced output that needs human review before the graph continues."
        if target_type == "graph_change":
            return "A proposed graph change needs approval or revision."
        return reason

    @staticmethod
    def _decision_target_label(target_type: str, target_id: str, metadata: dict[str, Any]) -> str:
        if target_type == "failure_recovery":
            return "latest failed execution"
        return str(metadata.get("node_id") or metadata.get("stage_id") or metadata.get("artifact_path") or target_id or "campaign")

    @staticmethod
    def _safe_actions_for_decision(target_type: str) -> list[str]:
        if target_type == "stage_completion":
            return ["approve", "rerun-stage", "rewrite-stage", "request-repair"]
        if target_type == "stage_failure":
            return ["rerun-stage", "rewrite-stage", "request-repair", "abort"]
        if target_type == "graph_change":
            return ["approve", "reject", "revise-proposal"]
        if target_type == "failure_recovery":
            return ["rerun-stage", "rewind", "request-repair", "abort"]
        return ["approve", "reject"]

    @staticmethod
    def _feedback_read_models(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        feedback: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in events:
            payload = event["payload"]
            if event["type"] not in {"InstructionSent", "HumanFeedbackRecorded"}:
                continue
            if event["type"] == "InstructionSent" and payload.get("direction") != "to_campaign":
                continue
            event_id = str(payload.get("message_id") or payload.get("feedback_id") or event["id"])
            if event_id in seen:
                continue
            seen.add(event_id)
            metadata = dict(payload.get("metadata") or {})
            scope = str(metadata.get("target_scope") or "campaign")
            if metadata.get("artifact_id") or metadata.get("artifact_path"):
                scope = "artifact"
            elif metadata.get("decision_id"):
                scope = "decision"
            elif metadata.get("node_id"):
                scope = "stage"
            feedback.append(
                {
                    "id": event_id,
                    "text": str(payload.get("text") or ""),
                    "type": str(payload.get("type") or payload.get("feedback_type") or "feedback"),
                    "target": {
                        "scope": scope,
                        "node_id": metadata.get("node_id"),
                        "artifact_id": metadata.get("artifact_id"),
                        "artifact_path": metadata.get("artifact_path"),
                        "decision_id": metadata.get("decision_id"),
                    },
                    "execution_id": payload.get("execution_id") or payload.get("run_id"),
                    "created_at": event["created_at"],
                    "actor": event["actor"],
                    "metadata": metadata,
                }
            )
        return sorted(feedback, key=lambda item: str(item["created_at"]), reverse=True)

    @staticmethod
    def _context_link_read_models(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        links: dict[str, dict[str, Any]] = {}
        for event in events:
            payload = event["payload"]
            if event["type"] == "ContextLinked":
                link_id = str(payload.get("link_id") or event["id"])
                target = dict(payload.get("target") or {})
                links[link_id] = {
                    "id": link_id,
                    "status": str(payload.get("status") or "active"),
                    "target": {
                        "scope": str(target.get("scope") or "campaign"),
                        "node_id": target.get("node_id"),
                        "artifact_id": target.get("artifact_id"),
                        "artifact_path": target.get("artifact_path"),
                        "decision_id": target.get("decision_id"),
                    },
                    "note": str(payload.get("note") or ""),
                    "created_at": event["created_at"],
                    "updated_at": event["created_at"],
                    "actor": event["actor"],
                    "metadata": dict(payload.get("metadata") or {}),
                }
            elif event["type"] == "ContextLinkUpdated":
                link_id = str(payload.get("link_id") or "")
                if not link_id:
                    continue
                current = links.get(link_id)
                if current is None:
                    current = {
                        "id": link_id,
                        "status": "active",
                        "target": {"scope": "campaign", "node_id": None, "artifact_id": None, "artifact_path": None, "decision_id": None},
                        "note": "",
                        "created_at": event["created_at"],
                        "actor": event["actor"],
                        "metadata": {},
                    }
                links[link_id] = {
                    **current,
                    "status": str(payload.get("status") or current["status"]),
                    "note": str(payload.get("note") if payload.get("note") is not None else current["note"]),
                    "updated_at": event["created_at"],
                    "metadata": {**dict(current.get("metadata") or {}), **dict(payload.get("metadata") or {})},
                }
        return sorted(links.values(), key=lambda item: str(item["updated_at"]), reverse=True)

    def _campaign_execution_read_model(
        self,
        *,
        campaign: dict[str, Any],
        graph: dict[str, Any],
        events: list[dict[str, Any]],
        pending_decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        attempts: dict[str, dict[str, Any]] = {}
        current_stage_id = self._current_stage_id(graph)
        graph_node_ids = {str(node.get("id") or "") for node in graph.get("nodes") or []}
        status = "not_started"
        started_at = None
        updated_at = campaign.get("updated_at")
        for event in events:
            payload = event["payload"]
            event_type = event["type"]
            updated_at = event["created_at"]
            if event_type in {"RunStarted", "CampaignExecutionStarted"}:
                execution_id = str(payload.get("execution_id") or payload.get("run_id") or event["id"])
                attempt = attempts.setdefault(execution_id, {"execution_id": execution_id, "run_id": payload.get("run_id")})
                attempt.update(
                    {
                        "status": "running",
                        "pid": payload.get("pid"),
                        "command": payload.get("command"),
                        "graph_version": payload.get("graph_version"),
                        "started_at": event["created_at"],
                        "updated_at": event["created_at"],
                    }
                )
                status = "running"
                started_at = started_at or event["created_at"]
            elif event_type in {"RunExited", "CampaignExecutionCompleted", "CampaignExecutionFailed"}:
                execution_id = str(payload.get("execution_id") or payload.get("run_id") or event["id"])
                attempt = attempts.setdefault(execution_id, {"execution_id": execution_id, "run_id": payload.get("run_id")})
                attempt_status = str(payload.get("status") or ("completed" if payload.get("exit_code") == 0 else "failed"))
                attempt.update(
                    {
                        "status": attempt_status,
                        "exit_code": payload.get("exit_code"),
                        "exited_at": event["created_at"],
                        "updated_at": event["created_at"],
                    }
                )
                if attempt_status == "dry_run_passed":
                    status = "dry_run_passed"
                elif event_type == "CampaignExecutionFailed" or attempt_status != "completed":
                    status = "human_decision_required"
                else:
                    status = "completed"
            elif event_type == "GraphNodeStatusChanged":
                if payload.get("status") in {"running", "human_decision_required", "failed"}:
                    current_stage_id = str(payload.get("node_id") or current_stage_id or "")
            elif event_type == "ApprovalRequested":
                pending_ids = {str(decision.get("id") or "") for decision in pending_decisions}
                pending_targets = {str(decision.get("target_id") or "") for decision in pending_decisions}
                approval_id = str(payload.get("approval_id") or "")
                target_id = str(payload.get("target_id") or "")
                if approval_id not in pending_ids and target_id not in pending_targets:
                    continue
                status = "human_decision_required"
                if target_id in graph_node_ids:
                    current_stage_id = target_id
            elif event_type == "HumanDecisionRequired":
                status = "human_decision_required"
                target_id = str(payload.get("target_id") or payload.get("stage_id") or "")
                if target_id in graph_node_ids:
                    current_stage_id = target_id
            elif event_type == "CampaignPaused":
                status = "paused"
            elif event_type == "CampaignStopped":
                status = "stopped"
            elif event_type == "CampaignResumed":
                status = "ready"
        if pending_decisions:
            status = "human_decision_required"
            target_id = str(pending_decisions[0].get("target_id") or "")
            if target_id in graph_node_ids:
                current_stage_id = target_id
        return {
            "status": status,
            "started_at": started_at,
            "updated_at": updated_at,
            "current_stage_id": current_stage_id,
            "pending_decision_count": len(pending_decisions),
            "attempts": sorted(
                attempts.values(),
                key=lambda item: str(item.get("updated_at") or item.get("started_at") or ""),
                reverse=True,
            ),
            "latest_attempt": next(
                iter(
                    sorted(
                        attempts.values(),
                        key=lambda item: str(item.get("updated_at") or item.get("started_at") or ""),
                        reverse=True,
                    )
                ),
                None,
            ),
        }

    @staticmethod
    def _current_stage_id(graph: dict[str, Any]) -> str | None:
        nodes = list(graph.get("nodes") or [])
        for status in ("running", "human_decision_required", "failed"):
            match = next((node for node in nodes if node.get("status") == status), None)
            if match:
                return str(match.get("id") or "")
        match = next((node for node in nodes if node.get("status") in {"planned", "approved"}), None)
        return str(match.get("id") or "") if match else None

    @staticmethod
    def _workspace_safe_next_actions(
        *,
        execution_status: str,
        pending_decisions: list[dict[str, Any]],
        graph: dict[str, Any],
    ) -> list[str]:
        if pending_decisions:
            actions: list[str] = []
            for decision in pending_decisions:
                for action in decision.get("safe_next_actions") or []:
                    if action not in actions:
                        actions.append(action)
            return actions or ["review-decision"]
        if execution_status == "not_started":
            return ["start-campaign"]
        if execution_status in {"paused", "ready", "human_decision_required"}:
            return ["continue-campaign", "record-feedback"]
        if execution_status == "running":
            return ["pause-campaign", "record-feedback"]
        if execution_status in {"completed", "dry_run_passed"}:
            return ["review-deliverables", "record-feedback", "rerun-stage"]
        if graph.get("nodes"):
            return ["continue-campaign"]
        return ["define-graph"]

    def update_node_status(
        self,
        campaign_ref: str | Path,
        node_id: str,
        status: str,
        *,
        actor: str = "runner",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        requested_status = status
        payload = dict(payload or {})
        completion = None
        approval = None
        if requested_status == "completed":
            completion = self.evaluate_stage_completion(campaign_id, node_id, run_id=payload.get("run_id"))
            payload["completion"] = completion
            if not completion["complete"]:
                status = "human_decision_required"
                payload["requested_status"] = requested_status
        elif requested_status == "failed":
            payload["human_decision_required"] = True
        with self.connect() as conn:
            snapshot = conn.execute(
                "SELECT * FROM graph_snapshots WHERE campaign_id=? ORDER BY version DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if snapshot is None:
                raise FileNotFoundError(f"Graph not found for campaign: {campaign_id}")
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
            conn.execute("UPDATE campaigns SET updated_at=? WHERE id=?", (now_iso(), campaign_id))
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="GraphNodeStatusChanged",
                actor=actor,
                payload={"node_id": node_id, "status": status, **payload},
            )
            if completion is not None:
                self._append_event(
                    conn,
                    campaign_id=campaign_id,
                    event_type="StageCompletionEvaluated",
                    actor=actor,
                    payload={"node_id": node_id, **completion},
                )
            if completion is not None and completion["complete"]:
                self._append_event(
                    conn,
                    campaign_id=campaign_id,
                    event_type="ValidationPassed",
                    actor=actor,
                    payload={"node_id": node_id, "run_id": payload.get("run_id"), "validators": completion["validators"]},
                )
            elif completion is not None and not completion["complete"]:
                self._append_event(
                    conn,
                    campaign_id=campaign_id,
                    event_type="ValidationFailed",
                    actor=actor,
                    payload={"node_id": node_id, "run_id": payload.get("run_id"), "completion": completion},
                )
                approval = self._create_approval(
                    conn,
                    campaign_id=campaign_id,
                    target_type="stage_completion",
                    target_id=node_id,
                    actor=actor,
                    metadata={
                        "run_id": payload.get("run_id"),
                        "requested_status": requested_status,
                        "missing_required_artifacts": completion["missing_required_artifacts"],
                        "safe_next_actions": ["request-repair", "rerun-stage", "rewrite-stage", "approve"],
                    },
                )
                conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("human_decision_required", now_iso(), campaign_id))
            elif requested_status == "failed":
                approval = self._create_approval(
                    conn,
                    campaign_id=campaign_id,
                    target_type="stage_failure",
                    target_id=node_id,
                    actor=actor,
                    metadata={
                        "run_id": payload.get("run_id"),
                        "error": payload.get("error"),
                        "safe_next_actions": ["request-repair", "rerun-stage", "rewrite-stage", "abort"],
                    },
                )
                conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("human_decision_required", now_iso(), campaign_id))
        self.write_snapshot(campaign_id)
        return {"ok": True, "campaign_id": campaign_id, "node_id": node_id, "status": status, "event": event, "completion": completion, "approval": approval}

    def evaluate_stage_completion(self, campaign_ref: str | Path, node_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        """Evaluate the product completion rule for a graph node.

        Completion is centralized here so the runner, UI, CLI, and OpenClaude
        controls all read the same semantics: required artifacts must exist,
        executable validators must pass, and the status transition must be
        represented by an event. Most historical validators are currently
        declared-but-unbound, so they are surfaced explicitly instead of being
        silently treated as runtime behavior.
        """

        campaign_id = self.resolve_ref(campaign_ref)
        contract = contracts_by_id().get(node_id)
        required_paths = [artifact.path for artifact in contract.required_artifacts] if contract else []
        declared_validators = list(contract.validators) if contract else []
        artifacts = self._artifact_rows_for_completion(campaign_id, node_id, run_id=run_id)
        present_paths = {row["path"] for row in artifacts if row["exists"]}
        missing = [path for path in required_paths if path not in present_paths]
        artifact_check = {
            "id": "required_artifacts_exist",
            "status": "passed" if not missing else "failed",
            "missing": missing,
        }
        validator_results = [artifact_check]
        validator_results.extend(
            {"id": validator, "status": "declared_unbound"}
            for validator in declared_validators
        )
        return {
            "run_id": run_id,
            "complete": not missing,
            "required_artifacts_ok": not missing,
            "missing_required_artifacts": missing,
            "validators": validator_results,
            "declared_validators": declared_validators,
            "validator_binding_complete": not declared_validators,
        }

    def _artifact_rows_for_completion(self, campaign_id: str, node_id: str, *, run_id: str | None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE campaign_id=? AND stage_id=?",
                (campaign_id, node_id),
            ).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            item = artifact_row_to_dict(dict(row), self.root)
            item_run_id = item["metadata"].get("run_id")
            if run_id and item_run_id not in {run_id, None}:
                continue
            resolved.append(item)
        return resolved

    def record_run_started(
        self,
        campaign_ref: str | Path,
        *,
        command: list[str] | dict[str, Any],
        pid: int | None = None,
        graph_version: int | None = None,
        actor: str = "runner",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        started_at = now_iso()
        run_id = stable_id(campaign_id, "run", str(pid or ""), started_at)
        command_json = command if isinstance(command, dict) else {"argv": command}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs
                (id, campaign_id, status, command_json, pid, started_at, exited_at, exit_code, budget_usd, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    campaign_id,
                    "running",
                    json_dumps(command_json),
                    pid,
                    started_at,
                    None,
                    None,
                    None,
                    json_dumps({"graph_version": graph_version, **(metadata or {})}),
                ),
            )
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", ("running", started_at, campaign_id))
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="RunStarted",
                actor=actor,
                payload={"run_id": run_id, "pid": pid, "command": command_json, "graph_version": graph_version},
            )
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="CampaignExecutionStarted",
                actor=actor,
                payload={
                    "execution_id": run_id,
                    "run_id": run_id,
                    "pid": pid,
                    "command": command_json,
                    "graph_version": graph_version,
                    "compatibility_event_id": event["id"],
                },
            )
        return {"ok": True, "campaign_id": campaign_id, "run_id": run_id, "event": event}

    def record_run_exited(
        self,
        campaign_ref: str | Path,
        run_id: str,
        *,
        exit_code: int | None,
        status: str | None = None,
        actor: str = "runner",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        exited_at = now_iso()
        run_status = status or ("completed" if exit_code == 0 else "failed")
        if run_status == "completed":
            campaign_status = "completed"
        elif run_status == "dry_run_passed":
            campaign_status = "approved"
        else:
            campaign_status = "human_decision_required"
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, exited_at=?, exit_code=? WHERE id=? AND campaign_id=?",
                (run_status, exited_at, exit_code, run_id, campaign_id),
            )
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", (campaign_status, exited_at, campaign_id))
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="RunExited",
                actor=actor,
                payload={"run_id": run_id, "status": run_status, "exit_code": exit_code, **(metadata or {})},
            )
            execution_completed = run_status in {"completed", "dry_run_passed"}
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="CampaignExecutionCompleted" if execution_completed else "CampaignExecutionFailed",
                actor=actor,
                payload={
                    "execution_id": run_id,
                    "run_id": run_id,
                    "status": run_status,
                    "exit_code": exit_code,
                    "compatibility_event_id": event["id"],
                    **(metadata or {}),
                },
            )
            approval = None
            if not execution_completed:
                approval = self._create_approval(
                    conn,
                    campaign_id=campaign_id,
                    target_type="failure_recovery",
                    target_id=run_id,
                    actor=actor,
                    metadata={"run_id": run_id, "exit_code": exit_code, "reason": (metadata or {}).get("error")},
                )
        self.refresh_artifact_files(campaign_id)
        return {"ok": True, "campaign_id": campaign_id, "run_id": run_id, "status": run_status, "event": event, "approval": approval}

    def record_artifact(
        self,
        campaign_ref: str | Path,
        *,
        stage_id: str,
        artifact_path: str,
        workspace: str,
        kind: str,
        required: bool,
        producer_node_id: str | None = None,
        run_id: str | None = None,
        actor: str = "runner",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a contract-native artifact written by a campaign-attached run."""

        campaign_id = self.resolve_ref(campaign_ref)
        full = self.root / workspace / artifact_path
        if not full.exists() or not full.is_file():
            raise FileNotFoundError(f"Artifact does not exist: {full}")
        artifact_id = f"{campaign_id}:{run_id}:{stage_id}:{artifact_path}" if run_id else f"{campaign_id}:{stage_id}:{artifact_path}"
        checksum = file_checksum(full)
        merged_metadata = {
            "workspace": workspace,
            "source_role": "contract_runtime",
            "audience": "deliverable" if required else "evidence",
            "run_id": run_id,
            **(metadata or {}),
        }
        with self.connect() as conn:
            previous = conn.execute("SELECT status, checksum FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts
                (id, campaign_id, stage_id, path, kind, required, status, producer_node_id,
                 size_bytes, checksum, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    campaign_id,
                    stage_id,
                    artifact_path,
                    kind,
                    1 if required else 0,
                    "existing",
                    producer_node_id or stage_id,
                    full.stat().st_size,
                    checksum,
                    json_dumps(merged_metadata),
                ),
            )
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="ArtifactIndexed",
                actor=actor,
                payload={
                    "artifact_id": artifact_id,
                    "stage_id": stage_id,
                    "path": artifact_path,
                    "kind": kind,
                    "required": required,
                    "producer_node_id": producer_node_id or stage_id,
                    "workspace": workspace,
                    "run_id": run_id,
                    "size_bytes": full.stat().st_size,
                    "checksum": checksum,
                    "source_role": "contract_runtime",
                    "metadata": metadata or {},
                },
            )
            if previous is None or previous["status"] != "existing" or previous["checksum"] != checksum:
                conn.execute("UPDATE campaigns SET updated_at=? WHERE id=?", (now_iso(), campaign_id))
        return {"ok": True, "campaign_id": campaign_id, "artifact_id": artifact_id, "event": event}

    def record_instruction(
        self,
        campaign_ref: str | Path,
        *,
        text: str,
        instruction_type: str = "m",
        run_id: str | None = None,
        direction: str = "to_runner",
        actor: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        created_at = now_iso()
        message_id = stable_id(campaign_id, "instruction", text, created_at)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO steering_messages
                (id, campaign_id, run_id, direction, text, type, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, campaign_id, run_id, direction, text, instruction_type, created_at, json_dumps(metadata or {})),
            )
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="InstructionSent",
                actor=actor,
                payload={
                    "message_id": message_id,
                    "run_id": run_id,
                    "direction": direction,
                    "text": text,
                    "type": instruction_type,
                    "metadata": metadata or {},
                },
            )
            if direction == "to_campaign":
                self._append_event(
                    conn,
                    campaign_id=campaign_id,
                    event_type="HumanFeedbackRecorded",
                    actor=actor,
                    payload={
                        "feedback_id": message_id,
                        "run_id": run_id,
                        "execution_id": run_id,
                        "text": text,
                        "feedback_type": instruction_type,
                        "metadata": metadata or {},
                        "compatibility_event_id": event["id"],
                    },
                )
        return {"ok": True, "campaign_id": campaign_id, "message_id": message_id, "event": event}

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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record durable researcher-selected context for OpenClaude sessions."""

        campaign_id = self.resolve_ref(campaign_ref)
        created_at = now_iso()
        link_id = stable_id(campaign_id, "context", target_scope, node_id or "", artifact_id or "", artifact_path or "", decision_id or "", note, created_at)
        target = {
            "scope": target_scope,
            "node_id": node_id,
            "artifact_id": artifact_id,
            "artifact_path": artifact_path,
            "decision_id": decision_id,
        }
        with self.connect() as conn:
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="ContextLinked",
                actor=actor,
                payload={
                    "link_id": link_id,
                    "status": "active",
                    "target": target,
                    "note": note,
                    "metadata": metadata or {},
                },
            )
            if note:
                feedback_metadata = {key: value for key, value in {
                        "context_link_id": link_id,
                        "node_id": node_id,
                        "artifact_id": artifact_id,
                        "artifact_path": artifact_path,
                        "decision_id": decision_id,
                        "target_scope": target_scope,
                    }.items() if value}
                message_id = stable_id(campaign_id, "instruction", note, link_id)
                conn.execute(
                    """
                    INSERT INTO steering_messages
                    (id, campaign_id, run_id, direction, text, type, created_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (message_id, campaign_id, None, "to_campaign", note, "context_note", now_iso(), json_dumps(feedback_metadata)),
                )
                feedback_event = self._append_event(
                    conn,
                    campaign_id=campaign_id,
                    event_type="InstructionSent",
                    actor=actor,
                    payload={
                        "message_id": message_id,
                        "run_id": None,
                        "direction": "to_campaign",
                        "text": note,
                        "type": "context_note",
                        "metadata": feedback_metadata,
                    },
                )
                self._append_event(
                    conn,
                    campaign_id=campaign_id,
                    event_type="HumanFeedbackRecorded",
                    actor=actor,
                    payload={
                        "feedback_id": message_id,
                        "run_id": None,
                        "execution_id": None,
                        "text": note,
                        "feedback_type": "context_note",
                        "metadata": feedback_metadata,
                        "compatibility_event_id": feedback_event["id"],
                    },
                )
        return {"ok": True, "campaign_id": campaign_id, "link_id": link_id, "event": event}

    def update_context_link(
        self,
        campaign_ref: str | Path,
        link_id: str,
        *,
        status: str,
        note: str | None = None,
        actor: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        with self.connect() as conn:
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="ContextLinkUpdated",
                actor=actor,
                payload={
                    "link_id": link_id,
                    "status": status,
                    "note": note,
                    "metadata": metadata or {},
                },
            )
        return {"ok": True, "campaign_id": campaign_id, "link_id": link_id, "status": status, "event": event}

    def list_context_links(self, campaign_ref: str | Path) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        links = self._context_link_read_models(self._event_rows(campaign_id))
        return {"ok": True, "campaign": campaign_id, "links": links, "active_links": [link for link in links if link["status"] == "active"]}

    def delete_campaign(self, campaign_ref: str | Path, *, actor: str = "user", delete_files: bool = True) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        campaign = self.get_campaign(campaign_id)
        candidates = self._campaign_delete_paths(campaign_id, campaign)
        removed_paths: list[str] = []
        skipped_paths: list[str] = []

        if delete_files:
            for candidate in candidates:
                result = self._remove_path_inside_root(candidate)
                if result["removed"]:
                    removed_paths.append(result["path"])
                elif result["path"]:
                    skipped_paths.append(result["path"])

        with self.connect() as conn:
            for table in (
                "graph_nodes",
                "graph_edges",
                "artifacts",
                "runs",
                "steering_messages",
                "approvals",
                "graph_snapshots",
                "campaign_events",
            ):
                conn.execute(f"DELETE FROM {table} WHERE campaign_id=?", (campaign_id,))
            conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))

        self._purge_campaign_from_event_log(campaign_id)
        return {
            "ok": True,
            "campaign_id": campaign_id,
            "deleted": True,
            "delete_files": delete_files,
            "removed_paths": removed_paths,
            "skipped_paths": skipped_paths,
            "actor": actor,
        }

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY updated_at DESC, created_at DESC").fetchall()
            return [dict(row) | {"source": "sqlite", "name": row["id"], "path": row["id"]} for row in rows]

    def get_campaign(self, ref: str | Path) -> dict[str, Any]:
        campaign_id = self.resolve_ref(ref)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if row is None:
                raise FileNotFoundError(f"Campaign not found: {ref}")
            return dict(row)

    def has_campaign(self, ref: str | Path) -> bool:
        try:
            self.resolve_ref(ref)
            return True
        except FileNotFoundError:
            return False

    def resolve_ref(self, ref: str | Path) -> str:
        value = str(ref)
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM campaigns WHERE id=?", (value,)).fetchone()
            if row:
                return str(row["id"])
            row = conn.execute("SELECT id FROM campaigns WHERE title=?", (value,)).fetchone()
            if row:
                return str(row["id"])
        raise FileNotFoundError(f"Campaign not found: {ref}")

    def inspect_dict(self, ref: str | Path) -> dict[str, Any]:
        campaign_id = self.resolve_ref(ref)
        projection = self._project_campaign(campaign_id)
        campaign = projection["campaign"]
        graph = self.graph(campaign_id)
        artifacts = self.artifacts(campaign_id)
        return {
            "campaign_id": campaign["id"],
            "path": campaign["bundle_path"] or campaign["id"],
            "name": campaign["title"],
            "workspace_root": str((self.root / campaign["workspace_root"]).resolve()) if not Path(campaign["workspace_root"]).is_absolute() else campaign["workspace_root"],
            "status": campaign["status"],
            "budget": {"total_usd": None, "limit_usd": campaign["budget_cap_usd"], "metadata": {"tier": campaign["tier"], "output_format": campaign["output_format"]}},
            "stages": store_stages_from_graph(graph, artifacts, self.root, campaign["workspace_root"]),
            "metadata": {"source": EVENT_PROJECTION_SOURCE, "graph_version": graph["version"], "graph_state": graph["state"], "objective": campaign["objective"]},
            "provenance": {
                "reader": "msc_sdk.campaign_store.CampaignStore",
                "source": EVENT_PROJECTION_SOURCE,
                "cache": str(self.db_path),
            },
        }

    def graph(self, ref: str | Path) -> dict[str, Any]:
        campaign_id = self.resolve_ref(ref)
        graph = self._project_campaign(campaign_id)["graph"]
        artifacts = self.artifacts(campaign_id)
        by_stage: dict[str, list[dict[str, Any]]] = {}
        optional_by_stage: dict[str, list[dict[str, Any]]] = {}
        for stage in artifacts["stages"]:
            by_stage[stage["stage_id"]] = stage["required_artifacts"]
            optional_by_stage[stage["stage_id"]] = stage["optional_artifacts"]
        for node in graph["nodes"]:
            node["required_artifacts"] = by_stage.get(node["id"], [])
            node["optional_artifacts"] = optional_by_stage.get(node["id"], [])
        return graph

    def artifacts(self, ref: str | Path, stage_id: str | None = None) -> dict[str, Any]:
        campaign_id = self.resolve_ref(ref)
        rows = self._project_campaign(campaign_id)["artifact_rows"]
        if stage_id is not None:
            rows = [row for row in rows if row.get("stage_id") == stage_id]
        collapsed = self._collapse_artifact_rows(rows)
        stages: dict[str, dict[str, Any]] = {}
        for row in collapsed:
            item = artifact_row_to_dict(row, self.root)
            bucket = stages.setdefault(item["stage_id"] or "", {"stage_id": item["stage_id"] or "", "required_artifacts": [], "optional_artifacts": []})
            if item["required"]:
                bucket["required_artifacts"].append(item)
            else:
                bucket["optional_artifacts"].append(item)
        return {"campaign": campaign_id, "stages": list(stages.values())}

    def _collapse_artifact_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prefer the most useful product artifact per stage/path.

        Declarations, legacy runtime files, and run-scoped contract artifacts
        can all describe the same logical output. Product read models should
        default to the best concrete artifact while events retain the full
        history.
        """

        def rank(row: dict[str, Any]) -> tuple[int, str]:
            metadata = json.loads(row.get("metadata_json") or "{}")
            status = str(row.get("status") or "")
            source_role = str(metadata.get("source_role") or "")
            has_run = bool(metadata.get("run_id"))
            concrete = status == "existing" or row.get("checksum") is not None
            role_rank = {
                "contract_runtime": 4,
                "runtime": 3,
                "runtime_legacy": 2,
                "stage_summary": 1,
            }.get(source_role, 0)
            return (10 if concrete else 0) + role_rank + (1 if has_run else 0), str(row.get("id") or "")

        by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("stage_id") or ""), str(row.get("path") or ""), int(row.get("required") or 0))
            current = by_key.get(key)
            if current is None or rank(row) > rank(current):
                by_key[key] = row
        return sorted(by_key.values(), key=lambda row: (str(row.get("stage_id") or ""), -int(row.get("required") or 0), str(row.get("path") or "")))

    def events(self, ref: str | Path, *, limit: int | None = None) -> dict[str, Any]:
        campaign_id = self.resolve_ref(ref)
        events = self._event_rows(campaign_id)
        if limit is not None:
            events = events[-limit:]
        return {"ok": True, "campaign": campaign_id, "events": events}

    def _project_campaign(self, campaign_id: str) -> dict[str, Any]:
        events = self._event_rows(campaign_id)
        return CampaignEventProjector(self.root).project(
            campaign_id,
            events,
            legacy_campaign=self._legacy_campaign_row(campaign_id),
            legacy_graph=self._legacy_snapshot_graph(campaign_id),
            legacy_artifact_rows=list(self._legacy_artifact_rows(campaign_id).values()),
        )

    def _event_rows(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM campaign_events WHERE campaign_id=? ORDER BY created_at, rowid",
                (campaign_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "campaign_id": row["campaign_id"],
                "type": row["type"],
                "actor": row["actor"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def _legacy_campaign_row(self, campaign_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        return dict(row) if row is not None else None

    def _legacy_snapshot_graph(self, campaign_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            snapshot = conn.execute(
                "SELECT * FROM graph_snapshots WHERE campaign_id=? ORDER BY version DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
        if snapshot is None:
            return {
                "campaign": campaign_id,
                "version": 0,
                "state": "planned",
                "nodes": [],
                "edges": [],
                "metadata": {"read_source": "empty"},
            }
        graph = json.loads(snapshot["graph_json"])
        graph["state"] = snapshot["state"]
        graph["version"] = snapshot["version"]
        graph["metadata"] = {**dict(graph.get("metadata") or {}), "read_source": "legacy_snapshot_fallback"}
        return graph

    def _legacy_artifact_rows(self, campaign_id: str) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE campaign_id=? ORDER BY stage_id, required DESC, path",
                (campaign_id,),
            ).fetchall()
        return {str(row["id"]): dict(row) for row in rows}

    def replay_jsonl(self) -> int:
        if not self.event_log_path.exists():
            return 0
        count = 0
        with self.connect() as conn:
            for line in self.event_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") == "CampaignCreated":
                    payload = event.get("payload") or {}
                    campaign_id = event["campaign_id"]
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO campaigns
                        (id, title, objective, status, created_at, updated_at, workspace_root,
                         budget_cap_usd, tier, output_format, bundle_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            payload.get("title") or campaign_id,
                            payload.get("objective") or "",
                            "draft",
                            event["created_at"],
                            event["created_at"],
                            str(Path("results") / campaign_id),
                            payload.get("budget"),
                            payload.get("tier"),
                            payload.get("output_format"),
                            str(self.root / "campaigns" / campaign_id),
                        ),
                    )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO campaign_events
                    (id, campaign_id, type, actor, created_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["id"],
                        event["campaign_id"],
                        event["type"],
                        event["actor"],
                        event["created_at"],
                        json_dumps(event["payload"]),
                    ),
                )
                count += 1
        return count

    def write_scaffold_artifacts(self, campaign_id: str) -> None:
        campaign = self.get_campaign(campaign_id)
        graph = self.graph(campaign_id)
        for node in graph["nodes"]:
            workspace = self.root / campaign["workspace_root"] / node["id"]
            for artifact in [*node.get("required_artifacts", []), *node.get("optional_artifacts", [])]:
                target = workspace / artifact["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(scaffold_text(campaign["title"], campaign["objective"], node), encoding="utf-8")
        self.refresh_artifact_files(campaign_id)

    def refresh_artifact_files(self, campaign_id: str) -> None:
        artifacts = self.artifacts(campaign_id)
        with self.connect() as conn:
            for stage in artifacts["stages"]:
                for artifact in [*stage["required_artifacts"], *stage["optional_artifacts"]]:
                    full = self.root / artifact["workspace"] / artifact["path"]
                    if full.exists():
                        checksum = file_checksum(full)
                        row = conn.execute("SELECT status, checksum FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()
                        conn.execute(
                            "UPDATE artifacts SET status=?, size_bytes=?, checksum=? WHERE id=?",
                            ("existing", full.stat().st_size, checksum, artifact["id"]),
                        )
                        if row is None or row["status"] != "existing" or row["checksum"] != checksum:
                            self._append_event(
                                conn,
                                campaign_id=campaign_id,
                                event_type="ArtifactIndexed",
                                actor="system",
                                payload={
                                    "artifact_id": artifact["id"],
                                    "stage_id": artifact.get("stage_id"),
                                    "path": artifact["path"],
                                    "kind": artifact.get("kind"),
                                    "required": artifact.get("required"),
                                    "producer_node_id": artifact.get("stage_id"),
                                    "workspace": artifact["workspace"],
                                    "size_bytes": full.stat().st_size,
                                    "checksum": checksum,
                                    "source_role": artifact.get("source_role"),
                                    "metadata": artifact.get("metadata") or {},
                                },
                            )

    def _runtime_workspaces_for_campaign(self, conn: sqlite3.Connection, campaign_id: str) -> list[str]:
        workspaces: list[str] = []
        for row in conn.execute("SELECT metadata_json FROM runs WHERE campaign_id=? ORDER BY started_at", (campaign_id,)).fetchall():
            metadata = json.loads(row["metadata_json"] or "{}")
            workspace = metadata.get("workspace_dir")
            if workspace and workspace not in workspaces:
                workspaces.append(str(workspace))
        for row in conn.execute(
            "SELECT payload_json FROM campaign_events WHERE campaign_id=? AND type IN ('RunExited', 'GraphNodeStatusChanged') ORDER BY created_at",
            (campaign_id,),
        ).fetchall():
            payload = json.loads(row["payload_json"] or "{}")
            workspace = payload.get("workspace_dir")
            if workspace and workspace not in workspaces:
                workspaces.append(str(workspace))
        return workspaces

    def _index_runtime_workspace_files(self, conn: sqlite3.Connection, campaign_id: str) -> None:
        contracts = contracts_by_id()
        for workspace in self._runtime_workspaces_for_campaign(conn, campaign_id):
            workspace_path = self.root / workspace
            if not workspace_path.exists():
                continue
            for stage_id, contract in contracts.items():
                artifacts = [
                    *[(artifact, True) for artifact in contract.required_artifacts],
                    *[(artifact, False) for artifact in contract.optional_artifacts],
                ]
                for artifact, required in artifacts:
                    for rel_path in (artifact.path, *artifact.legacy_paths):
                        full = workspace_path / rel_path
                        if not full.exists() or not full.is_file():
                            continue
                        self._upsert_runtime_artifact(
                            conn,
                            campaign_id=campaign_id,
                            stage_id=stage_id,
                            rel_path=rel_path,
                            full=full,
                            workspace=workspace,
                            required=required,
                            contract_path=artifact.path,
                            source_role="runtime_legacy" if rel_path != artifact.path else "runtime",
                        )

            summaries = workspace_path / "stage_summaries"
            if summaries.exists():
                for full in summaries.glob("*_summary.*"):
                    if not full.is_file():
                        continue
                    stage_id = full.stem.removesuffix("_summary")
                    rel_path = str(full.relative_to(workspace_path))
                    self._upsert_runtime_artifact(
                        conn,
                        campaign_id=campaign_id,
                        stage_id=stage_id,
                        rel_path=rel_path,
                        full=full,
                        workspace=workspace,
                        required=False,
                        contract_path=None,
                        source_role="stage_summary",
                    )

            for rel_path in ("run_summary.json", "run_status.json", "STATUS.txt", "budget_state.json", "budget_ledger.jsonl", "effective_models.json"):
                full = workspace_path / rel_path
                if full.exists() and full.is_file():
                    self._upsert_runtime_artifact(
                        conn,
                        campaign_id=campaign_id,
                        stage_id="_run",
                        rel_path=rel_path,
                        full=full,
                        workspace=workspace,
                        required=False,
                        contract_path=None,
                        source_role="run_metadata",
                    )

    def _upsert_runtime_artifact(
        self,
        conn: sqlite3.Connection,
        *,
        campaign_id: str,
        stage_id: str,
        rel_path: str,
        full: Path,
        workspace: str,
        required: bool,
        contract_path: str | None,
        source_role: str,
    ) -> None:
        artifact_id = f"{campaign_id}:{stage_id}:{rel_path}:runtime"
        checksum = file_checksum(full)
        metadata = {
            "workspace": workspace,
            "source_role": source_role,
            "audience": artifact_audience(source_role=source_role, required=required),
        }
        if contract_path:
            metadata["contract_path"] = contract_path
        previous = conn.execute("SELECT status, checksum FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        conn.execute(
            """
            INSERT OR REPLACE INTO artifacts
            (id, campaign_id, stage_id, path, kind, required, status, producer_node_id,
             size_bytes, checksum, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                campaign_id,
                stage_id,
                rel_path,
                full.suffix.replace(".", "") or "text",
                1 if required else 0,
                "existing",
                stage_id,
                full.stat().st_size,
                checksum,
                json_dumps(metadata),
            ),
        )
        if previous is None or previous["status"] != "existing" or previous["checksum"] != checksum:
            self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type="ArtifactIndexed",
                actor="system",
                payload={
                    "artifact_id": artifact_id,
                    "stage_id": stage_id,
                    "path": rel_path,
                    "kind": full.suffix.replace(".", "") or "text",
                    "required": required,
                    "producer_node_id": stage_id,
                    "workspace": workspace,
                    "size_bytes": full.stat().st_size,
                    "checksum": checksum,
                    "source_role": source_role,
                    "metadata": {"contract_path": contract_path} if contract_path else {},
                },
            )

    def write_readme(self, campaign_id: str) -> None:
        campaign = self.get_campaign(campaign_id)
        campaign_dir = self.root / "campaigns" / campaign_id
        campaign_dir.mkdir(parents=True, exist_ok=True)
        (campaign_dir / "README.md").write_text(
            f"# {campaign['title']}\n\n{campaign['objective']}\n\nThis campaign is indexed in `.msc/campaigns.db` and exported as a JSON campaign bundle.\n",
            encoding="utf-8",
        )

    def write_snapshot(self, campaign_id: str) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        graph = self.graph(campaign_id)
        (self.snapshot_dir / f"{campaign_id}.graph.json").write_text(json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8")

    def _campaign_delete_paths(self, campaign_id: str, campaign: dict[str, Any]) -> list[Path]:
        paths: list[Path] = [
            self.root / "campaigns" / campaign_id,
            self.root / "results" / campaign_id,
            self.snapshot_dir / f"{campaign_id}.graph.json",
            self.root / ".msc" / "openclaude_chats" / f"{safe_chat_name(campaign_id)}.json",
        ]
        bundle_path = campaign.get("bundle_path")
        if bundle_path:
            paths.append(self._rooted_path(bundle_path))
        workspace_root = campaign.get("workspace_root")
        if workspace_root:
            paths.append(self._rooted_path(workspace_root))

        unique: list[Path] = []
        seen: set[Path] = set()
        for item in paths:
            resolved = item.resolve()
            if resolved not in seen:
                unique.append(resolved)
                seen.add(resolved)
        return unique

    def _rooted_path(self, value: str | Path) -> Path:
        path_value = Path(value)
        return path_value if path_value.is_absolute() else self.root / path_value

    def _remove_path_inside_root(self, target: Path) -> dict[str, Any]:
        resolved = target.resolve()
        if not path_is_inside(resolved, self.root):
            return {"removed": False, "path": str(resolved), "reason": "outside_root"}
        if not resolved.exists():
            return {"removed": False, "path": "", "reason": "missing"}
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        return {"removed": True, "path": str(resolved)}

    def _purge_campaign_from_event_log(self, campaign_id: str) -> None:
        if not self.event_log_path.exists():
            return
        kept: list[str] = []
        for line in self.event_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if event.get("campaign_id") != campaign_id:
                kept.append(line)
        content = "\n".join(kept)
        self.event_log_path.write_text(f"{content}\n" if content else "", encoding="utf-8")

    def _unique_campaign_id(self, base: str) -> str:
        candidate = base
        index = 2
        with self.connect() as conn:
            while conn.execute("SELECT 1 FROM campaigns WHERE id=?", (candidate,)).fetchone() is not None:
                candidate = f"{base}-{index}"
                index += 1
        return candidate

    def _write_graph(self, conn: sqlite3.Connection, campaign_id: str, graph: dict[str, Any]) -> None:
        graph_id = f"{campaign_id}:graph:{graph['version']}"
        conn.execute(
            "INSERT INTO graph_snapshots(id, campaign_id, version, state, created_at, graph_json) VALUES (?, ?, ?, ?, ?, ?)",
            (graph_id, campaign_id, graph["version"], graph["state"], now_iso(), json_dumps(graph)),
        )
        for node in graph["nodes"]:
            conn.execute(
                """
                INSERT INTO graph_nodes
                (campaign_id, graph_version, node_id, type, title, status, budget_policy_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    graph["version"],
                    node["id"],
                    node["type"],
                    node["title"],
                    node["status"],
                    json_dumps(node.get("budgetPolicy") or {}),
                    json_dumps(node.get("metadata") or {}),
                ),
            )
        for edge in graph["edges"]:
            conn.execute(
                "INSERT INTO graph_edges(campaign_id, graph_version, source, target, kind, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                (campaign_id, graph["version"], edge["source"], edge["target"], edge["kind"], json_dumps(edge.get("metadata") or {})),
            )
        self._append_event(
            conn,
            campaign_id=campaign_id,
            event_type="GraphProjected",
            actor="system",
            payload={
                "graph_version": graph["version"],
                "state": graph["state"],
                "graph": graph,
            },
        )

    def _declare_graph_artifacts(self, conn: sqlite3.Connection, campaign_id: str, graph: dict[str, Any]) -> None:
        for node in graph["nodes"]:
            for required, paths in ((True, node.get("outputs", [])), (False, node.get("optionalOutputs", []))):
                for artifact_path in paths:
                    artifact_id = f"{campaign_id}:{node['id']}:{artifact_path}"
                    kind = Path(artifact_path).suffix.replace(".", "") or "markdown"
                    metadata = {
                        "graph_version": graph["version"],
                        "workspace": node.get("workspace") or str(Path("results") / campaign_id / node["id"]),
                        "audience": "deliverable" if required else "evidence",
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
                            campaign_id,
                            node["id"],
                            artifact_path,
                            kind,
                            1 if required else 0,
                            "declared",
                            node["id"],
                            None,
                            None,
                            json_dumps(metadata),
                        ),
                    )
                    self._append_event(
                        conn,
                        campaign_id=campaign_id,
                        event_type="ArtifactDeclared",
                        actor="system",
                        payload={
                            "artifact_id": artifact_id,
                            "stage_id": node["id"],
                            "path": artifact_path,
                            "kind": kind,
                            "required": required,
                            "producer_node_id": node["id"],
                            "workspace": metadata["workspace"],
                            "metadata": metadata,
                        },
                    )

    def _import_bundle_artifacts(
        self,
        conn: sqlite3.Connection,
        campaign_id: str,
        artifacts: dict[str, Any],
        *,
        original_id: str | None = None,
    ) -> None:
        for stage in artifacts.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            stage_id = str(stage.get("stage_id") or stage.get("id") or "")
            for required, key in ((True, "required_artifacts"), (False, "optional_artifacts")):
                for artifact in stage.get(key) or []:
                    if not isinstance(artifact, dict):
                        continue
                    artifact_path = str(artifact.get("path") or "")
                    if not artifact_path:
                        continue
                    metadata = dict(artifact.get("metadata") or {})
                    if artifact.get("workspace") and "workspace" not in metadata:
                        metadata["workspace"] = artifact["workspace"]
                    if original_id and metadata.get("workspace"):
                        metadata["workspace"] = remap_campaign_workspace(str(metadata["workspace"]), original_id, campaign_id)
                    artifact_id = str(
                        artifact.get("id")
                        or f"{campaign_id}:{stage_id}:{artifact_path}:{'required' if required else 'optional'}"
                    )
                    if not artifact_id.startswith(f"{campaign_id}:"):
                        artifact_id = f"{campaign_id}:{stage_id}:{artifact_path}:{'required' if required else 'optional'}"
                    kind = str(artifact.get("kind") or artifact.get("type") or Path(artifact_path).suffix.replace(".", "") or "artifact")
                    status = str(artifact.get("status") or ("existing" if artifact.get("exists") else "declared"))
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO artifacts
                        (id, campaign_id, stage_id, path, kind, required, status, producer_node_id,
                         size_bytes, checksum, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            artifact_id,
                            campaign_id,
                            stage_id,
                            artifact_path,
                            kind,
                            1 if required else 0,
                            status,
                            str(artifact.get("producer_node_id") or stage_id or ""),
                            artifact.get("size_bytes"),
                            artifact.get("checksum"),
                            json_dumps(metadata),
                        ),
                    )
                    declaration_payload = {
                        "artifact_id": artifact_id,
                        "stage_id": stage_id,
                        "path": artifact_path,
                        "kind": kind,
                        "required": required,
                        "producer_node_id": str(artifact.get("producer_node_id") or stage_id or ""),
                        "workspace": metadata.get("workspace"),
                        "metadata": metadata,
                    }
                    self._append_event(
                        conn,
                        campaign_id=campaign_id,
                        event_type="ArtifactDeclared",
                        actor="system",
                        payload=declaration_payload,
                    )
                    if status == "existing" or artifact.get("exists"):
                        self._append_event(
                            conn,
                            campaign_id=campaign_id,
                            event_type="ArtifactIndexed",
                            actor="system",
                            payload={
                                **declaration_payload,
                                "size_bytes": artifact.get("size_bytes"),
                                "checksum": artifact.get("checksum"),
                                "source_role": metadata.get("source_role"),
                            },
                        )

    def _campaign_state_event(
        self,
        campaign_ref: str | Path,
        status: str,
        event_type: str,
        *,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        campaign_id = self.resolve_ref(campaign_ref)
        with self.connect() as conn:
            conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", (status, now_iso(), campaign_id))
            event = self._append_event(
                conn,
                campaign_id=campaign_id,
                event_type=event_type,
                actor=actor,
                payload=payload,
            )
        return {"ok": True, "campaign_id": campaign_id, "status": status, "event": event}

    def _create_approval(
        self,
        conn: sqlite3.Connection,
        *,
        campaign_id: str,
        target_type: str,
        target_id: str,
        actor: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = now_iso()
        approval_id = stable_id(campaign_id, "approval", target_type, target_id, created_at)
        conn.execute(
            """
            INSERT INTO approvals
            (id, campaign_id, target_type, target_id, status, created_at, decided_at, actor, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                campaign_id,
                target_type,
                target_id,
                "pending",
                created_at,
                None,
                actor,
                json_dumps(metadata),
            ),
        )
        self._append_event(
            conn,
            campaign_id=campaign_id,
            event_type="ApprovalRequested",
            actor=actor,
            payload={
                "approval_id": approval_id,
                "target_type": target_type,
                "target_id": target_id,
                "metadata": metadata,
            },
        )
        return {
            "id": approval_id,
            "campaign_id": campaign_id,
            "target_type": target_type,
            "target_id": target_id,
            "status": "pending",
            "created_at": created_at,
            "metadata": metadata,
        }

    def _append_event(
        self,
        conn: sqlite3.Connection,
        *,
        campaign_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = now_iso()
        clean_payload = redact(payload)
        event = {
            "id": stable_id(campaign_id, event_type, actor, created_at, json_dumps(clean_payload)),
            "campaign_id": campaign_id,
            "type": event_type,
            "actor": actor,
            "created_at": created_at,
            "payload": clean_payload,
        }
        conn.execute(
            """
            INSERT INTO campaign_events(id, campaign_id, type, actor, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event["id"], campaign_id, event_type, actor, created_at, json_dumps(clean_payload)),
        )
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event


def build_graph_ir(campaign_id: str, title: str, template: str, tier: str, budget: float) -> dict[str, Any]:
    template = template if template in TEMPLATE_NAMES else TARGET_RESEARCH_TEMPLATE
    graph = compile_kernel_graph(
        graph_id=f"{campaign_id}:{template}",
        template=template,
        budget=budget,
    )
    return project_kernel_graph(
        graph=graph,
        campaign_id=campaign_id,
        title=title,
        template=template,
        tier=tier,
    )


def normalize_imported_graph(graph: dict[str, Any], campaign_id: str, *, original_id: str | None = None) -> dict[str, Any]:
    normalized = json.loads(json.dumps(graph))
    normalized["campaign"] = campaign_id
    normalized["version"] = int(normalized.get("version") or 1)
    normalized["state"] = str(normalized.get("state") or "planned")
    nodes = []
    for index, node in enumerate(normalized.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or node.get("stage_id") or f"stage_{index + 1}")
        outputs = [str(item) for item in node.get("outputs") or []]
        optional_outputs = [str(item) for item in node.get("optionalOutputs") or node.get("optional_outputs") or []]
        nodes.append(
            {
                "id": node_id,
                "type": str(node.get("type") or f"agent.{node_id.replace('_agent', '')}"),
                "title": str(node.get("title") or node.get("label") or title_for_stage(node_id)),
                "status": str(node.get("status") or "planned"),
                "inputs": [str(item) for item in node.get("inputs") or []],
                "outputs": outputs,
                "optionalOutputs": optional_outputs,
                "budgetPolicy": dict(node.get("budgetPolicy") or node.get("budget_policy") or {}),
                "workspace": remap_campaign_workspace(
                    str(node.get("workspace") or Path("results") / campaign_id / node_id),
                    original_id,
                    campaign_id,
                ),
                "metadata": dict(node.get("metadata") or {}),
            }
        )
    edges = []
    for edge in normalized.get("edges") or []:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            continue
        edges.append(
            {
                "source": str(edge["source"]),
                "target": str(edge["target"]),
                "kind": str(edge.get("kind") or "stage_order"),
                "metadata": dict(edge.get("metadata") or {}),
            }
        )
    if not edges:
        edges = [
            {"source": nodes[index]["id"], "target": nodes[index + 1]["id"], "kind": "stage_order", "metadata": {}}
            for index in range(len(nodes) - 1)
        ]
    normalized["nodes"] = nodes
    normalized["edges"] = edges
    return normalized


def remap_campaign_workspace(workspace: str, original_id: str | None, campaign_id: str) -> str:
    if not original_id or original_id == campaign_id:
        return workspace
    parts = Path(workspace).parts
    if len(parts) >= 2 and parts[0] == "results" and parts[1] == original_id:
        return str(Path("results", campaign_id, *parts[2:]))
    return workspace.replace(f"/{original_id}/", f"/{campaign_id}/")


def title_for_stage(stage_id: str) -> str:
    contract = contracts_by_id().get(stage_id)
    if contract:
        return contract.title
    return stage_id.replace("_agent", "").replace("_", " ").title()


def artifact_row_to_dict(row: dict[str, Any], root: Path) -> dict[str, Any]:
    metadata = json.loads(row.get("metadata_json") or "{}")
    workspace = metadata.get("workspace") or str(Path("results") / row["campaign_id"] / (row.get("stage_id") or ""))
    full = root / workspace / row["path"]
    source_role = str(metadata.get("source_role") or "raw")
    audience = str(metadata.get("audience") or "")
    status = "existing" if full.exists() else row["status"]
    if full.exists() and source_role == "raw" and looks_like_scaffold_artifact(full):
        source_role = "scaffold_prompt"
        audience = "prompt"
        status = "scaffold_prompt"
        metadata = {**metadata, "source_role": source_role, "audience": audience}
    return {
        "id": row["id"],
        "path": row["path"],
        "kind": row["kind"],
        "type": row["kind"],
        "exists": full.exists() or row["status"] == "existing",
        "status": status,
        "size_bytes": full.stat().st_size if full.exists() else row["size_bytes"],
        "source_role": source_role,
        "audience": audience or artifact_audience(source_role=source_role, required=bool(row["required"])),
        "required": bool(row["required"]),
        "stage_id": row.get("stage_id"),
        "workspace": workspace,
        "metadata": metadata,
    }


def looks_like_scaffold_artifact(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return False
    if "\x00" in text:
        return False
    markers = [
        "Campaign:",
        "Stage:",
        "Kind:",
        "Contract:",
        "Tool families:",
        "Human pause policy:",
        "Failure policy: stop_and_await_human_feedback",
        "Research objective:",
    ]
    return sum(1 for marker in markers if marker in text) >= 5


def store_stages_from_graph(graph: dict[str, Any], artifacts: dict[str, Any], root: Path, workspace_root: str) -> list[dict[str, Any]]:
    by_stage = {stage["stage_id"]: stage for stage in artifacts.get("stages", [])}
    stages = []
    for node in graph.get("nodes", []):
        stage_artifacts = by_stage.get(node["id"], {"required_artifacts": [], "optional_artifacts": []})
        stages.append(
            {
                "stage_id": node["id"],
                "status": node.get("status", "planned"),
                "workspace": str(root / node.get("workspace", str(Path(workspace_root) / node["id"]))),
                "required_artifacts": stage_artifacts["required_artifacts"],
                "optional_artifacts": stage_artifacts["optional_artifacts"],
                "budget": {"total_usd": None, "limit_usd": (node.get("budgetPolicy") or {}).get("maxUsd"), "metadata": {}},
                "logs": [],
                "metadata": node.get("metadata") or {},
            }
        )
    return stages


def scaffold_text(title: str, objective: str, node: dict[str, Any]) -> str:
    metadata = node.get("metadata") or {}
    validators = ", ".join(metadata.get("validators") or []) or "none declared"
    tools = ", ".join(metadata.get("toolFamilies") or []) or "none declared"
    pause_policy = ", ".join(metadata.get("humanPausePolicy") or []) or "none"
    return "\n".join(
        [
            f"# {node['title']}",
            "",
            f"Campaign: {title}",
            f"Stage: {node['id']}",
            f"Kind: {metadata.get('kind') or node.get('type') or 'node'}",
            "",
            "Purpose:",
            metadata.get("purpose") or "Zero-spend scaffold artifact for graph, artifact, and preview integration testing.",
            "",
            "Contract:",
            f"- Validators: {validators}",
            f"- Tool families: {tools}",
            f"- Human pause policy: {pause_policy}",
            f"- Failure policy: {metadata.get('failurePolicy') or 'stop_and_await_human_feedback'}",
            "",
            "Research objective:",
            objective,
            "",
        ]
    )


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
