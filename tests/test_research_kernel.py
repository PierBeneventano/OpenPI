from __future__ import annotations

from pathlib import Path

import pytest

from msc_sdk.kernel import (
    BudgetPolicy,
    EvidenceLink,
    GraphSpec,
    InputSpec,
    InMemoryEventBus,
    KERNEL_NATIVE_SCAFFOLD_STAGE_IDS,
    KERNEL_NATIVE_STAGE_IDS,
    ModelPolicy,
    ModelRegistry,
    ResearchKernel,
    RouteSpec,
    RunSpec,
    SchemaRegistry,
    StageAdapterRegistry,
    StageSpec,
    ToolRegistry,
    ValidationResult,
    ValidatorRegistry,
    build_kernel_native_research_kernel,
    project_run,
)
from msc_sdk.kernel.engine import artifact, non_empty_artifact
from msc_sdk.kernel.models import JsonlEventBus
from msc_sdk.kernel.read_models import read_jsonl_events
from msc_sdk.read_models import campaign_model_from_kernel_run
from msc_sdk.stage_contracts import compile_kernel_graph


def _run_spec(tmp_path: Path, *stages: StageSpec) -> RunSpec:
    return RunSpec(
        id="run_1",
        campaign_id="campaign_1",
        objective="Produce a small research artifact.",
        workspace=tmp_path / "run_1",
        budget=BudgetPolicy(max_usd=10, spend_allowed=True),
        graph=GraphSpec(id="graph_1", stages=stages, entry_stage_id=stages[0].id),
    )


def test_kernel_completes_only_when_required_artifacts_and_validators_pass(tmp_path: Path):
    stage = StageSpec(
        id="literature",
        title="Literature Review",
        kind="agent",
        purpose="Ground the question in prior work.",
        outputs=(artifact("artifacts/literature_matrix.md"),),
        validator_ids=("non_empty:artifacts/literature_matrix.md",),
    )
    validators = ValidatorRegistry()
    validators.register("non_empty:artifacts/literature_matrix.md", non_empty_artifact("artifacts/literature_matrix.md"))
    events = InMemoryEventBus()
    kernel = ResearchKernel(validators=validators, event_bus=events)

    def handler(context):
        context.write_artifact(stage.outputs[0], "# Matrix\n\n- Paper A supports the framing.")

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"literature": handler})

    assert outcomes[0].status == "completed"
    assert outcomes[0].validation[0].validator_id == "required_artifacts_exist"
    assert all(result.passed for result in outcomes[0].validation)
    assert [event.type for event in events.events] == [
        "RunStarted",
        "StageStarted",
        "ArtifactWritten",
        "ArtifactIndexed",
        "ValidationPassed",
        "StageCompleted",
        "RunCompleted",
    ]


