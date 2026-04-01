import json
import logging
import os
import re
from typing import Any, Optional
from langchain_community.chat_models import ChatLiteLLM

# Configure litellm retries globally for transient API errors
import litellm as _litellm
_litellm.num_retries = 3
_litellm.request_timeout = 300  # 5 min timeout per request

from .models import AVAILABLE_MODELS  # noqa: F401 — re-exported for backward compat

logger = logging.getLogger(__name__)


def normalize_model_for_litellm(model: str) -> str:
    """Add OpenRouter provider prefix for direct litellm.completion() calls.

    The LangChain ChatLiteLLM wrapper (create_model) already handles this,
    but direct litellm.completion() calls need the prefix to route correctly.
    All models are routed through OpenRouter.
    """
    if model.startswith("openrouter/"):
        return model
    from .models import get_openrouter_name
    return f"openrouter/{get_openrouter_name(model)}"


def _require_env(key: str, model_name: str) -> str:
    """Get a required environment variable or raise a descriptive error."""
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"API key '{key}' is required for model '{model_name}' but is not set. "
            f"Add it to your .env file: {key}=your_key_here"
        )
    return value


def extract_content_between_markers(response: str, start_marker: str, end_marker: str) -> Optional[str]:
    """Extract content between specified start and end markers from a response string."""
    try:
        start_escaped = re.escape(start_marker)
        end_escaped = re.escape(end_marker)
        pattern = f"{start_escaped}(.*?){end_escaped}"
        matches = re.findall(pattern, response, re.DOTALL)
        if matches:
            return matches[0].strip()
        return None
    except Exception as e:
        logger.warning("extract_content_between_markers failed: %s", e)
        return None


def create_model(
    model_name,
    reasoning_effort="medium",
    verbosity="medium",
    budget_tokens=None,
    budget_config=None,
    budget_dir=None,
    effort=None,
):
    """Create a ChatLiteLLM model from the given model name using API keys from environment.

    Args:
        model_name:       Name of the model to create
        reasoning_effort: GPT-5 reasoning effort level (minimal, low, medium, high)
        verbosity:        GPT-5 verbosity level (low, medium, high)
        budget_tokens:    Claude Extended Thinking token budget (min: 1024)
        effort:           Claude 4.x effort level (low, medium, high, max)
    """
    model_kwargs: dict = {}

    # --- Provider-specific model_kwargs setup ---
    if "claude" in model_name:
        if budget_tokens is not None:
            model_kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
        if effort is not None and model_name in {"claude-opus-4-6", "claude-sonnet-4-6"}:
            model_kwargs["reasoning_effort"] = effort

    elif "codex" in model_name:
        model_kwargs["reasoning_effort"] = reasoning_effort

    elif "gpt-5" in model_name:
        model_kwargs["reasoning_effort"] = reasoning_effort
        model_kwargs["verbosity"] = verbosity

    elif "gemini" in model_name:
        if "gemini-3-pro" in model_name:
            model_kwargs["thinking_budget"] = 65536
        elif "gemini-2.5-pro" in model_name:
            model_kwargs["thinking_budget"] = 32768

    # --- Unified OpenRouter routing for all models ---
    from .models import get_openrouter_name
    or_model_name = f"openrouter/{get_openrouter_name(model_name)}"
    api_key = _require_env("OPENROUTER_API_KEY", model_name)

    base_model = ChatLiteLLM(
        model=or_model_name,
        api_key=api_key,
        model_kwargs=model_kwargs if model_kwargs else None,
    )

    # Optional hard budget enforcement wrapper
    if budget_config and budget_config.get("usd_limit"):
        from .budget import BudgetManager, BudgetedLiteLLMModel
        budget_dir = budget_dir or os.getcwd()
        state_path = os.path.join(budget_dir, "budget_state.json")
        ledger_path = os.path.join(budget_dir, "budget_ledger.jsonl")
        lock_path = os.path.join(budget_dir, "budget.lock")

        manager = BudgetManager(
            usd_limit=float(budget_config["usd_limit"]),
            pricing=budget_config.get("pricing", {}),
            state_path=state_path,
            ledger_path=ledger_path,
            lock_path=lock_path,
            hard_stop=bool(budget_config.get("hard_stop", True)),
            fail_closed=bool(budget_config.get("fail_closed", True)),
        )
        # Register globally so the monkey-patched litellm.completion() can
        # record budget without every callsite passing BudgetManager explicitly.
        from .budget import set_global_budget_manager
        set_global_budget_manager(manager)
        return BudgetedLiteLLMModel(base_model, manager)

    return base_model


# ---------------------------------------------------------------------------
# Per-agent model tiering
# ---------------------------------------------------------------------------

class ModelRegistry:
    """Maps agent names to model instances with a default fallback.

    Used by the graph builder to assign different models to different agents
    based on the ``per_agent_models`` config section in ``.llm_config.yaml``.
    """

    def __init__(self, default_model: Any, agent_models: Optional[dict[str, Any]] = None):
        self._default = default_model
        self._agent_models = agent_models or {}

    def get(self, agent_name: str) -> Any:
        """Return the model for *agent_name*, falling back to the default."""
        return self._agent_models.get(agent_name, self._default)

    @property
    def default_model(self) -> Any:
        return self._default


