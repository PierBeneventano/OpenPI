"""OpenClaude integration contracts for the MSc product shell."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .validation import public_operation_contract

OPENCLAUDE_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENCLAUDE_MODEL = "openai/gpt-5-mini"


@dataclass(frozen=True)
class OpenClaudeReadiness:
    """Readiness state for launching OpenClaude through MSc."""

    openclaude_available: bool
    openclaude_path: str | None
    openrouter_configured: bool
    openrouter_source: str | None
    skill_path: str
    skill_exists: bool
    launch_ready: bool
    model: str
    base_url: str = OPENCLAUDE_BASE_URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def openclaude_skill_path(project_root: str | Path | None = None) -> Path:
    """Return the repository-local MSc OpenClaude skill path."""
    root = Path(project_root or Path.cwd()).resolve()
    return root / "integrations" / "openclaude" / "MSC_SKILL.md"


def openclaude_readiness(
    *,
    project_root: str | Path | None = None,
    openrouter_configured: bool,
    openrouter_source: str | None = None,
    model: str = DEFAULT_OPENCLAUDE_MODEL,
) -> OpenClaudeReadiness:
    """Build readiness state without exposing credentials."""
    binary = shutil.which("openclaude")
    skill = openclaude_skill_path(project_root)
    return OpenClaudeReadiness(
        openclaude_available=binary is not None,
        openclaude_path=binary,
        openrouter_configured=openrouter_configured,
        openrouter_source=openrouter_source,
        skill_path=str(skill),
        skill_exists=skill.is_file(),
        launch_ready=bool(binary and openrouter_configured and skill.is_file()),
        model=model,
    )


def openclaude_env_contract(
    *,
    openrouter_configured: bool,
    model: str = DEFAULT_OPENCLAUDE_MODEL,
) -> dict[str, Any]:
    """Return the OpenAI-compatible launch env shape without secret values."""
    return {
        "CLAUDE_CODE_USE_OPENAI": "1",
        "OPENAI_BASE_URL": OPENCLAUDE_BASE_URL,
        "OPENAI_MODEL": model,
        "OPENAI_API_KEY": "[REDACTED]" if openrouter_configured else None,
        "secret_source": "OPENROUTER_API_KEY",
    }


def openclaude_launch_plan(
    *,
    project_root: str | Path,
    openrouter_configured: bool,
    model: str = DEFAULT_OPENCLAUDE_MODEL,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Return an auditable launch plan without executing OpenClaude."""
    args = ["openclaude", *(extra_args or [])]
    return {
        "ok": openrouter_configured,
        "command": args,
        "cwd": str(Path(project_root).resolve()),
        "env": openclaude_env_contract(openrouter_configured=openrouter_configured, model=model),
        "skill_path": str(openclaude_skill_path(project_root)),
        "capability_profile": "openclaude_v1",
        "operation_contract": public_operation_contract("openclaude_v1"),
    }
