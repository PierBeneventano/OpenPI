"""Typed product contracts for the target research graph.

The contracts in this module are intentionally descriptive. They expose the
feedback-derived SDK graph shape, artifact contracts, routing posture, pause
rules, and adapter ids used by native campaign execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .kernel import (
    ArtifactSpec,
    BudgetPolicy as KernelBudgetPolicy,
    CompletionPolicy as KernelCompletionPolicy,
    CouncilPolicy,
    FailurePolicy,
    GatePolicy as KernelGatePolicy,
    GraphSpec,
    InputSpec,
    ModelPolicy,
    RouteSpec,
    RetryPolicy,
    StageSpec,
    TimeoutPolicy,
)
from .feedback_graph import (
    state_field_contracts_for_stage,
    subgraph_for_stage,
    target_research_graph_template,
)
from .research_tiers import TARGET_RESEARCH_TEMPLATE, TARGET_WORKFLOW_STAGE_IDS, template_metadata


CONTRACT_GRAPH_VERSION = 1


@dataclass(frozen=True)
class ArtifactContract:
    """Artifact promised or optionally produced by a stage/control node."""

    path: str
    kind: str = "markdown"
    required: bool = True
    description: str = ""

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
    council_policy: str = "none"
    budget_policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    human_pause_policy: tuple[str, ...] = ()
    failure_policy: str = "stop_and_await_human_feedback"
    allowed_routes: tuple[RouteContract, ...] = ()
    diagnostic_runtime_mapping: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_artifacts"] = [artifact.to_dict() for artifact in self.required_artifacts]
        data["optional_artifacts"] = [artifact.to_dict() for artifact in self.optional_artifacts]
        data["allowed_routes"] = [route.to_edge(self.id) for route in self.allowed_routes]
        return data


def historical_stage_contracts() -> list[StageContract]:
    """Load the feedback-derived source contracts."""

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
    return {TARGET_RESEARCH_TEMPLATE, "consortium_scaffold", "consortium_budget", "literature_only", "experiment_design", "blank"}


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
    if template == TARGET_RESEARCH_TEMPLATE:
        return [node_id for node_id in TARGET_WORKFLOW_STAGE_IDS if node_id in contracts_by_id()]
    # Scaffold and budget now project the target workflow shape, while keeping
    # their old names as compatibility aliases for existing commands/tests.
    return [
        node_id for node_id in TARGET_WORKFLOW_STAGE_IDS if node_id in contracts_by_id()
    ]


def compile_kernel_graph(
    *,
    graph_id: str,
    template: str,
    budget: float = 0.0,
) -> GraphSpec:
    """Compile target research source contracts into the kernel graph language."""

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

    graph_template = target_research_graph_template()
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
            "sdkGraphAuthority": "feedback_graph",
            "topLevelNodeCount": len(graph_template.main_stage_ids),
            "graphEdits": "proposal_only",
            **template_metadata(template, tier),
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
        "contract_source": "feedback_stage_contract",
        "runtime_mapping_diagnostics": dict(contract.diagnostic_runtime_mapping),
        "human_pause_policy": list(contract.human_pause_policy),
        "failure_policy": contract.failure_policy,
        "council_policy": _council_policy_for_contract(contract),
        "budget_policy": contract.budget_policy.to_dict(tier="kernel", budget_share_usd=budget_share),
        "completion_policy": _completion_policy_for_contract(contract).to_dict(),
        "gate_policy": _gate_policy_for_contract(contract).to_dict(),
        "timeout_policy": TimeoutPolicy().to_dict(),
        "required_artifact_contracts": [
            _artifact_contract_dict(_artifact_spec(artifact, required=True))
            for artifact in contract.required_artifacts
        ],
        "optional_artifact_contracts": [
            _artifact_contract_dict(_artifact_spec(artifact, required=False))
            for artifact in contract.optional_artifacts
        ],
    }
    field_contracts = state_field_contracts_for_stage(contract.id)
    if field_contracts:
        metadata["state_field_contracts"] = [field.to_dict() for field in field_contracts]
        metadata["state_reads"] = [
            field.field for field in field_contracts if field.direction in {"read", "read_write"}
        ]
        metadata["state_writes"] = [
            field.field for field in field_contracts if field.direction in {"write", "read_write"}
        ]
    router = target_research_graph_template().router_map().get(contract.id)
    if router is not None:
        metadata["router_spec"] = router.to_dict()
    subgraph = subgraph_for_stage(contract.id)
    if subgraph is not None:
        metadata["subgraph_spec"] = subgraph.to_dict()
        metadata["subgraph_id"] = subgraph.id
        if contract.id == subgraph.id:
            metadata["subgraph_collapsed_by_default"] = True
    routes = tuple(
        _route_spec(route, source_stage_id=contract.id)
        for route in contract.allowed_routes
        if route.target in included_stage_ids
    )
    if not routes and fallback_next_stage_id is not None:
        routes = (
            RouteSpec(
                target=fallback_next_stage_id,
                kind="next",
                condition="always",
                metadata={"source_kind": "template_order", "synthesized": True},
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
        council_policy=CouncilPolicy(kind=_council_policy_for_contract(contract)),
        model_policy=ModelPolicy(),
        adapter_id=f"sdk_native.{contract.id}",
        budget=KernelBudgetPolicy(max_usd=budget_share, spend_allowed=contract.budget_policy.spend),
        failure=FailurePolicy(mode="stop_for_human"),
        completion_policy=_completion_policy_for_contract(contract),
        gate_policy=_gate_policy_for_contract(contract),
        retry_policy=_retry_policy_for_contract(contract),
        timeout_policy=TimeoutPolicy(),
        routes=routes,
        pause_before=any(policy.startswith("before_") for policy in contract.human_pause_policy),
        pause_after=any(policy.startswith("after_") for policy in contract.human_pause_policy),
        metadata={
            **metadata,
            **({"requires_duality_pass": True, "duality_required": True} if _requires_duality_pass(contract.id) else {}),
        },
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
        role="deliverable" if required else "evidence",
        schema_id=_schema_id_for_artifact(artifact.path, artifact.kind),
        description=artifact.description,
    )


def _schema_id_for_artifact(path: str, kind: str) -> str | None:
    if kind != "json":
        return None
    name = Path(path).name
    return {
        "duality_check.json": "msc.duality_check.v1",
        "duality_gate_decision.json": "msc.gate_decision.v1",
        "claims_and_limitations.json": "msc.claims_and_limitations.v1",
        "paper_contract.json": "msc.paper_contract.v1",
        "writeup_gate_decision.json": "msc.writeup_gate_decision.v1",
    }.get(name)


def _completion_policy_for_contract(contract: StageContract) -> KernelCompletionPolicy:
    required = tuple(artifact.path for artifact in contract.required_artifacts)
    required_by_format: dict[str, tuple[str, ...]] = {}
    optional_paths = {artifact.path for artifact in contract.optional_artifacts}
    if contract.id == "writeup_agent":
        required_by_format = {
            "latex": tuple(path for path in ("artifacts/final_paper.tex",) if path in optional_paths),
            "pdf": tuple(path for path in ("artifacts/final_paper.pdf",) if path in optional_paths),
        }
    return KernelCompletionPolicy(
        required_artifacts=required,
        required_artifacts_by_output_format=required_by_format,
        optional_artifacts_by_output_format={
            "markdown": tuple(path for path in optional_paths if path.endswith((".pdf", ".tex"))),
        },
        validators=tuple(contract.validators),
    )


def _gate_policy_for_contract(contract: StageContract) -> KernelGatePolicy:
    if contract.id == "duality_gate":
        return KernelGatePolicy(
            required=True,
            verdict_schema_id="msc.gate_decision.v1",
            blocks_stages=tuple(stage_id for stage_id in TARGET_WORKFLOW_STAGE_IDS if _requires_duality_pass(stage_id)),
            safe_next_actions=("revise-goals", "rerun-literature", "rerun-experiment-track", "reroute", "stop-campaign"),
            metadata={"gate": "duality"},
        )
    if contract.kind in {"gate", "router", "validator"}:
        return KernelGatePolicy(required=True, safe_next_actions=("rewrite-stage", "rerun-stage", "abort"))
    return KernelGatePolicy()


def _retry_policy_for_contract(contract: StageContract) -> RetryPolicy:
    attempts = [
        int(route.metadata.get("retry", {}).get("max_attempts"))
        for route in contract.allowed_routes
        if isinstance(route.metadata.get("retry"), dict) and route.metadata.get("retry", {}).get("max_attempts") is not None
    ]
    return RetryPolicy(max_attempts=max(attempts) if attempts else None)


def _council_policy_for_contract(contract: StageContract) -> str:
    if contract.council_policy != "none":
        return contract.council_policy
    if contract.id == "persona_council":
        return "persona_council"
    if contract.id == "duality_check":
        return "duality_check"
    if contract.kind in {"gate", "router", "control", "approval", "validator"}:
        return "deterministic_gate"
    if "counsel" in contract.tool_families or "ensemble_review_optional" in contract.tool_families:
        return "model_council"
    return "none"


def _requires_duality_pass(stage_id: str) -> bool:
    return stage_id in {
        "resource_preparation_agent",
        "paper_contract_builder",
        "writeup_agent",
        "writeup_artifact_gate",
        "proofreading_entry",
        "proofreading_agent",
        "proofread_gate",
        "reviewer_agent",
        "review_gate",
        "milestone_review",
        "validation_gate",
    }


def _route_spec(route: RouteContract, *, source_stage_id: str) -> RouteSpec:
    route_metadata: dict[str, Any] = {"source_kind": route.kind}
    router = target_research_graph_template().router_map().get(source_stage_id)
    if router is not None:
        branch = _router_branch_for_route(router, route)
        if branch is not None:
            route_metadata["routerId"] = router.id
            route_metadata["routeLabel"] = branch.label
            route_metadata["terminal"] = branch.terminal
            if branch.expression:
                route_metadata["expression"] = branch.expression
            if branch.feature_flag:
                route_metadata["featureFlag"] = branch.feature_flag
            if branch.retry:
                route_metadata["retry"] = branch.retry.to_dict()
            if branch.metadata:
                route_metadata["branchMetadata"] = dict(branch.metadata)
    return RouteSpec(
        target=route.target,
        condition=route.condition,
        kind=_route_kind(route.kind),
        max_visits=_max_visits_for_route(source_stage_id, route),
        description=route.description,
        metadata=route_metadata,
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


def _router_branch_for_route(router: Any, route: RouteContract) -> Any | None:
    for branch in router.branches:
        if branch.target == route.target and branch.label == route.condition:
            return branch
    for branch in router.branches:
        if branch.target == route.target:
            return branch
    for branch in router.branches:
        if branch.label == route.condition:
            return branch
    return None


def _max_visits_for_route(source_stage_id: str, route: RouteContract) -> int | None:
    router = target_research_graph_template().router_map().get(source_stage_id)
    if router is None:
        return None
    branch = _router_branch_for_route(router, route)
    if branch is None or branch.retry is None:
        return None
    return branch.retry.max_attempts


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
            "kind": metadata.get("source_kind") or stage.kind,
            "purpose": stage.purpose,
            "validators": list(stage.validator_ids),
            "toolFamilies": list(stage.tool_ids),
            "humanPausePolicy": list(metadata.get("human_pause_policy") or []),
            "failurePolicy": metadata.get("failure_policy") or stage.failure.mode,
            "councilPolicy": stage.council_policy.to_dict(),
            "completionPolicy": stage.completion_policy.to_dict(),
            "gatePolicy": stage.gate_policy.to_dict(),
            "retryPolicy": stage.retry_policy.to_dict(),
            "timeoutPolicy": stage.timeout_policy.to_dict(),
            "executionModelPolicy": asdict(stage.model_policy),
            "tierPolicy": template_metadata(template, tier)["tierPolicy"],
            "modelPolicy": template_metadata(template, tier)["modelPolicy"],
            "dualityRequired": bool(metadata.get("duality_required") or stage.id == "duality_check"),
            "requiresDualityPass": bool(metadata.get("requires_duality_pass")),
            "allowedRoutes": [_route_edge(stage.id, route) for route in stage.routes],
            "adapter": metadata.get("adapter") or "sdk_native",
            "diagnostics": {
                "runtimeMapping": dict(metadata.get("runtime_mapping_diagnostics") or {}),
            },
            "routerSpec": metadata.get("router_spec"),
            "subgraphSpec": metadata.get("subgraph_spec"),
            "subgraphId": metadata.get("subgraph_id"),
            "stateReads": list(metadata.get("state_reads") or []),
            "stateWrites": list(metadata.get("state_writes") or []),
            "stateFieldContracts": list(metadata.get("state_field_contracts") or []),
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
        "type": stage.metadata.get("source_kind") or stage.kind,
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
        "kind": metadata.get("source_kind") or route.kind,
        "metadata": metadata,
    }


def _artifact_contract_dict(artifact: ArtifactSpec) -> dict[str, Any]:
    return {
        "path": artifact.path,
        "kind": artifact.kind,
        "required": artifact.required,
        "schema_id": artifact.schema_id,
        "schemaId": artifact.schema_id,
        "description": artifact.description,
    }
