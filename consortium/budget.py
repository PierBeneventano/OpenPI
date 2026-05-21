import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton so that the monkey-patched litellm.completion()
# (see config.py) can record budget without every callsite threading
# BudgetManager through its arguments.
# ---------------------------------------------------------------------------
_global_budget_manager: Optional["BudgetManager"] = None
_global_lock = threading.Lock()


def set_global_budget_manager(manager: "BudgetManager") -> None:
    """Set the process-wide BudgetManager singleton. Called once at startup."""
    global _global_budget_manager
    with _global_lock:
        _global_budget_manager = manager


def get_global_budget_manager() -> Optional["BudgetManager"]:
    """Return the process-wide BudgetManager, or None if not configured."""
    return _global_budget_manager


class BudgetExceededError(RuntimeError):
    """Raised when the configured USD budget has been exhausted."""


class BudgetManager:
    """
    Tracks cumulative spend and enforces a hard USD limit.

    Notes:
    - Uses model-reported token_usage (prompt_tokens/completion_tokens) when available.
    - Requires per-model pricing to be provided by the user (input/output per 1K tokens).
    - Writes a lock file when the limit is reached to prevent further calls.
    """

    def __init__(
        self,
        usd_limit: float,
        pricing: Dict[str, Dict[str, float]],
        state_path: str,
        ledger_path: str,
        lock_path: str,
        hard_stop: bool = True,
        fail_closed: bool = True,
    ) -> None:
        self.usd_limit = float(usd_limit)
        self.pricing = pricing or {}
        self.state_path = state_path
        self.ledger_path = ledger_path
        self.lock_path = lock_path
        self.hard_stop = hard_stop
        self.fail_closed = fail_closed
        self.total_usd = 0.0
        self.by_model: Dict[str, float] = {}
        self._record_lock = threading.Lock()
        self._record_count = 0
        self._FLUSH_INTERVAL = 10  # flush every N records
        self._load_state()

    def _load_state(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.total_usd = float(data.get("total_usd", 0.0))
            self.by_model = data.get("by_model", {}) or {}
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            if self.fail_closed:
                raise BudgetExceededError(
                    f"Budget state file '{self.state_path}' is corrupted and "
                    f"fail_closed is enabled. Cannot safely continue without "
                    f"accurate spend tracking. Error: {exc}"
                ) from exc
            # Attempt recovery from the append-only ledger (more resilient
            # than the state file since it's never overwritten in place).
            recovered = self._recover_from_ledger()
            if recovered:
                logger.warning(
                    "Budget state file '%s' corrupted (%s); recovered $%.4f from ledger.",
                    self.state_path, exc, self.total_usd,
                )
            else:
                logger.warning(
                    "Budget state file '%s' corrupted (%s) and ledger unavailable; "
                    "resetting to zero. Spend tracking may be inaccurate.",
                    self.state_path, exc,
                )
                self.total_usd = 0.0
                self.by_model = {}

    def _recover_from_ledger(self) -> bool:
        """Attempt to reconstruct spend totals from the append-only ledger file."""
        if not os.path.exists(self.ledger_path):
            return False
        try:
            total = 0.0
            by_model: Dict[str, float] = {}
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    cost = float(entry.get("cost_usd", 0.0))
                    model = entry.get("model_id", "unknown")
                    total += cost
                    by_model[model] = by_model.get(model, 0.0) + cost
            self.total_usd = total
            self.by_model = by_model
            # Persist the recovered state so next load doesn't re-scan
            self._save_state()
            return True
        except Exception as exc:
            logger.debug("Ledger recovery failed: %s", exc)
            return False

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        data = {
            "usd_limit": self.usd_limit,
            "total_usd": round(self.total_usd, 6),
            "by_model": self.by_model,
            "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        # Atomic write: write to tmp then rename to prevent corruption
        # from crashes during write.
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.state_path)

    def _write_ledger(self, entry: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _normalize_model_id(self, model_id: str) -> str:
        if not model_id:
            return ""
        # Prefer provider-qualified exact match, but allow fallback to bare model name
        if model_id in self.pricing:
            return model_id
        if "/" in model_id:
            return model_id.split("/")[-1]
        return model_id

    def _get_pricing(self, model_id: str) -> Optional[Dict[str, float]]:
        if not model_id:
            return None
        if model_id in self.pricing:
            return self.pricing[model_id]
        normalized = self._normalize_model_id(model_id)
        return self.pricing.get(normalized)

    def _suggest_sibling_pricing(self, model_id: str) -> Optional[Dict[str, float]]:
        """Find a similarly-named model that *does* have pricing.

        Heuristic: strip trailing version/date suffixes and look for a prefix
        match. Returns the sibling's pricing dict (with a ``"sibling"`` key
        added) so the caller can surface 'suggested rate based on X' to the
        user.
        """
        if not model_id or not self.pricing:
            return None
        normalized = self._normalize_model_id(model_id)
        candidates = [model_id, normalized]
        # Try progressively shorter prefixes
        for ref in candidates:
            for known in self.pricing:
                if ref and known and known != ref and (ref.startswith(known) or known.startswith(ref.rsplit("-", 1)[0])):
                    pricing = dict(self.pricing[known])
                    pricing["sibling"] = known
                    return pricing
        return None

    def _read_pricing_disposition(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Check the campaign metadata for a previously-chosen disposition.

        If the user already decided how to handle an uncovered model in this
        campaign (via the VSCode pricing pause UI), don't re-prompt — apply
        the saved choice. Returns ``None`` outside a campaign context or when
        no disposition has been recorded yet.
        """
        campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
        campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
        if not campaign_id or not campaign_root:
            return None
        try:
            from msc_sdk.campaign_store import CampaignStore

            meta = CampaignStore(campaign_root).get_campaign_metadata(campaign_id)
            dispositions = meta.get("pricing_dispositions") or {}
            return dispositions.get(model_id) or dispositions.get(self._normalize_model_id(model_id))
        except Exception:
            return None

    def _pause_for_pricing_decision(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Open a ``PricingUnknown`` decision and wait for the user's choice.

        Writes a decision via ``propose_repair`` with the suggested sibling
        rate (if any). Blocks until the user picks one of:

          ``{"action": "use_suggested"}``                — use the sibling rate
          ``{"action": "set_rate", "input_per_1k": ..., "output_per_1k": ...}``
          ``{"action": "treat_as_zero"}``                — charge $0 from now on
          ``{"action": "skip_model"}``                   — calls return error

        The choice is read from
        ``<campaign>/steering/<run_id>/pricing_inbox.jsonl`` (one record per
        instruction, latest wins). The chosen disposition is persisted to
        campaign metadata so we don't re-prompt for the same model later.

        Returns ``None`` if we cannot reach a decision (no campaign context;
        timeout); callers should fall back to ``fail_closed`` behaviour.
        """
        campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
        campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
        run_id = os.environ.get("MSC_CAMPAIGN_RUN_ID") or os.environ.get("CONSORTIUM_RUN_ID")
        if not campaign_id or not campaign_root:
            return None  # foreground / standalone runs: nothing to wait on

        suggestion = self._suggest_sibling_pricing(model_id)
        try:
            from msc_sdk.campaign_store import CampaignStore

            store = CampaignStore(campaign_root)
            reason = (
                f"Pricing unknown for model '{model_id}'. "
                + (f"Suggested rate based on {suggestion.get('sibling')}: "
                   f"${suggestion.get('input_per_1k')}/1k in, "
                   f"${suggestion.get('output_per_1k')}/1k out. " if suggestion else "")
                + "Choose: use-suggested / set-rate / treat-as-zero / skip-model."
            )
            store.propose_repair(
                campaign_id,
                node_id=None,
                reason=reason,
                actor="budget",
            )
            # Also emit a PricingUnknown event so the UI can surface it
            store.append_event(
                campaign_id, "PricingUnknown", actor="budget",
                payload={"model_id": model_id, "suggestion": suggestion, "run_id": run_id},
            )
        except Exception as exc:
            logger.warning("Could not open pricing-unknown decision for %s: %s", model_id, exc)
            return None

        # Block on pricing_inbox.jsonl until a disposition arrives.
        if not run_id:
            return None
        inbox_path = os.path.join(
            campaign_root, "results", campaign_id, "steering", run_id, "pricing_inbox.jsonl"
        )
        # Fall back to the standard run_dir layout used by hpc submit
        if not os.path.isdir(os.path.dirname(inbox_path)):
            try:
                from msc_sdk.campaign_store import CampaignStore

                info = CampaignStore(campaign_root).inspect_dict(campaign_id)
                ws = info.get("workspace_root") or info.get("path")
                if ws:
                    inbox_path = os.path.join(str(ws), "steering", run_id, "pricing_inbox.jsonl")
            except Exception:
                pass
        os.makedirs(os.path.dirname(inbox_path), exist_ok=True)
        logger.warning(
            "[budget] Paused on uncovered pricing for %s. Waiting on %s. "
            "Use 'msc hpc set-pricing-disposition' to resume.",
            model_id, inbox_path,
        )

        # Poll the inbox for ~15 minutes; check every 2s.
        deadline = datetime.now(timezone.utc).timestamp() + 15 * 60
        last_size = 0
        while datetime.now(timezone.utc).timestamp() < deadline:
            try:
                size = os.path.getsize(inbox_path) if os.path.exists(inbox_path) else 0
                if size > last_size:
                    last_size = size
                    with open(inbox_path, "r", encoding="utf-8") as f:
                        lines = [ln for ln in f.read().splitlines() if ln.strip()]
                    for ln in reversed(lines):
                        try:
                            rec = json.loads(ln)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("model_id") != model_id and rec.get("model_id") is not None:
                            continue
                        action = rec.get("action")
                        if action in ("use_suggested", "set_rate", "treat_as_zero", "skip_model"):
                            return rec
            except OSError:
                pass
            threading.Event().wait(2.0)
        logger.warning(
            "[budget] Pricing-unknown decision for %s timed out after 15min.", model_id
        )
        return None

    def _apply_pricing_disposition(
        self, model_id: str, disposition: Dict[str, Any]
    ) -> Optional[Dict[str, float]]:
        """Apply a saved disposition: returns effective pricing, or None to skip.

        Side effect: persists the disposition into the campaign metadata so
        we don't re-prompt for the same model on subsequent calls.
        """
        action = disposition.get("action")
        suggestion = self._suggest_sibling_pricing(model_id) or {}
        if action == "use_suggested" and suggestion:
            effective = {
                "input_per_1k": float(suggestion.get("input_per_1k", 0.0)),
                "output_per_1k": float(suggestion.get("output_per_1k", 0.0)),
            }
        elif action == "set_rate":
            try:
                effective = {
                    "input_per_1k": float(disposition.get("input_per_1k", 0.0)),
                    "output_per_1k": float(disposition.get("output_per_1k", 0.0)),
                }
            except (TypeError, ValueError):
                return None
        elif action == "treat_as_zero":
            effective = {"input_per_1k": 0.0, "output_per_1k": 0.0}
        elif action == "skip_model":
            effective = None
        else:
            return None

        # Persist in memory + in campaign metadata for future calls.
        if effective is not None:
            self.pricing[model_id] = effective
        self._persist_pricing_disposition(model_id, {"action": action, **(effective or {})})
        return effective

    def _persist_pricing_disposition(self, model_id: str, disposition: Dict[str, Any]) -> None:
        campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
        campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
        if not campaign_id or not campaign_root:
            return
        try:
            from msc_sdk.campaign_store import CampaignStore

            store = CampaignStore(campaign_root)
            meta = store.get_campaign_metadata(campaign_id)
            dispositions = dict(meta.get("pricing_dispositions") or {})
            dispositions[model_id] = disposition
            store.update_campaign_metadata(
                campaign_id, {"pricing_dispositions": dispositions}, actor="budget"
            )
        except Exception as exc:
            logger.warning("Could not persist pricing disposition for %s: %s", model_id, exc)

    def _compute_cost(self, model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = self._get_pricing(model_id)
        if not pricing:
            # Check whether the user has previously decided how to treat this
            # model in this campaign. If so, apply silently.
            disposition = self._read_pricing_disposition(model_id)
            if disposition:
                pricing = self._apply_pricing_disposition(model_id, disposition)
                if pricing is None and disposition.get("action") == "skip_model":
                    raise BudgetExceededError(
                        f"Model '{model_id}' is configured to be skipped (no pricing)."
                    )
            else:
                # First encounter — pause and ask. The runner blocks here
                # until the user clicks a button in the VSCode pricing UI.
                disposition = self._pause_for_pricing_decision(model_id)
                if disposition:
                    pricing = self._apply_pricing_disposition(model_id, disposition)
                    if pricing is None and disposition.get("action") == "skip_model":
                        raise BudgetExceededError(
                            f"Model '{model_id}' skipped per user disposition."
                        )

        if not pricing:
            # No campaign context, timeout, or fail_closed-style fallback.
            if self.fail_closed:
                raise BudgetExceededError(
                    f"Budget enforcement: no pricing configured for model '{model_id}'. "
                    f"Add pricing to .llm_config.yaml under budget.pricing, "
                    f"or set a disposition via `msc hpc set-pricing-disposition`."
                )
            return 0.0
        input_per_1k = float(pricing.get("input_per_1k", 0.0))
        output_per_1k = float(pricing.get("output_per_1k", 0.0))
        return (prompt_tokens / 1000.0) * input_per_1k + (completion_tokens / 1000.0) * output_per_1k

    def check_budget(self) -> None:
        if not self.hard_stop:
            return
        if os.path.exists(self.lock_path):
            raise BudgetExceededError(
                f"Budget lock file present: {self.lock_path}. "
                f"USD limit {self.usd_limit} reached."
            )
        if self.total_usd >= self.usd_limit:
            raise BudgetExceededError(
                f"USD budget limit reached: {self.total_usd:.4f} / {self.usd_limit:.2f}."
            )

    def record_usage(
        self,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        call_id: Optional[str] = None,
    ) -> float:
        cost = self._compute_cost(model_id, prompt_tokens, completion_tokens)

        entry = {
            "call_id": call_id or str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "model_id": model_id,
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "cost_usd": round(cost, 6),
        }

        with self._record_lock:
            self.total_usd += cost
            self.by_model[model_id] = round(self.by_model.get(model_id, 0.0) + cost, 6)
            entry["total_usd"] = round(self.total_usd, 6)
            entry["usd_limit"] = self.usd_limit
            self._record_count += 1

        # Ledger append is always immediate (cheap append-only I/O)
        self._write_ledger(entry)

        # Batch the expensive state JSON rewrite every _FLUSH_INTERVAL calls.
        # Always write on the first call (ensures state file exists for readers).
        with self._record_lock:
            if self._record_count >= self._FLUSH_INTERVAL or self._record_count == 1:
                self._save_state()
                self._record_count = 0

        # Also track run-scoped cumulative tokens used by terminal step summaries.
        try:
            from .token_usage_tracker import record_token_usage

            record_token_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                source="budgeted_model",
                model_id=model_id,
            )
        except Exception as exc:
            logger.warning("Token tracker recording failed: %s", exc)

        if self.hard_stop and self.total_usd >= self.usd_limit:
            # Persist state immediately on budget exceeded, then create lock file
            self.flush()
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            with open(self.lock_path, "w", encoding="utf-8") as f:
                f.write(
                    f"Budget exceeded: {self.total_usd:.4f} / {self.usd_limit:.2f} USD\n"
                )
        return cost

    def flush(self) -> None:
        """Public flush — call at pipeline end to ensure state is persisted."""
        with self._record_lock:
            self._save_state()
            self._record_count = 0


class BudgetTrackingCallback(BaseCallbackHandler):
    """LangChain callback that records token usage to BudgetManager.

    When BudgetedLiteLLMModel must be unwrapped (e.g. for LangGraph's
    create_react_agent which requires a BaseChatModel), attach this callback
    via ``config={"callbacks": [cb]}`` to keep budget tracking active.
    """

    def __init__(self, budget_manager: "BudgetManager", model_id: str = "unknown"):
        self.budget_manager = budget_manager
        self.model_id = model_id

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        pass

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Extract token usage from LLMResult and record it."""
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = llm_output.get("token_usage", {})
            # Usage may be a dict or a pydantic Usage object — handle both
            if hasattr(usage, "prompt_tokens"):
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            elif isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            else:
                prompt_tokens = completion_tokens = 0
            if prompt_tokens or completion_tokens:
                # model_name may be under "model_name" or "model" key
                model_id = (
                    llm_output.get("model_name")
                    or llm_output.get("model")
                    or self.model_id
                )
                self.budget_manager.record_usage(
                    model_id=model_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
        except (FileNotFoundError, OSError):
            pass  # sandbox dir may not have budget files; silently skip

    def on_llm_error(self, error: Any, **kwargs: Any) -> None:
        pass

    def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        pass

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        pass

    def on_chain_error(self, error: Any, **kwargs: Any) -> None:
        pass

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        pass

    def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
        pass

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        pass

    def on_tool_error(self, error: Any, **kwargs: Any) -> None:
        pass

    def on_retriever_start(self, serialized: Any, query: str, **kwargs: Any) -> None:
        pass

    def on_retriever_end(self, documents: Any, **kwargs: Any) -> None:
        pass

    def on_retriever_error(self, error: Any, **kwargs: Any) -> None:
        pass

    def on_text(self, text: str, **kwargs: Any) -> None:
        pass

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        pass

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        pass


class BudgetedLiteLLMModel:
    """
    Wrapper that enforces a BudgetManager on all model calls.
    """

    def __init__(self, model, budget_manager: BudgetManager):
        self.model = model
        self.budget_manager = budget_manager

    def _get_model_id(self) -> str:
        if hasattr(self.model, "model_id"):
            return self.model.model_id
        if hasattr(self.model, "model"):
            return self.model.model
        return "unknown_model"

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            if value is None:
                return 0
            return int(value)
        except Exception:
            return 0

    @classmethod
    def _extract_usage_from_obj(cls, usage_obj: Any) -> Optional[Dict[str, int]]:
        if not usage_obj:
            return None

        def _get(*keys: str) -> int:
            for key in keys:
                # dict-style usage
                if isinstance(usage_obj, dict) and key in usage_obj:
                    value = cls._safe_int(usage_obj.get(key))
                    if value:
                        return value
                # object-style usage
                if hasattr(usage_obj, key):
                    value = cls._safe_int(getattr(usage_obj, key))
                    if value:
                        return value
            return 0

        # Support both conventions:
        # - prompt/completion (OpenAI-style usage)
        # - input/output (Anthropic-style usage)
        prompt_tokens = _get("prompt_tokens", "input_tokens")
        completion_tokens = _get("completion_tokens", "output_tokens")
        total_tokens = _get("total_tokens")
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        # Cost accounting requires an input/output split. If unavailable, fail closed upstream.
        if prompt_tokens == 0 and completion_tokens == 0:
            return None

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_token_usage(self, response) -> Optional[Dict[str, int]]:
        # 1) ChatMessage.token_usage attribute
        usage = self._extract_usage_from_obj(getattr(response, "token_usage", None))
        if usage:
            return usage

        # 2) direct response.usage
        usage = self._extract_usage_from_obj(getattr(response, "usage", None))
        if usage:
            return usage

        # 3) nested raw provider response (e.g., ChatMessage.raw.usage)
        raw = getattr(response, "raw", None)
        usage = self._extract_usage_from_obj(getattr(raw, "usage", None))
        if usage:
            return usage

        return None

    def generate(self, messages, *args, **kwargs):
        # Budget check and recording are now handled automatically by the
        # monkey-patched litellm.completion() in config.py.  This method
        # only performs a pre-call check; the post-call recording happens
        # inside litellm.completion via the global singleton.
        self.budget_manager.check_budget()
        return self.model.generate(messages, *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.generate(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.model, name)
