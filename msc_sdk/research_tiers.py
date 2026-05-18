"""Target research workflow model and tier policy.

This module is the SDK-owned home for product-visible model and tier defaults.
Runtime adapters may still translate these policies into provider-specific
config, but UI, OpenClaude, and tests should read the product posture here
instead of hard-coding model names in separate surfaces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .feedback_graph import (
    FEEDBACK_GRAPH_REFERENCE,
    FEEDBACK_ITERATE_STAGE_IDS,
    FEEDBACK_MAIN_STAGE_IDS,
    TARGET_RESEARCH_TEMPLATE_ID,
    target_research_graph_template,
)

TARGET_RESEARCH_TEMPLATE = TARGET_RESEARCH_TEMPLATE_ID

TARGET_WORKFLOW_STAGE_IDS: tuple[str, ...] = FEEDBACK_MAIN_STAGE_IDS

REVISION_ROUTE_STAGE_IDS: tuple[str, ...] = FEEDBACK_ITERATE_STAGE_IDS


TARGET_MODEL_DEFAULTS: dict[str, Any] = {
    "persona": {
        "practical_compass": "claude-opus-4-6",
        "rigor_novelty": "gpt-5.4",
        "narrative_architect": "gemini-3.1-pro-preview",
        "empirical_grounding": "claude-opus-4-6",
        "synthesis": "claude-opus-4-6",
    },
    "duality_check": "claude-opus-4-6",
    "model_council": {
        "members": (
            "claude-opus-4-6",
            "gpt-5.4",
            "gemini-3.1-pro-preview",
            "claude-sonnet-4-6",
        ),
        "synthesis": "claude-opus-4-6",
    },
}


@dataclass(frozen=True)
class TierPolicy:
    id: str
    label: str
    description: str
    persona_council: bool
    empirical_grounding_persona: bool = False
    model_council_default: bool = False
    duality_required: bool = True
    spend_allowed: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TARGET_TIER_POLICIES: dict[str, TierPolicy] = {
    "scaffold": TierPolicy(
        id="scaffold",
        label="Scaffold",
        description="Zero-spend graph/artifact planning and UI validation.",
        persona_council=False,
        duality_required=False,
        spend_allowed=False,
    ),
    "lean": TierPolicy(
        id="lean",
        label="Lean",
        description="Single-model exploratory execution for low-cost campaigns.",
        persona_council=False,
    ),
    "standard": TierPolicy(
        id="standard",
        label="Standard",
        description="Persona council plus single-model specialist stages.",
        persona_council=True,
    ),
    "serious": TierPolicy(
        id="serious",
        label="Serious",
        description="Persona council, required duality check, and selected model councils.",
        persona_council=True,
        model_council_default=True,
    ),
    "ultra": TierPolicy(
        id="ultra",
        label="Ultra",
        description="Adds empirical-grounding persona and broad model-council use.",
        persona_council=True,
        empirical_grounding_persona=True,
        model_council_default=True,
    ),
}


def normalize_tier(tier: str | None) -> str:
    value = str(tier or "standard").strip().lower()
    aliases = {
        "budget": "lean",
        "light": "lean",
        "medium": "standard",
        "pro": "serious",
        "max": "ultra",
        "live-smoke": "scaffold",
    }
    value = aliases.get(value, value)
    return value if value in TARGET_TIER_POLICIES else "standard"


def tier_policy(tier: str | None) -> TierPolicy:
    return TARGET_TIER_POLICIES[normalize_tier(tier)]


def target_model_policy() -> dict[str, Any]:
    return {
        "schema": "msc.research.model_policy.v1",
        "defaults": TARGET_MODEL_DEFAULTS,
        "tiers": [policy.to_dict() for policy in TARGET_TIER_POLICIES.values()],
        "default_tier": "standard",
    }


def template_metadata(template: str, tier: str | None) -> dict[str, Any]:
    policy = tier_policy(tier)
    graph_template = target_research_graph_template()
    return {
        "targetWorkflow": template == TARGET_RESEARCH_TEMPLATE,
        "canonicalChain": "CampaignGoal -> ResearchGraphTemplate -> GraphSpec -> StageSpec -> RuntimeContext -> EventRecord -> ReadModel",
        "researcherFeedbackSnapshot": FEEDBACK_GRAPH_REFERENCE,
        "graphTemplateSpec": graph_template.to_dict(),
        "subgraphs": [subgraph.to_dict() for subgraph in graph_template.subgraphs],
        "routers": [router.to_dict() for router in graph_template.routers],
        "featureFlags": [flag.to_dict() for flag in graph_template.feature_flags],
        "stateFields": [field.to_dict() for field in graph_template.state_fields],
        "revisionRoutes": list(REVISION_ROUTE_STAGE_IDS),
        "dualityRequired": bool(policy.duality_required),
        "tierPolicy": policy.to_dict(),
        "modelPolicy": target_model_policy(),
    }
