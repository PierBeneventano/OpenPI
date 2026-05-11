"""
Core run logic for the consortium multi-agent system.

Separated from launch_multiagent.py so the entrypoint stays thin and
this module can be imported/tested independently.
"""

import importlib.util
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

import litellm

from .args import parse_arguments
from .cli.core.env_manager import get_runtime_env_sources, inject_runtime_env
from .config import load_llm_config, filter_model_params
from .interaction.callback_tools import setup_user_input_socket
from .interaction.http_steering import add_http_steering
from .prereqs import check_latex_prereqs
from .run_status import read_run_status, write_run_status
from .supervision import sanitize_result_payload
from .token_usage_tracker import initialize_run_token_tracker
from .counsel import create_counsel_models, DEFAULT_COUNSEL_MODEL_SPECS, set_counsel_timeout
from .graph import build_pipeline_stages_v2, get_default_checkpointer
from .utils import create_model, create_model_registry, save_agent_memory

logger = logging.getLogger(__name__)

litellm.drop_params = True
litellm.completion = filter_model_params(litellm.completion)


class _VertexErrorFilter:
    """Suppress repetitive Vertex AI credential errors from litellm logs."""
    _SUPPRESSED = frozenset([
        "Failed to load vertex credentials",
        "Google Cloud SDK not found",
        "vertex_llm_base",
    ])

    def filter(self, record):
        msg = str(getattr(record, "msg", ""))
        return not any(s in msg for s in self._SUPPRESSED)


class _TeeStream:
    """Mirror writes to both the original stream and a file handle."""

    def __init__(self, primary: TextIO, mirror: TextIO):
        self._primary = primary
        self._mirror = mirror

    def write(self, data: str) -> int:
        written = self._primary.write(data)
        self._mirror.write(data)
        return written

    def flush(self) -> None:
        self._primary.flush()
        self._mirror.flush()

    def isatty(self) -> bool:
        return self._primary.isatty()

    @property
    def encoding(self):  # pragma: no cover - passthrough attribute
        return getattr(self._primary, "encoding", None)


def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_project_root() -> Path | None:
    explicit = os.getenv("CONSORTIUM_PROJECT_ROOT")
    if explicit:
        try:
            return Path(explicit).resolve()
        except OSError:
            return None

    try:
        from .cli.core.paths import find_project_root
        return find_project_root()
    except Exception:
        logger.debug("Failed to resolve project root", exc_info=True)
        return None


def _campaign_root_for_args(args) -> Path:
    explicit = getattr(args, "campaign_root", None) or os.getenv("MSC_CAMPAIGN_ROOT") or os.getenv("CONSORTIUM_PROJECT_ROOT")
    if explicit:
        return Path(explicit).resolve()
    project_root = _resolve_project_root()
    return project_root or Path.cwd()


def _record_campaign_run_started(args, *, workspace_dir: str | None, command: list[str], dry_run: bool = False) -> str | None:
    campaign_id = getattr(args, "campaign_id", None) or os.getenv("MSC_CAMPAIGN_ID")
    if not campaign_id:
        return None
    try:
        from msc_sdk.campaign_store import CampaignStore

        root = _campaign_root_for_args(args)
        store = CampaignStore(root)
        record = store.record_run_started(
            campaign_id,
            command=command,
            pid=os.getpid(),
            graph_version=getattr(args, "campaign_graph_version", None),
            metadata={"workspace_dir": workspace_dir, "dry_run": dry_run},
        )
        run_id = record["run_id"]
        os.environ["MSC_CAMPAIGN_ID"] = campaign_id
        os.environ["MSC_CAMPAIGN_ROOT"] = str(root)
        os.environ["MSC_CAMPAIGN_RUN_ID"] = run_id
        if getattr(args, "campaign_graph_version", None):
            os.environ["MSC_CAMPAIGN_GRAPH_VERSION"] = str(args.campaign_graph_version)
        return run_id
    except Exception:
        logger.warning("Failed to attach run to campaign %s", campaign_id, exc_info=True)
        return None


def _record_campaign_run_exited(args, run_id: str | None, *, exit_code: int | None, status: str | None = None, metadata: dict | None = None) -> None:
    campaign_id = getattr(args, "campaign_id", None) or os.getenv("MSC_CAMPAIGN_ID")
    if not campaign_id or not run_id:
        return
    try:
        from msc_sdk.campaign_store import CampaignStore

        CampaignStore(_campaign_root_for_args(args)).record_run_exited(
            campaign_id,
            run_id,
            exit_code=exit_code,
            status=status,
            metadata=metadata or {},
        )
    except Exception:
        logger.warning("Failed to record campaign run exit for %s", campaign_id, exc_info=True)


def _record_campaign_known_artifacts(args) -> None:
    campaign_id = getattr(args, "campaign_id", None) or os.getenv("MSC_CAMPAIGN_ID")
    if not campaign_id:
        return
    try:
        from msc_sdk.campaign_store import CampaignStore

        CampaignStore(_campaign_root_for_args(args)).refresh_artifact_files(campaign_id)
    except Exception:
        logger.warning("Failed to refresh campaign artifact index for %s", campaign_id, exc_info=True)


def _resolve_summary_model_id(llm_config: dict | None, model_name: str) -> str:
    if llm_config:
        summary_cfg = llm_config.get("summary_model", {})
        if isinstance(summary_cfg, dict) and summary_cfg.get("model"):
            return str(summary_cfg["model"])
    return model_name

_DEFAULT_TASK = (
    "Investigate whether training small language models with multiple paraphrased responses "
    "reduces hallucinations and improves response quality. Generate research ideas exploring: "
    "(1) Fine-tuning GPT-2 Small (124M) on instruction-following datasets with single vs multiple "
    "response variants, using small subsets (5K samples) for rapid experimentation, "
    "(2) Comparing response diversity and factual accuracy between single-response and multi-response "
    "training regimes, (3) Testing whether exposure to diverse correct answers during training acts "
    "as implicit regularization against repetitive or factually incorrect outputs, "
    "(4) Measuring trade-offs between response quality, diversity, and training efficiency with "
    "automated metrics. Use small-scale datasets like Alpaca-5K with automated paraphrase generation. "
    "Focus on fast automated evaluation including response diversity via Self-BLEU, factual consistency "
    "via rule-based checks, and response quality via ROUGE scores. Target achieving measurable "
    "improvements in response diversity while maintaining quality, with each experimental run "
    "completing in under 1 hour."
)

_CONTINUATION_TASK = (
    "Continue working on a previous research task. Please meticulously analyze the existing "
    "files and then plan how to call the relevant agents to further progress the research task "
    "and deliver better research outputs."
)

