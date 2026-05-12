"""Kernel-native research adapters for the first replacement workflow."""

from __future__ import annotations

from collections.abc import Iterable

from .engine import ResearchKernel
from .models import (
    GraphSpec,
    RuntimeContext,
    StageAdapterRegistry,
    ToolRegistry,
    ValidationResult,
    ValidatorRegistry,
)


KERNEL_NATIVE_STAGE_IDS = (
    "persona_council",
    "literature_review_agent",
    "lit_review_gate",
    "brainstorm_agent",
    "brainstorm_artifact_gate",
    "formalize_goals_entry",
    "formalize_goals_agent",
    "research_plan_writeup_agent",
)

KERNEL_NATIVE_SCAFFOLD_STAGE_IDS = (
    "persona_council",
    "literature_review_agent",
    "lit_review_gate",
    "brainstorm_agent",
    "brainstorm_artifact_gate",
    "formalize_goals_entry",
    "formalize_goals_agent",
    "research_plan_writeup_agent",
    "track_decomposition_gate",
    "milestone_goals",
    "theory_track",
    "math_literature_agent",
    "math_proposer_agent",
    "goal_tag_validation_gate",
    "math_prover_agent",
    "math_rigorous_verifier_agent",
    "human_review_gate",
    "math_empirical_verifier_agent",
    "proof_transcription_agent",
    "theory_track_repair_gate",
    "experiment_track",
    "experiment_literature_agent",
    "experiment_design_agent",
    "experimentation_agent",
    "experiment_verification_agent",
    "experiment_transcription_agent",
    "track_merge",
    "verify_completion",
    "formalize_results_agent",
    "duality_check",
    "duality_gate",
    "followup_lit_review",
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
)

ROUTE_CONDITIONS_BY_STAGE = {
    "lit_review_gate": ("feasible",),
    "brainstorm_artifact_gate": ("valid",),
    "milestone_goals": ("math_enabled_and_theory_questions", "empirical_questions_or_default"),
    "theory_track": ("expanded_control_view",),
    "theory_track_repair_gate": ("theory_complete",),
    "experiment_track": ("expanded_control_view",),
    "verify_completion": ("complete",),
    "duality_gate": ("pass",),
    "writeup_artifact_gate": ("valid",),
    "proofread_gate": ("ready_for_review",),
    "review_gate": ("review_accepted",),
}


def build_kernel_native_research_kernel(graph: GraphSpec) -> ResearchKernel:
    """Build a kernel runtime for the first native literature workflow."""

    adapters = StageAdapterRegistry()
    register_kernel_native_adapters(adapters)
    validators = ValidatorRegistry()
    register_declared_pass_validators(validators, graph)
    tools = ToolRegistry()
    register_declared_noop_tools(tools, graph)
    return ResearchKernel(
        adapter_registry=adapters,
        validators=validators,
        tool_registry=tools,
    )


def register_kernel_native_adapters(registry: StageAdapterRegistry) -> None:
    handlers = {
        "persona_council": _persona_council,
        "literature_review_agent": _literature_review,
        "lit_review_gate": _literature_gate,
        "brainstorm_agent": _brainstorm,
        "brainstorm_artifact_gate": _brainstorm_gate,
        "formalize_goals_entry": _formalize_goals_entry,
        "formalize_goals_agent": _formalize_goals,
        "research_plan_writeup_agent": _research_plan,
    }
    for stage_id in KERNEL_NATIVE_SCAFFOLD_STAGE_IDS:
        handler = handlers.get(stage_id) or _generic_stage(stage_id)
        registry.register(f"historical.{stage_id}", handler)


def register_declared_pass_validators(registry: ValidatorRegistry, graph: GraphSpec) -> None:
    for validator_id in _unique(stage.validator_ids for stage in graph.stages):
        registry.register(validator_id, _pass_validator(validator_id))


def register_declared_noop_tools(registry: ToolRegistry, graph: GraphSpec) -> None:
    for tool_id in _unique(stage.tool_ids for stage in graph.stages):
        registry.register(tool_id, lambda **kwargs: {"ok": True, "arguments": kwargs})


def _persona_council(context: RuntimeContext) -> None:
    _write_if_declared(
        context,
        "artifacts/persona_debate.md",
        "# Persona Debate\n\n- Expert personas agree the research question is testable.",
    )
    _write_if_declared(
        context,
        "artifacts/research_proposal.md",
        "# Research Proposal\n\nStudy a narrow optimizer generalization question.",
    )


