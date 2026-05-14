"""OpenClaude integration contracts for the MSc product shell."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .campaigns import CampaignClient
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
    root = Path(project_root).resolve()
    skill = openclaude_skill_path(root)
    args = [
        "openclaude",
        "--append-system-prompt-file",
        str(skill),
        "--add-dir",
        str(root),
        *(extra_args or []),
    ]
    return {
        "ok": openrouter_configured,
        "command": args,
        "cwd": str(root),
        "env": openclaude_env_contract(openrouter_configured=openrouter_configured, model=model),
        "skill_path": str(skill),
        "capability_profile": "openclaude_v1",
        "operation_contract": public_operation_contract("openclaude_v1"),
    }


def openclaude_researcher_workflows(campaign_ref: str) -> dict[str, Any]:
    """Return the campaign operations OpenClaude should use as its harness."""

    return {
        "status_and_orientation": [
            f"msc campaigns workspace {campaign_ref} --json",
            f"msc campaigns explain-node {campaign_ref} <stage_id> --json",
            f"msc campaigns summarize-artifacts {campaign_ref} --json",
        ],
        "researcher_questions": [
            "Answer from the campaign workspace read model first.",
            "Use graph node purposes, validators, decisions, feedback, and deliverables as evidence.",
            "Open raw files only after the read model points to a produced deliverable or diagnostic.",
        ],
        "feedback_and_steering": [
            f"msc campaigns feedback {campaign_ref} --text <feedback> --node <stage_id> --json",
            f"msc campaigns rerun-stage {campaign_ref} <stage_id> --reason <reason> --json",
            f"msc campaigns rewrite-stage {campaign_ref} <stage_id> --instruction <instruction> --json",
            f"msc campaigns reroute {campaign_ref} --from <stage_id> --to <stage_id> --reason <reason> --json",
        ],
        "decisions": [
            "Use pending_decisions from the workspace read model.",
            "Only approve/reject explicit pending decision ids after the researcher asks.",
            "Use `msc campaigns approve <approval_id> --json` or `msc campaigns reject <approval_id> --json`.",
        ],
        "execution": [
            "OpenClaude should not launch local execution unless the researcher explicitly asks.",
            "Prefer the VS Code cockpit for start/stop controls until kernel-native execution is complete.",
            "When execution is active, treat raw process logs as diagnostics, not product truth.",
        ],
    }


def openclaude_campaign_harness(
    campaign_ref: str,
    *,
    project_root: str | Path,
    openrouter_configured: bool,
    openrouter_source: str | None = None,
    model: str = DEFAULT_OPENCLAUDE_MODEL,
) -> dict[str, Any]:
    """Return the full redacted OpenClaude harness packet for one campaign."""

    root = Path(project_root).resolve()
    workspace = CampaignClient(root).workspace(campaign_ref)
    readiness = openclaude_readiness(
        project_root=root,
        openrouter_configured=openrouter_configured,
        openrouter_source=openrouter_source,
        model=model,
    ).to_dict()
    return {
        "ok": True,
        "schema": "msc.openclaude.campaign_harness.v1",
        "campaign_ref": campaign_ref,
        "project_root": str(root),
        "readiness": readiness,
        "skill_path": readiness["skill_path"],
        "capability_profile": "openclaude_v1",
        "operation_contract": public_operation_contract("openclaude_v1"),
        "workspace": workspace,
        "researcher_workflows": openclaude_researcher_workflows(campaign_ref),
        "guardrails": {
            "source_of_truth": "campaign workspace read model plus campaign events",
            "do_not_use_as_truth": ["run_status.json", "raw process logs", "SQLite tables", "legacy LangGraph internals"],
            "mutation_rule": "write only through public msc campaign commands and only when researcher intent is explicit",
            "secret_rule": "never print API keys or token values",
        },
    }
