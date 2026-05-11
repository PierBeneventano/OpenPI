"""Typed product contracts for the historical MSc research engine.

The contracts in this module are intentionally descriptive. They do not drive
LangGraph execution yet; they expose the semantics of the existing engine so
campaign state, graph previews, OpenClaude steering, and tests can reason about
the same nodes the runtime already knows how to run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


CONTRACT_GRAPH_VERSION = 1


@dataclass(frozen=True)
class ArtifactContract:
    """Artifact promised or optionally produced by a stage/control node."""

    path: str
    kind: str = "markdown"
    required: bool = True
    description: str = ""
    legacy_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteContract:
    """Allowed graph route from one node to another."""

    target: str
    kind: str = "stage_order"
    condition: str = "always"
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_edge(self, source: str) -> dict[str, Any]:
        metadata = dict(self.metadata)
        if self.condition:
            metadata["condition"] = self.condition
        if self.description:
            metadata["description"] = self.description
        return {
            "source": source,
            "target": self.target,
            "kind": self.kind,
            "metadata": metadata,
        }


@dataclass(frozen=True)
class BudgetPolicy:
    """Default budget posture for a node."""

    relative_weight: float = 1.0
    spend: bool = True
    notes: str = ""

    def to_dict(self, *, tier: str, budget_share_usd: float) -> dict[str, Any]:
        return {
            "tier": tier,
            "maxUsd": round(float(budget_share_usd), 4) if self.spend else 0,
            "relativeWeight": self.relative_weight,
            "spend": self.spend,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class StageContract:
    """Stable product-facing contract for one runtime node."""

    id: str
    title: str
    kind: str
    purpose: str
    inputs: tuple[str, ...] = ()
    required_artifacts: tuple[ArtifactContract, ...] = ()
    optional_artifacts: tuple[ArtifactContract, ...] = ()
    validators: tuple[str, ...] = ()
    tool_families: tuple[str, ...] = ()
    budget_policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    human_pause_policy: tuple[str, ...] = ()
    failure_policy: str = "stop_and_await_human_feedback"
    allowed_routes: tuple[RouteContract, ...] = ()
    legacy_runtime_mapping: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_node(
        self,
        *,
        campaign_id: str,
        template: str,
        order: int,
        tier: str,
        budget_share_usd: float,
    ) -> dict[str, Any]:
        required_paths = [artifact.path for artifact in self.required_artifacts]
        optional_paths = [artifact.path for artifact in self.optional_artifacts]
        return {
            "id": self.id,
            "type": self.kind,
            "title": self.title,
            "status": "planned",
            "inputs": list(self.inputs),
            "outputs": required_paths,
            "optionalOutputs": optional_paths,
            "budgetPolicy": self.budget_policy.to_dict(
                tier=tier,
                budget_share_usd=budget_share_usd,
            ),
            "workspace": str(Path("results") / campaign_id / self.id),
            "metadata": {
                "template": template,
                "order": order,
                "kind": self.kind,
                "purpose": self.purpose,
                "validators": list(self.validators),
                "toolFamilies": list(self.tool_families),
                "humanPausePolicy": list(self.human_pause_policy),
                "failurePolicy": self.failure_policy,
                "allowedRoutes": [route.to_edge(self.id) for route in self.allowed_routes],
                "legacyRuntimeMapping": dict(self.legacy_runtime_mapping),
                "requiredArtifactContracts": [artifact.to_dict() for artifact in self.required_artifacts],
                "optionalArtifactContracts": [artifact.to_dict() for artifact in self.optional_artifacts],
                **dict(self.metadata),
            },
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_artifacts"] = [artifact.to_dict() for artifact in self.required_artifacts]
        data["optional_artifacts"] = [artifact.to_dict() for artifact in self.optional_artifacts]
        data["allowed_routes"] = [route.to_edge(self.id) for route in self.allowed_routes]
        return data


def historical_stage_contracts() -> list[StageContract]:
    """Load runtime-owned contracts for the current historical engine."""

    from consortium.stage_contracts.historical import CONTRACTS

    return list(CONTRACTS)


def contracts_by_id() -> dict[str, StageContract]:
    return {contract.id: contract for contract in historical_stage_contracts()}


def get_stage_contract(node_id: str) -> StageContract:
    try:
        return contracts_by_id()[node_id]
    except KeyError as exc:
        raise KeyError(f"No stage contract registered for node: {node_id}") from exc


def template_names() -> set[str]:
    return {"consortium_scaffold", "consortium_budget", "literature_only", "experiment_design", "blank"}


def template_node_ids(template: str) -> list[str]:
    if template == "blank":
        return []
    if template == "literature_only":
        return [
            "persona_council",
            "literature_review_agent",
            "lit_review_gate",
            "brainstorm_agent",
            "brainstorm_artifact_gate",
            "research_plan_writeup_agent",
        ]
    if template == "experiment_design":
        return [
            "persona_council",
            "brainstorm_agent",
            "brainstorm_artifact_gate",
            "formalize_goals_entry",
            "formalize_goals_agent",
            "research_plan_writeup_agent",
            "experiment_literature_agent",
            "experiment_design_agent",
        ]
    # Scaffold and budget project the full historical engine, including control
    # nodes and the theory/math track, before any fresh run begins. Revision
    # mode nodes remain registered but are only projected into revision-specific
    # graphs later.
    return [
        contract.id
        for contract in historical_stage_contracts()
        if contract.metadata.get("graph_scope", "runtime") == "runtime"
    ]


def build_contract_graph(
    *,
    campaign_id: str,
    title: str,
    template: str,
    tier: str,
    budget: float,
) -> dict[str, Any]:
    """Build graph IR from contracts rather than a hand-maintained stage list."""

    ids = template_node_ids(template)
    all_contracts = contracts_by_id()
    contracts = [all_contracts[node_id] for node_id in ids if node_id in all_contracts]
    total_weight = sum(
        contract.budget_policy.relative_weight
        for contract in contracts
        if contract.budget_policy.spend
    ) or 1.0

    nodes: list[dict[str, Any]] = []
    node_set = {contract.id for contract in contracts}
    for order, contract in enumerate(contracts, start=1):
        share = 0.0
        if contract.budget_policy.spend:
            share = float(budget) * (contract.budget_policy.relative_weight / total_weight)
        nodes.append(
            contract.to_node(
                campaign_id=campaign_id,
                template=template,
                order=order,
                tier=tier,
                budget_share_usd=share,
            )
        )

    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for contract in contracts:
        for route in contract.allowed_routes:
            if route.target not in node_set:
                continue
            edge = route.to_edge(contract.id)
            key = (edge["source"], edge["target"], edge["kind"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(edge)

    return {
        "campaign": campaign_id,
        "title": title,
        "version": CONTRACT_GRAPH_VERSION,
        "state": "planned",
        "mode": "control",
        "modes": ["pipeline", "control", "runtime"],
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "source": "stage_contract_registry",
            "template": template,
            "includesControlNodes": True,
            "graphEdits": "proposal_only",
        },
    }


def validate_contract_coverage(runtime_node_ids: Iterable[str]) -> dict[str, Any]:
    """Compare runtime node ids with the registered contract ids."""

    runtime = {node_id for node_id in runtime_node_ids if node_id}
    registered = set(contracts_by_id())
    return {
        "ok": runtime <= registered,
        "missing": sorted(runtime - registered),
        "extra": sorted(registered - runtime),
        "runtime_count": len(runtime),
        "registered_count": len(registered),
    }
