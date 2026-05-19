"""OpenClaw optional automation readiness and capability contracts."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .events import redact
from .validation import public_operation_contract


OPENCLAW_PROFILES = {
    "read_only": {
        "default": True,
        "capabilities": [
            "read.project",
            "read.campaigns",
            "read.artifacts",
            "read.budget",
            "read.events",
        ],
        "mutations_allowed": False,
    },
    "operator": {
        "default": False,
        "capabilities": ["mutate.launch", "mutate.plan_approval"],
        "mutations_allowed": True,
        "confirmation_required": True,
    },
    "repair": {
        "default": False,
        "capabilities": ["mutate.repair"],
        "mutations_allowed": True,
        "confirmation_required": True,
    },
    "admin": {
        "default": False,
        "capabilities": ["mutate.budget", "mutate.archive", "mutate.config"],
        "mutations_allowed": True,
        "confirmation_required": True,
    },
}


@dataclass(frozen=True)
class OpenClawReadiness:
    """Readiness state for optional OpenClaw automation."""

    configured: bool
    config_path: str
    launch_script: str | None
    launch_script_exists: bool
    default_profile: str = "read_only"
    telegram_enabled: bool = False
    gateway_port: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def openclaw_config_path() -> Path:
    """Return the configured OpenClaw config path."""
    return Path(os.getenv("OPENCLAW_CONFIG", Path.home() / ".openclaw" / "openclaw.json"))


def read_openclaw_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read and redact OpenClaw config."""
    config_path = Path(path) if path else openclaw_config_path()
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return redact(data if isinstance(data, dict) else {})


def openclaw_readiness(
    *,
    config_path: str | Path | None = None,
    launch_script: str | Path | None = None,
) -> OpenClawReadiness:
    """Build redaction-safe OpenClaw readiness state."""
    resolved_config = Path(config_path) if config_path else openclaw_config_path()
    config = read_openclaw_config(resolved_config)
    gateway = config.get("gateway", {}) if isinstance(config.get("gateway"), dict) else {}
    channels = config.get("channels", {}) if isinstance(config.get("channels"), dict) else {}
    telegram = channels.get("telegram", {}) if isinstance(channels.get("telegram"), dict) else {}
    launch_path = Path(launch_script) if launch_script else None
    return OpenClawReadiness(
        configured=resolved_config.exists(),
        config_path=str(resolved_config),
        launch_script=str(launch_path) if launch_path else None,
        launch_script_exists=bool(launch_path and launch_path.is_file()),
        telegram_enabled=bool(telegram.get("enabled")),
        gateway_port=gateway.get("port") if isinstance(gateway.get("port"), int) else None,
    )


def openclaw_launch_plan(launch_script: str | Path | None) -> dict[str, Any]:
    """Return a dry-run OpenClaw launch plan."""
    script = Path(launch_script) if launch_script else None
    return {
        "ok": bool(script and script.is_file()),
        "command": ["bash", str(script)] if script else None,
        "mutates": True,
        "executes": False,
        "capability_profile": "read_only",
        "confirmation_required_for_mutations": True,
        "operation_contract": public_operation_contract("openclaw_read_only"),
    }