def test_kernel_turns_missing_artifact_into_human_decision(tmp_path: Path):
    stage = StageSpec(
        id="plan",
        title="Research Plan",
        kind="agent",
        purpose="Define the research plan.",
        outputs=(artifact("artifacts/research_plan.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"plan": lambda context: None})

    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0] == ValidationResult(
        validator_id="required_artifacts_exist",
        passed=False,
        message="missing required artifacts",
        details={"missing": ["artifacts/research_plan.md"]},
    )
    assert "HumanDecisionRequired" in [event.type for event in events.events]
    assert events.events[-1].type == "RunFailed"


def test_kernel_can_bind_stage_handlers_from_adapter_registry(tmp_path: Path):
    stage = StageSpec(
        id="literature",
        title="Literature Review",
        kind="agent",
        purpose="Ground the question in prior work.",
        adapter_id="literature_adapter",
        outputs=(artifact("artifacts/literature_matrix.md"),),
    )
    adapters = StageAdapterRegistry()

    def adapter(context):
        context.write_artifact(stage.outputs[0], "# Matrix")

    adapters.register("literature_adapter", adapter)
    kernel = ResearchKernel(adapter_registry=adapters)

    outcomes = kernel.run(_run_spec(tmp_path, stage))

    assert outcomes[0].status == "completed"


def test_kernel_requires_declared_stage_adapters_to_be_registered(tmp_path: Path):
    stage = StageSpec(
        id="literature",
        title="Literature Review",
        kind="agent",
        purpose="Ground the question in prior work.",
        adapter_id="literature_adapter",
        outputs=(artifact("artifacts/literature_matrix.md"),),
    )

    with pytest.raises(KeyError, match="Missing stage adapters: literature_adapter"):
        ResearchKernel().run(_run_spec(tmp_path, stage))


def test_kernel_requires_declared_validators_before_running(tmp_path: Path):
    stage = StageSpec(
        id="goals",
        title="Goals",
        kind="agent",
        purpose="Formalize goals.",
        outputs=(artifact("artifacts/goals.json", "json"),),
        validator_ids=("goals_schema",),
    )
    kernel = ResearchKernel()

    with pytest.raises(KeyError, match="Missing validators: goals_schema"):
        kernel.run(_run_spec(tmp_path, stage), {"goals": lambda context: None})


def test_kernel_runs_happy_path_in_graph_order(tmp_path: Path):
    stage_a = StageSpec(
        id="proposal",
        title="Proposal",
        kind="agent",
        purpose="Create proposal.",
        outputs=(artifact("artifacts/proposal.md"),),
        routes=(RouteSpec(target="writeup"),),
    )
    stage_b = StageSpec(
        id="writeup",
        title="Writeup",
        kind="agent",
        purpose="Create writeup.",
        outputs=(artifact("artifacts/writeup.md"),),
    )
    kernel = ResearchKernel()

    def write_first(context):
        context.write_artifact(stage_a.outputs[0], "proposal")

    def write_second(context):
        context.write_artifact(stage_b.outputs[0], "writeup")

    outcomes = kernel.run(
        _run_spec(tmp_path, stage_a, stage_b),
        {"proposal": write_first, "writeup": write_second},
    )

    assert [outcome.stage_id for outcome in outcomes] == ["proposal", "writeup"]
    assert all(outcome.status == "completed" for outcome in outcomes)
    assert outcomes[0].scheduled_stage_ids == ("writeup",)


def test_kernel_resolves_declared_input_artifacts_for_downstream_stage(tmp_path: Path):
    plan = StageSpec(
        id="plan",
        title="Plan",
        kind="agent",
        purpose="Create plan.",
        outputs=(artifact("artifacts/plan.md"),),
        routes=(RouteSpec(target="synthesis"),),
    )
    synthesis = StageSpec(
        id="synthesis",
        title="Synthesis",
        kind="agent",
        purpose="Use the plan.",
        inputs=(InputSpec(path="artifacts/plan.md", source_stage_id="plan"),),
        outputs=(artifact("artifacts/synthesis.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def write_plan(context):
        context.write_artifact(plan.outputs[0], "plan text")

    def write_synthesis(context):
        input_record = context.input_artifact("artifacts/plan.md", source_stage_id="plan")
        context.write_artifact(synthesis.outputs[0], f"using {input_record.path}")

    outcomes = kernel.run(
        _run_spec(tmp_path, plan, synthesis),
        {"plan": write_plan, "synthesis": write_synthesis},
    )

    assert [outcome.stage_id for outcome in outcomes] == ["plan", "synthesis"]
    assert all(outcome.status == "completed" for outcome in outcomes)
    assert any(event.type == "StageInputResolved" for event in events.events)


def test_kernel_blocks_stage_when_declared_inputs_are_missing(tmp_path: Path):
    synthesis = StageSpec(
        id="synthesis",
        title="Synthesis",
        kind="agent",
        purpose="Use upstream plan.",
        inputs=(InputSpec(path="artifacts/plan.md", source_stage_id="plan"),),
        outputs=(artifact("artifacts/synthesis.md"),),
    )
    plan = StageSpec(
        id="plan",
        title="Plan",
        kind="agent",
        purpose="Create plan.",
        outputs=(artifact("artifacts/plan.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)
    called = False

    def handler(context):
        nonlocal called
        called = True

    outcomes = kernel.run(_run_spec(tmp_path, synthesis, plan), {"synthesis": handler, "plan": handler})
    model = project_run(events.events)

    assert not called
    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0].validator_id == "stage_inputs"
    assert outcomes[0].validation[0].details["missing"][0]["reason"] == "not_available"
    assert any(event.type == "StageInputMissing" for event in events.events)
    assert model.stages["synthesis"].failure_reason == "stage_inputs_missing"
    assert model.stages["synthesis"].safe_next_actions == [
        "rerun-upstream",
        "rewrite-stage",
        "skip-stage",
        "abort",
    ]


def test_kernel_schedules_branch_fanout_and_join_barrier(tmp_path: Path):
    plan = StageSpec(
        id="plan",
        title="Plan",
        kind="agent",
        purpose="Plan the split.",
        outputs=(artifact("artifacts/plan.md"),),
        routes=(
            RouteSpec(target="theory", kind="branch"),
            RouteSpec(target="experiment", kind="branch"),
        ),
    )
    theory = StageSpec(
        id="theory",
        title="Theory",
        kind="agent",
        purpose="Run theory branch.",
        outputs=(artifact("artifacts/theory.md"),),
        routes=(RouteSpec(target="synthesis", kind="join"),),
    )
    experiment = StageSpec(
        id="experiment",
        title="Experiment",
        kind="agent",
        purpose="Run experiment branch.",
        outputs=(artifact("artifacts/experiment.md"),),
        routes=(RouteSpec(target="synthesis", kind="join"),),
    )
    synthesis = StageSpec(
        id="synthesis",
        title="Synthesis",
        kind="agent",
        purpose="Join evidence.",
        outputs=(artifact("artifacts/synthesis.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def writer(stage):
        def handle(context):
            context.write_artifact(stage.outputs[0], stage.id)
        return handle

    outcomes = kernel.run(
        _run_spec(tmp_path, plan, theory, experiment, synthesis),
        {
            "plan": writer(plan),
            "theory": writer(theory),
            "experiment": writer(experiment),
            "synthesis": writer(synthesis),
        },
    )

    assert [outcome.stage_id for outcome in outcomes] == ["plan", "theory", "experiment", "synthesis"]
    assert outcomes[0].scheduled_stage_ids == ("theory", "experiment")
    assert outcomes[1].scheduled_stage_ids == ()
    assert outcomes[2].scheduled_stage_ids == ("synthesis",)
    assert any(event.type == "JoinWaiting" for event in events.events)
    assert [event.type for event in events.events].count("RouteSelected") == 4


def test_kernel_enforces_bounded_loop_and_then_routes_forward(tmp_path: Path):
    draft = StageSpec(
        id="draft",
        title="Draft",
        kind="agent",
        purpose="Draft until acceptable.",
        outputs=(artifact("artifacts/draft.md"),),
        routes=(
            RouteSpec(target="draft", kind="loop", condition="needs_revision", max_visits=2),
            RouteSpec(target="final", kind="next", condition="accepted"),
        ),
    )
    final = StageSpec(
        id="final",
        title="Final",
        kind="agent",
        purpose="Finalize.",
        outputs=(artifact("artifacts/final.md"),),
    )
    kernel = ResearchKernel()
    calls = {"draft": 0}

    def draft_handler(context):
        calls["draft"] += 1
        context.write_artifact(draft.outputs[0], f"draft {calls['draft']}")
        return {"route_condition": "needs_revision" if calls["draft"] == 1 else "accepted"}

    def final_handler(context):
        context.write_artifact(final.outputs[0], "final")

    outcomes = kernel.run(
        _run_spec(tmp_path, draft, final),
        {"draft": draft_handler, "final": final_handler},
    )

    assert calls["draft"] == 2
    assert [outcome.stage_id for outcome in outcomes] == ["draft", "draft", "final"]
    assert outcomes[0].scheduled_stage_ids == ("draft",)
    assert outcomes[1].scheduled_stage_ids == ("final",)
    assert all(outcome.status == "completed" for outcome in outcomes)


def test_kernel_loop_limit_stops_for_human(tmp_path: Path):
    draft = StageSpec(
        id="draft",
        title="Draft",
        kind="agent",
        purpose="Draft until acceptable.",
        outputs=(artifact("artifacts/draft.md"),),
        routes=(RouteSpec(target="draft", kind="loop", condition="needs_revision", max_visits=2),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def draft_handler(context):
        context.write_artifact(draft.outputs[0], "still weak")
        return {"route_condition": "needs_revision"}

    outcomes = kernel.run(_run_spec(tmp_path, draft), {"draft": draft_handler})
    model = project_run(events.events)

    assert [outcome.stage_id for outcome in outcomes] == ["draft", "draft"]
    assert outcomes[-1].status == "human_decision_required"
    assert any(event.type == "LoopLimitReached" for event in events.events)
    assert model.status == "human_decision_required"
    assert model.stages["draft"].failure_reason == "loop_limit_reached"


def test_kernel_records_budget_spend_in_events_and_read_model(tmp_path: Path):
    stage = StageSpec(
        id="literature",
        title="Literature",
        kind="agent",
        purpose="Spend a small amount on search.",
        budget=BudgetPolicy(max_usd=1.0, spend_allowed=True),
        outputs=(artifact("artifacts/literature.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def handler(context):
        context.charge_budget(0.25, reason="paper search")
        context.write_artifact(stage.outputs[0], "literature")

    kernel.run(_run_spec(tmp_path, stage), {"literature": handler})
    model = project_run(events.events)

    assert any(event.type == "BudgetSpent" for event in events.events)
    assert model.budget_spent_usd == 0.25
    assert model.stages["literature"].budget_spent_usd == 0.25


def test_kernel_budget_exceeded_stops_for_human(tmp_path: Path):
    stage = StageSpec(
        id="experiment",
        title="Experiment",
        kind="agent",
        purpose="Try to overspend.",
        budget=BudgetPolicy(max_usd=0.5, spend_allowed=True),
        outputs=(artifact("artifacts/experiment.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def handler(context):
        context.charge_budget(0.75, reason="expensive run")
        context.write_artifact(stage.outputs[0], "experiment")

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"experiment": handler})
    model = project_run(events.events)

    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0].validator_id == "budget_policy"
    assert any(event.type == "BudgetExceeded" for event in events.events)
    assert model.status == "human_decision_required"
    assert model.stages["experiment"].failure_reason == "budget_policy_failed"
    assert model.stages["experiment"].safe_next_actions == [
        "approve-budget-increase",
        "rewrite-stage",
        "rerun-stage",
        "abort",
    ]


def test_kernel_allows_only_declared_stage_tools(tmp_path: Path):
    stage = StageSpec(
        id="literature",
        title="Literature",
        kind="agent",
        purpose="Search papers.",
        tool_ids=("paper_search",),
        outputs=(artifact("artifacts/literature.md"),),
    )
    tools = ToolRegistry()
    tools.register("paper_search", lambda query: f"result for {query}")
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, tool_registry=tools)

    def handler(context):
        result = context.use_tool("paper_search", query="optimizer benchmarking")
        context.write_artifact(stage.outputs[0], result)

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"literature": handler})

    assert outcomes[0].status == "completed"
    assert any(event.type == "ToolInvoked" for event in events.events)


def test_kernel_denies_undeclared_tool_use_and_requests_decision(tmp_path: Path):
    stage = StageSpec(
        id="literature",
        title="Literature",
        kind="agent",
        purpose="Search papers.",
        tool_ids=("paper_search",),
        outputs=(artifact("artifacts/literature.md"),),
    )
    tools = ToolRegistry()
    tools.register("paper_search", lambda query: f"result for {query}")
    tools.register("shell", lambda command: f"ran {command}")
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, tool_registry=tools)

    def handler(context):
        context.use_tool("shell", command="rm -rf /")

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"literature": handler})
    model = project_run(events.events)

    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0].validator_id == "tool_policy"
    assert any(event.type == "ToolDenied" for event in events.events)
    assert model.stages["literature"].failure_reason == "tool_policy_failed"
    assert model.stages["literature"].safe_next_actions == [
        "rewrite-stage",
        "rerun-stage",
        "approve-tool-access",
        "abort",
    ]


def test_kernel_requires_declared_tools_to_be_registered(tmp_path: Path):
    stage = StageSpec(
        id="experiment",
        title="Experiment",
        kind="agent",
        purpose="Run experiments.",
        tool_ids=("experiment_runner",),
        outputs=(artifact("artifacts/experiment.md"),),
    )

    with pytest.raises(KeyError, match="Missing tools: experiment_runner"):
        ResearchKernel().run(_run_spec(tmp_path, stage), {"experiment": lambda context: None})


def test_kernel_allows_only_declared_stage_models(tmp_path: Path):
    stage = StageSpec(
        id="writeup",
        title="Writeup",
        kind="agent",
        purpose="Draft a section.",
        model_policy=ModelPolicy(allowed_model_ids=("writer-small",), max_output_tokens=100),
        outputs=(artifact("artifacts/writeup.md"),),
    )
    models = ModelRegistry()
    models.register("writer-small", lambda prompt, max_output_tokens: f"draft: {prompt}")
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, model_registry=models)

    def handler(context):
        result = context.use_model("writer-small", prompt="summarize results", max_output_tokens=50)
        context.write_artifact(stage.outputs[0], result)

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"writeup": handler})

    assert outcomes[0].status == "completed"
    assert any(event.type == "ModelInvoked" for event in events.events)


def test_kernel_denies_undeclared_model_use_and_requests_decision(tmp_path: Path):
    stage = StageSpec(
        id="writeup",
        title="Writeup",
        kind="agent",
        purpose="Draft a section.",
        model_policy=ModelPolicy(allowed_model_ids=("writer-small",)),
        outputs=(artifact("artifacts/writeup.md"),),
    )
    models = ModelRegistry()
    models.register("writer-small", lambda prompt: "small draft")
    models.register("frontier-model", lambda prompt: "expensive draft")
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, model_registry=models)

    def handler(context):
        context.use_model("frontier-model", prompt="summarize results")

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"writeup": handler})
    model = project_run(events.events)

    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0].validator_id == "model_policy"
    assert any(event.type == "ModelDenied" for event in events.events)
    assert model.stages["writeup"].failure_reason == "model_policy_failed"
    assert model.stages["writeup"].safe_next_actions == [
        "rewrite-stage",
        "rerun-stage",
        "approve-model-access",
        "abort",
    ]


def test_kernel_enforces_model_output_and_structured_response_policy(tmp_path: Path):
    stage = StageSpec(
        id="extraction",
        title="Extraction",
        kind="agent",
        purpose="Extract structured claims.",
        model_policy=ModelPolicy(
            allowed_model_ids=("extractor",),
            max_output_tokens=10,
            structured_output_required=True,
        ),
        outputs=(artifact("artifacts/claims.json"),),
    )
    models = ModelRegistry()
    models.register("extractor", lambda **kwargs: {"claims": []})
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, model_registry=models)

    def handler(context):
        context.use_model("extractor", prompt="extract claims", max_output_tokens=20)

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"extraction": handler})

    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0].validator_id == "model_policy"
    assert events.events[2].payload["reason"] == "max_output_tokens_exceeded"


