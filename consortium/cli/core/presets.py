"""Research run tiers — map price tiers to consortium flags + LLM config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    tier_label: str
    budget_range: str
    output_format: str
    model: str
    budget_usd: int
    reasoning_effort: str
    budget_tokens: Optional[int]
    enable_counsel: bool
    no_counsel: bool
    enable_math_agents: bool
    enable_tree_search: bool
    adversarial_verification: bool
    enable_planning: bool
    enforce_paper_artifacts: bool
    enforce_editorial_artifacts: bool
    autonomous_mode: bool
    time_estimate: str
    cost_estimate: str
    # Counsel settings
    counsel_model_specs: Optional[tuple] = None  # tuple of dicts for frozen
    counsel_debate_rounds: int = 0
    counsel_synthesis_model: Optional[str] = None
    # Per-agent model tiers
    per_agent_models_enabled: bool = False
    per_agent_tiers: Optional[tuple] = None      # tuple of (name, config) pairs
    agent_tier_assignments: Optional[tuple] = None  # tuple of (agent, tier) pairs
    # Experiment tool models
    experiment_tool_models: Optional[tuple] = None  # tuple of (key, model) pairs
    # Persona council specs (override default personas)
    persona_council_specs: Optional[tuple] = None  # tuple of persona spec dicts
    # Quality knobs (override defaults when set)
    followup_max_iterations: Optional[int] = None
    max_rebuttal_iterations: Optional[int] = None
    min_review_score: Optional[int] = None
    manager_max_steps: Optional[int] = None
    theory_repair_max_attempts: Optional[int] = None
    duality_max_attempts: Optional[int] = None
    persona_post_vote_retries: Optional[int] = None
    max_validation_retries: Optional[int] = None
    tree_max_breadth: Optional[int] = None
    tree_max_depth: Optional[int] = None
    tree_max_parallel: Optional[int] = None
    tree_pruning_threshold: Optional[float] = None
    # Ensemble review
    enable_ensemble_review: bool = False


# ── Tier definitions ───────────────────────────────────────────────────

TIERS: dict[str, Preset] = {
    "live-smoke": Preset(
        name="live-smoke",
        tier_label="LiveSmoke",
        budget_range="$1-5",
        description="Live OpenRouter node smoke test — cheapest single-model path",
        output_format="markdown",
        model="deepseek-chat",
        budget_usd=2,
        reasoning_effort="low",
        budget_tokens=None,
        enable_counsel=False,
        no_counsel=True,
        enable_math_agents=False,
        enable_tree_search=False,
        adversarial_verification=False,
        enable_planning=False,
        enforce_paper_artifacts=False,
        enforce_editorial_artifacts=False,
        autonomous_mode=True,
        time_estimate="~10-30 min",
        cost_estimate="$1-5",
        counsel_debate_rounds=1,
        persona_post_vote_retries=0,
        followup_max_iterations=1,
        max_rebuttal_iterations=0,
        min_review_score=1,
        manager_max_steps=25,
        theory_repair_max_attempts=0,
        duality_max_attempts=0,
        max_validation_retries=0,
        experiment_tool_models=(
            ("code_model", "deepseek-chat"),
            ("feedback_model", "deepseek-chat"),
            ("vlm_model", "deepseek-chat"),
            ("report_model", "deepseek-chat"),
        ),
        persona_council_specs=(
            {"name": "smoke_researcher", "model": "deepseek-chat", "model_kwargs": {}},
        ),
    ),
    "budget": Preset(
        name="budget",
        tier_label="Budget",
        budget_range="$20-50",
        description="Initial ideation — fast, cheap, single model",
        output_format="markdown",
        model="gpt-5-mini",
        budget_usd=35,
        reasoning_effort="low",
        budget_tokens=None,
        enable_counsel=False,
        no_counsel=True,
        enable_math_agents=False,
        enable_tree_search=False,
        adversarial_verification=False,
        enable_planning=False,
        enforce_paper_artifacts=False,
        enforce_editorial_artifacts=False,
        autonomous_mode=True,
        time_estimate="~30 min",
        cost_estimate="$20-50",
        experiment_tool_models=(
            ("code_model", "gpt-5-mini"),
            ("feedback_model", "gpt-5-mini"),
            ("vlm_model", "gpt-5-mini"),
            ("report_model", "gpt-5-mini"),
        ),
    ),
    "light": Preset(
        name="light",
        tier_label="Light",
        budget_range="$50-100",
        description="Exploration — planning enabled, single model",
        output_format="markdown",
        model="gpt-5-mini",
        budget_usd=75,
        reasoning_effort="high",
        budget_tokens=None,
        enable_counsel=False,
        no_counsel=True,
        enable_math_agents=False,
        enable_tree_search=False,
        adversarial_verification=False,
        enable_planning=True,
        enforce_paper_artifacts=False,
        enforce_editorial_artifacts=False,
        autonomous_mode=True,
        time_estimate="~2 hours",
        cost_estimate="$50-100",
        experiment_tool_models=(
            ("code_model", "gpt-5-mini"),
            ("feedback_model", "gpt-5-mini"),
            ("vlm_model", "gpt-5-mini"),
            ("report_model", "gpt-5-mini"),
        ),
    ),
    "medium": Preset(
        name="medium",
        tier_label="Medium",
        budget_range="$100-300",
        description="Basic boilerplate paper — LaTeX, math agents",
        output_format="latex",
        model="claude-sonnet-4-6",
        budget_usd=200,
        reasoning_effort="high",
        budget_tokens=128000,
        enable_counsel=False,
        no_counsel=True,
        enable_math_agents=True,
        enable_tree_search=False,
        adversarial_verification=False,
        enable_planning=True,
        enforce_paper_artifacts=True,
        enforce_editorial_artifacts=False,
        autonomous_mode=True,
        time_estimate="~6 hours",
        cost_estimate="$100-300",
        per_agent_models_enabled=True,
        per_agent_tiers=(
            ("sonnet", {"model": "claude-sonnet-4-6", "reasoning_effort": "high", "budget_tokens": 128000}),
            ("economy", {"model": "claude-sonnet-4-6", "reasoning_effort": "low"}),
        ),
        agent_tier_assignments=(
            ("literature_review_agent", "sonnet"),
            ("math_literature_agent", "sonnet"),
            ("experiment_literature_agent", "sonnet"),
            ("math_empirical_verifier_agent", "sonnet"),
            ("formalize_results_agent", "sonnet"),
            ("writeup_agent", "sonnet"),
            ("reviewer_agent", "sonnet"),
            ("experimentation_agent", "sonnet"),
            ("followup_lit_review", "sonnet"),
            ("proofreading_agent", "economy"),
            ("proof_transcription_agent", "economy"),
            ("experiment_transcription_agent", "economy"),
            ("resource_preparation_agent", "economy"),
        ),
        experiment_tool_models=(
            ("code_model", "claude-sonnet-4-6"),
            ("feedback_model", "claude-sonnet-4-6"),
            ("vlm_model", "claude-sonnet-4-6"),
            ("report_model", "claude-sonnet-4-6"),
        ),
    ),
    "pro": Preset(
        name="pro",
        tier_label="Pro",
        budget_range="$300-500",
        description="Workshop-level paper — counsel debate, adversarial",
        output_format="latex",
        model="claude-opus-4-6",
        budget_usd=400,
        reasoning_effort="high",
        budget_tokens=128000,
        enable_counsel=True,
        no_counsel=False,
        enable_math_agents=True,
        enable_tree_search=False,
        adversarial_verification=True,
        enable_planning=True,
        enforce_paper_artifacts=True,
        enforce_editorial_artifacts=True,
        autonomous_mode=True,
        time_estimate="~12 hours",
        cost_estimate="$300-500",
        counsel_model_specs=(
            {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
            {"model": "gpt-5.4", "reasoning_effort": "high", "verbosity": "high"},
        ),
        counsel_debate_rounds=2,
        counsel_synthesis_model="claude-sonnet-4-6",
        per_agent_models_enabled=True,
        per_agent_tiers=(
            ("opus", {"model": "claude-opus-4-6", "reasoning_effort": "high", "budget_tokens": 128000}),
            ("sonnet", {"model": "claude-sonnet-4-6", "reasoning_effort": "high", "budget_tokens": 128000}),
            ("economy", {"model": "claude-sonnet-4-6", "reasoning_effort": "low"}),
        ),
        agent_tier_assignments=(
            ("writeup_agent", "opus"),
            ("reviewer_agent", "opus"),
            ("formalize_results_agent", "opus"),
            ("literature_review_agent", "sonnet"),
            ("math_literature_agent", "sonnet"),
            ("experiment_literature_agent", "sonnet"),
            ("math_empirical_verifier_agent", "sonnet"),
            ("experimentation_agent", "sonnet"),
            ("followup_lit_review", "sonnet"),
            ("proofreading_agent", "economy"),
            ("proof_transcription_agent", "economy"),
            ("experiment_transcription_agent", "economy"),
            ("resource_preparation_agent", "economy"),
        ),
        experiment_tool_models=(
            ("code_model", "gpt-5"),
            ("feedback_model", "claude-sonnet-4-6"),
            ("vlm_model", "claude-sonnet-4-6"),
            ("report_model", "claude-sonnet-4-6"),
        ),
    ),
    "max": Preset(
        name="max",
        tier_label="Max",
        budget_range="$500+",
        description="Conference-level paper — full counsel, tree search",
        output_format="latex",
        model="claude-opus-4-6",
        budget_usd=750,
        reasoning_effort="high",
        budget_tokens=128000,
        enable_counsel=True,
        no_counsel=False,
        enable_math_agents=True,
        enable_tree_search=True,
        adversarial_verification=True,
        enable_planning=True,
        enforce_paper_artifacts=True,
        enforce_editorial_artifacts=True,
        autonomous_mode=True,
        time_estimate="~24+ hours",
        cost_estimate="$500-1000",
        counsel_model_specs=(
            {"model": "claude-opus-4-6", "reasoning_effort": "high"},
            {"model": "claude-sonnet-4-6", "reasoning_effort": "high"},
            {"model": "gpt-5.4", "reasoning_effort": "high", "verbosity": "high"},
            {"model": "gemini-3-pro-preview", "thinking_budget": 131072},
        ),
        counsel_debate_rounds=3,
        counsel_synthesis_model="claude-opus-4-6",
        per_agent_models_enabled=True,
        per_agent_tiers=(
            ("opus", {"model": "claude-opus-4-6", "reasoning_effort": "high", "budget_tokens": 128000}),
            ("sonnet", {"model": "claude-sonnet-4-6", "reasoning_effort": "high", "budget_tokens": 128000}),
            ("economy", {"model": "claude-sonnet-4-6", "reasoning_effort": "low"}),
        ),
        agent_tier_assignments=(
            ("writeup_agent", "opus"),
            ("reviewer_agent", "opus"),
            ("formalize_results_agent", "opus"),
            ("brainstorm_agent", "opus"),
            ("formalize_goals_agent", "opus"),
            ("experiment_design_agent", "opus"),
            ("experiment_verification_agent", "opus"),
            ("math_proposer_agent", "opus"),
            ("math_prover_agent", "opus"),
            ("math_rigorous_verifier_agent", "opus"),
            ("literature_review_agent", "sonnet"),
            ("math_literature_agent", "sonnet"),
            ("experiment_literature_agent", "sonnet"),
            ("math_empirical_verifier_agent", "sonnet"),
            ("experimentation_agent", "sonnet"),
            ("followup_lit_review", "sonnet"),
            ("proofreading_agent", "economy"),
            ("proof_transcription_agent", "economy"),
            ("experiment_transcription_agent", "economy"),
            ("resource_preparation_agent", "economy"),
        ),
        experiment_tool_models=(
            ("code_model", "gpt-5"),
            ("feedback_model", "claude-opus-4-6"),
            ("vlm_model", "claude-sonnet-4-6"),
            ("report_model", "claude-opus-4-6"),
        ),
    ),
    "ultra": Preset(
        name="ultra",
        tier_label="Ultra",
        budget_range="$1000+",
        description="Best possible paper — all-opus, deep debate, ensemble review",
        output_format="latex",
        model="claude-opus-4-6",
        budget_usd=2000,
        reasoning_effort="high",
        budget_tokens=131072,
        enable_counsel=True,
        no_counsel=False,
        enable_math_agents=True,
        enable_tree_search=True,
        adversarial_verification=True,
        enable_planning=True,
        enforce_paper_artifacts=True,
        enforce_editorial_artifacts=True,
        autonomous_mode=True,
        time_estimate="~48+ hours",
        cost_estimate="$1000-3000",
        counsel_model_specs=(
            {"model": "claude-opus-4-6", "reasoning_effort": "high"},
            {"model": "gpt-5.4", "reasoning_effort": "high", "verbosity": "high"},
            {"model": "gemini-3.1-pro-preview", "thinking_budget": 131072},
            {"model": "o3-pro"},
        ),
        counsel_debate_rounds=5,
        counsel_synthesis_model="claude-opus-4-6",
        per_agent_models_enabled=True,
        per_agent_tiers=(
            ("opus", {"model": "claude-opus-4-6", "reasoning_effort": "high", "budget_tokens": 131072}),
            ("sonnet_high", {"model": "claude-sonnet-4-6", "reasoning_effort": "high", "budget_tokens": 128000}),
        ),
        agent_tier_assignments=(
            ("writeup_agent", "opus"),
            ("reviewer_agent", "opus"),
            ("formalize_results_agent", "opus"),
            ("brainstorm_agent", "opus"),
            ("formalize_goals_agent", "opus"),
            ("experiment_design_agent", "opus"),
            ("experiment_verification_agent", "opus"),
            ("math_proposer_agent", "opus"),
            ("math_prover_agent", "opus"),
            ("math_rigorous_verifier_agent", "opus"),
            ("literature_review_agent", "opus"),
            ("math_literature_agent", "opus"),
            ("experiment_literature_agent", "opus"),
            ("math_empirical_verifier_agent", "opus"),
            ("experimentation_agent", "opus"),
            ("followup_lit_review", "opus"),
            ("proofreading_agent", "sonnet_high"),
            ("proof_transcription_agent", "sonnet_high"),
            ("experiment_transcription_agent", "sonnet_high"),
            ("resource_preparation_agent", "sonnet_high"),
        ),
        experiment_tool_models=(
            ("code_model", "gpt-5"),
            ("feedback_model", "claude-opus-4-6"),
            ("vlm_model", "claude-opus-4-6"),
            ("report_model", "claude-opus-4-6"),
        ),
        persona_council_specs=(
            {"name": "practical_compass", "model": "claude-opus-4-6", "model_kwargs": {"reasoning_effort": "high"}},
            {"name": "rigor_novelty", "model": "gpt-5.4", "model_kwargs": {"reasoning_effort": "high"}},
            {"name": "narrative_architect", "model": "gemini-3.1-pro-preview", "model_kwargs": {"thinking_budget": 65536}},
            {"name": "empirical_grounding", "model": "claude-opus-4-6", "model_kwargs": {"reasoning_effort": "high"}},
        ),
        # Quality knobs — tuned for best possible output
        followup_max_iterations=5,
        max_rebuttal_iterations=4,
        min_review_score=9,
        manager_max_steps=100,
        theory_repair_max_attempts=4,
        duality_max_attempts=3,
        persona_post_vote_retries=3,
        max_validation_retries=5,
        tree_max_breadth=5,
        tree_max_depth=6,
        tree_max_parallel=8,
        tree_pruning_threshold=0.1,
        enable_ensemble_review=True,
    ),
}

TIER_ORDER = ("live-smoke", "budget", "light", "medium", "pro", "max", "ultra")

# ── Backward compatibility ─────────────────────────────────────────────

_PRESET_TO_TIER = {
    "quick": "budget",
    "standard": "light",
    "thorough": "medium",
    "maximum": "pro",
}

PRESETS: dict[str, Preset] = {
    **TIERS,
    **{old: TIERS[new] for old, new in _PRESET_TO_TIER.items()},
}


def list_tiers() -> list[Preset]:
    """Return all tiers in order of increasing quality."""
    return [TIERS[k] for k in TIER_ORDER]


def list_presets() -> list[Preset]:
    """Deprecated — use list_tiers() instead."""
    return list_tiers()


def resolve_tier_name(name: str) -> str:
    """Resolve a preset or tier name to a canonical tier name."""
    if name in TIERS:
        return name
    if name in _PRESET_TO_TIER:
        return _PRESET_TO_TIER[name]
    raise ValueError(f"Unknown tier/preset: {name}. Choose from: {', '.join(TIER_ORDER)}")
