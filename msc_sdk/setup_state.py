"""Guided setup readiness models for MSc product surfaces."""

from __future__ import annotations

import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SetupState:
    """Redaction-safe setup readiness state."""

    project_detected: bool
    python_ready: bool
    cli_ready: bool
    config_dir: str
    credential_source: str | None
    openrouter_configured: bool
    openrouter_verified: bool
    results_dir_ready: bool
    slurm_available: bool
    latex_available: bool
    rg_available: bool
    sdk_json_ready: bool
    vscode_extension_ready: bool
    openclaude_available: bool
    openclaude_launch_ready: bool
    openclaw_enabled: bool
    telegram_enabled: bool
    warnings: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_setup_state(
    *,
    project_root: str | Path | None,
    config_dir: str | Path,
    credential_source: str | None,
    openrouter_configured: bool,
    results_dir: str | Path | None,
    openclaude_available: bool,
    openclaude_launch_ready: bool,
    telegram_enabled: bool = False,
    openclaw_enabled: bool = False,
    openrouter_verified: bool = False,
) -> SetupState:
    """Build a no-secret setup state from already-resolved environment inputs."""
    root = Path(project_root).resolve() if project_root else None
    warnings: list[str] = []
    next_actions: list[str] = []

    python_ready = sys.version_info >= (3, 10)
    cli_ready = shutil.which("msc") is not None or bool(root and (root / "consortium").is_dir())
    sdk_json_ready = bool(root and (root / "msc_sdk").is_dir())
    vscode_extension_ready = bool(root and (root / "extensions" / "vscode-msc" / "package.json").is_file())

    if root is None:
        warnings.append("project_root_not_found")
        next_actions.append("Open the MSc repository root or install the package.")
    if not python_ready:
        warnings.append("python_version_below_3_10")
        next_actions.append("Use Python 3.10 or newer.")
    if not openrouter_configured:
        warnings.append("openrouter_key_missing")
        next_actions.append("Run `msc setup` or set OPENROUTER_API_KEY in the supported config location.")
    if not openclaude_available:
        next_actions.append("Optional: install OpenClaude with `npm install -g @gitlawb/openclaude`.")
    if not results_dir:
        next_actions.append("Create or select a results directory when runs exist.")

    return SetupState(
        project_detected=root is not None,
        python_ready=python_ready,
        cli_ready=cli_ready,
        config_dir=str(Path(config_dir)),
        credential_source=credential_source,
        openrouter_configured=openrouter_configured,
        openrouter_verified=openrouter_verified,
        results_dir_ready=bool(results_dir),
        slurm_available=shutil.which("sbatch") is not None,
        latex_available=shutil.which("pdflatex") is not None,
        rg_available=shutil.which("rg") is not None,
        sdk_json_ready=sdk_json_ready,
        vscode_extension_ready=vscode_extension_ready,
        openclaude_available=openclaude_available,
        openclaude_launch_ready=openclaude_launch_ready,
        openclaw_enabled=openclaw_enabled,
        telegram_enabled=telegram_enabled,
        warnings=warnings,
        next_actions=next_actions,
    )


def tutorial_plan() -> dict[str, Any]:
    """Return the no-cost guided tutorial plan."""
    return {
        "ok": True,
        "spends_budget": False,
        "runs_paid_pipeline": False,
        "steps": [
            {
                "id": "project_readiness",
                "command": "msc project readiness --json",
                "purpose": "Confirm project and CLI readiness.",
            },
            {
                "id": "setup_state",
                "command": "msc project setup-state --json",
                "purpose": "Show required and optional setup state without secrets.",
            },
            {
                "id": "command_surface",
                "command": "msc selftest commands --json",
                "purpose": "Confirm public SDK/CLI command discovery.",
            },
            {
                "id": "openclaude_readiness",
                "command": "msc openclaude readiness --json",
                "purpose": "Check optional OpenClaude launch readiness.",
            },
            {
                "id": "dry_run",
                "command": "scripts/validation/cheap_dry_run.sh",
                "purpose": "Validate launch argument wiring without spending budget.",
            },
            {
                "id": "dashboard",
                "command": "MSc: Open Dashboard",
                "purpose": "Explore read-only VS Code dashboard state.",
            },
        ],
    }