def test_kernel_requires_declared_models_to_be_registered(tmp_path: Path):
    stage = StageSpec(
        id="writeup",
        title="Writeup",
        kind="agent",
        purpose="Draft a section.",
        model_policy=ModelPolicy(allowed_model_ids=("writer-small",)),
        outputs=(artifact("artifacts/writeup.md"),),
    )

    with pytest.raises(KeyError, match="Missing models: writer-small"):
        ResearchKernel().run(_run_spec(tmp_path, stage), {"writeup": lambda context: None})


def test_kernel_validates_artifact_schema_and_records_evidence_links(tmp_path: Path):
    output = artifact(
        "artifacts/synthesis.json",
        "json",
        schema_id="synthesis_v1",
        claim_ids=("claim:optimizer-generalizes",),
        evidence_links=(
            EvidenceLink(
                claim_id="claim:optimizer-generalizes",
                evidence_path="artifacts/literature_matrix.md",
                relationship="supports",
                locator="row:smith-2024",
            ),
        ),
    )
    stage = StageSpec(
        id="synthesis",
        title="Synthesis",
        kind="agent",
        purpose="Turn evidence into explicit claims.",
        outputs=(output,),
    )
    schemas = SchemaRegistry()
    schemas.register(
        "synthesis_v1",
        lambda content, artifact: ValidationResult(
            validator_id="schema:synthesis_v1",
            passed=isinstance(content, dict) and "claims" in content,
            message="synthesis schema accepted",
        ),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, schema_registry=schemas)

    def handler(context):
        context.write_artifact(output, {"claims": [{"id": "claim:optimizer-generalizes"}]})

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"synthesis": handler})
    model = project_run(events.events)
    record = outcomes[0].artifacts[0]
    artifact_model = model.stages["synthesis"].artifacts[0]

    assert outcomes[0].status == "completed"
    assert record.schema_id == "synthesis_v1"
    assert record.claim_ids == ("claim:optimizer-generalizes",)
    assert record.evidence_links[0].evidence_path == "artifacts/literature_matrix.md"
    assert artifact_model.schema_id == "synthesis_v1"
    assert artifact_model.claim_ids == ["claim:optimizer-generalizes"]
    assert artifact_model.evidence_links[0]["relationship"] == "supports"
    assert any(event.type == "SchemaValidationPassed" for event in events.events)


