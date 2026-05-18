"""OpenClaude integration contracts for the MSc product shell."""

from __future__ import annotations

import shutil
import shlex
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .campaigns import CampaignClient
from .validation import public_operation_contract

OPENCLAUDE_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENCLAUDE_MODEL = "openai/gpt-5-mini"
OPENCLAUDE_MODEL_ALIASES = {
    "fast": "openai/gpt-5-mini",
    "balanced": "anthropic/claude-sonnet-4.5",
    "deep": "anthropic/claude-opus-4.1",
}


def msc_cli_invocation(project_root: str | Path) -> dict[str, Any]:
    """Return the repo-local command OpenClaude should use for MSc SDK calls."""

    root = Path(project_root).resolve()
    local_msc = root / ".venv" / ("Scripts/msc.exe" if _is_windows() else "bin/msc")
    if local_msc.exists():
        argv = [str(local_msc), "--no-banner"]
    else:
        local_python = root / ".venv" / ("Scripts/python.exe" if _is_windows() else "bin/python")
        if local_python.exists():
            argv = [str(local_python), "-m", "consortium.cli.main", "--no-banner"]
        else:
            argv = ["python", "-m", "consortium.cli.main", "--no-banner"]
    shell = " ".join(shlex.quote(part) for part in argv)
    return {
        "argv": argv,
        "shell_prefix": shell,
        "cwd": str(root),
        "env": {"PYTHONPATH": str(root)},
        "examples": {
            "campaign_workspace": f"{shell} campaigns --root {shlex.quote(str(root))} workspace <campaign> --json",
            "context_pack": f"{shell} openclaude context-pack <campaign> --json",
        },
    }


def _is_windows() -> bool:
    return os.name == "nt"


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


def openclaude_models(*, default_model: str = DEFAULT_OPENCLAUDE_MODEL) -> dict[str, Any]:
    """Return the model choices the extension can show without hard-coding UI state."""

    return {
        "ok": True,
        "default_model": default_model,
        "aliases": [
            {"id": alias, "model": model}
            for alias, model in OPENCLAUDE_MODEL_ALIASES.items()
        ],
        "custom_model_allowed": True,
    }


def openclaude_context_pack(
    campaign_ref: str,
    *,
    project_root: str | Path,
    max_artifacts: int = 8,
    max_feedback: int = 8,
) -> dict[str, Any]:
    """Build compact campaign context for one OpenClaude chat turn."""

    root = Path(project_root).resolve()
    workspace = CampaignClient(root).workspace(campaign_ref)
    context = workspace.get("context") or {}
    deliverables = list(workspace.get("deliverables") or [])
    active_links = list(context.get("active_links") or [])
    linked_paths = {
        str(link.get("target", {}).get("artifact_path") or "")
        for link in active_links
        if link.get("target", {}).get("artifact_path")
    }
    linked_ids = {
        str(link.get("target", {}).get("artifact_id") or "")
        for link in active_links
        if link.get("target", {}).get("artifact_id")
    }

    selected_artifacts: list[dict[str, Any]] = []
    for artifact in deliverables:
        if artifact.get("id") in linked_ids or artifact.get("path") in linked_paths:
            selected_artifacts.append(artifact)
    for artifact in deliverables:
        if len(selected_artifacts) >= max_artifacts:
            break
        if artifact not in selected_artifacts:
            selected_artifacts.append(artifact)

    return {
        "ok": True,
        "schema": "msc.openclaude.context_pack.v1",
        "campaign_ref": campaign_ref,
        "msc_cli": msc_cli_invocation(root),
        "campaign": workspace.get("campaign"),
        "execution": workspace.get("execution"),
        "safe_next_actions": workspace.get("safe_next_actions") or [],
        "current_stage": _current_stage(workspace),
        "pending_decisions": workspace.get("pending_decisions") or [],
        "active_context_links": active_links,
        "recent_feedback": list(workspace.get("feedback") or [])[:max_feedback],
        "selected_artifacts": selected_artifacts[:max_artifacts],
        "context_policy": {
            "source_of_truth": "campaign workspace read model plus campaign events",
            "included_artifacts": "produced deliverables/evidence and user-linked artifacts",
            "excluded_by_default": ["prompt", "log", "system_state", "diagnostic"],
        },
    }