_STAGE_ALIASES = {
    "literature": "literature_review_agent",
    "litreview": "literature_review_agent",
    "literature_review": "literature_review_agent",
    "literature_review_agent": "literature_review_agent",
    "experiment": "experimentation_agent",
    "experimentation": "experimentation_agent",
    "experimentation_agent": "experimentation_agent",
    "analysis": "results_analysis_agent",
    "results_analysis": "results_analysis_agent",
    "results_analysis_agent": "results_analysis_agent",
    "math_literature": "math_literature_agent",
    "math_literature_agent": "math_literature_agent",
    "math_proposer": "math_proposer_agent",
    "math_proposer_agent": "math_proposer_agent",
    "math_prover": "math_prover_agent",
    "math_prover_agent": "math_prover_agent",
    "math_rigorous_verifier": "math_rigorous_verifier_agent",
    "math_rigorous_verifier_agent": "math_rigorous_verifier_agent",
    "math_empirical_verifier": "math_empirical_verifier_agent",
    "math_empirical_verifier_agent": "math_empirical_verifier_agent",
    "proof_transcription": "proof_transcription_agent",
    "proof_transcription_agent": "proof_transcription_agent",
    "resources": "resource_preparation_agent",
    "resource_preparation": "resource_preparation_agent",
    "resource_preparation_agent": "resource_preparation_agent",
    "writeup": "writeup_agent",
    "writeup_agent": "writeup_agent",
    "proofread": "proofreading_agent",
    "proofreading": "proofreading_agent",
    "proofreading_agent": "proofreading_agent",
    "review": "reviewer_agent",
    "reviewer": "reviewer_agent",
    "reviewer_agent": "reviewer_agent",
    "persona_council": "persona_council",
    "council": "persona_council",
    "brainstorm": "brainstorm_agent",
    "brainstorm_agent": "brainstorm_agent",
    "formalize_goals": "formalize_goals_agent",
    "formalize_goals_agent": "formalize_goals_agent",
    "goals": "formalize_goals_agent",
    "research_plan_writeup": "research_plan_writeup_agent",
    "research_plan_writeup_agent": "research_plan_writeup_agent",
    "plan_writeup": "research_plan_writeup_agent",
    "formalize_results": "formalize_results_agent",
    "formalize_results_agent": "formalize_results_agent",
}


def _setup_optional_tracing():
    enabled = os.getenv("CONSORTIUM_ENABLE_TRACING", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not enabled:
        logger.debug("Tracing disabled (set CONSORTIUM_ENABLE_TRACING=1 to enable).")
        return
    # LangSmith auto-tracing is enabled via LANGCHAIN_TRACING_V2 env var.
    # Just confirm it's configured.
    if os.getenv("LANGCHAIN_TRACING_V2"):
        logger.info("LangSmith tracing enabled.")
    else:
        logger.info("Set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY to enable LangSmith tracing.")


def _install_litellm_token_callback():
    """Register a litellm success callback that writes every API call to the
    private token ledger.  This is a belt-and-suspenders layer: even if
    BudgetTrackingCallback or BudgetedLiteLLMModel is bypassed, every litellm
    call gets recorded for cost accounting.

    Also logs full input/output pairs for training data when the
    TrainingDataLogger is active.
    """
    from .token_usage_tracker import record_token_usage

    def _on_litellm_success(kwargs, response_obj, start_time, end_time):
        prompt_tokens = 0
        completion_tokens = 0
        model = "unknown"
        try:
            usage = getattr(response_obj, "usage", None)
            if usage is None:
                return
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            if not prompt_tokens and not completion_tokens:
                return
            model = kwargs.get("model") or getattr(response_obj, "model", "unknown")
            record_token_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                source="litellm_callback",
                model_id=model,
            )
        except Exception:
            # Log but don't break the LLM call chain.
            # Use a counter to avoid flooding logs — warn after 10 failures.
            _on_litellm_success._fail_count = getattr(_on_litellm_success, "_fail_count", 0) + 1
            if _on_litellm_success._fail_count <= 3:
                logger.debug(
                    "Token recording failed (occurrence #%d)", _on_litellm_success._fail_count,
                    exc_info=True,
                )
            elif _on_litellm_success._fail_count == 10:
                logger.warning(
                    "Token recording has failed %d times — token tracking may be inaccurate. "
                    "Check disk space and permissions.",
                    _on_litellm_success._fail_count,
                )

        # --- Training data logging (best-effort) ---
        try:
            from .logging.training_data_logger import get_training_data_logger
            tdl = get_training_data_logger()
            if tdl is None or not tdl.enabled:
                return

            messages = kwargs.get("messages", [])
            response_content = ""
            tool_calls = None
            if hasattr(response_obj, "choices") and response_obj.choices:
                choice = response_obj.choices[0]
                msg = getattr(choice, "message", None)
                if msg:
                    response_content = getattr(msg, "content", "") or ""
                    tool_calls = getattr(msg, "tool_calls", None)

            duration_ms = None
            if start_time and end_time:
                try:
                    duration_ms = int((end_time - start_time).total_seconds() * 1000)
                except Exception:
                    pass

            tdl.log_completion(
                messages=messages,
                response_content=response_content,
                model_id=model,
                source="litellm_callback",
                token_usage={
                    "input_tokens": prompt_tokens,
                    "output_tokens": completion_tokens,
                },
                call_id=kwargs.get("litellm_call_id"),
                duration_ms=duration_ms,
                tool_calls=tool_calls,
            )
        except Exception:
            pass  # Never break LLM calls for training data logging

    litellm.success_callback.append(_on_litellm_success)


def _filter_installed_imports(import_names):
    available, missing = [], []
    for name in import_names:
        root = name.split(".")[0]
        if importlib.util.find_spec(root) is not None:
            available.append(name)
        else:
            missing.append(name)
    if missing:
        logger.info("Skipping unavailable authorized imports: " + ", ".join(sorted(set(missing))))
    return available


def _resolve_model_settings(args, llm_config):
    """Return (model_name, reasoning_effort, verbosity, budget_tokens, effort)."""
    model_name = "claude-sonnet-4-6"
    reasoning_effort = "high"
    verbosity = "medium"
    budget_tokens = None
    effort = None

    if llm_config and "main_agents" in llm_config:
        cfg = llm_config["main_agents"]
        model_name = cfg.get("model", model_name)
        reasoning_effort = cfg.get("reasoning_effort", reasoning_effort)
        verbosity = cfg.get("verbosity", verbosity)
        budget_tokens = cfg.get("budget_tokens", budget_tokens)
        # Support both "effort" (legacy) and "reasoning_effort" config keys
        effort = cfg.get("effort") or cfg.get("reasoning_effort", effort)
        logger.info("Loaded config file settings for main agents")

    if args.model is not None:
        model_name = args.model
        logger.info("CLI override: using model %s", model_name)
    if args.reasoning_effort is not None:
        reasoning_effort = args.reasoning_effort
        logger.info("CLI override: reasoning_effort=%s", reasoning_effort)
    if args.verbosity is not None:
        verbosity = args.verbosity
        logger.info("CLI override: verbosity=%s", verbosity)

    return model_name, reasoning_effort, verbosity, budget_tokens, effort