def test_kernel_rejects_invalid_schema_before_artifact_write(tmp_path: Path):
    output = artifact("artifacts/synthesis.json", "json", schema_id="synthesis_v1")
    stage = StageSpec(
        id="synthesis",
        title="Synthesis",
        kind="agent",
        purpose="Turn evidence into explicit claims.",
        outputs=(output,),
    )
    schemas = SchemaRegistry()
    schemas.register(
        "synthesis_v1",
        lambda content, artifact: ValidationResult(
            validator_id="schema:synthesis_v1",
            passed=isinstance(content, dict) and "claims" in content,
            message="claims field is required",
        ),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events, schema_registry=schemas)

    def handler(context):
        context.write_artifact(output, {"notes": []})

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"synthesis": handler})
    model = project_run(events.events)

    assert outcomes[0].status == "human_decision_required"
    assert outcomes[0].validation[0].validator_id == "schema:synthesis_v1"
    assert outcomes[0].validation[0].details["path"] == "artifacts/synthesis.json"
    assert not (tmp_path / "run_1" / "synthesis" / "artifacts" / "synthesis.json").exists()
    assert any(event.type == "SchemaValidationFailed" for event in events.events)
    assert model.stages["synthesis"].failure_reason == "artifact_schema_failed"
    assert model.stages["synthesis"].safe_next_actions == [
        "rewrite-stage",
        "rerun-stage",
        "revise-schema",
        "abort",
    ]


