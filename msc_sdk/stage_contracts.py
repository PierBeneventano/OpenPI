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

from .kernel import (
    ArtifactSpec,
    BudgetPolicy as KernelBudgetPolicy,
    FailurePolicy,
    GraphSpec,
    InputSpec,
    RouteSpec,
    StageSpec,
)


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
            "formalize_goals_entry",
            "formalize_goals_agent",
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


def compile_kernel_graph(
    *,
    graph_id: str,
    template: str,
    budget: float = 0.0,
) -> GraphSpec:
    """Compile historical source contracts into the kernel graph language."""

    ids = template_node_ids(template)
    all_contracts = contracts_by_id()
    contracts = [all_contracts[node_id] for node_id in ids if node_id in all_contracts]
    included_stage_ids = set(ids)
    stages = tuple(
        _stage_spec_from_contract(
            contract,
            included_stage_ids=included_stage_ids,
            fallback_next_stage_id=contracts[index + 1].id if index + 1 < len(contracts) else None,
            total_budget_usd=budget,
            total_weight=_total_budget_weight(contracts),
        )
        for index, contract in enumerate(contracts)
    )
    entry_stage_id = stages[0].id if stages else "empty"
    if not stages:
        stages = (
            StageSpec(
                id="empty",
                title="Empty Graph",
                kind="control",
                purpose="Empty campaign template.",
                metadata={"template": template},
            ),
        )
    graph = GraphSpec(id=graph_id, stages=stages, entry_stage_id=entry_stage_id)
    graph.validate()
    return graph


def project_kernel_graph(
    *,
    graph: GraphSpec,
    campaign_id: str,
    title: str,
    template: str,
    tier: str,
) -> dict[str, Any]:
    """Project a kernel graph spec into the product graph JSON shape."""

    nodes = [
        _stage_node(stage, campaign_id=campaign_id, template=template, tier=tier, order=order)
        for order, stage in enumerate(graph.stages, start=1)
    ]
    node_set = {stage.id for stage in graph.stages}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for stage in graph.stages:
        for route in stage.routes:
            if route.target not in node_set:
                continue
            edge = _route_edge(stage.id, route)
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
            "source": "kernel_graph_projection",
            "kernelGraphId": graph.id,
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


def _total_budget_weight(contracts: Iterable[StageContract]) -> float:
    total = sum(
        contract.budget_policy.relative_weight
        for contract in contracts
        if contract.budget_policy.spend
    )
    return total or 1.0


def _stage_spec_from_contract(
    contract: StageContract,
    *,
    included_stage_ids: set[str],
    fallback_next_stage_id: str | None,
    total_budget_usd: float,
    total_weight: float,
) -> StageSpec:
    budget_share = 0.0
    if contract.budget_policy.spend:
        budget_share = float(total_budget_usd) * (contract.budget_policy.relative_weight / total_weight)
    metadata = {
        **dict(contract.metadata),
        "contract_source": "historical_stage_contract",
        "legacy_runtime_mapping": dict(contract.legacy_runtime_mapping),
        "human_pause_policy": list(contract.human_pause_policy),
        "failure_policy": contract.failure_policy,
        "budget_policy": contract.budget_policy.to_dict(tier="kernel", budget_share_usd=budget_share),
        "required_artifact_contracts": [artifact.to_dict() for artifact in contract.required_artifacts],
        "optional_artifact_contracts": [artifact.to_dict() for artifact in contract.optional_artifacts],
    }
    routes = tuple(
        _route_spec(route)
        for route in contract.allowed_routes
        if route.target in included_stage_ids
    )
    if not routes and fallback_next_stage_id is not None:
        routes = (
            RouteSpec(
                target=fallback_next_stage_id,
                kind="next",
                condition="always",
                metadata={"legacy_kind": "template_order", "synthesized": True},
            ),
        )
    return StageSpec(
        id=contract.id,
        title=contract.title,
        kind=_stage_kind(contract.kind),
        purpose=contract.purpose,
        inputs=tuple(_input_spec(item, included_stage_ids) for item in contract.inputs),
        outputs=tuple(
            _artifact_spec(artifact, required=True)
            for artifact in contract.required_artifacts
        ) + tuple(
            _artifact_spec(artifact, required=False)
            for artifact in contract.optional_artifacts
        ),
        validator_ids=tuple(contract.validators),
        tool_ids=tuple(contract.tool_families),
        adapter_id=f"historical.{contract.id}",
        budget=KernelBudgetPolicy(max_usd=budget_share, spend_allowed=contract.budget_policy.spend),
        failure=FailurePolicy(mode="stop_for_human"),
        routes=routes,
        pause_before=any(policy.startswith("before_") for policy in contract.human_pause_policy),
        pause_after=any(policy.startswith("after_") for policy in contract.human_pause_policy),
        metadata=metadata,
    )


