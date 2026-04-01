import logging
import os
import yaml
import functools

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Budget-aware litellm.completion() decorator
# ---------------------------------------------------------------------------

def _record_budget_from_response(model_id: str, response) -> None:
    """Best-effort budget recording for any litellm.completion() call.

    Called automatically by the monkey-patched ``litellm.completion``.
    Lets ``BudgetExceededError`` propagate (enforces the budget limit) but
    swallows all other exceptions so tracking never breaks a completion call.
    """
    from .budget import BudgetExceededError, get_global_budget_manager
    try:
        mgr = get_global_budget_manager()
        if mgr is None:
            return
        usage = getattr(response, "usage", None)
        if not usage:
            return
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if prompt_tokens or completion_tokens:
            mgr.record_usage(
                model_id=model_id or "unknown",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
    except BudgetExceededError:
        raise
    except Exception:
        pass  # Never break a completion call for tracking errors


def _check_budget_before_call() -> None:
    """Pre-call budget enforcement. Raises BudgetExceededError if limit hit."""
    from .budget import BudgetExceededError, get_global_budget_manager
    try:
        mgr = get_global_budget_manager()
        if mgr is not None:
            mgr.check_budget()
    except BudgetExceededError:
        raise
    except Exception:
        pass  # Don't block calls on tracking infrastructure errors


# Custom parameter filtering function for model-specific requirements
def filter_model_params(original_func):
    """Decorator to filter unsupported parameters for different models.

    Also performs pre-call budget enforcement and post-call budget recording
    via the global BudgetManager singleton (set in utils.create_model).
    """
    @functools.wraps(original_func)
    def wrapper(*args, **kwargs):
        model = kwargs.get('model', args[0] if args else '')

        # --- Pre-call budget check ---
        _check_budget_before_call()

        # --- Parameter filtering per model family ---
        final_args = args
        if isinstance(model, str) and ('gpt-5' in model or "codex" in model):
            # GPT-5/Codex specific filtering
            unsupported_params = {
                'stop', 'temperature', 'top_p', 'presence_penalty',
                'frequency_penalty', 'logprobs', 'top_logprobs',
                'logit_bias', 'max_tokens'
            }
            filtered_kwargs = {k: v for k, v in kwargs.items()
                             if k not in unsupported_params}
            if 'max_tokens' in kwargs:
                filtered_kwargs['max_completion_tokens'] = kwargs['max_tokens']
            final_kwargs = filtered_kwargs

        elif isinstance(model, str) and ("claude" in model or "anthropic" in model):
            # Anthropic / Claude branch
            fk = kwargs.copy()

            # Translate legacy "effort" param to litellm-supported "reasoning_effort"
            if "effort" in fk and "reasoning_effort" not in fk:
                fk["reasoning_effort"] = fk.pop("effort")
            elif "effort" in fk:
                fk.pop("effort")

            # Don't send both temperature + top_p to Claude
            if "temperature" in fk and "top_p" in fk:
                fk.pop("top_p")

            # Normalize extended thinking:
            # Accept user-friendly `budget_tokens` and convert to Anthropic `thinking`
            budget = fk.pop("budget_tokens", None)
            thinking = fk.get("thinking")

            if budget is not None:
                if budget <= 0:
                    fk["thinking"] = {"type": "disabled"}
                else:
                    fk["thinking"] = {"type": "enabled", "budget_tokens": int(budget)}
            elif thinking is None:
                pass

            # Enforce invariant: max_tokens > thinking.budget_tokens
            if isinstance(fk.get("thinking"), dict) and fk["thinking"].get("type") == "enabled":
                budget_tokens = int(fk["thinking"].get("budget_tokens", 0))
                margin = 2048
                mt = fk.get("max_tokens")
                if mt is None or int(mt) <= budget_tokens:
                    fk["max_tokens"] = int(budget_tokens + margin)

            final_kwargs = fk

        else:
            final_kwargs = kwargs

        # --- Call the original litellm.completion ---
        response = original_func(*final_args, **final_kwargs)

        # --- Post-call budget recording ---
        _record_budget_from_response(model if isinstance(model, str) else str(model), response)

        return response
    return wrapper

def _validate_config(config):
    """Warn about missing or malformed config sections. Does not block execution."""
    if not isinstance(config, dict):
        logger.warning("LLM config is not a dict — ignoring")
        return
    if "main_agents" in config:
        ma = config["main_agents"]
        if not isinstance(ma, dict):
            logger.warning("main_agents must be a dict in .llm_config.yaml")
        elif "model" not in ma:
            logger.warning("main_agents.model is not set in .llm_config.yaml")
    if "budget" in config:
        b = config["budget"]
        if not isinstance(b, dict):
            logger.warning("budget must be a dict in .llm_config.yaml")
        elif "usd_limit" not in b:
            logger.warning("budget.usd_limit is not set — cost enforcement will be disabled")
        elif not b.get("pricing"):
            logger.warning("budget.pricing is empty — cost enforcement will fail for unknown models")


def load_llm_config():
    """Load LLM configuration from .llm_config.yaml if it exists."""
    config_path = ".llm_config.yaml"
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info("Loaded LLM config from %s", config_path)
            _validate_config(config)
            return config
        except yaml.YAMLError as e:
            logger.warning("Error loading %s: %s", config_path, e)
            return None
    else:
        logger.info("No %s found, using CLI arguments", config_path)
        return None