def _literature_review(context: RuntimeContext) -> None:
    _write_if_declared(
        context,
        "artifacts/literature_matrix.md",
        "# Literature Matrix\n\n| Paper | Relevance |\n| --- | --- |\n| Smith 2024 | Supports feasibility |",
    )
    _write_if_declared(
        context,
        "artifacts/lit_review_feasibility.json",
        {"decision": "feasible", "novelty": "plausible", "risks": []},
    )


def _literature_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _write_if_declared(
        context,
        "artifacts/lit_review_gate_decision.json",
        {"decision": "feasible", "route": "feasible"},
    )
    return {"route_conditions": ["feasible"]}


def _brainstorm(context: RuntimeContext) -> None:
    _write_if_declared(
        context,
        "artifacts/brainstorm.md",
        "# Brainstorm\n\n## Recommended Priority Ordering\n\n1. Run a small controlled comparison.",
    )
    _write_if_declared(
        context,
        "artifacts/approach_menu.json",
        {"approaches": [{"id": "baseline-comparison", "priority": 1}]},
    )


def _brainstorm_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _write_if_declared(
        context,
        "artifacts/brainstorm_gate_decision.json",
        {"decision": "valid", "route": "valid"},
    )
    return {"route_conditions": ["valid"]}


def _formalize_goals_entry(context: RuntimeContext) -> None:
    _write_if_declared(
        context,
        "artifacts/formalize_goals_entry.json",
        {"ready": True, "source": "brainstorm"},
    )


def _formalize_goals(context: RuntimeContext) -> None:
    _write_if_declared(
        context,
        "artifacts/research_goals.json",
        {"goals": [{"id": "goal:minimal-comparison", "success_criteria": ["clear baseline"]}]},
    )
    _write_if_declared(
        context,
        "artifacts/goal_spec.md",
        "# Goal Spec\n\nMeasure whether the proposed direction survives a minimal baseline.",
    )


def _research_plan(context: RuntimeContext) -> None:
    _write_if_declared(
        context,
        "artifacts/research_plan.md",
        "# Research Plan\n\n1. Confirm assumptions.\n2. Run minimal experiments.\n3. Synthesize claims.",
    )


def _generic_stage(stage_id: str):
    def run(context: RuntimeContext) -> dict[str, list[str]] | None:
        for artifact in context.stage.outputs:
            context.write_artifact(artifact, _artifact_content(stage_id, artifact.path, artifact.kind))
        conditions = ROUTE_CONDITIONS_BY_STAGE.get(stage_id)
        if conditions:
            return {"route_conditions": list(conditions)}
        return None

    return run


def _artifact_content(stage_id: str, path: str, kind: str) -> object:
    title = stage_id.replace("_", " ").title()
    if kind == "json":
        return {
            "stage_id": stage_id,
            "status": "complete",
            "path": path,
            "summary": f"{title} completed through the kernel-native adapter.",
            **_json_decision_fields(stage_id),
        }
    if kind == "bib":
        return "@article{kernel_native_2026,\n  title={Kernel Native Research Workflow},\n  year={2026}\n}\n"
    if kind == "python":
        return "def run():\n    return {'status': 'complete'}\n"
    if kind == "tex":
        return f"\\section{{{title}}}\nKernel-native {title.lower()} output.\n"
    if kind == "pdf":
        return b"%PDF-1.4\n% kernel-native placeholder\n"
    return f"# {title}\n\nKernel-native {title.lower()} output for `{path}`.\n"


def _json_decision_fields(stage_id: str) -> dict[str, object]:
    if stage_id == "track_decomposition_gate":
        return {"tracks": {"theory": True, "experiment": True}}
    if stage_id == "milestone_goals":
        return {"decision": "approved"}
    if stage_id == "verify_completion":
        return {"decision": "complete"}
    if stage_id == "duality_gate":
        return {"decision": "pass"}
    if stage_id == "writeup_artifact_gate":
        return {"decision": "valid"}
    if stage_id == "proofread_gate":
        return {"decision": "ready_for_review"}
    if stage_id == "review_gate":
        return {"decision": "review_accepted"}
    if stage_id == "validation_gate":
        return {"decision": "validated"}
    return {}


def _write_if_declared(context: RuntimeContext, path: str, content: object) -> None:
    for artifact in context.stage.outputs:
        if artifact.path == path:
            context.write_artifact(artifact, content)  # type: ignore[arg-type]
            return


def _pass_validator(validator_id: str):
    def validate(context: RuntimeContext, stage, artifacts) -> ValidationResult:
        return ValidationResult(
            validator_id=validator_id,
            passed=True,
            message="kernel-native placeholder validator passed",
        )

    return validate


def _unique(groups: Iterable[Iterable[str]]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)
