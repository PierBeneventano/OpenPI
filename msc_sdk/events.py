"""Append-only product-shell event log helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEY_FRAGMENTS = ("key", "token", "secret", "password", "credential")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_id(timestamp: str, kind: str, target: str | None, summary: str) -> str:
    digest = hashlib.sha256(f"{timestamp}:{kind}:{target}:{summary}".encode("utf-8")).hexdigest()
    return f"evt_{digest[:16]}"


def redact(value: Any) -> Any:
    """Redact secret-like values from nested event details."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            if any(fragment in str(key).lower() for fragment in SENSITIVE_KEY_FRAGMENTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact(child)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class Event:
    """Product-shell audit event."""

    id: str
    timestamp: str
    kind: str
    summary: str
    actor: str = "system"
    capability: str | None = None
    target: str | None = None
    source: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventStore:
    """Append-only JSONL event store under product-shell state."""

    def __init__(self, root: str | Path = ".msc"):
        self.root = Path(root)
        self.path = self.root / "events.jsonl"

    def append(
        self,
        *,
        kind: str,
        summary: str,
        actor: str = "system",
        capability: str | None = None,
        target: str | None = None,
        source: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> Event:
        timestamp = _now()
        event = Event(
            id=_event_id(timestamp, kind, target, summary),
            timestamp=timestamp,
            kind=kind,
            summary=summary,
            actor=actor,
            capability=capability,
            target=target,
            source=source,
            details=redact(details or {}),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
        return event

    def list(self, *, limit: int | None = None) -> list[Event]:
        if not self.path.exists():
            return []
        rows: list[Event] = []
        for line in self.path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                rows.append(Event(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        if limit is not None:
            rows = rows[-limit:]
        return rows

    def show(self, event_id: str) -> Event | None:
        for event in self.list():
            if event.id == event_id:
                return event
        return None
