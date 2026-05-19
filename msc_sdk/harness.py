"""Read-first orchestrator harness for product-shell clients."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capabilities import CapabilityClient
from .campaigns import CampaignClient
from .events import EventStore, redact
from .manifest import write_manifest
from .project import ProjectClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"act_{digest}"


def _confirmation_token(request_id: str, operation: str, target: str) -> str:
    digest = hashlib.sha256(f"{request_id}:{operation}:{target}".encode("utf-8")).hexdigest()[:12]
    return f"confirm:{digest}"


@dataclass(frozen=True)
class ActionRequest:
    """Confirmation request for a product-shell action."""

    id: str
    operation: str
    target: str
    capability: str
    actor: str
    created_at: str
    confirmation_token: str
    status: str = "pending"
    risk_summary: str = "Mutation execution is not wired in this stage."
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrchestratorHarness:
    """Single product-shell harness with read operations and action requests."""

    def __init__(
        self,
        *,
        root: str | Path = ".",
        state_dir: str | Path = ".msc",
        index_dir: str | Path = ".msc_index",
        profile: str = "default",
    ):
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.index_dir = Path(index_dir)
        self.capabilities = CapabilityClient(profile)
        self.events = EventStore(self.state_dir)

    def inspect_project(self) -> dict[str, Any]:
        data = ProjectClient(self.root).inspect().to_dict()
        return {"ok": True, "project": data}

    def campaigns(self) -> dict[str, Any]:
        return {"ok": True, "campaigns": CampaignClient(self.root).list()}

    def campaign_graph(self, campaign_ref: str | Path) -> dict[str, Any]:
        return {"ok": True, "graph": CampaignClient(self.root).graph(campaign_ref)}

    def refresh_manifest(self, path: str | Path, *, actor: str = "system") -> dict[str, Any]:
        decision = self.capabilities.check("write.index")
        if not decision.ok:
            event = self.events.append(
                kind="capability_denied",
                summary="Manifest refresh denied.",
                actor=actor,
                capability="write.index",
                target=str(path),
                details=decision.to_dict(),
            )
            return {"ok": False, "error_category": "capability_denied", "event": event.to_dict()}
        manifest_path = write_manifest(path, self.index_dir)
        event = self.events.append(
            kind="artifact_indexed",
            summary="Derived manifest refreshed.",
            actor=actor,
            capability="write.index",
            target=str(path),
            details={"manifest_path": str(manifest_path)},
        )
        return {"ok": True, "manifest_path": str(manifest_path), "event": event.to_dict()}

    def request_action(
        self,
        *,
        operation: str,
        target: str,
        capability: str,
        actor: str = "user",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision = self.capabilities.check(capability)
        if not decision.ok:
            event = self.events.append(
                kind="capability_denied",
                summary=f"Action denied: {operation}",
                actor=actor,
                capability=capability,
                target=target,
                details={"decision": decision.to_dict(), "operation": operation},
            )
            return {
                "ok": False,
                "error_code": "capability_denied",
                "error_category": "capability_denied",
                "decision": decision.to_dict(),
                "event": event.to_dict(),
            }

        created_at = _now()
        request_id = _stable_id(operation, target, actor, created_at)
        request = ActionRequest(
            id=request_id,
            operation=operation,
            target=target,
            capability=capability,
            actor=actor,
            created_at=created_at,
            confirmation_token=_confirmation_token(request_id, operation, target),
            details=redact(details or {}),
        )
        path = self._write_action_request(request)
        event = self.events.append(
            kind="confirmation_requested",
            summary=f"Confirmation requested: {operation}",
            actor=actor,
            capability=capability,
            target=target,
            details={"request_id": request.id, "request_path": str(path)},
        )
        return {"ok": True, "request": request.to_dict(), "request_path": str(path), "event": event.to_dict()}

    def execute_action(
        self,
        request_id: str,
        confirmation: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        request = self._read_action_request(request_id)
        if request is None:
            return {
                "ok": False,
                "error_code": "missing_action_request",
                "error_category": "missing_file",
                "message": f"Action request not found: {request_id}",
            }
        if confirmation != request.confirmation_token:
            event = self.events.append(
                kind="confirmation_denied",
                summary=f"Confirmation denied: {request.operation}",
                actor=actor,
                capability=request.capability,
                target=request.target,
                details={"request_id": request.id},
            )
            return {
                "ok": False,
                "error_code": "confirmation_mismatch",
                "error_category": "confirmation_required",
                "event": event.to_dict(),
            }
        event = self.events.append(
            kind="execution_deferred",
            summary=f"Confirmed execution deferred: {request.operation}",
            actor=actor,
            capability=request.capability,
            target=request.target,
            details={"request_id": request.id},
        )
        return {
            "ok": False,
            "error_code": "execution_deferred",
            "error_category": "unavailable",
            "message": "Stage 4 records confirmations but does not execute mutations yet.",
            "event": event.to_dict(),
        }

    def list_events(self, *, limit: int | None = None) -> dict[str, Any]:
        return {"ok": True, "events": [event.to_dict() for event in self.events.list(limit=limit)]}

    def _action_dir(self) -> Path:
        return self.state_dir / "action_requests"

    def _write_action_request(self, request: ActionRequest) -> Path:
        action_dir = self._action_dir()
        action_dir.mkdir(parents=True, exist_ok=True)
        path = action_dir / f"{request.id}.json"
        path.write_text(json.dumps(request.to_dict(), indent=2, sort_keys=True) + "\n")
        return path

    def _read_action_request(self, request_id: str) -> ActionRequest | None:
        path = self._action_dir() / f"{request_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return ActionRequest(**data)
        except (json.JSONDecodeError, TypeError):
            return None