def _resolve_counsel_settings(args, llm_config):
    """Return the effective counsel configuration after CLI/config precedence."""
    counsel_cfg = llm_config.get("counsel", {}) if llm_config else {}

    counsel_enabled = getattr(args, "enable_counsel", False)
    counsel_disabled = getattr(args, "no_counsel", False)
    if counsel_disabled:
        counsel_enabled = False
    elif not counsel_enabled:
        counsel_enabled = bool(counsel_cfg.get("enabled", False))

    max_debate_rounds = getattr(args, "counsel_max_debate_rounds", None)
    if max_debate_rounds is None:
        max_debate_rounds = int(counsel_cfg.get("max_debate_rounds", 3))

    configured_model_specs = counsel_cfg.get("models") or DEFAULT_COUNSEL_MODEL_SPECS
    effective_model_specs = configured_model_specs if counsel_enabled else []
    synthesis_model = counsel_cfg.get("synthesis_model", "claude-opus-4-6")

    return {
        "enabled": counsel_enabled,
        "configured_model_specs": configured_model_specs,
        "effective_model_specs": effective_model_specs,
        "effective_model_names": [spec.get("model", "unknown") for spec in effective_model_specs],
        "max_debate_rounds": max_debate_rounds if counsel_enabled else 0,
        "synthesis_model": synthesis_model if counsel_enabled else None,
    }


def _build_effective_model_manifest(
    *,
    args,
    llm_config: dict | None,
    model_name: str,
    summary_model_id: str,
    counsel_settings: dict,
    persona_specs: list[dict] | None,
    persona_synthesis_model: str,
    duality_check_model: str,
) -> tuple[dict, dict]:
    policy_source = os.getenv("CONSORTIUM_MODEL_POLICY_SOURCE") or ("config" if llm_config else "default")
    selected_tier = os.getenv("CONSORTIUM_SELECTED_TIER")
    provenance = {
        "main_model": "cli" if getattr(args, "model", None) else policy_source,
        "summary_model": policy_source if llm_config else "derived",
        "counsel": ("cli" if getattr(args, "enable_counsel", False) or getattr(args, "no_counsel", False) else policy_source),
        "persona_council": policy_source if persona_specs else "default",
        "duality_check": policy_source if llm_config else "default",
        "run_experiment_tool": policy_source if llm_config and llm_config.get("run_experiment_tool") else "derived",
        "per_agent_models": policy_source if llm_config and llm_config.get("per_agent_models", {}).get("enabled") else "disabled",
    }

    manifest = {
        "selected_tier": selected_tier,
        "main_model": model_name,
        "summary_model": summary_model_id,
        "counsel": {
            "enabled": bool(counsel_settings.get("enabled", False)),
            "models": list(counsel_settings.get("effective_model_specs", [])),
            "synthesis_model": counsel_settings.get("synthesis_model"),
        },
        "persona_council": {
            "personas": list(persona_specs or []),
            "synthesis_model": persona_synthesis_model,
        },
        "duality_check": {
            "model": duality_check_model,
        },
        "run_experiment_tool": dict((llm_config or {}).get("run_experiment_tool", {})),
        "per_agent_models": dict((llm_config or {}).get("per_agent_models", {})),
    }
    return manifest, provenance


def _write_effective_model_manifest(workspace_dir: str, manifest: dict) -> str | None:
    out_path = os.path.join(workspace_dir, "effective_models.json")
    try:
        with open(out_path, "w") as f:
            json.dump(manifest, f, indent=2)
        return out_path
    except OSError:
        logger.debug("Failed to write effective_models.json", exc_info=True)
        return None


def _build_required_artifacts(args, enforce_paper_artifacts, enforce_editorial_artifacts,
                               require_experiment_plan):
    from .paper_contract import (
        COPYEDIT_REPORT_PDF,
        COPYEDIT_REPORT_TEX,
        FINAL_PAPER_PDF,
        FINAL_PAPER_TEX,
        PAPER_CONTRACT_PATH,
        PERSONA_VERDICTS_JSON,
        REVIEW_VERDICT_JSON,
        canonical_section_paths,
    )

    artifacts = []
    if enforce_paper_artifacts:
        artifacts.extend([PAPER_CONTRACT_PATH, FINAL_PAPER_TEX, *canonical_section_paths()])
        if require_experiment_plan:
            artifacts.append("experiments_to_run_later.md")
        if args.require_pdf or enforce_editorial_artifacts:
            artifacts.append(FINAL_PAPER_PDF)
        if getattr(args, "iterate", None):
            artifacts.append(PERSONA_VERDICTS_JSON)
        if enforce_editorial_artifacts:
            artifacts.extend([
                COPYEDIT_REPORT_TEX,
                COPYEDIT_REPORT_PDF,
                REVIEW_VERDICT_JSON,
                PERSONA_VERDICTS_JSON,
                "paper_workspace/author_style_guide.md",
                "paper_workspace/intro_skeleton.tex",
                "paper_workspace/style_macros.tex",
                "paper_workspace/reader_contract.json",
                "paper_workspace/editorial_contract.md",
                "paper_workspace/theorem_map.json",
                "paper_workspace/revision_log.md",
                "paper_workspace/review_report.tex",
                "paper_workspace/review_report.pdf",
            ])
            if args.enable_math_agents:
                artifacts.append("paper_workspace/claim_traceability.json")
    return artifacts


def _canonical_stage_name(stage_name: str) -> str:
    normalized = stage_name.strip().lower().replace("-", "_")
    return _STAGE_ALIASES.get(normalized, normalized)


def _resolve_start_stage_index(stage_name: str, pipeline_stages: list[str]) -> int:
    canonical = _canonical_stage_name(stage_name)
    if canonical not in pipeline_stages:
        valid = ", ".join(pipeline_stages)
        raise ValueError(
            f"Unknown --start-from-stage '{stage_name}' (resolved: '{canonical}'). "
            f"Valid stages for this run: {valid}"
        )
    return pipeline_stages.index(canonical)


def _validate_api_keys(model_name: str) -> list[str]:
    """Return a list of error messages for missing API keys given a model name.

    All models are routed through OpenRouter, so only OPENROUTER_API_KEY is required.
    Returns an empty list if all required keys are present.
    """
    errors = []
    if not os.getenv("OPENROUTER_API_KEY"):
        errors.append(
            f"OPENROUTER_API_KEY is required for model '{model_name}' but is not set.\n"
            f"  Add it to your .env file:  OPENROUTER_API_KEY=your_key_here"
        )
    return errors


def _list_runs(results_dir: str = "results") -> None:
    """Print a summary table of past runs in the results directory."""
    from .cli.core.run_inspector import inspect_run

    if not os.path.isdir(results_dir):
        print(f"No results directory found at '{results_dir}'.")
        return

    entries = sorted(
        (d for d in os.listdir(results_dir) if os.path.isdir(os.path.join(results_dir, d))),
        reverse=True,
    )
    if not entries:
        print("No past runs found in results/.")
        return

    header = f"{'Workspace':<45} {'Task (truncated)':<45} {'Cost':>8}  Status"
    print(header)
    print("-" * len(header))

    for name in entries:
        info = inspect_run(Path(results_dir) / name)
        cost_str = f"${info['budget_usd']:.2f}" if info["budget_usd"] is not None else "?"
        task_str = str(info["task"] or "")[:43]
        print(f"{name:<45} {task_str:<45} {cost_str:>8}  {info['status']}")