def _input_spec(value: str, included_stage_ids: set[str]) -> InputSpec:
    if value in included_stage_ids:
        return InputSpec(path="__stage_outputs__", source_stage_id=value, required=False)
    return InputSpec(path=value, required=False)


def _artifact_spec(artifact: ArtifactContract, *, required: bool) -> ArtifactSpec:
    return ArtifactSpec(
        path=artifact.path,
        kind=artifact.kind,
        required=required,
        role="deliverable" if required else "diagnostic",
        description=artifact.description,
    )


def _route_spec(route: RouteContract) -> RouteSpec:
    return RouteSpec(
        target=route.target,
        condition=route.condition,
        kind=_route_kind(route.kind),
        description=route.description,
        metadata={"legacy_kind": route.kind},
    )


def _stage_kind(kind: str) -> str:
    if kind in {"gate", "router"}:
        return "router"
    if kind in {"approval"}:
        return "approval"
    if kind in {"control", "track"}:
        return "control"
    if kind in {"validator"}:
        return "validator"
    if kind in {"tool"}:
        return "tool"
    return "agent"


def _route_kind(kind: str) -> str:
    return {
        "stage_order": "next",
        "route": "next",
        "revision_route": "next",
        "subgraph": "next",
        "fanout": "branch",
        "fanin": "join",
        "loop": "loop",
        "failure": "failure",
    }.get(kind, "next")


def _stage_node(
    stage: StageSpec,
    *,
    campaign_id: str,
    template: str,
    tier: str,
    order: int,
) -> dict[str, Any]:
    metadata = dict(stage.metadata)
    metadata.update(
        {
            "template": template,
            "order": order,
            "kind": metadata.get("legacy_kind") or stage.kind,
            "purpose": stage.purpose,
            "validators": list(stage.validator_ids),
            "toolFamilies": list(stage.tool_ids),
            "humanPausePolicy": list(metadata.get("human_pause_policy") or []),
            "failurePolicy": metadata.get("failure_policy") or stage.failure.mode,
            "allowedRoutes": [_route_edge(stage.id, route) for route in stage.routes],
            "legacyRuntimeMapping": dict(metadata.get("legacy_runtime_mapping") or {}),
            "requiredArtifactContracts": [
                _artifact_contract_dict(artifact) for artifact in stage.outputs if artifact.required
            ],
            "optionalArtifactContracts": [
                _artifact_contract_dict(artifact) for artifact in stage.outputs if not artifact.required
            ],
        }
    )
    return {
        "id": stage.id,
        "type": stage.metadata.get("legacy_kind") or stage.kind,
        "title": stage.title,
        "status": "planned",
        "inputs": [
            input_spec.source_stage_id or input_spec.path
            for input_spec in stage.input_specs
        ],
        "outputs": [artifact.path for artifact in stage.outputs if artifact.required],
        "optionalOutputs": [artifact.path for artifact in stage.outputs if not artifact.required],
        "required_artifacts": [
            _artifact_contract_dict(artifact) for artifact in stage.outputs if artifact.required
        ],
        "optional_artifacts": [
            _artifact_contract_dict(artifact) for artifact in stage.outputs if not artifact.required
        ],
        "budgetPolicy": {
            "tier": tier,
            "maxUsd": stage.budget.max_usd,
            "relativeWeight": (stage.metadata.get("budget_policy") or {}).get("relativeWeight", 0),
            "spend": stage.budget.spend_allowed,
            "notes": (stage.metadata.get("budget_policy") or {}).get("notes", ""),
        },
        "workspace": str(Path("results") / campaign_id / stage.id),
        "metadata": metadata,
    }


def _route_edge(source: str, route: RouteSpec) -> dict[str, Any]:
    metadata = dict(route.metadata)
    if route.condition:
        metadata["condition"] = route.condition
    if route.description:
        metadata["description"] = route.description
    return {
        "source": source,
        "target": route.target,
        "kind": metadata.get("legacy_kind") or route.kind,
        "metadata": metadata,
    }


def _artifact_contract_dict(artifact: ArtifactSpec) -> dict[str, Any]:
    return {
        "path": artifact.path,
        "kind": artifact.kind,
        "required": artifact.required,
        "description": artifact.description,
        "legacy_paths": [],
    }
