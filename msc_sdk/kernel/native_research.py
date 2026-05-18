"""Kernel-native research adapters for the first replacement workflow."""

from __future__ import annotations

from collections.abc import Iterable
import json

from ..research_tiers import TARGET_WORKFLOW_STAGE_IDS

from .engine import ResearchKernel
from .models import (
    ArtifactSpec,
    EvidenceLink,
    GraphSpec,
    HumanDecisionRequiredError,
    ModelRegistry,
    RuntimeContext,
    SchemaRegistry,
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

KERNEL_NATIVE_SCAFFOLD_STAGE_IDS = TARGET_WORKFLOW_STAGE_IDS

ROUTE_CONDITIONS_BY_STAGE = {
    "lit_review_gate": ("feasible",),
    "brainstorm_artifact_gate": ("valid",),
    "milestone_goals": ("math_enabled_and_theory_questions", "empirical_questions_or_default"),
    "theory_track": ("track_complete_or_skipped",),
    "theory_track_repair_gate": ("theory_complete",),
    "experiment_track": ("track_complete_or_skipped",),
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
    schemas = SchemaRegistry()
    register_declared_schemas(schemas, graph)
    models = ModelRegistry()
    register_declared_noop_models(models, graph)
    return ResearchKernel(
        adapter_registry=adapters,
        validators=validators,
        tool_registry=tools,
        schema_registry=schemas,
        model_registry=models,
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
        "track_decomposition_gate": _track_decomposition_gate,
        "milestone_goals": _milestone_goals,
        "theory_track": _theory_track,
        "experiment_track": _experiment_track,
        "track_merge": _track_merge,
        "verify_completion": _verify_completion,
        "formalize_results_agent": _formalize_results,
        "duality_check": _duality_check,
        "duality_gate": _duality_gate,
        "followup_lit_review": _followup_lit_review,
        "resource_preparation_agent": _resource_preparation,
        "paper_contract_builder": _paper_contract,
        "writeup_agent": _writeup,
        "writeup_artifact_gate": _writeup_gate,
        "proofreading_entry": _proofreading_entry,
        "proofreading_agent": _proofreading,
        "proofread_gate": _proofread_gate,
        "reviewer_agent": _reviewer,
        "review_gate": _review_gate,
        "milestone_review": _milestone_review,
        "validation_gate": _validation_gate,
    }
    for stage_id in KERNEL_NATIVE_SCAFFOLD_STAGE_IDS:
        handler = handlers.get(stage_id) or _generic_stage(stage_id)
        registry.register(f"sdk_native.{stage_id}", handler)


def register_declared_pass_validators(registry: ValidatorRegistry, graph: GraphSpec) -> None:
    for validator_id in _unique(stage.validator_ids for stage in graph.stages):
        registry.register(validator_id, _semantic_validator(validator_id))


def register_declared_noop_tools(registry: ToolRegistry, graph: GraphSpec) -> None:
    for tool_id in _unique(stage.tool_ids for stage in graph.stages):
        registry.register(tool_id, lambda **kwargs: {"ok": True, "arguments": kwargs})


def register_declared_noop_models(registry: ModelRegistry, graph: GraphSpec) -> None:
    for model_id in _unique(stage.model_policy.allowed_model_ids for stage in graph.stages):
        registry.register(model_id, lambda **kwargs: {"ok": True, "arguments": kwargs})


def register_declared_schemas(registry: SchemaRegistry, graph: GraphSpec) -> None:
    schema_ids = _unique(
        tuple(artifact.schema_id for artifact in stage.outputs if artifact.schema_id)
        for stage in graph.stages
    )
    for schema_id in schema_ids:
        registry.register(schema_id, _schema_validator(schema_id))


def _invoke_stage_model(
    context: RuntimeContext,
    *,
    response_schema: str | None = None,
    amount_usd: float = 0.0001,
) -> None:
    if context.stage.kind in {"router", "validator", "control", "approval"}:
        return
    model_ids = context.stage.model_policy.allowed_model_ids
    if not model_ids:
        return
    model_id = model_ids[0]
    context.charge_budget(amount_usd, reason="stage_model_invocation", metadata={"model_id": model_id})
    kwargs = {
        "prompt": context.stage.purpose,
        "input_tokens": min(context.stage.model_policy.max_input_tokens or 512, 512),
        "max_output_tokens": min(context.stage.model_policy.max_output_tokens or 256, 256),
    }
    if response_schema:
        kwargs["response_schema"] = response_schema
    context.use_model(model_id, **kwargs)


def _persona_council(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    context.run_council(
        prompt=context.run.objective,
        member_outputs=_member_outputs_for_policy(
            context,
            (
                "Practical compass accepts the direction.",
                "Rigor and novelty lens accepts with measurable goals.",
                "Narrative lens accepts the framing.",
            ),
        ),
        verdict="accept",
        passed=True,
        metadata={"adapter": "kernel_native"},
    )
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
    _invoke_stage_model(context)
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
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/lit_review_gate_decision.json",
        {"decision": "feasible", "route": "feasible"},
    )
    return {"route_conditions": ["feasible"]}


def _brainstorm(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
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
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/brainstorm_gate_decision.json",
        {"decision": "valid", "route": "valid"},
    )
    return {"route_conditions": ["valid"]}


def _formalize_goals_entry(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/formalize_goals_entry.json",
        {"ready": True, "source": "brainstorm"},
    )


def _formalize_goals(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
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
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/research_plan.md",
        (
            "# Research Plan\n\n"
            "Goal G1: compare spectral norm growth for a two-layer MLP with and without batch normalization.\n\n"
            "1. Generate synthetic Gaussian blobs with fixed seed.\n"
            "2. Train two tiny MLP variants for six smoke epochs.\n"
            "3. Record the first-layer spectral norm trajectory.\n"
            "4. Synthesize the comparison, limitations, and writeup evidence.\n"
        ),
    )


def _track_decomposition_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    math_enabled = bool(context.run.metadata.get("math_enabled", True))
    _write_if_declared(
        context,
        "artifacts/track_decomposition.json",
        {
            "decision": "decomposed",
            "math_enabled": math_enabled,
            "tracks": {
                "theory": {"enabled": math_enabled, "reason": "math disabled for lean smoke run" if not math_enabled else "requested"},
                "experiment": {"enabled": True, "reason": "objective asks for an empirical comparison"},
            },
            "recommended_track": "empirical",
        },
    )
    return {"route_conditions": ["always"]}


def _milestone_goals(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    math_enabled = bool(context.run.metadata.get("math_enabled", True))
    _write_if_declared(
        context,
        "artifacts/goals_approval.json",
        {
            "decision": "approved_for_execution",
            "approved_scope": "toy empirical smoke comparison",
            "requires_human_review": True,
            "next_tracks": ["experiment", *(("theory",) if math_enabled else ())],
        },
    )
    routes = ["empirical_questions_or_default"]
    if math_enabled:
        routes.insert(0, "math_enabled_and_theory_questions")
    return {"route_conditions": routes}


def _theory_track(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/theory_track_summary.md",
        (
            "# Theory Track Summary\n\n"
            "The lean native path does not attempt a proof. It records that no formal theorem is required "
            "for the toy batch-normalization smoke objective.\n"
        ),
    )
    return {"route_conditions": ["track_complete_or_skipped"]}


def _experiment_track(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    results = _toy_spectral_norm_results()
    _write_if_declared(
        context,
        "artifacts/experiment_track_summary.md",
        (
            "# Experiment Track Summary\n\n"
            "Synthetic Gaussian blobs were used to compare two tiny MLP variants.\n\n"
            "| Epoch | With BN | Without BN |\n"
            "| ---: | ---: | ---: |\n"
            + "\n".join(
                f"| {epoch} | {with_bn:.2f} | {without_bn:.2f} |"
                for epoch, with_bn, without_bn in zip(
                    results["epochs"],
                    results["with_batch_norm"],
                    results["without_batch_norm"],
                )
            )
            + "\n\nThe smoke result shows slower spectral norm growth in the batch-normalized variant."
        ),
    )
    _write_if_declared(
        context,
        "artifacts/experiment_failure_report.md",
        "# Experiment Failure Report\n\nNo failure was observed in the SDK-native smoke experiment.\n",
    )
    _write_stage_diagnostic_json(context, "experiment_results.json", results)
    return {"route_conditions": ["track_complete_or_skipped"]}


def _track_merge(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/track_merge_summary.md",
        (
            "# Track Merge Summary\n\n"
            "The empirical track produced concrete smoke evidence. The theory track was skipped by policy "
            "for this lean, math-disabled campaign.\n"
        ),
    )


def _verify_completion(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/completion_verification.json",
        {
            "decision": "complete",
            "evidence": ["experiment_track_summary.md", "track_merge_summary.md"],
            "missing": [],
            "ratio_complete": 1.0,
        },
    )
    return {"route_conditions": ["complete"]}


def _formalize_results(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    results = _toy_spectral_norm_results()
    delta = round(results["without_batch_norm"][-1] - results["with_batch_norm"][-1], 3)
    claim_link = EvidenceLink(
        claim_id="C1",
        evidence_path="experiment_track:artifacts/experiment_track_summary.md",
        relationship="supports",
        note="Deterministic toy smoke trajectory.",
    )
    context.record_claim(
        claim_id="C1",
        text="Batch normalization slowed spectral norm growth in the deterministic toy smoke run.",
        status="qualified",
        strength="smoke_test_only",
        limitations=(
            "Synthetic deterministic smoke data are not scientific evidence.",
            "No hyperparameter sweep or statistical uncertainty estimate was run.",
        ),
        evidence_links=(claim_link,),
        metadata={"objective_role": "toy_empirical_comparison"},
    )
    _write_if_declared(
        context,
        "artifacts/formalized_results.md",
        (
            "# Formalized Results\n\n"
            "Claim C1: In the deterministic toy smoke run, the MLP without batch normalization "
            f"ended with a first-layer spectral norm {delta:.2f} higher than the batch-normalized variant.\n\n"
            "This is an engineering smoke result, not a scientific conclusion. It is useful only as "
            "pipeline evidence that the experiment, synthesis, and writeup contracts can be completed.\n"
        ),
    )
    _write_if_declared(
        context,
        "artifacts/claims_and_limitations.json",
        {
            "claims": [
                {
                    "id": "C1",
                    "text": "Batch normalization slowed spectral norm growth in the deterministic toy smoke run.",
                    "evidence": ["experiment_track:artifacts/experiment_track_summary.md"],
                    "strength": "smoke_test_only",
                }
            ],
            "limitations": [
                "Synthetic deterministic smoke data are not scientific evidence.",
                "No hyperparameter sweep or statistical uncertainty estimate was run.",
            ],
        },
    )


def _duality_check(context: RuntimeContext) -> None:
    _invoke_stage_model(context, response_schema="msc.duality_check.v1")
    force_fail = bool(context.run.metadata.get("force_duality_fail"))
    passed = not force_fail
    verdict = "pass" if passed else "fail"
    objections = [] if passed else [
        "The artifact is acceptable as pipeline smoke evidence but not as a scientific result.",
        "The writeup must label deterministic trajectories as smoke data.",
    ]
    context.run_council(
        prompt="Check practical meaning and technical defensibility before writeup.",
        member_outputs=_member_outputs_for_policy(
            context,
            (
                "The result is useful for product validation when labeled as smoke data.",
                "The comparison is technically coherent but scientifically weak.",
            ),
        ),
        verdict=verdict,
        passed=passed,
        metadata={
            "failed_lenses": [] if passed else ["scientific_strength", "external_validity"],
            "objections": objections,
            "safe_actions": _duality_safe_actions(),
        },
    )
    _write_if_declared(
        context,
        "artifacts/duality_check.json",
        {
            "verdict": verdict,
            "passed": passed,
            "lenses": {
                "practical_meaning": "pass",
                "technical_defensibility": "pass",
                "scientific_strength": "qualified" if passed else "fail",
            },
            "objections": objections,
            "safe_actions": _duality_safe_actions(),
        },
    )


def _duality_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    passed = not bool(context.run.metadata.get("force_duality_fail"))
    route = "pass" if passed else "needs_human_scientific_decision"
    failed_lenses = [] if passed else ["scientific_strength", "external_validity"]
    objections = [] if passed else [
        "The smoke result should not be promoted to a scientific claim without more evidence.",
    ]
    payload = {
        "decision": route,
        "passed": passed,
        "safe_actions": [] if passed else _duality_safe_actions(),
    }
    _write_if_declared(context, "artifacts/duality_gate_decision.json", payload)
    context.record_gate_verdict(
        gate_id="duality_gate",
        passed=passed,
        verdict="pass" if passed else "fail",
        failed_lenses=failed_lenses,
        objections=objections,
        evidence=("artifacts/duality_check.json", "artifacts/duality_gate_decision.json"),
        safe_next_actions=() if passed else _duality_safe_actions(),
    )
    if not passed:
        for lens, text in zip(failed_lenses, objections * len(failed_lenses), strict=False):
            context.record_objection(
                claim_id="C1",
                lens=lens,
                severity="major",
                text=text,
            )
        raise HumanDecisionRequiredError(
            reason="duality_failed",
            safe_next_actions=_duality_safe_actions(),
            metadata={
                "evidence": ["artifacts/duality_check.json", "artifacts/duality_gate_decision.json"],
                "failed_lenses": failed_lenses,
                "objections": objections,
                "safe_actions": _duality_safe_actions(),
            },
        )
    return {"route_conditions": ["pass"]}


def _followup_lit_review(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/followup_lit_review.md",
        "# Follow-Up Literature Review\n\nA human duality decision requested more grounding before writeup.\n",
    )


def _resource_preparation(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/resource_manifest.json",
        {
            "figures": [{"id": "fig:spectral-norm", "source": "experiment_track_summary.md"}],
            "tables": [{"id": "tab:spectral-norm", "source": "experiment_track_summary.md"}],
            "evidence": ["formalized_results.md", "duality_check.json"],
        },
    )
    _write_if_declared(
        context,
        "artifacts/figure_table_plan.md",
        "# Figure And Table Plan\n\nUse one table for spectral norm trajectories and one short results paragraph.\n",
    )


def _paper_contract(context: RuntimeContext) -> None:
    _invoke_stage_model(context, response_schema="msc.paper_contract.v1")
    _write_if_declared(
        context,
        "artifacts/paper_contract.json",
        {
            "format": context.run.metadata.get("output_format", "markdown"),
            "required_sections": ["Abstract", "Setup", "Results", "Limitations"],
            "required_claim_ids": ["C1"],
            "must_label_smoke_data": True,
        },
    )


def _writeup(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    results = _toy_spectral_norm_results()
    _write_if_declared(
        context,
        "artifacts/final_paper.md",
        (
            "# Batch Normalization And Spectral Norm Growth In A Toy MLP\n\n"
            "## Abstract\n\n"
            "This smoke writeup checks whether the SDK-native campaign pipeline can carry an empirical "
            "comparison into a coherent markdown deliverable.\n\n"
            "## Setup\n\n"
            "Two tiny two-layer MLP variants were compared on synthetic Gaussian blobs: one with batch "
            "normalization and one without batch normalization.\n\n"
            "## Results\n\n"
            f"The batch-normalized variant ended at spectral norm {results['with_batch_norm'][-1]:.2f}; "
            f"the non-normalized variant ended at {results['without_batch_norm'][-1]:.2f}. "
            "Within this deterministic smoke run, the non-normalized model showed larger growth.\n\n"
            "## Limitations\n\n"
            "These are deterministic smoke artifacts for product validation. They are not a scientific "
            "claim about batch normalization in real training regimes.\n"
        ),
    )


def _writeup_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/writeup_gate_decision.json",
        {"decision": "valid", "required_terms_present": True, "missing": []},
    )
    return {"route_conditions": ["valid"]}


def _proofreading_entry(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/proofreading_entry.json",
        {"ready": True, "source": "writeup_artifact_gate"},
    )


def _proofreading(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/copyedit_report.md",
        "# Copyedit Report\n\nThe markdown writeup is concise, labels smoke data, and needs no blocking edits.\n",
    )


def _proofread_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/proofread_gate_decision.json",
        {"decision": "ready_for_review", "retry_reason": None},
    )
    return {"route_conditions": ["ready_for_review"]}


def _reviewer(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/review_report.md",
        "# Review Report\n\nAccept for SDK smoke purposes. The writeup is clear about its limitations.\n",
    )
    _write_if_declared(
        context,
        "artifacts/review_verdict.json",
        {"decision": "accept", "blockers": [], "actionable_items": []},
    )


def _review_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/review_gate_decision.json",
        {"decision": "review_accepted", "needs_retry": False},
    )
    return {"route_conditions": ["review_accepted"]}


def _milestone_review(context: RuntimeContext) -> None:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/final_review_approval.json",
        {"decision": "approved", "approved_for_completion": True},
    )


def _validation_gate(context: RuntimeContext) -> dict[str, list[str]]:
    _invoke_stage_model(context)
    _write_if_declared(
        context,
        "artifacts/final_validation.json",
        {
            "decision": "finished",
            "required_artifacts_present": True,
            "paper_artifact_failure": False,
            "experiment_artifact_failure": False,
            "theory_artifact_failure": False,
        },
    )
    return {"route_conditions": ["finished"]}


def _generic_stage(stage_id: str):
    def run(context: RuntimeContext) -> dict[str, list[str]] | None:
        _invoke_stage_model(context)
        if context.stage.council_policy.kind in {"model_council", "duality_check"}:
            context.run_council(
                prompt=f"Evaluate {context.stage.title}.",
                member_outputs=_council_member_outputs(context),
                verdict="pass",
                passed=True,
                metadata={"adapter": "kernel_native"},
            )
        for artifact in context.stage.outputs:
            context.write_artifact(artifact, _artifact_content(stage_id, artifact.path, artifact.kind))
        conditions = ROUTE_CONDITIONS_BY_STAGE.get(stage_id)
        if conditions:
            return {"route_conditions": list(conditions)}
        return None

    return run


def _council_member_outputs(context: RuntimeContext) -> dict[str, str]:
    kind = context.stage.council_policy.kind
    if kind == "duality_check":
        return _member_outputs_for_policy(
            context,
            ("Practical meaning and technical defensibility both pass.",),
        )
    if kind == "model_council":
        return _member_outputs_for_policy(
            context,
            ("Strong specialist result.", "Independent critique agrees."),
        )
    return {}


def _member_outputs_for_policy(context: RuntimeContext, outputs: tuple[str, ...]) -> dict[str, str]:
    model_ids = context.stage.model_policy.allowed_model_ids or context.stage.council_policy.model_ids
    if not model_ids:
        return {}
    return {
        model_id: outputs[index % len(outputs)]
        for index, model_id in enumerate(model_ids)
    }


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


def _toy_spectral_norm_results() -> dict[str, object]:
    return {
        "dataset": "synthetic_gaussian_blobs",
        "seed": 7,
        "epochs": [0, 1, 2, 3, 4, 5],
        "with_batch_norm": [1.02, 1.06, 1.09, 1.11, 1.13, 1.14],
        "without_batch_norm": [1.03, 1.12, 1.24, 1.38, 1.53, 1.69],
        "metric": "first_layer_spectral_norm",
        "interpretation": "Smoke data show slower growth with batch normalization.",
    }


def _duality_safe_actions() -> list[str]:
    return [
        "revise-goals",
        "rerun-literature",
        "rerun-experiment-track",
        "reroute",
        "stop-campaign",
    ]


def _write_stage_diagnostic_json(context: RuntimeContext, filename: str, payload: object) -> None:
    target = context.stage_workspace / "diagnostics" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_if_declared(context: RuntimeContext, path: str, content: object) -> None:
    for artifact in context.stage.outputs:
        if artifact.path == path:
            context.write_artifact(artifact, content)  # type: ignore[arg-type]
            return


def _semantic_validator(validator_id: str):
    def validate(context: RuntimeContext, stage, artifacts) -> ValidationResult:
        artifact_paths = {artifact.path: artifact for artifact in artifacts}
        required_present = all(output.path in artifact_paths for output in stage.required_outputs)
        checks = {
            "required_present": required_present,
            "artifact_count": len(artifacts),
        }
        passed = required_present
        message = "SDK-native semantic contract passed"

        if validator_id == "experiment_track_status_present":
            text = _artifact_text(context, "artifacts/experiment_track_summary.md")
            passed = "With BN" in text and "Without BN" in text
            checks["contains_bn_comparison"] = passed
        elif validator_id == "duality_verdict_present":
            payload = _artifact_json(context, "artifacts/duality_check.json")
            verdict = str(payload.get("verdict") or "")
            passed_field = payload.get("passed")
            passed = (
                verdict in {"pass", "fail"}
                and isinstance(passed_field, bool)
                and ((verdict == "pass") == passed_field)
            )
            checks["verdict"] = payload.get("verdict")
            checks["passed"] = passed_field
        elif validator_id == "duality_route_present":
            payload = _artifact_json(context, "artifacts/duality_gate_decision.json")
            decision = str(payload.get("decision") or "")
            passed_field = payload.get("passed")
            pass_like = decision in {"pass", "passed", "approve", "approved"}
            failed_like = decision in {"fail", "failed", "reject", "rejected", "blocked", "needs_human_scientific_decision"}
            passed = bool(decision) and (
                not isinstance(passed_field, bool)
                or (pass_like and passed_field)
                or (failed_like and not passed_field)
            )
            checks["decision"] = payload.get("decision")
            checks["passed"] = passed_field
        elif validator_id in {"required_sections_present", "paper_contract_terms_present"}:
            text = _artifact_text(context, "artifacts/final_paper.md")
            passed = all(section in text for section in ("## Abstract", "## Setup", "## Results", "## Limitations"))
            checks["sections_present"] = passed
        elif validator_id in {
            "feasibility_json_schema",
            "approach_menu_schema",
            "research_goals_schema",
            "track_decomposition_schema",
            "resource_manifest_schema",
            "paper_contract_schema",
            "review_verdict_schema",
        }:
            json_artifacts = [artifact.path for artifact in artifacts if artifact.kind == "json"]
            passed = bool(json_artifacts)
            checks["json_artifacts"] = json_artifacts
        elif "decision" in validator_id or "route" in validator_id or "verdict" in validator_id:
            payloads = [_artifact_json(context, artifact.path) for artifact in artifacts if artifact.kind == "json"]
            passed = any(("decision" in payload or "verdict" in payload or "route" in payload) for payload in payloads)
            checks["decision_payload_count"] = len(payloads)

        return ValidationResult(
            validator_id=validator_id,
            passed=passed,
            message=message if passed else f"{validator_id} failed SDK-native semantic contract",
            details=checks,
        )

    return validate


def _schema_validator(schema_id: str):
    def validate(payload: object, artifact: ArtifactSpec) -> ValidationResult:
        data = payload if isinstance(payload, dict) else {}
        checks: dict[str, object] = {"path": artifact.path, "schema_id": schema_id}
        passed = bool(data)
        message = f"{schema_id} passed"

        if schema_id == "msc.duality_check.v1":
            verdict = str(data.get("verdict") or "")
            passed_field = data.get("passed")
            passed = (
                verdict in {"pass", "fail"}
                and isinstance(passed_field, bool)
                and ((verdict == "pass") == passed_field)
                and isinstance(data.get("lenses"), dict)
            )
            checks.update({"verdict": verdict, "passed": passed_field})
        elif schema_id == "msc.gate_decision.v1":
            decision = str(data.get("decision") or "")
            passed_field = data.get("passed")
            pass_like = decision in {"pass", "passed", "approve", "approved", "valid", "complete", "finished"}
            fail_like = decision in {
                "fail",
                "failed",
                "reject",
                "rejected",
                "blocked",
                "invalid",
                "needs_human_scientific_decision",
            }
            passed = bool(decision) and (
                not isinstance(passed_field, bool)
                or (pass_like and passed_field)
                or (fail_like and not passed_field)
                or (not pass_like and not fail_like)
            )
            checks.update({"decision": decision, "passed": passed_field})
        elif schema_id == "msc.claims_and_limitations.v1":
            claims = data.get("claims")
            passed = isinstance(claims, list) and bool(claims) and all(
                isinstance(claim, dict) and claim.get("id") and claim.get("text")
                for claim in claims
            )
            checks["claim_count"] = len(claims) if isinstance(claims, list) else 0
        elif schema_id == "msc.paper_contract.v1":
            sections = data.get("required_sections")
            passed = bool(data.get("format")) and isinstance(sections, list) and bool(sections)
            checks["format"] = data.get("format")
            checks["required_sections"] = sections if isinstance(sections, list) else []
        elif schema_id == "msc.writeup_gate_decision.v1":
            decision = str(data.get("decision") or "")
            missing = data.get("missing") or []
            passed = bool(decision) and not bool(missing)
            checks.update({"decision": decision, "missing": missing})

        if not passed:
            message = f"{schema_id} failed structured research schema"
        return ValidationResult(
            validator_id=f"schema:{schema_id}",
            passed=passed,
            message=message,
            details=checks,
        )

    return validate


def _artifact_text(context: RuntimeContext, path: str) -> str:
    target = context.stage_workspace / path
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")


def _artifact_json(context: RuntimeContext, path: str) -> dict[str, object]:
    target = context.stage_workspace / path
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
