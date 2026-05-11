"""Local-first campaign store backed by project-local SQLite.

The store is intentionally local and boring: SQLite is the operational index,
JSON bundles are explicit import/export artifacts, and normal research outputs
remain visible in the repo.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import redact
from .stage_contracts import build_contract_graph, contracts_by_id, template_names


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
        template: str = "consortium_scaffold",
        budget: float = 1.0,
        tier: str = "budget",
        output_format: str = "markdown",
        actor: str = "user",
    ) -> dict[str, Any]:
        title = title.strip()
        objective = objective.strip()
        if not title:
            raise ValueError("Campaign title is required.")
        if not objective:
            raise ValueError("Research objective is required.")
        template = template if template in TEMPLATE_NAMES else "consortium_scaffold"
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
                    "budget": budget,
                    "tier": tier,
                    "output_format": output_format,
                },
            )
            self._write_graph(conn, campaign_id, graph)
            self._declare_graph_artifacts(conn, campaign_id, graph)

        if template == "consortium_scaffold":
            self.write_scaffold_artifacts(campaign_id)
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
            approval = None
            if run_status != "completed":
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
                    "workspace": workspace,
                    "run_id": run_id,
                    "size_bytes": full.stat().st_size,
                    "checksum": checksum,
                    "source_role": "contract_runtime",
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
                payload={"message_id": message_id, "run_id": run_id, "direction": direction, "text": text, "type": instruction_type},
            )
        return {"ok": True, "campaign_id": campaign_id, "message_id": message_id, "event": event}

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
        campaign = self.get_campaign(ref)
        graph = self.graph(campaign["id"])
        artifacts = self.artifacts(campaign["id"])
        return {
            "campaign_id": campaign["id"],
            "path": campaign["bundle_path"] or campaign["id"],
            "name": campaign["title"],
            "workspace_root": str((self.root / campaign["workspace_root"]).resolve()) if not Path(campaign["workspace_root"]).is_absolute() else campaign["workspace_root"],
            "status": campaign["status"],
            "budget": {"total_usd": None, "limit_usd": campaign["budget_cap_usd"], "metadata": {"tier": campaign["tier"], "output_format": campaign["output_format"]}},
            "stages": store_stages_from_graph(graph, artifacts, self.root, campaign["workspace_root"]),
            "metadata": {"source": "sqlite", "graph_version": graph["version"], "graph_state": graph["state"], "objective": campaign["objective"]},
            "provenance": {"reader": "msc_sdk.campaign_store.CampaignStore", "source": str(self.db_path)},
        }

    def graph(self, ref: str | Path) -> dict[str, Any]:
        campaign_id = self.resolve_ref(ref)
        with self.connect() as conn:
            snapshot = conn.execute(
                "SELECT * FROM graph_snapshots WHERE campaign_id=? ORDER BY version DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if snapshot is None:
                return {"campaign": campaign_id, "version": 0, "state": "planned", "nodes": [], "edges": []}
            graph = json.loads(snapshot["graph_json"])
            graph["state"] = snapshot["state"]
            graph["version"] = snapshot["version"]
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
        with self.connect() as conn:
            if stage_id is None:
                rows = conn.execute("SELECT * FROM artifacts WHERE campaign_id=? ORDER BY stage_id, required DESC, path", (campaign_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE campaign_id=? AND stage_id=? ORDER BY required DESC, path",
                    (campaign_id, stage_id),
                ).fetchall()
        collapsed = self._collapse_artifact_rows([dict(row) for row in rows])
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
        query = "SELECT * FROM campaign_events WHERE campaign_id=? ORDER BY created_at"
        params: tuple[Any, ...] = (campaign_id,)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        events = [
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
        if limit is not None:
            events = events[-limit:]
        return {"ok": True, "campaign": campaign_id, "events": events}

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
                                    "size_bytes": full.stat().st_size,
                                    "checksum": checksum,
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
                    "workspace": workspace,
                    "size_bytes": full.stat().st_size,
                    "checksum": checksum,
                    "source_role": source_role,
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

    def _declare_graph_artifacts(self, conn: sqlite3.Connection, campaign_id: str, graph: dict[str, Any]) -> None:
        for node in graph["nodes"]:
            for required, paths in ((True, node.get("outputs", [])), (False, node.get("optionalOutputs", []))):
                for artifact_path in paths:
                    artifact_id = f"{campaign_id}:{node['id']}:{artifact_path}"
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
                            Path(artifact_path).suffix.replace(".", "") or "markdown",
                            1 if required else 0,
                            "declared",
                            node["id"],
                            None,
                            None,
                            json_dumps({"graph_version": graph["version"], "audience": "deliverable" if required else "evidence"}),
                        ),
                    )
                    self._append_event(
                        conn,
                        campaign_id=campaign_id,
                        event_type="ArtifactDeclared",
                        actor="system",
                        payload={"stage_id": node["id"], "path": artifact_path, "required": required},
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
                            str(artifact.get("kind") or artifact.get("type") or Path(artifact_path).suffix.replace(".", "") or "artifact"),
                            1 if required else 0,
                            str(artifact.get("status") or ("existing" if artifact.get("exists") else "declared")),
                            str(artifact.get("producer_node_id") or stage_id or ""),
                            artifact.get("size_bytes"),
                            artifact.get("checksum"),
                            json_dumps(metadata),
                        ),
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
    template = template if template in TEMPLATE_NAMES else "consortium_scaffold"
    return build_contract_graph(
        campaign_id=campaign_id,
        title=title,
        template=template,
        tier=tier,
        budget=budget,
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


def artifact_audience(*, source_role: str, required: bool) -> str:
    if source_role in {"run_metadata"}:
        return "system_state"
    if source_role in {"stage_summary"}:
        return "diagnostic"
    if source_role in {"prompt", "system_prompt"}:
        return "prompt"
    if source_role in {"log"}:
        return "log"
    return "deliverable" if required else "evidence"


def artifact_row_to_dict(row: dict[str, Any], root: Path) -> dict[str, Any]:
    metadata = json.loads(row.get("metadata_json") or "{}")
    workspace = metadata.get("workspace") or str(Path("results") / row["campaign_id"] / (row.get("stage_id") or ""))
    full = root / workspace / row["path"]
    return {
        "id": row["id"],
        "path": row["path"],
        "kind": row["kind"],
        "type": row["kind"],
        "exists": full.exists() or row["status"] == "existing",
        "status": "existing" if full.exists() else row["status"],
        "size_bytes": full.stat().st_size if full.exists() else row["size_bytes"],
        "source_role": metadata.get("source_role", "raw"),
        "audience": metadata.get("audience") or artifact_audience(source_role=metadata.get("source_role", "raw"), required=bool(row["required"])),
        "required": bool(row["required"]),
        "stage_id": row.get("stage_id"),
        "workspace": workspace,
        "metadata": metadata,
    }


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
