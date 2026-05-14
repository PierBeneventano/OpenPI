"""Campaign event projection helpers.

This module owns the read-side reconstruction of campaign state from campaign
events. Storage backends may pass cache rows as fallbacks, but product views
should be derived here rather than from SQLite tables directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EVENT_PROJECTION_SOURCE = "campaign_events"


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def artifact_audience(*, source_role: str, required: bool) -> str:
    if source_role in {"run_metadata"}:
        return "system_state"
    if source_role in {"stage_summary"}:
        return "diagnostic"
    if source_role in {"prompt", "system_prompt", "scaffold_prompt"}:
        return "prompt"
    if source_role in {"log"}:
        return "log"
    return "deliverable" if required else "evidence"


class CampaignEventProjector:
    """Rebuild campaign read state from append-only campaign events."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def project(
        self,
        campaign_id: str,
        events: list[dict[str, Any]],
        *,
        legacy_campaign: dict[str, Any] | None = None,
        legacy_graph: dict[str, Any] | None = None,
        legacy_artifact_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "campaign": self._campaign_header(campaign_id, events, legacy_campaign=legacy_campaign),
            "graph": self._graph(campaign_id, events, legacy_graph=legacy_graph),
            "artifact_rows": self._artifact_rows(campaign_id, events, legacy_artifact_rows=legacy_artifact_rows or []),
            "events": events,
        }

    def _campaign_header(
        self,
        campaign_id: str,
        events: list[dict[str, Any]],
        *,
        legacy_campaign: dict[str, Any] | None,
    ) -> dict[str, Any]:
        created = next((event for event in events if event["type"] == "CampaignCreated"), None)
        payload = dict(created["payload"]) if created else {}
        created_at = created["created_at"] if created else str((legacy_campaign or {}).get("created_at") or "")
        status = str((legacy_campaign or {}).get("status") or "draft")
        updated_at = created_at
        bundle_path = str((legacy_campaign or {}).get("bundle_path") or self.root / "campaigns" / campaign_id)
        for event in events:
            updated_at = event["created_at"]
            event_type = event["type"]
            event_payload = event["payload"]
            if event_type == "GraphApproved":
                status = "approved"
            elif event_type == "GraphChangeProposed":
                status = "pending_approval"
            elif event_type == "CampaignPaused":
                status = "paused"
            elif event_type == "CampaignResumed":
                status = "approved"
            elif event_type == "CampaignStopped":
                status = "stopped"
            elif event_type in {"RunStarted", "CampaignExecutionStarted"}:
                status = "running"
            elif event_type in {"RunExited", "CampaignExecutionCompleted", "CampaignExecutionFailed"}:
                status = "completed" if event_payload.get("status") == "completed" else "human_decision_required"
            elif event_type == "ApprovalRequested":
                status = "human_decision_required"
            elif event_type == "ApprovalDecided":
                status = "approved" if event_payload.get("status") == "approved" else "draft"
            elif event_type == "CampaignExported":
                bundle_path = str(event_payload.get("bundle_path") or bundle_path)
        return {
            "id": campaign_id,
            "title": str(payload.get("title") or (legacy_campaign or {}).get("title") or campaign_id),
            "objective": str(payload.get("objective") or (legacy_campaign or {}).get("objective") or ""),
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
            "workspace_root": str(payload.get("workspace_root") or (legacy_campaign or {}).get("workspace_root") or Path("results") / campaign_id),
            "budget_cap_usd": payload.get("budget") if "budget" in payload else (legacy_campaign or {}).get("budget_cap_usd"),
            "tier": payload.get("tier") if "tier" in payload else (legacy_campaign or {}).get("tier"),
            "output_format": payload.get("output_format") if "output_format" in payload else (legacy_campaign or {}).get("output_format"),
            "bundle_path": bundle_path,
        }

    def _graph(
        self,
        campaign_id: str,
        events: list[dict[str, Any]],
        *,
        legacy_graph: dict[str, Any] | None,
    ) -> dict[str, Any]:
        graph: dict[str, Any] | None = None
        state = "planned"
        version = 0
        node_status: dict[str, str] = {}
        for event in events:
            payload = event["payload"]
            if event["type"] == "GraphProjected":
                graph = json.loads(json.dumps(payload["graph"]))
                state = str(graph.get("state") or state)
                version = int(graph.get("version") or version or 1)
                node_status = {
                    str(node.get("id")): str(node.get("status") or "planned")
                    for node in graph.get("nodes", [])
                }
            elif event["type"] == "GraphApproved":
                state = "approved"
                node_status = {node_id: "approved" for node_id in node_status}
            elif event["type"] == "GraphNodeStatusChanged":
                node_status[str(payload.get("node_id"))] = str(payload.get("status") or "unknown")
        if graph is None:
            graph = json.loads(json.dumps(legacy_graph)) if legacy_graph is not None else {
                "campaign": campaign_id,
                "version": 0,
                "state": "planned",
                "nodes": [],
                "edges": [],
                "metadata": {"read_source": "empty"},
            }
            state = str(graph.get("state") or state)
            version = int(graph.get("version") or version)
            node_status = {
                str(node.get("id")): str(node.get("status") or "planned")
                for node in graph.get("nodes", [])
            }
        graph["campaign"] = campaign_id
        graph["state"] = state
        graph["version"] = version
        graph["metadata"] = {**dict(graph.get("metadata") or {}), "read_source": EVENT_PROJECTION_SOURCE}
        for node in graph.get("nodes", []):
            node_id = str(node.get("id"))
            if node_id in node_status:
                node["status"] = node_status[node_id]
        return graph

    def _artifact_rows(
        self,
        campaign_id: str,
        events: list[dict[str, Any]],
        *,
        legacy_artifact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        declarations_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
        for event in events:
            payload = event["payload"]
            if event["type"] == "ArtifactDeclared":
                row = self._artifact_declaration_row(campaign_id, payload)
                rows[row["id"]] = row
                declarations_by_key[(str(row["stage_id"]), str(row["path"]), int(row["required"]))] = row
            elif event["type"] == "ArtifactIndexed":
                row = self._artifact_index_row(campaign_id, payload, declarations_by_key)
                rows[row["id"]] = row
        if not rows:
            return legacy_artifact_rows
        return sorted(
            rows.values(),
            key=lambda row: (
                str(row.get("stage_id") or ""),
                -int(row.get("required") or 0),
                str(row.get("path") or ""),
                str(row.get("id") or ""),
            ),
        )

    def _artifact_declaration_row(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        stage_id = str(payload.get("stage_id") or "")
        path = str(payload.get("path") or "")
        required = 1 if payload.get("required") else 0
        artifact_id = str(payload.get("artifact_id") or f"{campaign_id}:{stage_id}:{path}")
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("workspace", str(payload.get("workspace") or Path("results") / campaign_id / stage_id))
        metadata.setdefault("audience", artifact_audience(source_role=metadata.get("source_role", "raw"), required=bool(required)))
        return {
            "id": artifact_id,
            "campaign_id": campaign_id,
            "stage_id": stage_id,
            "path": path,
            "kind": str(payload.get("kind") or Path(path).suffix.replace(".", "") or "markdown"),
            "required": required,
            "status": "declared",
            "producer_node_id": str(payload.get("producer_node_id") or stage_id),
            "size_bytes": None,
            "checksum": None,
            "metadata_json": json_dumps(metadata),
        }

    def _artifact_index_row(
        self,
        campaign_id: str,
        payload: dict[str, Any],
        declarations_by_key: dict[tuple[str, str, int], dict[str, Any]],
    ) -> dict[str, Any]:
        stage_id = str(payload.get("stage_id") or "")
        path = str(payload.get("path") or "")
        required = 1 if payload.get("required") else 0
        declared = declarations_by_key.get((stage_id, path, required))
        if declared is None and "required" not in payload:
            declared = declarations_by_key.get((stage_id, path, 1)) or declarations_by_key.get((stage_id, path, 0))
            if declared is not None:
                required = int(declared.get("required") or 0)
        artifact_id = str(payload.get("artifact_id") or (declared or {}).get("id") or f"{campaign_id}:{stage_id}:{path}:event")
        metadata = dict(payload.get("metadata") or {})
        if declared is not None:
            metadata = {**json.loads(declared.get("metadata_json") or "{}"), **metadata}
        if payload.get("workspace"):
            metadata["workspace"] = payload["workspace"]
        if payload.get("run_id"):
            metadata["run_id"] = payload["run_id"]
        if payload.get("source_role"):
            metadata["source_role"] = payload["source_role"]
        metadata.setdefault("workspace", str(Path("results") / campaign_id / stage_id))
        metadata.setdefault("audience", artifact_audience(source_role=metadata.get("source_role", "raw"), required=bool(required)))
        return {
            "id": artifact_id,
            "campaign_id": campaign_id,
            "stage_id": stage_id,
            "path": path,
            "kind": str(payload.get("kind") or (declared or {}).get("kind") or Path(path).suffix.replace(".", "") or "artifact"),
            "required": required,
            "status": "existing",
            "producer_node_id": str(payload.get("producer_node_id") or stage_id),
            "size_bytes": payload.get("size_bytes"),
            "checksum": payload.get("checksum"),
            "metadata_json": json_dumps(metadata),
        }