def test_kernel_requires_declared_artifact_schemas_to_be_registered(tmp_path: Path):
    output = artifact("artifacts/synthesis.json", "json", schema_id="synthesis_v1")
    stage = StageSpec(
        id="synthesis",
        title="Synthesis",
        kind="agent",
        purpose="Turn evidence into explicit claims.",
        outputs=(output,),
    )

    with pytest.raises(KeyError, match="Missing artifact schemas: synthesis_v1"):
        ResearchKernel().run(_run_spec(tmp_path, stage), {"synthesis": lambda context: None})


def test_kernel_events_project_to_canonical_run_read_model(tmp_path: Path):
    stage = StageSpec(
        id="review",
        title="Review",
        kind="agent",
        purpose="Review the paper.",
        outputs=(
            artifact("artifacts/review_report.md"),
            artifact("artifacts/review_notes.md", required=False, role="diagnostic"),
        ),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def handler(context):
        context.write_artifact(stage.outputs[0], "looks good")
        context.write_artifact(stage.outputs[1], "minor note")

    kernel.run(_run_spec(tmp_path, stage), {"review": handler})
    model = project_run(events.events)

    assert model.status == "completed"
    assert model.completed_stage_ids == ["review"]
    assert model.stage_list()[0].status == "completed"
    assert [artifact.path for artifact in model.stage_list()[0].deliverables] == [
        "artifacts/review_report.md",
    ]
    assert model.to_dict()["stages"][0]["deliverables"][0]["role"] == "deliverable"


def test_product_campaign_read_model_can_project_from_kernel_events(tmp_path: Path):
    stage = StageSpec(
        id="review",
        title="Review",
        kind="agent",
        purpose="Review the paper.",
        outputs=(
            artifact(
                "artifacts/review_report.md",
                schema_id="review_v1",
                claim_ids=("claim:ready",),
                evidence_links=(
                    EvidenceLink(
                        claim_id="claim:ready",
                        evidence_path="artifacts/final_paper.md",
                    ),
                ),
            ),
        ),
    )
    events = InMemoryEventBus()
    schemas = SchemaRegistry()
    schemas.register(
        "review_v1",
        lambda content, artifact: ValidationResult(
            validator_id="schema:review_v1",
            passed=bool(content),
            message="review accepted",
        ),
    )
    kernel = ResearchKernel(event_bus=events, schema_registry=schemas)

    def handler(context):
        context.write_artifact(stage.outputs[0], "ready")

    kernel.run(_run_spec(tmp_path, stage), {"review": handler})
    campaign_model = campaign_model_from_kernel_run(project_run(events.events))
    review = campaign_model.stages[0]
    report = review.required_artifacts[0]

    assert campaign_model.status == "completed"
    assert campaign_model.metadata["source"] == "kernel_events"
    assert review.status == "completed"
    assert report.source_role == "deliverable"
    assert report.metadata["schema_id"] == "review_v1"
    assert report.metadata["claim_ids"] == ["claim:ready"]


def test_kernel_native_literature_workflow_runs_without_langgraph(tmp_path: Path):
    graph = compile_kernel_graph(
        graph_id="literature-only",
        template="literature_only",
        budget=1,
    )
    events = InMemoryEventBus()
    kernel = build_kernel_native_research_kernel(graph)
    kernel.event_bus = events
    run = RunSpec(
        id="run_1",
        campaign_id="campaign_1",
        objective="Produce a literature-grounded research plan.",
        workspace=tmp_path / "run_1",
        graph=graph,
        budget=BudgetPolicy(max_usd=1, spend_allowed=True),
    )

    outcomes = kernel.run(run)
    all_outcomes = list(outcomes)
    while outcomes and outcomes[-1].status == "human_decision_required":
        decision = kernel.decision_queue.pending(run_id=run.id)[0]
        kernel.decide(decision.id, approved=True, actor="researcher")
        outcomes = kernel.resume(decision.id)
        all_outcomes.extend(outcomes)
    model = project_run(events.events)

    assert model.completed_stage_ids == list(KERNEL_NATIVE_STAGE_IDS)
    assert any(outcome.stage_id == "research_plan_writeup_agent" for outcome in all_outcomes)
    assert model.status == "completed"
    assert (tmp_path / "run_1" / "research_plan_writeup_agent" / "artifacts" / "research_plan.md").exists()


def test_kernel_native_scaffold_workflow_runs_without_langgraph(tmp_path: Path):
    graph = compile_kernel_graph(
        graph_id="scaffold",
        template="consortium_scaffold",
        budget=1,
    )
    events = InMemoryEventBus()
    kernel = build_kernel_native_research_kernel(graph)
    kernel.event_bus = events
    run = RunSpec(
        id="run_1",
        campaign_id="campaign_1",
        objective="Produce a complete kernel-native scaffold run.",
        workspace=tmp_path / "run_1",
        graph=graph,
        budget=BudgetPolicy(max_usd=1, spend_allowed=True),
    )

    outcomes = kernel.run(run)
    while outcomes and outcomes[-1].status == "human_decision_required":
        decision = kernel.decision_queue.pending(run_id=run.id)[0]
        kernel.decide(decision.id, approved=True, actor="researcher")
        outcomes = kernel.resume(decision.id)
    model = project_run(events.events)

    assert model.status == "completed"
    assert set(model.completed_stage_ids) == set(KERNEL_NATIVE_SCAFFOLD_STAGE_IDS) - {"followup_lit_review"}
    assert model.completed_stage_ids[-1] == "validation_gate"
    assert (tmp_path / "run_1" / "writeup_agent" / "artifacts" / "final_paper.md").exists()
    assert (tmp_path / "run_1" / "validation_gate" / "artifacts" / "final_validation.json").exists()


def test_kernel_events_project_human_decision_required(tmp_path: Path):
    stage = StageSpec(
        id="experiment",
        title="Experiment",
        kind="agent",
        purpose="Run the experiment.",
        outputs=(artifact("artifacts/experiment_results.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    kernel.run(_run_spec(tmp_path, stage), {"experiment": lambda context: None})
    model = project_run(events.events)
    experiment = model.stages["experiment"]

    assert model.status == "human_decision_required"
    assert experiment.status == "human_decision_required"
    assert experiment.failure_reason == "stage_validation_failed"
    assert experiment.safe_next_actions == ["rewrite-stage", "rerun-stage", "abort"]


def test_jsonl_event_bus_can_feed_same_read_model(tmp_path: Path):
    stage = StageSpec(
        id="proposal",
        title="Proposal",
        kind="agent",
        purpose="Write a proposal.",
        outputs=(artifact("artifacts/proposal.md"),),
    )
    event_path = tmp_path / "events.jsonl"
    kernel = ResearchKernel(event_bus=JsonlEventBus(event_path))

    def handler(context):
        context.write_artifact(stage.outputs[0], "proposal")

    kernel.run(_run_spec(tmp_path, stage), {"proposal": handler})
    model = project_run(read_jsonl_events(event_path))

    assert model.status == "completed"
    assert model.stages["proposal"].artifacts[0].path == "artifacts/proposal.md"


def test_kernel_enforces_pause_before_stage_without_running_handler(tmp_path: Path):
    stage = StageSpec(
        id="spend_gate",
        title="Spend Gate",
        kind="approval",
        purpose="Ask before spending money.",
        outputs=(artifact("artifacts/approval.json", "json"),),
        pause_before=True,
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)
    called = False

    def handler(context):
        nonlocal called
        called = True

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"spend_gate": handler})
    model = project_run(events.events)

    assert not called
    assert outcomes[0].status == "human_decision_required"
    assert len(kernel.decision_queue.pending(run_id="run_1")) == 1
    assert model.stages["spend_gate"].failure_reason == "pause_before_stage"
    assert model.stages["spend_gate"].pending_decision_id
    assert model.stages["spend_gate"].decision_status == "pending"
    assert model.stages["spend_gate"].safe_next_actions == ["approve", "rewrite-stage", "skip-stage", "abort"]


def test_kernel_decision_queue_approves_or_rejects_pending_decision(tmp_path: Path):
    stage = StageSpec(
        id="review_gate",
        title="Review Gate",
        kind="approval",
        purpose="Require a human decision.",
        outputs=(artifact("artifacts/review_gate.json", "json"),),
        pause_before=True,
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    kernel.run(_run_spec(tmp_path, stage), {"review_gate": lambda context: None})
    decision = kernel.decision_queue.pending(run_id="run_1")[0]
    decided = kernel.decide(decision.id, approved=False, actor="researcher")
    model = project_run(events.events)

    assert decided["status"] == "rejected"
    assert decided["actor"] == "researcher"
    assert model.stages["review_gate"].pending_decision_id == decision.id
    assert model.stages["review_gate"].decision_status == "rejected"
    assert events.events[-1].type == "ApprovalDecided"


def test_kernel_checkpoints_and_resumes_after_pause_before_approval(tmp_path: Path):
    stage = StageSpec(
        id="approval_gate",
        title="Approval Gate",
        kind="approval",
        purpose="Wait for approval before running.",
        outputs=(artifact("artifacts/approval.md"),),
        pause_before=True,
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)
    calls = {"count": 0}

    def handler(context):
        calls["count"] += 1
        context.write_artifact(stage.outputs[0], "approved")

    first = kernel.run(_run_spec(tmp_path, stage), {"approval_gate": handler})
    decision = kernel.decision_queue.pending(run_id="run_1")[0]
    kernel.decide(decision.id, approved=True, actor="researcher")
    resumed = kernel.resume(decision.id, {"approval_gate": handler})
    model = project_run(events.events)

    assert first[0].status == "human_decision_required"
    assert calls["count"] == 1
    assert resumed[0].status == "completed"
    assert any(event.type == "RunCheckpointed" for event in events.events)
    assert any(event.type == "RunResumed" for event in events.events)
    assert model.status == "completed"


def test_kernel_resume_after_pause_after_continues_with_next_stage(tmp_path: Path):
    plan = StageSpec(
        id="plan",
        title="Plan",
        kind="agent",
        purpose="Create plan.",
        outputs=(artifact("artifacts/plan.md"),),
        routes=(RouteSpec(target="writeup"),),
        pause_after=True,
    )
    writeup = StageSpec(
        id="writeup",
        title="Writeup",
        kind="agent",
        purpose="Continue after approval.",
        inputs=(InputSpec(path="artifacts/plan.md", source_stage_id="plan"),),
        outputs=(artifact("artifacts/writeup.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)
    calls = {"plan": 0, "writeup": 0}

    def plan_handler(context):
        calls["plan"] += 1
        context.write_artifact(plan.outputs[0], "plan")

    def writeup_handler(context):
        calls["writeup"] += 1
        context.write_artifact(writeup.outputs[0], "writeup")

    first = kernel.run(
        _run_spec(tmp_path, plan, writeup),
        {"plan": plan_handler, "writeup": writeup_handler},
    )
    decision = kernel.decision_queue.pending(run_id="run_1")[0]
    kernel.decide(decision.id, approved=True, actor="researcher")
    resumed = kernel.resume(decision.id, {"plan": plan_handler, "writeup": writeup_handler})
    model = project_run(events.events)

    assert first[0].status == "human_decision_required"
    assert [outcome.stage_id for outcome in resumed] == ["writeup"]
    assert calls == {"plan": 1, "writeup": 1}
    assert model.completed_stage_ids == ["plan", "writeup"]
    assert model.stages["plan"].status == "completed"
    assert model.stages["writeup"].status == "completed"


def test_kernel_resume_after_pause_after_schedules_branch_routes(tmp_path: Path):
    gate = StageSpec(
        id="gate",
        title="Gate",
        kind="approval",
        purpose="Approve before fanout.",
        outputs=(artifact("artifacts/gate.json", "json"),),
        routes=(
            RouteSpec(target="left", kind="branch", condition="approved"),
            RouteSpec(target="right", kind="branch", condition="approved"),
        ),
        pause_after=True,
    )
    left = StageSpec(
        id="left",
        title="Left",
        kind="agent",
        purpose="Left branch.",
        inputs=(InputSpec(path="artifacts/gate.json", source_stage_id="gate"),),
        outputs=(artifact("artifacts/left.md"),),
    )
    right = StageSpec(
        id="right",
        title="Right",
        kind="agent",
        purpose="Right branch.",
        inputs=(InputSpec(path="artifacts/gate.json", source_stage_id="gate"),),
        outputs=(artifact("artifacts/right.md"),),
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def gate_handler(context):
        context.write_artifact(gate.outputs[0], {"approved": True})
        return {"route_condition": "approved"}

    def branch_handler(context):
        context.write_artifact(context.stage.outputs[0], context.stage.id)

    kernel.run(
        _run_spec(tmp_path, gate, left, right),
        {"gate": gate_handler, "left": branch_handler, "right": branch_handler},
    )
    decision = kernel.decision_queue.pending(run_id="run_1")[0]
    kernel.decide(decision.id, approved=True, actor="researcher")
    resumed = kernel.resume(
        decision.id,
        {"gate": gate_handler, "left": branch_handler, "right": branch_handler},
    )

    assert [outcome.stage_id for outcome in resumed] == ["left", "right"]
    assert all(outcome.status == "completed" for outcome in resumed)


def test_kernel_resume_requires_approved_decision(tmp_path: Path):
    stage = StageSpec(
        id="approval_gate",
        title="Approval Gate",
        kind="approval",
        purpose="Wait for approval before running.",
        outputs=(artifact("artifacts/approval.md"),),
        pause_before=True,
    )
    kernel = ResearchKernel()

    kernel.run(_run_spec(tmp_path, stage), {"approval_gate": lambda context: None})
    decision = kernel.decision_queue.pending(run_id="run_1")[0]

    with pytest.raises(ValueError, match="Decision must be approved before resume"):
        kernel.resume(decision.id, {"approval_gate": lambda context: None})


def test_kernel_enforces_pause_after_validated_stage(tmp_path: Path):
    stage = StageSpec(
        id="plan",
        title="Plan",
        kind="agent",
        purpose="Produce an execution plan.",
        outputs=(artifact("artifacts/plan.md"),),
        pause_after=True,
    )
    events = InMemoryEventBus()
    kernel = ResearchKernel(event_bus=events)

    def handler(context):
        context.write_artifact(stage.outputs[0], "plan")

    outcomes = kernel.run(_run_spec(tmp_path, stage), {"plan": handler})
    model = project_run(events.events)

    assert outcomes[0].status == "human_decision_required"
    assert any(event.type == "ValidationPassed" for event in events.events)
    assert not any(event.type == "StageCompleted" for event in events.events)
    assert model.stages["plan"].failure_reason == "pause_after_stage"
    assert model.stages["plan"].artifacts[0].path == "artifacts/plan.md"