def _current_stage(workspace: dict[str, Any]) -> dict[str, Any] | None:
    current_stage_id = (workspace.get("execution") or {}).get("current_stage_id")
    if not current_stage_id:
        return None
    for node in (workspace.get("graph") or {}).get("nodes") or []:
        if node.get("id") == current_stage_id:
            return node
    return None


def openclaude_researcher_workflows(campaign_ref: str, *, cli_prefix: str = "msc") -> dict[str, Any]:
    """Return the campaign operations OpenClaude should use as its harness."""

    return {
        "cli_invocation": [
            f"Run SDK commands from the project root with this prefix: `{cli_prefix}`.",
            "If bare `msc` is not on PATH, do not stop; use the provided repo-local prefix from `msc_cli.shell_prefix`.",
        ],
        "status_and_orientation": [
            f"{cli_prefix} campaigns workspace {campaign_ref} --json",
            f"{cli_prefix} campaigns explain-node {campaign_ref} <stage_id> --json",
            f"{cli_prefix} campaigns summarize-artifacts {campaign_ref} --json",
        ],
        "researcher_questions": [
            "Answer from the campaign workspace read model first.",
            "Use graph node purposes, validators, decisions, feedback, and deliverables as evidence.",
            "Open raw files only after the read model points to a produced deliverable or diagnostic.",
        ],
        "feedback_and_steering": [
            f"{cli_prefix} campaigns feedback {campaign_ref} --text <feedback> --node <stage_id> --artifact-path <path> --json",
            f"{cli_prefix} campaigns context link {campaign_ref} --note <note> --artifact-path <path> --json",
            f"{cli_prefix} campaigns rerun-stage {campaign_ref} <stage_id> --reason <reason> --json",
            f"{cli_prefix} campaigns rewind {campaign_ref} <stage_id> --reason <reason> --decision-id <decision_id> --run-id <run_id> --json",
            f"{cli_prefix} campaigns rewrite-stage {campaign_ref} <stage_id> --instruction <instruction> --json",
            f"{cli_prefix} campaigns reroute {campaign_ref} --from <stage_id> --to <stage_id> --reason <reason> --json",
            f"Use `{cli_prefix} selftest commands --json` and the operation contract to discover the current SDK surface before declaring that an operation is unavailable.",
            "If the SDK lacks a command that would make the requested task cleaner, perform the best supported action and explicitly report the missing SDK capability as a recommended improvement.",
        ],
        "decisions": [
            "Use pending_decisions from the workspace read model.",
            "Only approve/reject explicit pending decision ids after the researcher asks.",
            f"Use `{cli_prefix} campaigns approve <approval_id> --json` or `{cli_prefix} campaigns reject <approval_id> --json`.",
        ],
        "execution": [
            "OpenClaude should not launch local execution unless the researcher explicitly asks.",
            "There is no `msc campaigns start` command. Do not use it.",
            (
                f"To start or continue execution, use `{cli_prefix} run --campaign-id {campaign_ref} "
                "--campaign-root <project_root> --campaign-graph-version 1 --model <model> "
                "--tier <tier> --budget <usd> --output-format markdown --mode local "
                "--no-counsel --no-math --no-tree-search <campaign objective>`."
            ),
            "Use the campaign objective from the workspace read model as the run task unless the researcher provides a replacement.",
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
        "msc_cli": msc_cli_invocation(root),
        "readiness": readiness,
        "skill_path": readiness["skill_path"],
        "capability_profile": "openclaude_v1",
        "operation_contract": public_operation_contract("openclaude_v1"),
        "workspace": workspace,
        "researcher_workflows": openclaude_researcher_workflows(campaign_ref, cli_prefix=msc_cli_invocation(root)["shell_prefix"]),
        "guardrails": {
            "source_of_truth": "campaign workspace read model plus campaign events",
            "do_not_use_as_truth": ["run_status.json", "raw process logs", "SQLite tables", "legacy LangGraph internals"],
            "mutation_rule": "OpenClaude may autonomously use public msc campaign commands after researcher intent; never mutate truth by editing files directly",
            "hard_stops": ["do not delete campaigns", "do not delete artifacts", "do not edit repo code", "do not increase budget without an explicit budget command"],
            "secret_rule": "never print API keys or token values",
        },
        "context_pack": openclaude_context_pack(campaign_ref, project_root=root),
        "model_options": openclaude_models(default_model=model),
    }