def _write_experiment_metadata(
    workspace_dir: str,
    args,
    model_name: str,
    task: str,
    *,
    project_root: str | None = None,
    counsel_settings: dict | None = None,
    persona_settings: dict | None = None,
    effective_models: dict | None = None,
    model_provenance: dict | None = None,
    credential_sources: dict | None = None,
    log_paths: dict[str, str] | None = None,
) -> None:
    """Write experiment_metadata.json to the workspace at run start."""
    git_commit = None
    git_dirty = None
    git_cwd = project_root or None
    if git_cwd:
        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=git_cwd,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            logger.debug("Could not determine git commit hash", exc_info=True)

        try:
            result = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=git_cwd,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            git_dirty = bool(result.strip())
        except (OSError, subprocess.CalledProcessError):
            logger.debug("Could not determine git dirty status", exc_info=True)

    metadata = {
        "timestamp": datetime.now().isoformat(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "project_root": project_root,
        "llm_config_path": os.path.abspath(
            os.path.expandvars(
                os.path.expanduser(
                    os.environ.get("CONSORTIUM_LLM_CONFIG_PATH", ".llm_config.yaml")
                )
            )
        ),
        "model": model_name,
        "task_preview": task[:200],
        "cli_args": {
            "enable_math_agents": getattr(args, "enable_math_agents", False),
            "enable_counsel": getattr(args, "enable_counsel", False),
            "output_format": getattr(args, "output_format", "latex"),
            "enforce_paper_artifacts": getattr(args, "enforce_paper_artifacts", False),
            "min_review_score": getattr(args, "min_review_score", 8),
        },
    }
    if effective_models is not None:
        metadata["effective_models"] = effective_models
    if model_provenance is not None:
        metadata["model_provenance"] = model_provenance
    if credential_sources is not None:
        metadata["credential_sources"] = credential_sources
    if log_paths is not None:
        metadata["log_files"] = log_paths
    if counsel_settings is not None:
        metadata["counsel"] = {
            "enabled": bool(counsel_settings.get("enabled", False)),
            "model_names": list(counsel_settings.get("effective_model_names", [])),
            "model_specs": list(counsel_settings.get("effective_model_specs", [])),
            "max_debate_rounds": int(counsel_settings.get("max_debate_rounds", 0)),
            "synthesis_model": counsel_settings.get("synthesis_model"),
        }
    if persona_settings is not None:
        metadata["persona_council"] = {
            "debate_rounds": int(persona_settings.get("debate_rounds", 0)),
            "synthesis_model": persona_settings.get("synthesis_model"),
            "max_post_vote_retries": int(persona_settings.get("max_post_vote_retries", 0)),
            "personas": list(persona_settings.get("personas", [])),
            "duality_check_model": persona_settings.get("duality_check_model"),
        }
    out_path = os.path.join(workspace_dir, "experiment_metadata.json")
    try:
        with open(out_path, "w") as f:
            json.dump(metadata, f, indent=2)
    except OSError:
        logger.debug("Failed to write experiment_metadata.json", exc_info=True)


def _write_run_summary(workspace_dir: str, task: str, model_name: str,
                        start_time: datetime, stages_completed: list[str], *,
                        status: str = "completed",
                        status_reason: str | None = None,
                        current_stage: str | None = None) -> None:
    """Write run_summary.json to the workspace at run end."""
    duration_s = (datetime.now() - start_time).total_seconds()

    total_cost = None
    budget_path = os.path.join(workspace_dir, "budget_state.json")
    if os.path.exists(budget_path):
        try:
            with open(budget_path) as f:
                bd = json.load(f)
            total_cost = bd.get("total_usd", bd.get("total_cost_usd"))
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to read budget_state.json in run summary", exc_info=True)

    total_tokens = None
    token_path = os.path.join(workspace_dir, "run_token_usage.json")
    if os.path.exists(token_path):
        try:
            with open(token_path) as f:
                tok = json.load(f)
            total_tokens = tok.get("total_tokens")
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to read run_token_usage.json in run summary", exc_info=True)

    # Detect final paper path
    paper_path = None
    for candidate in [
        "paper_workspace/final_paper.pdf",
        "paper_workspace/final_paper.tex",
        "paper_workspace/final_paper.md",
        "final_paper.pdf",
        "final_paper.tex",
        "final_paper.md",
    ]:
        p = os.path.join(workspace_dir, candidate)
        if os.path.exists(p):
            paper_path = candidate
            break

    summary = {
        "task": task,
        "model": model_name,
        "started_at": start_time.isoformat(),
        "completed_at": datetime.now().isoformat(),
        "duration_seconds": round(duration_s, 1),
        "stages_completed": stages_completed,
        "total_cost_usd": total_cost,
        "total_tokens": total_tokens,
        "final_paper": paper_path,
        "workspace": workspace_dir,
        "status": status,
        "completed": status == "completed",
        "failed": status == "failed",
        "status_reason": status_reason,
        "current_stage": current_stage,
    }
    out_path = os.path.join(workspace_dir, "run_summary.json")
    try:
        with open(out_path, "w") as f:
            json.dump(summary, f, indent=2)
        logger.info("Run summary written: %s", out_path)
    except OSError:
        logger.debug("Failed to write run_summary.json", exc_info=True)


def main():
    args = parse_arguments()
    campaign_run_id: str | None = None
    project_root = _resolve_project_root()
    repo_env_override = os.getenv("CONSORTIUM_USE_REPO_ENV")
    allow_repo_env = None if repo_env_override in {None, ""} else _parse_bool_env(
        "CONSORTIUM_USE_REPO_ENV",
        default=False,
    )
    base_env = dict(os.environ)
    inject_runtime_env(
        repo_root=project_root,
        allow_repo_env=allow_repo_env,
    )
    credential_sources_env = os.getenv("CONSORTIUM_CREDENTIAL_SOURCES_JSON")
    if credential_sources_env:
        try:
            credential_sources = json.loads(credential_sources_env)
        except json.JSONDecodeError:
            credential_sources = {}
    else:
        credential_sources = get_runtime_env_sources(
            repo_root=project_root,
            allow_repo_env=allow_repo_env,
            base_env=base_env,
        )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_start_time = datetime.now()
    effective_pipeline_mode = "full_research"

    # --list-runs: print past workspaces and exit
    if getattr(args, "list_runs", False):
        _list_runs()
        return 0

    # --- Logging setup ---
    env_log_default = os.getenv("CONSORTIUM_LOG_TO_FILES", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }
    enable_file_logs = env_log_default if args.log_to_files is None else args.log_to_files
    log_paths: dict[str, str] | None = None

    if args.debug:
        os.environ["LITELLM_LOG"] = "DEBUG"
    else:
        os.environ.setdefault("LITELLM_LOG", "WARNING")

    # P2-9: Suppress non-fatal Vertex AI credential errors that flood stderr.
    # These fire every time litellm tries (and fails) to use Vertex AI as a
    # fallback provider. They are harmless but produce thousands of log lines.
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    _vertex_logger = logging.getLogger("LiteLLM Router")
    _vertex_logger.setLevel(logging.ERROR)
    for _handler in logging.root.handlers[:]:
        _handler.addFilter(_VertexErrorFilter())

    _setup_optional_tracing()
    _install_litellm_token_callback()

    # --- Deployment mode resolution ---
    from .mode import resolve_mode, load_mode_config, apply_mode_defaults
    mode = resolve_mode(args)
    mode_config = load_mode_config(mode)
    apply_mode_defaults(args, mode_config)
    logger.info("[PoggioAI] Running in %s mode — %s", mode, mode_config.get('description', ''))

    llm_config = load_llm_config()

    if args.pipeline_mode is not None:
        logger.warning(
            "--pipeline-mode is deprecated and ignored. "
            "Running fixed-stage full pipeline mode."
        )

    if args.start_from_stage and not args.resume:
        logger.error("--start-from-stage requires --resume <workspace_dir>.")
        return 1

    model_name, reasoning_effort, verbosity, budget_tokens, effort = _resolve_model_settings(
        args, llm_config
    )
    counsel_settings = _resolve_counsel_settings(args, llm_config)

    # --- API key validation (always run; also serves as the dry-run check) ---
    key_errors = _validate_api_keys(model_name)
    # Eagerly validate counsel API keys so we don't fail hours into an expensive run
    if counsel_settings["enabled"]:
        for spec in counsel_settings["effective_model_specs"]:
            key_errors.extend(_validate_api_keys(spec["model"]))
    if key_errors:
        for err in key_errors:
            logger.error(err)
        logger.error("Fix the above before running. See .env.example for the full list of keys.")
        return 1

    # --- Dry-run mode: validate config and exit without touching the pipeline ---
    if getattr(args, "dry_run", False):
        budget_config = llm_config.get("budget", {}) if llm_config else {}
        usd_limit = budget_config.get("usd_limit", "not set")
        logger.info("[dry-run] Environment checks passed:")
        logger.info("  model           : %s", model_name)
        logger.info("  reasoning_effort: %s", reasoning_effort)
        logger.info("  budget cap      : $%s", usd_limit)
        logger.info("  math agents     : %s", getattr(args, 'enable_math_agents', False))
        logger.info("  counsel mode    : %s", counsel_settings["enabled"])
        logger.info("  output format   : %s", getattr(args, 'output_format', 'latex'))
        logger.info("[dry-run] All checks passed. Remove --dry-run to start the real run.")
        campaign_run_id = _record_campaign_run_started(
            args,
            workspace_dir=None,
            command=sys.argv[1:],
            dry_run=True,
        )
        _record_campaign_run_exited(
            args,
            campaign_run_id,
            exit_code=0,
            status="dry_run_passed",
            metadata={"dry_run": True},
        )
        return 0

    # Set up interrupt socket (used by live-steering via state injection)
    input_queue = None
    if not getattr(args, "no_steering", False):
        input_queue = setup_user_input_socket(args.callback_host, args.callback_port)
        logger.info("Interruption port available at %s:%s", args.callback_host, args.callback_port)
        # Also start HTTP steering server on port+1 for programmatic clients (e.g. OpenClaw)
        add_http_steering(input_queue, host=args.callback_host, port=args.callback_port + 1)
    else:
        from queue import Queue
        input_queue = Queue()
        logger.info("[PoggioAI] Steering sockets disabled (--no-steering)")

    logger.info("LangGraph Research System Initialized — model: %s", model_name)

    # --- Experiment tool env config ---
    if llm_config and "run_experiment_tool" in llm_config:
        exp = llm_config["run_experiment_tool"]
        os.environ["RUN_EXPERIMENT_CODE_MODEL"] = exp.get("code_model", "gpt-5")
        os.environ["RUN_EXPERIMENT_FEEDBACK_MODEL"] = exp.get("feedback_model", "gpt-5")
        os.environ["RUN_EXPERIMENT_VLM_MODEL"] = exp.get("vlm_model", "gpt-5")
        os.environ["RUN_EXPERIMENT_REPORT_MODEL"] = exp.get("report_model", "gpt-5")
        os.environ["RUN_EXPERIMENT_REASONING_EFFORT"] = exp.get("reasoning_effort", "high")

    # --- Workspace setup ---
    # Iterate mode takes priority over resume — the campaign runner always
    # passes --resume <workspace>, but --iterate needs its own workspace setup.
    if getattr(args, "iterate", None):
        # --- Iterate mode: revision from prior paper + feedback ---
        if args.start_from_stage:
            logger.error("--iterate and --start-from-stage cannot be used together. "
                         "Use --iterate-start-stage instead.")
            return 1
        from .iterate import validate_iterate_dir, build_iterate_state_seed
        try:
            validate_iterate_dir(args.iterate)
        except ValueError as e:
            logger.error("Iterate validation failed: %s", e)
            return 1
        # Use --resume workspace if provided (campaign runner), else create new
        if args.resume:
            results_base_dir = os.path.abspath(args.resume)
            os.makedirs(results_base_dir, exist_ok=True)
        else:
            results_base_dir = os.path.join("results", f"consortium_{timestamp}_iterate")
            os.makedirs(results_base_dir, exist_ok=True)
        task = args.task or "Revise and improve the paper based on reviewer feedback."
        iterate_seed = build_iterate_state_seed(args.iterate, results_base_dir)
        logger.info("Created iterate workspace: %s", results_base_dir)
        logger.info("Prior paper: %s", iterate_seed.get('iterate_prior_paper_path', 'N/A'))
        logger.info("Feedback: %s", iterate_seed.get('iterate_feedback_summary', 'N/A'))
    elif args.resume:
        results_base_dir = os.path.abspath(args.resume)
        if not os.path.exists(results_base_dir):
            logger.error("Workspace directory does not exist: %s", results_base_dir)
            return 1
        if not os.path.isdir(results_base_dir):
            logger.error("Path is not a directory: %s", results_base_dir)
            return 1
        task = args.task or _CONTINUATION_TASK
        logger.info("Resuming from: %s", results_base_dir)
    else:
        results_base_dir = os.path.join("results", f"consortium_{timestamp}")
        os.makedirs(results_base_dir, exist_ok=True)
        task = args.task or _DEFAULT_TASK
        logger.info("Created workspace: %s", results_base_dir)

    os.environ["RESULTS_BASE_DIR"] = results_base_dir
    os.environ["CONSORTIUM_OUTPUT_FORMAT"] = getattr(args, "output_format", "latex")

    if enable_file_logs and not getattr(args, "dry_run", False):
        logs_dir = os.path.join(results_base_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        out_path = os.path.join(logs_dir, f"consortium_{timestamp}.out")
        err_path = os.path.join(logs_dir, f"consortium_{timestamp}.err")
        logger.info("Teeing stdout/stderr to: %s, %s", out_path, err_path)
        stdout_file = open(out_path, "w", buffering=1)
        stderr_file = open(err_path, "w", buffering=1)
        sys.stdout = _TeeStream(sys.stdout, stdout_file)
        sys.stderr = _TeeStream(sys.stderr, stderr_file)
        log_paths = {"stdout": out_path, "stderr": err_path}

    # --- Token tracking ---
    run_id = f"{timestamp}_{os.getpid()}"
    token_file = initialize_run_token_tracker(
        workspace_dir=results_base_dir, run_id=run_id, reset=True,
    )
    logger.info("Token tracker initialized: %s", token_file)

    # --- Training data logging ---
    log_training = getattr(args, "log_training_data", False) or \
        os.getenv("CONSORTIUM_LOG_TRAINING_DATA", "").strip().lower() in {"1", "true", "yes"}
    if log_training:
        from .logging.training_data_logger import initialize_training_data_logger
        include_tool_calls = not getattr(args, "no_tool_calls_in_training_data", False)
        initialize_training_data_logger(
            workspace_dir=results_base_dir,
            run_id=run_id,
            enabled=True,
            include_tool_calls=include_tool_calls,
        )
        logger.info("Training data logging enabled: %s/training_data.jsonl", results_base_dir)

    logger.info("Task: %s%s", task[:100], '...' if len(task) > 100 else '')
    logger.info("Pipeline mode: full_research (fixed-stage)")
    if args.enable_math_agents:
        logger.info("Math agent workflow enabled.")

    pipeline_stages = build_pipeline_stages_v2(args.enable_math_agents)
    logger.info("Pipeline version: v2 (persona-council-driven)")
    try:
        start_stage_index = (
            _resolve_start_stage_index(args.start_from_stage, pipeline_stages)
            if args.start_from_stage
            else 0
        )
    except ValueError as e:
        logger.error("%s", e)
        return 1

    if args.start_from_stage:
        resolved_stage = pipeline_stages[start_stage_index]
        logger.info(
            "Stage-based resume requested: start from '%s' (index %d).",
            resolved_stage, start_stage_index,
        )

    write_run_status(
        results_base_dir,
        status="running",
        current_stage=pipeline_stages[start_stage_index] if pipeline_stages else None,
        pid=os.getpid(),
        started_at=run_start_time.isoformat(),
        extra={"selected_tier": os.getenv("CONSORTIUM_SELECTED_TIER")},
    )
    campaign_run_id = _record_campaign_run_started(
        args,
        workspace_dir=results_base_dir,
        command=sys.argv[1:],
        dry_run=False,
    )

    # --- Artifact gate setup ---
    auto_enforce = "final_paper" in task.lower() or "experiments_to_run_later" in task.lower()
    enforce_paper_artifacts = args.enforce_paper_artifacts or auto_enforce
    enforce_editorial_artifacts = args.enforce_editorial_artifacts and enforce_paper_artifacts
    require_experiment_plan = args.require_experiment_plan or (
        "experiments_to_run_later" in task.lower()
    )
    required_paper_artifacts = _build_required_artifacts(
        args, enforce_paper_artifacts, enforce_editorial_artifacts, require_experiment_plan
    )

    os.environ["CONSORTIUM_REQUIRE_PDF"] = "1" if args.require_pdf else "0"
    os.environ["CONSORTIUM_ENFORCE_PAPER_ARTIFACTS"] = "1" if enforce_paper_artifacts else "0"
    os.environ["CONSORTIUM_REQUIRE_EXPERIMENT_PLAN"] = "1" if require_experiment_plan else "0"
    os.environ["CONSORTIUM_ENFORCE_EDITORIAL_ARTIFACTS"] = (
        "1" if enforce_editorial_artifacts else "0"
    )

    if enforce_paper_artifacts:
        logger.info("Paper artifact gate: %s", ", ".join(required_paper_artifacts))

    # --- LaTeX prereq check ---
    require_latex = enforce_paper_artifacts or args.require_pdf or enforce_editorial_artifacts
    if require_latex:
        pdflatex_path, bibtex_path, latex_error = check_latex_prereqs()
        if latex_error:
            logger.error("Missing LaTeX prerequisites.\n%s", latex_error)
            return 1
        os.environ["CONSORTIUM_PDFLATEX_PATH"] = pdflatex_path
        os.environ["CONSORTIUM_BIBTEX_PATH"] = bibtex_path
        logger.info("LaTeX toolchain: pdflatex=%s, bibtex=%s", pdflatex_path, bibtex_path)

    # --- Model setup ---
    budget_config = llm_config.get("budget", {}) if llm_config else {}
    model = create_model(
        model_name, reasoning_effort, verbosity, budget_tokens,
        effort=effort,
        budget_config=budget_config, budget_dir=results_base_dir,
    )
    logger.info("Created model: %s", getattr(model, 'model', model_name))

    # --- Per-agent model tiering ---
    model_registry = create_model_registry(
        llm_config, model, budget_config=budget_config, budget_dir=results_base_dir,
    )
    summary_model_id = _resolve_summary_model_id(llm_config, model_name)
    if model_registry._agent_models:
        tier_counts: dict[str, int] = {}
        for _agent, _m in model_registry._agent_models.items():
            mid = getattr(_m, "model", "unknown")
            tier_counts[mid] = tier_counts.get(mid, 0) + 1
        logger.info("Per-agent model tiers: %s", tier_counts)

    essential_imports = _filter_installed_imports([
        "json", "os", "posixpath", "ntpath", "sys", "datetime", "uuid", "typing",
        "pathlib", "shutil", "textwrap", "functools", "copy", "pickle", "logging",
        "warnings", "gc", "argparse", "configparser", "yaml", "toml", "requests",
        "urllib", "datasets", "transformers", "huggingface_hub", "tokenizers",
        "wandb", "tensorboard", "tqdm", "zipfile", "tarfile",
    ])

    # --- Counsel model setup ---
    counsel_cfg = llm_config.get("counsel", {}) if llm_config else {}
    counsel_enabled = counsel_settings["enabled"]

    if counsel_enabled:
        os.environ.pop("CONSORTIUM_COUNSEL_DISABLED", None)
    else:
        os.environ["CONSORTIUM_COUNSEL_DISABLED"] = "1"

    counsel_models_list = None
    if counsel_enabled:
        max_debate_rounds = counsel_settings["max_debate_rounds"]
        model_specs = counsel_settings["effective_model_specs"] or None

        logger.info("Counsel mode enabled — %d models, %d debate rounds per stage.",
                    len(model_specs or DEFAULT_COUNSEL_MODEL_SPECS), max_debate_rounds)
        counsel_models_list = create_counsel_models(
            budget_config=budget_config,
            budget_dir=results_base_dir,
            model_specs=model_specs,
        )
        os.environ["CONSORTIUM_COUNSEL_MAX_DEBATE_ROUNDS"] = str(max_debate_rounds)

        # Set per-model counsel timeout from env or default (600s)
        counsel_timeout = int(os.environ.get("COUNSEL_MODEL_TIMEOUT_SECONDS", "600"))
        set_counsel_timeout(counsel_timeout)

    # Build tree search config if enabled
    tree_search_config = None
    if getattr(args, "enable_tree_search", False):
        from consortium.tree_search.tree_state import CounselMode, TreeSearchConfig
        tree_search_config = TreeSearchConfig(
            enabled=True,
            max_breadth=getattr(args, "tree_max_breadth", 3),
            max_depth=getattr(args, "tree_max_depth", 4),
            max_parallel=getattr(args, "tree_max_parallel", 6),
            pruning_threshold=getattr(args, "tree_pruning_threshold", 0.2),
            counsel_mode=CounselMode(getattr(args, "tree_counsel_mode", "all_nodes")),
        )

    try:
        autonomous_mode = getattr(args, "autonomous_mode", True)
        enable_milestone_gates = getattr(args, "enable_milestone_gates", False)
        # In autonomous mode, force milestone gates off regardless of other flags
        if autonomous_mode and enable_milestone_gates:
            logger.info("Autonomous mode: milestone gates disabled.")
            enable_milestone_gates = False
        milestone_timeout = getattr(args, "milestone_timeout", 3600)
        adversarial_verification = getattr(args, "adversarial_verification", False)

        # --- Persona-council-driven pipeline ---
        pc_cfg = llm_config.get("persona_council", {}) if llm_config else {}
        dc_cfg = llm_config.get("duality_check", {}) if llm_config else {}

        persona_debate_rounds = getattr(args, "persona_debate_rounds", None)
        if persona_debate_rounds is None:
            persona_debate_rounds = int(pc_cfg.get("max_debate_rounds", 3))

        persona_council_specs = pc_cfg.get("personas") or None
        persona_synthesis_model = pc_cfg.get("synthesis_model", model_name)
        persona_post_vote_retries = getattr(args, "persona_post_vote_retries", None)
        if persona_post_vote_retries is None:
            persona_post_vote_retries = int(pc_cfg.get("max_post_vote_retries", 1))
        duality_check_model = dc_cfg.get("model", model_name)
        enable_duality_check = not getattr(args, "no_duality_check", False)

        # Quality limits (new args, fallback to defaults)
        theory_repair_max_attempts = getattr(args, "theory_repair_max_attempts", None) or 2
        duality_max_attempts = getattr(args, "duality_max_attempts", None) or 2
        max_validation_retries = getattr(args, "max_validation_retries", None) or 3
        enable_ensemble_review = getattr(args, "enable_ensemble_review", False)

        # Extract budget manager if available
        budget_manager = None
        if hasattr(model, "budget_manager"):
            budget_manager = model.budget_manager

        from .cli.core.model_policy import build_default_persona_specs
        from .graph import build_research_graph_v2
        from .graph_config import (
            ResearchGraphConfig,
            PersonaCouncilConfig,
            DualityCheckConfig,
            ArtifactEnforcementConfig,
        )
        checkpointer = get_default_checkpointer(results_base_dir)
        effective_persona_specs = persona_council_specs or build_default_persona_specs(
            model=model_name,
            reasoning_effort=reasoning_effort,
            budget_tokens=budget_tokens,
        )
        effective_models, model_provenance = _build_effective_model_manifest(
            args=args,
            llm_config=llm_config,
            model_name=model_name,
            summary_model_id=summary_model_id,
            counsel_settings=counsel_settings,
            persona_specs=effective_persona_specs,
            persona_synthesis_model=persona_synthesis_model,
            duality_check_model=duality_check_model,
        )
        manifest_path = _write_effective_model_manifest(results_base_dir, effective_models)
        _write_experiment_metadata(
            results_base_dir,
            args,
            model_name,
            task,
            project_root=str(project_root) if project_root else None,
            counsel_settings=counsel_settings,
            persona_settings={
                "debate_rounds": persona_debate_rounds,
                "synthesis_model": persona_synthesis_model,
                "max_post_vote_retries": persona_post_vote_retries,
                "personas": effective_persona_specs,
                "duality_check_model": duality_check_model,
            },
            effective_models=effective_models,
            model_provenance=model_provenance,
            credential_sources=credential_sources,
            log_paths=log_paths,
        )
        graph_config = ResearchGraphConfig(
            model=model,
            workspace_dir=results_base_dir,
            pipeline_mode=effective_pipeline_mode,
            enable_math_agents=args.enable_math_agents,
            artifacts=ArtifactEnforcementConfig(
                enforce_paper_artifacts=enforce_paper_artifacts,
                enforce_editorial_artifacts=enforce_editorial_artifacts,
                require_pdf=args.require_pdf,
                require_experiment_plan=require_experiment_plan,
            ),
            min_review_score=args.min_review_score,
            followup_max_iterations=args.followup_max_iterations,
            manager_max_steps=args.manager_max_steps or 50,
            authorized_imports=essential_imports,
            summary_model_id=summary_model_id,
            checkpointer=checkpointer,
            counsel_models=counsel_models_list,
            tree_search=tree_search_config,
            enable_milestone_gates=enable_milestone_gates,
            adversarial_verification=adversarial_verification,
            iterate_mode=bool(getattr(args, "iterate", None)),
            theory_repair_max_attempts=theory_repair_max_attempts,
            duality_max_attempts=duality_max_attempts,
            max_validation_retries=max_validation_retries,
            enable_ensemble_review=enable_ensemble_review,
            persona_council=PersonaCouncilConfig(
                specs=effective_persona_specs,
                debate_rounds=persona_debate_rounds,
                synthesis_model=persona_synthesis_model,
                max_post_vote_retries=persona_post_vote_retries,
            ),
            duality_check=DualityCheckConfig(
                enabled=enable_duality_check,
                model=duality_check_model,
            ),
            budget_manager=budget_manager,
            model_registry=model_registry,
        )
        graph = build_research_graph_v2(graph_config)
        logger.info("Pipeline: %d persona debate rounds, duality_check=%s",
                    persona_debate_rounds, 'enabled' if enable_duality_check else 'disabled')

        if manifest_path:
            logger.info("Effective model manifest: %s", manifest_path)

        if adversarial_verification:
            logger.info("Adversarial verification enabled — red-team verifiers will "
                        "challenge proofs and experiments after cooperative verification passes.")

        if enable_milestone_gates:
            logger.info("Milestone gates enabled (timeout=%ds). "
                        "POST /milestone_response to approve/modify/abort at each gate.",
                        milestone_timeout)

        # Build initial state
        initial_state = {
            "messages": [],
            "task": task,
            "workspace_dir": results_base_dir,
            "pipeline_mode": effective_pipeline_mode,
            "math_enabled": args.enable_math_agents,
            "enforce_paper_artifacts": enforce_paper_artifacts,
            "enforce_editorial_artifacts": enforce_editorial_artifacts,
            "require_pdf": args.require_pdf,
            "require_experiment_plan": require_experiment_plan,
            "min_review_score": args.min_review_score,
            "followup_max_iterations": args.followup_max_iterations,
            "manager_max_steps": args.manager_max_steps if args.manager_max_steps else 50,
            "pipeline_stages": pipeline_stages,
            "pipeline_stage_index": start_stage_index,
            "current_agent": None,
            "agent_task": None,
            "iterate_start_stage_override": None,
            "agent_outputs": {},
            "artifacts": {},
            "executed_stages": [],
            "iteration_count": 0,
            "followup_iteration": 0,
            "research_cycle": 0,
            "max_research_cycles": args.followup_max_iterations,
            "novelty_check_attempts": 0,
            "rebuttal_iteration": 0,
            "max_rebuttal_iterations": args.max_rebuttal_iterations,
            "validation_results": {},
            "interrupt_instruction": None,
            "theory_track_status": None,
            "experiment_track_status": None,
            "track_decomposition": None,
            "tree_search_enabled": getattr(args, "enable_tree_search", False),
            "tree_state_path": None,
            "active_branch_id": None,
            "milestone_reports": [],
            "human_feedback_history": [],
            "enable_milestone_gates": enable_milestone_gates,
            "milestone_timeout": milestone_timeout,
            "intermediate_validation_log": [],
            "finished": False,
            # Pipeline fields
            "autonomous_mode": autonomous_mode,
            "research_proposal": None,
            "brainstorm_output": None,
            "brainstorm_history": [],
            "research_goals": None,
            "formalized_results": None,
            "lit_review_feasibility": None,
            "verify_completion_result": None,
            "verify_completion_history": [],
            "duality_check_result": None,
            "lit_review_attempts": 0,
            "brainstorm_cycle": 0,
            "brainstorm_artifact_retries": 0,
            "verify_rework_attempts": 0,
            "duality_rework_attempts": 0,
            # Iterate mode fields
            "iterate_mode": False,
            "iterate_prior_paper_path": None,
            "iterate_feedback_path": None,
            "iterate_feedback_summary": None,
            "iterate_binding_constraints": None,
            "iterate_route": None,
            # Critical failure halts pipeline on non-retryable errors
            "critical_failure": None,
            # Theory repair tracking
            "theory_repair_count": 0,
            "theory_track_summary": None,
            "validation_retry_count": 0,
            "max_validation_retries": max_validation_retries,
        }

        # --- Iterate mode: merge state seed and override entry stage ---
        if getattr(args, "iterate", None):
            initial_state.update(iterate_seed)  # noqa: F821 (defined in iterate branch above)
            iterate_entry_stage = getattr(args, "iterate_start_stage", None)
            if iterate_entry_stage:
                canonical = _canonical_stage_name(iterate_entry_stage)
                if canonical not in pipeline_stages:
                    logger.error(
                        "iterate-start-stage '%s' resolved to unknown stage '%s'.",
                        iterate_entry_stage,
                        canonical,
                    )
                    return 1
                initial_state["iterate_start_stage_override"] = canonical
                initial_state["pipeline_stage_index"] = pipeline_stages.index(canonical)

        # Use workspace_dir as thread_id for default resumability.
        # For stage-based resume, create a fresh thread in the same workspace so
        # previous checkpoint state does not override the requested stage index.
        if args.start_from_stage:
            canonical_stage = _canonical_stage_name(args.start_from_stage)
            thread_id = f"{results_base_dir}::stage_resume::{canonical_stage}::{timestamp}"
        else:
            thread_id = results_base_dir
        run_config = {"configurable": {"thread_id": thread_id}}

        logger.info("=" * 50)
        logger.info("Running LangGraph research pipeline...")
        logger.info("Task: %s", task)
        logger.info("=" * 50)

        # --- Progress heartbeat watchdog (Tier 1.1) ---
        # Writes a .progress_heartbeat file every 2 minutes so the campaign
        # heartbeat / OpenClaw overseer can detect hung stages (process alive
        # but not making progress for >30 min).
        progress_file = os.path.join(results_base_dir, ".progress_heartbeat")
        _graph_done = threading.Event()
        stages_done: list[str] = []

        def _watchdog_writer():
            while not _graph_done.is_set():
                try:
                    current_status = read_run_status(results_base_dir)
                    tmp = progress_file + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(
                            {
                                "ts": time.time(),
                                "pid": os.getpid(),
                                "stage": current_status.get("current_stage"),
                            },
                            f,
                        )
                    os.replace(tmp, progress_file)
                except OSError:
                    logger.debug("Heartbeat write failed (disk full / permissions)", exc_info=True)
                _graph_done.wait(120)

        watchdog_thread = threading.Thread(target=_watchdog_writer, daemon=True)
        watchdog_thread.start()

        # --- Hard timeout via SIGALRM (Tier 1.1) ---
        max_run_seconds = getattr(args, "max_run_seconds", None)
        _old_alarm_handler = None
        if max_run_seconds and hasattr(signal, "SIGALRM"):
            def _timeout_handler(signum, frame):
                raise TimeoutError(
                    f"Pipeline exceeded --max-run-seconds={max_run_seconds}. "
                    f"Killing run to prevent infinite hang."
                )
            _old_alarm_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(max_run_seconds)
            logger.info("Hard timeout set: %ds", max_run_seconds)

        # Validate initial state before graph invocation
        from .state import validate_initial_state
        try:
            state_warnings = validate_initial_state(initial_state)
            for w in state_warnings:
                logger.warning("[state] %s", w)
        except ValueError as e:
            logger.error("[state] FATAL: %s", e)
            raise

        try:
            final_state = graph.invoke(initial_state, config=run_config)
        finally:
            _graph_done.set()
            if max_run_seconds and hasattr(signal, "SIGALRM"):
                signal.alarm(0)  # cancel alarm
                if _old_alarm_handler is not None:
                    signal.signal(signal.SIGALRM, _old_alarm_handler)
            # Clean up progress heartbeat file on normal completion
            try:
                os.remove(progress_file)
            except OSError:
                logger.debug("Could not remove progress heartbeat file", exc_info=True)

        result = final_state.get("agent_outputs", {})
        result = sanitize_result_payload(
            result=str(result),
            workspace_dir=results_base_dir,
            required_artifacts=required_paper_artifacts,
        )
        if isinstance(result, dict) and result.get("status") == "incomplete":
            logger.warning(
                "Run marked incomplete — missing: %s",
                ", ".join(result.get("missing_required_artifacts", [])),
            )

        # Determine which stages completed (read from state if available)
        if isinstance(final_state, dict):
            executed = final_state.get("executed_stages") or []
            if isinstance(executed, list):
                stages_done = [stage for stage in executed if isinstance(stage, str)]

        write_run_status(
            results_base_dir,
            status="completed",
            current_stage=stages_done[-1] if stages_done else None,
            pid=os.getpid(),
            finished_at=datetime.now().isoformat(),
        )
        _write_run_summary(
            workspace_dir=results_base_dir,
            task=task,
            model_name=model_name,
            start_time=run_start_time,
            stages_completed=stages_done,
            status="completed",
            current_stage=stages_done[-1] if stages_done else None,
        )
        _record_campaign_known_artifacts(args)
        _record_campaign_run_exited(
            args,
            campaign_run_id,
            exit_code=0,
            status="completed",
            metadata={"workspace_dir": results_base_dir, "stages_completed": stages_done},
        )

        logger.info("=" * 50)
        logger.info("Task finished.")
        logger.info("=" * 50)

    except Exception as e:
        if "results_base_dir" in locals():
            current_status = read_run_status(results_base_dir)
            write_run_status(
                results_base_dir,
                status="failed",
                current_stage=current_status.get("current_stage"),
                status_reason=str(e),
                pid=os.getpid(),
                finished_at=datetime.now().isoformat(),
            )
            _write_run_summary(
                workspace_dir=results_base_dir,
                task=task if "task" in locals() else _DEFAULT_TASK,
                model_name=model_name if "model_name" in locals() else "unknown",
                start_time=run_start_time,
                stages_completed=stages_done if "stages_done" in locals() else [],
                status="failed",
                status_reason=str(e),
                current_stage=current_status.get("current_stage"),
            )
            _record_campaign_known_artifacts(args)
            _record_campaign_run_exited(
                args,
                campaign_run_id,
                exit_code=1,
                status="failed",
                metadata={"workspace_dir": results_base_dir, "error": str(e)},
            )
        logger.error("Error during pipeline execution: %s", e, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