def create_model_registry(
    llm_config: Optional[dict],
    default_model: Any,
    budget_config: Optional[dict] = None,
    budget_dir: Optional[str] = None,
) -> ModelRegistry:
    """Build a :class:`ModelRegistry` from the ``per_agent_models`` config.

    All tier models share the same :class:`BudgetManager` extracted from
    *default_model* so that spend is tracked against a single ledger and
    USD limit.  If ``per_agent_models.enabled`` is falsy the registry simply
    wraps the default model and every agent gets the same instance.
    """
    cfg = (llm_config or {}).get("per_agent_models", {})
    if not cfg.get("enabled"):
        return ModelRegistry(default_model)

    # Shared budget manager — all tier models record to the same ledger.
    budget_manager = getattr(default_model, "_budget_manager", None)

    tier_models: dict[str, Any] = {}
    for tier_name, tier_spec in cfg.get("tiers", {}).items():
        bare = create_model(
            tier_spec["model"],
            reasoning_effort=tier_spec.get("reasoning_effort", "medium"),
            budget_tokens=tier_spec.get("budget_tokens"),
            effort=tier_spec.get("effort"),
            # Deliberately omit budget_config so create_model returns an
            # unwrapped ChatLiteLLM.  We wrap with the *shared* manager below.
        )
        if budget_manager is not None:
            from .budget import BudgetedLiteLLMModel
            bare = BudgetedLiteLLMModel(bare, budget_manager)
        tier_models[tier_name] = bare

    # Map agent names → tier model instances.
    agent_models: dict[str, Any] = {}
    for agent_name, tier_name in cfg.get("agent_tiers", {}).items():
        if tier_name in tier_models:
            agent_models[agent_name] = tier_models[tier_name]
        else:
            logger.warning(
                "per_agent_models.agent_tiers: unknown tier %r for agent %r — using default",
                tier_name, agent_name,
            )

    tier_summary = {}
    for agent_name, m in agent_models.items():
        model_id = getattr(m, "model", "unknown")
        tier_summary[model_id] = tier_summary.get(model_id, 0) + 1
    logger.info("Per-agent model registry: %s (default → %s)",
                tier_summary, getattr(default_model, "model", "unknown"))

    return ModelRegistry(default_model, agent_models)


def build_research_graph(
    model,
    workspace_dir,
    essential_imports=None,
    enable_planning=False,
    planning_interval=3,
    interrupt_callback=None,
    require_pdf=False,
    enforce_paper_artifacts=False,
    require_experiment_plan=False,
    enable_math_agents=False,
    enforce_editorial_artifacts=False,
    min_review_score=8,
    pipeline_mode="default",
    followup_max_iterations=3,
    manager_max_steps=50,
    counsel_models=None,
    summary_model_id="claude-sonnet-4-6",
    tree_search_config=None,
    enable_milestone_gates=False,
    adversarial_verification=False,
):
    """
    Build and return the compiled LangGraph research pipeline.

    The active graph is a directly wired three-phase workflow:
    discovery -> parallel theory/experiment execution -> synthesis/papering.

    Args:
        model:                    ChatLiteLLM instance
        workspace_dir:            Absolute path to the run workspace
        essential_imports:        Authorized Python imports for code tools
        enable_planning:          Unused (kept for call-site compat)
        planning_interval:        Unused (kept for call-site compat)
        interrupt_callback:       Unused (interrupt handled via state)
        require_pdf:              Require final_paper.pdf at finish
        enforce_paper_artifacts:  Run artifact gate before FINISH
        require_experiment_plan:  Require experiments_to_run_later.md at finish
        enable_math_agents:       Include math pipeline agents
        enforce_editorial_artifacts: Run full editorial gates
        min_review_score:         Reviewer score threshold
        pipeline_mode:            "default" | "full_research" | "quick"
        followup_max_iterations:  Seeds the max follow-up research cycles
        manager_max_steps:        Kept for call-site compatibility
        summary_model_id:         Model for stage summary PDF generation.
                                  Set to None to disable.
        enable_milestone_gates:   Pause at milestone points for human review.
        adversarial_verification: Run adversarial red-team verifiers after cooperative pass.

    Returns:
        (compiled_graph, checkpointer) tuple.
    """
    logger.info("Building LangGraph research pipeline...")

    from .graph import build_research_graph as _build_graph, get_default_checkpointer

    checkpointer = get_default_checkpointer(workspace_dir)

    compiled = _build_graph(
        model=model,
        workspace_dir=workspace_dir,
        pipeline_mode=pipeline_mode,
        enable_math_agents=enable_math_agents,
        enforce_paper_artifacts=enforce_paper_artifacts,
        enforce_editorial_artifacts=enforce_editorial_artifacts,
        require_pdf=require_pdf,
        require_experiment_plan=require_experiment_plan,
        min_review_score=min_review_score,
        followup_max_iterations=followup_max_iterations,
        manager_max_steps=manager_max_steps,
        authorized_imports=essential_imports,
        checkpointer=checkpointer,
        counsel_models=counsel_models,
        summary_model_id=summary_model_id,
        tree_search_config=tree_search_config,
        enable_milestone_gates=enable_milestone_gates,
        adversarial_verification=adversarial_verification,
    )
    logger.info("LangGraph pipeline ready.")
    return compiled, checkpointer



def save_agent_memory(manager):
    """No-op after LangGraph migration — SqliteSaver checkpointer handles persistence."""
    pass
