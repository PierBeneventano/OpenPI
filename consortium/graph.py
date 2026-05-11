"""
LangGraph research pipeline — directly wired multi-phase workflow.

This graph replaces the manager hub-and-spoke loop with:
1. Discovery: ideation -> literature review -> research planner
2. Parallel execution: theory and experiment tracks in parallel
3. Synthesis loop: merge -> synthesis literature review -> results analysis
4. Paper production: resource preparation -> writeup -> proofreading -> reviewer
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

from .utils import resolve_or_model
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from .agents import (
    build_experiment_design_node,
    build_experiment_literature_node,
    build_experiment_transcription_node,
    build_experiment_verification_node,
    build_experimentation_node,
    build_literature_review_node,
    build_math_empirical_verifier_node,
    build_math_literature_node,
    build_math_proposer_node,
    build_math_prover_node,
    build_math_rigorous_verifier_node,
    build_proof_transcription_node,
    build_proofreading_node,
    build_resource_preparation_node,
    build_results_analysis_node,
    build_reviewer_node,
    build_track_merge_node,
    build_writeup_node,
    build_brainstorm_node,
    build_formalize_goals_node,
    build_formalize_results_node,
    build_research_plan_writeup_node,
)
from .milestone_report import (
    generate_milestone_report,
    wait_for_human_response,
)
from .paper_contract import (
    COPYEDIT_REPORT_PDF,
    COPYEDIT_REPORT_TEX,
    PAPER_CONTRACT_PATH,
    REVIEW_REPORT_PDF,
    REVIEW_REPORT_TEX,
    REVIEW_VERDICT_JSON,
    missing_writeup_artifacts,
    validate_required_terms,
    write_paper_contract,
)
from .pdf_summary import with_pdf_summary
from .run_status import write_run_status
from .state import ResearchState
from .workflow_utils import (
    classify_review_fixes,
    followup_decision_requires_loop,
    run_intermediate_validation,
    run_validation_gates,
    safe_int,
)
from msc_sdk.stage_runtime import StageRunContext, materialize_stage_outputs

MATH_PIPELINE_STAGES = [
    "math_literature_agent",
    "math_proposer_agent",
    "math_prover_agent",
    "math_rigorous_verifier_agent",
    "math_empirical_verifier_agent",
    "proof_transcription_agent",
]

EXPERIMENT_PIPELINE_STAGES = [
    "experiment_literature_agent",
    "experiment_design_agent",
    "experimentation_agent",
    "experiment_verification_agent",
    "experiment_transcription_agent",
]

# ---------------------------------------------------------------------------
# V2 pipeline stage rosters
# ---------------------------------------------------------------------------

V2_PRE_TRACK_STAGES = [
    "persona_council",
    "literature_review_agent",
    "brainstorm_agent",
    "formalize_goals_entry",
    "formalize_goals_agent",
    "research_plan_writeup_agent",
]

V2_POST_TRACK_STAGES = [
    "formalize_results_agent",
    "resource_preparation_agent",
    "writeup_agent",
    "proofreading_agent",
    "reviewer_agent",
]

_BRAINSTORM_REQUIRED_MD_SECTIONS = (
    "Executive Summary",
    "Per-Hypothesis Approach Menu",
    "Recommended Priority Ordering",
    "Open Questions and Decision Points",
)

_BRAINSTORM_REQUIRED_APPROACH_FIELDS = (
    "id",
    "title",
    "type",
    "hypothesis_ids",
    "priority_rank",
)


def build_pipeline_stages_v2(enable_math_agents: bool) -> list[str]:
    stages = list(V2_PRE_TRACK_STAGES)
    if enable_math_agents:
        stages.extend(MATH_PIPELINE_STAGES)
    stages.extend(EXPERIMENT_PIPELINE_STAGES)
    stages.extend(V2_POST_TRACK_STAGES)
    return stages


def _emit_campaign_node_status(workspace_dir: str, node_id: str, status: str, extra: dict | None = None) -> None:
    """Best-effort bridge from the historical runner into campaign events."""
    campaign_id = os.getenv("MSC_CAMPAIGN_ID")
    campaign_root = os.getenv("MSC_CAMPAIGN_ROOT") or os.getenv("CONSORTIUM_PROJECT_ROOT")
    if not campaign_id or not campaign_root:
        return
    try:
        from msc_sdk.campaign_store import CampaignStore

        CampaignStore(campaign_root).update_node_status(
            campaign_id,
            node_id,
            status,
            actor="runner",
            payload={
                "workspace_dir": workspace_dir,
                "run_id": os.getenv("MSC_CAMPAIGN_RUN_ID"),
                **(extra or {}),
            },
        )
    except Exception:
        logger.debug("Failed to emit campaign node status for %s", node_id, exc_info=True)


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _format_track_task(state: dict, track_name: str, questions: list[str]) -> str:
    cycle = safe_int(state.get("research_cycle", 0), 0)
    if not questions:
        return f"No {track_name} questions were identified for this cycle."

    question_lines = "\n".join(f"- {question}" for question in questions)
    base = (
        f"Research cycle: {cycle}\n"
        f"Execute the {track_name} track for the current research plan.\n"
        f"Questions assigned to this track:\n{question_lines}\n\n"
        "Read the latest planning artifacts from `paper_workspace/`, "
        "produce the mandatory artifacts for your track, and ground all work "
        "in workspace evidence and cited literature."
    )

    # Inject structured goal context for the theory track
    if track_name == "theory":
        try:
            research_goals = state.get("research_goals") or {}
            theory_goals = [
                g for g in research_goals.get("goals", [])
                if g.get("track") in ("theory", "both")
            ]
            if theory_goals:
                goal_lines = []
                for g in theory_goals:
                    reframed = g.get("novelty_reframed", False)
                    reframe_note = (
                        f" [REFRAMED from {g.get('reframed_from_claim', '?')} — "
                        f"strategy: {g.get('reframing_strategy', 'N/A')}]"
                        if reframed else ""
                    )
                    sc = g.get("success_criteria") or {}
                    goal_lines.append(
                        f"  Goal {g.get('id', '?')}{reframe_note}:\n"
                        f"    Hypothesis: {g.get('hypothesis_id', 'N/A')}\n"
                        f"    Description: {str(g.get('description', ''))[:300]}\n"
                        f"    Strong success: {sc.get('strong', 'N/A')}\n"
                        f"    Minimum viable: {sc.get('minimum_viable', 'N/A')}"
                    )
                base += (
                    "\n\nTHEORY GOAL CONTEXT (from research_goals.json):\n"
                    + "\n".join(goal_lines)
                )
        except Exception:
            pass  # silently skip if goals unavailable

    # Inject cross-track dependency context for the experiment track
    if track_name == "experiment":
        td = state.get("track_decomposition") or {}
        deps = td.get("cross_track_dependencies") or []
        if deps:
            dep_lines = []
            for dep in deps:
                eq_idx = dep.get("empirical_question_index", "?")
                tq_idx = dep.get("depends_on_theory_question_index", "?")
                dtype = dep.get("dependency_type", "assumes_result")
                fallback = dep.get(
                    "fallback_if_theory_fails",
                    "Proceed with relaxed assumptions."
                )
                dep_lines.append(
                    f"  - Empirical Q{eq_idx} depends on Theory Q{tq_idx} "
                    f"({dtype}). If theory result unavailable: {fallback}"
                )
            base += (
                "\n\nCROSS-TRACK DEPENDENCIES (theory results may not be "
                "available yet):\n" + "\n".join(dep_lines)
            )

    return base


def track_router(state: ResearchState) -> list[Send]:
    """Fan out to the theory and/or experiment tracks based on track decomposition."""
    track_decomposition = state.get("track_decomposition") or {}
    theory_questions = list(track_decomposition.get("theory_questions") or [])
    empirical_questions = list(track_decomposition.get("empirical_questions") or [])
    recommended_track = str(track_decomposition.get("recommended_track", "")).strip().lower()

    sends: list[Send] = []
    theory_allowed = state.get("math_enabled", False) and recommended_track in {"", "both", "theory"}
    experiment_allowed = recommended_track in {"", "both", "empirical"}

    if theory_allowed and theory_questions:
        sends.append(
            Send(
                "theory_track",
                {
                    **state,
                    "agent_task": _format_track_task(state, "theory", theory_questions),
                    "theory_track_status": "in_progress",
                },
            )
        )

    if experiment_allowed and empirical_questions:
        sends.append(
            Send(
                "experiment_track",
                {
                    **state,
                    "agent_task": _format_track_task(state, "experiment", empirical_questions),
                    "experiment_track_status": "in_progress",
                },
            )
        )

    if not sends:
        sends.append(
            Send(
                "track_merge",
                {
                    **state,
                    "agent_task": (
                        "No theory or empirical execution track was selected. "
                        "Proceed directly to synthesis and results analysis."
                    ),
                },
            )
        )
    return sends


def build_track_decomposition_gate_node(
    workspace_dir: str,
    enable_math_agents: bool = False,
) -> Any:
    """
    Validate track_decomposition.json before milestone_goals and track_router().

    Catches:
    - Empty theory_questions when theory track is requested
    - Empty empirical_questions when experiment track is requested
    - recommended_track requesting theory when enable_math_agents=False
    - Missing or malformed track_decomposition.json
    """
    def track_decomposition_gate_node(state: dict) -> dict:
        paper_ws = os.path.join(workspace_dir, "paper_workspace")
        td_path = os.path.join(paper_ws, "track_decomposition.json")

        default_td = {
            "theory_questions": [],
            "empirical_questions": [
                "Execute the research plan as specified in research_goals.json."
            ],
            "recommended_track": "empirical",
            "rationale": "",
        }

        if not os.path.exists(td_path):
            logger.warning(
                "[track_decomposition_gate] track_decomposition.json "
                "missing — defaulting to empirical only."
            )
            default_td["rationale"] = (
                "track_decomposition.json was missing; defaulted to empirical track."
            )
            return {"track_decomposition": default_td, "agent_task": None}

        try:
            with open(td_path) as f:
                td = json.load(f)
        except Exception as e:
            logger.error(
                "[track_decomposition_gate] Failed to parse "
                "track_decomposition.json: %s", e
            )
            default_td["rationale"] = (
                f"track_decomposition.json parse failed: {e}"
            )
            return {"track_decomposition": default_td, "agent_task": None}

        recommended = (
            td.get("recommended_track", "empirical").strip().lower()
        )
        theory_q = td.get("theory_questions") or []
        empirical_q = td.get("empirical_questions") or []
        issues: list[str] = []

        # Enforce math agents constraint
        if not enable_math_agents and recommended in ("theory", "both"):
            issues.append(
                f"recommended_track='{recommended}' but "
                "enable_math_agents=False. Downgrading to 'empirical'."
            )
            recommended = "empirical"
            td["recommended_track"] = "empirical"

        # Enforce non-empty questions for active tracks
        goals_path = os.path.join(paper_ws, "research_goals.json")

        if recommended in ("theory", "both") and not theory_q:
            issues.append(
                "theory_questions is empty despite theory track being requested."
            )
            try:
                with open(goals_path) as f:
                    goals_data = json.load(f)
                theory_q = [
                    f"Address goal {g['id']}: {g['description']}"
                    for g in goals_data.get("goals", [])
                    if g.get("track") in ("theory", "both")
                ]
                td["theory_questions"] = theory_q
                issues.append(
                    f"Recovered {len(theory_q)} theory questions "
                    "from research_goals.json."
                )
            except Exception:
                recommended = "empirical"
                td["recommended_track"] = "empirical"
                issues.append(
                    "Could not recover theory questions — "
                    "downgrading to empirical."
                )

        if recommended in ("empirical", "both") and not empirical_q:
            issues.append(
                "empirical_questions is empty despite empirical track "
                "being requested."
            )
            try:
                with open(goals_path) as f:
                    goals_data = json.load(f)
                empirical_q = [
                    f"Address goal {g['id']}: {g['description']}"
                    for g in goals_data.get("goals", [])
                    if g.get("track") in ("experiment", "both")
                ]
                td["empirical_questions"] = empirical_q
                issues.append(
                    f"Recovered {len(empirical_q)} empirical questions "
                    "from research_goals.json."
                )
            except Exception:
                issues.append("Could not recover empirical questions.")

        if issues:
            logger.info(
                "[track_decomposition_gate] Issues corrected: %s", issues
            )

        # Write corrected version back to disk (atomic: write tmp then rename)
        import fcntl
        td_tmp = td_path + ".tmp"
        try:
            with open(td_tmp, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(td, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f, fcntl.LOCK_UN)
            os.replace(td_tmp, td_path)
        except OSError as e:
            logger.warning(
                "[track_decomposition_gate] atomic write failed: %s", e
            )
            # Fallback to direct write
            with open(td_path, "w") as f:
                json.dump(td, f, indent=2)

        return {"track_decomposition": td, "agent_task": None}

    track_decomposition_gate_node.__name__ = "track_decomposition_gate"
    return track_decomposition_gate_node


def build_formalize_goals_entry_node(workspace_dir: str) -> Any:
    """Inject a first-entry agent_task for formalize_goals_agent.

    On the brainstorm → formalize_goals path, agent_task is typically None.
    This node sets a clear task prompt with brainstorm data quality signal.
    On verify_completion re-entry, this node is bypassed because
    verify_completion routes directly to formalize_goals_agent.
    """

    def formalize_goals_entry_node(state: dict) -> dict:
        brainstorm_md_path, brainstorm_json_path, md_label, json_label = _brainstorm_artifact_paths(workspace_dir)
        brainstorm_json_exists = os.path.exists(brainstorm_json_path)
        brainstorm_md_exists = os.path.exists(brainstorm_md_path)

        if brainstorm_json_exists and brainstorm_md_exists:
            task = (
                "BEGIN GOAL FORMALIZATION.\n\n"
                "The brainstorm is complete. Read the canonical campaign artifacts "
                f"`{json_label}` and `{md_label}`. Use them with the research "
                "proposal artifact and formalize the research goals into the "
                "stage's required contract artifacts. "
                "Run all programmatic validations before returning."
            )
            ctx = StageRunContext.from_env("formalize_goals_entry")
            if ctx is not None:
                ctx.write_required(
                    "artifacts/formalize_goals_entry.json",
                    {
                        "brainstorm_md": md_label,
                        "approach_menu": json_label,
                        "ready": True,
                    },
                    kind="json",
                    metadata={"run_id": ctx.run_id},
                )
        else:
            missing = []
            if not brainstorm_json_exists:
                missing.append(json_label)
            if not brainstorm_md_exists:
                missing.append(md_label)
            return {
                "critical_failure": (
                    "formalize_goals_agent requires brainstorm artifacts in strict "
                    f"pipeline mode, but they are missing: {', '.join(missing)}"
                ),
                "agent_task": None,
            }

        return {"agent_task": task}

    formalize_goals_entry_node.__name__ = "formalize_goals_entry"
    return formalize_goals_entry_node


def _brainstorm_artifact_paths(workspace_dir: str) -> tuple[str, str, str, str]:
    ctx = StageRunContext.from_env("brainstorm_agent")
    if ctx is not None:
        md = str(ctx.artifact_path("artifacts/brainstorm.md"))
        menu = str(ctx.artifact_path("artifacts/approach_menu.json"))
        return md, menu, "artifacts/brainstorm.md", "artifacts/approach_menu.json"
    paper_ws = os.path.join(workspace_dir, "paper_workspace")
    return (
        os.path.join(paper_ws, "brainstorm.md"),
        os.path.join(paper_ws, "brainstorm.json"),
        "paper_workspace/brainstorm.md",
        "paper_workspace/brainstorm.json",
    )


def _validate_brainstorm_artifacts(workspace_dir: str) -> list[str]:
    errors: list[str] = []
    brainstorm_md_path, brainstorm_json_path, md_label, json_label = _brainstorm_artifact_paths(workspace_dir)

    if not os.path.exists(brainstorm_md_path):
        errors.append(md_label)
    else:
        try:
            brainstorm_md = open(brainstorm_md_path, "r", encoding="utf-8").read()
        except Exception as exc:
            brainstorm_md = ""
            errors.append(f"{md_label} unreadable: {exc}")
        if not brainstorm_md.strip():
            errors.append(f"{md_label} is empty")
        for section in _BRAINSTORM_REQUIRED_MD_SECTIONS:
            if section not in brainstorm_md:
                errors.append(
                    f"{md_label} missing required section "
                    f"'{section}'"
                )

    payload: Optional[dict] = None
    if not os.path.exists(brainstorm_json_path):
        errors.append(json_label)
    else:
        try:
            with open(brainstorm_json_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            errors.append(f"{json_label} is not valid JSON: {exc}")

    if isinstance(payload, dict):
        hypotheses = payload.get("hypotheses_addressed")
        if not isinstance(hypotheses, list) or not any(
            isinstance(item, str) and item.strip() for item in hypotheses
        ):
            errors.append(
                f"{json_label} missing non-empty hypotheses_addressed list"
            )

        approaches = payload.get("approaches")
        if not isinstance(approaches, list) or not approaches:
            errors.append(
                f"{json_label} missing non-empty approaches list"
            )
        else:
            for idx, approach in enumerate(approaches, start=1):
                if not isinstance(approach, dict):
                    errors.append(
                        f"{json_label} approach entry "
                        f"#{idx} must be a JSON object"
                    )
                    continue
                label = approach.get("id") or f"approach #{idx}"
                for field in _BRAINSTORM_REQUIRED_APPROACH_FIELDS:
                    value = approach.get(field)
                    if field == "hypothesis_ids":
                        if not isinstance(value, list) or not any(
                            isinstance(item, str) and item.strip() for item in value
                        ):
                            errors.append(
                                f"{json_label} "
                                f"{label} missing non-empty '{field}'"
                            )
                    elif field == "priority_rank":
                        if value is None or (isinstance(value, str) and not value.strip()):
                            errors.append(
                                f"{json_label} "
                                f"{label} missing '{field}'"
                            )
                    elif not isinstance(value, str) or not value.strip():
                        errors.append(
                            f"{json_label} "
                            f"{label} missing non-empty '{field}'"
                        )

    return errors


def build_brainstorm_artifact_gate_node(
    workspace_dir: str, max_retries: int = 2
) -> Any:
    def brainstorm_artifact_gate_node(state: dict) -> dict:
        errors = _validate_brainstorm_artifacts(workspace_dir)
        gate_ctx = StageRunContext.from_env("brainstorm_artifact_gate")
        validation_results = {
            **state.get("validation_results", {}),
            "brainstorm_artifact_gate": {
                "is_valid": len(errors) == 0,
                "errors": errors,
            },
        }
        retries = safe_int(state.get("brainstorm_artifact_retries", 0), 0)

        if errors:
            formatted = "\n".join(f"- {error}" for error in errors)
            if retries >= max_retries:
                return {
                    "validation_results": validation_results,
                    "brainstorm_artifact_retries": retries,
                    "current_agent": None,
                    "agent_task": None,
                    "critical_failure": (
                        "BRAINSTORM ARTIFACT GATE FAILURE.\n\n"
                        "The brainstorm stage did not produce canonical brainstorm artifacts "
                        f"after {max_retries} repair attempt(s).\n"
                        "Stage summaries under `stage_summaries/` are non-canonical and do "
                        "not satisfy brainstorm completion.\n"
                        f"{formatted}"
                    ),
                }

            return {
                "validation_results": validation_results,
                "brainstorm_artifact_retries": retries + 1,
                "current_agent": "brainstorm_agent",
                "agent_task": (
                    "BRAINSTORM ARTIFACT GATE FAILURE.\n\n"
                    "Repair only the canonical brainstorm artifacts for this campaign run.\n"
                    "Do not restart the whole pipeline, do not hand off to formalization yet, "
                    "and do not treat files under `stage_summaries/` as completion evidence.\n"
                    "Before you finish, verify that:\n"
                    "- `artifacts/brainstorm.md` exists and includes the required sections\n"
                    "- `artifacts/approach_menu.json` exists and parses as valid JSON\n"
                    "- the JSON has a non-empty `hypotheses_addressed` list and non-empty "
                    "`approaches` list\n"
                    "- every approach has `id`, `title`, `type`, `hypothesis_ids`, and "
                    "`priority_rank`\n\n"
                    f"Problems to fix (attempt {retries + 1}/{max_retries}):\n{formatted}"
                ),
            }

        validation_results.pop("brainstorm_artifact_gate", None)
        if gate_ctx is not None:
            gate_ctx.write_required(
                "artifacts/brainstorm_gate_decision.json",
                {"is_valid": True, "errors": [], "next": "formalize_goals_entry"},
                kind="json",
                metadata={"run_id": gate_ctx.run_id},
            )
        return {
            "validation_results": validation_results,
            "brainstorm_artifact_retries": 0,
            "current_agent": "formalize_goals_entry",
            "agent_task": None,
        }

    brainstorm_artifact_gate_node.__name__ = "brainstorm_artifact_gate"
    return brainstorm_artifact_gate_node


def build_proofreading_entry_node(workspace_dir: str) -> Any:
    """Inject a targeted agent_task for proofreading_agent.

    On first entry, sets a generic copy-edit task.  On re-entry after
    validation_gate failure, injects the specific validation errors so the
    proofreader can run a targeted pass rather than a full re-audit.
    """

    def proofreading_entry_node(state: dict) -> dict:
        validation_results = state.get("validation_results", {})
        _PROOFREADING_GATES = {"paper_quality", "artifact_gate"}
        failed_gates = {
            gate: result
            for gate, result in validation_results.items()
            if not result.get("is_valid") and gate in _PROOFREADING_GATES
        }
        paper_ws = os.path.join(workspace_dir, "paper_workspace")
        has_prior_report = os.path.exists(
            os.path.join(paper_ws, "copyedit_report.tex")
        )

        if failed_gates:
            error_lines = [
                f"- {g}: {'; '.join(r.get('errors', []))}"
                for g, r in failed_gates.items()
            ]
            task = (
                "TARGETED COPY-EDIT PASS (re-entry after validation failure).\n\n"
                "Prior validation failures:\n" + "\n".join(error_lines) + "\n\n"
                + (
                    "A prior copyedit_report.tex exists — read it first to "
                    "avoid re-auditing fixed issues.\n"
                    if has_prior_report
                    else ""
                )
                + "Focus your edits on addressing these specific failures."
            )
        else:
            task = (
                "BEGIN COPY-EDIT PASS.\n\n"
                "Perform a full proofreading and copy-editing pass on the paper."
            )

        cleared = {
            k: v for k, v in validation_results.items()
            if k not in _PROOFREADING_GATES
        }
        return {"agent_task": task, "validation_results": cleared}

    proofreading_entry_node.__name__ = "proofreading_entry"
    return proofreading_entry_node


def _read_existing_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def _paper_bundle_text(workspace_dir: str) -> str:
    paper_ws = os.path.join(workspace_dir, "paper_workspace")
    parts = [_read_existing_text(os.path.join(workspace_dir, PAPER_CONTRACT_PATH))]
    for name in [
        "abstract.tex",
        "introduction.tex",
        "methods.tex",
        "results.tex",
        "discussion.tex",
        "conclusion.tex",
        "final_paper.tex",
    ]:
        parts.append(_read_existing_text(os.path.join(paper_ws, name)))
    return "\n\n".join(part for part in parts if part)


def build_paper_contract_node(workspace_dir: str) -> Any:
    def paper_contract_node(state: dict) -> dict:
        path = write_paper_contract(workspace_dir, state)
        logger.info("[paper_contract] Wrote canonical contract: %s", path)
        return {
            "artifacts": {
                **state.get("artifacts", {}),
                "paper_contract": path,
            },
        }

    paper_contract_node.__name__ = "paper_contract_builder"
    return paper_contract_node


def build_writeup_artifact_gate_node(workspace_dir: str) -> Any:
    def writeup_artifact_gate_node(state: dict) -> dict:
        require_pdf = bool(state.get("require_pdf") or state.get("enforce_editorial_artifacts"))
        errors = list(missing_writeup_artifacts(workspace_dir, require_pdf=require_pdf))

        contract = None
        contract_path = os.path.join(workspace_dir, PAPER_CONTRACT_PATH)
        if os.path.exists(contract_path):
            try:
                with open(contract_path, "r", encoding="utf-8") as fh:
                    contract = json.load(fh)
            except Exception as exc:
                errors.append(f"{PAPER_CONTRACT_PATH}: unreadable ({exc})")
        else:
            errors.append(PAPER_CONTRACT_PATH)

        paper_text = _paper_bundle_text(workspace_dir)
        missing_terms = validate_required_terms(paper_text, contract)
        if missing_terms:
            errors.append(
                "paper contract terms missing from canonical paper sources: "
                + ", ".join(missing_terms)
            )

        validation_results = {
            **state.get("validation_results", {}),
            "writeup_artifact_gate": {
                "is_valid": len(errors) == 0,
                "errors": errors,
            },
        }

        if errors:
            task = (
                "WRITEUP ARTIFACT GATE FAILURE.\n\n"
                "Do not proceed to proofreading until every canonical paper artifact exists.\n"
                "Fix the following problems in paper_workspace/:\n"
                + "\n".join(f"- {error}" for error in errors)
            )
            return {
                "validation_results": validation_results,
                "current_agent": "writeup_agent",
                "agent_task": task,
            }

        validation_results.pop("writeup_artifact_gate", None)
        return {
            "validation_results": validation_results,
            "current_agent": "proofreading_entry",
            "agent_task": None,
        }

    writeup_artifact_gate_node.__name__ = "writeup_artifact_gate"
    return writeup_artifact_gate_node


def writeup_artifact_gate_router(state: ResearchState) -> str:
    return state.get("current_agent") or "proofreading_entry"


def build_proofread_gate_node(workspace_dir: str) -> Any:
    def proofread_gate_node(state: dict) -> dict:
        require_pdf = bool(state.get("require_pdf") or state.get("enforce_editorial_artifacts"))
        missing_writeup = list(missing_writeup_artifacts(workspace_dir, require_pdf=require_pdf))
        errors = list(missing_writeup)
        for rel_path in (COPYEDIT_REPORT_TEX, COPYEDIT_REPORT_PDF):
            if not os.path.exists(os.path.join(workspace_dir, rel_path)):
                errors.append(rel_path)

        validation_results = {
            **state.get("validation_results", {}),
            "proofread_gate": {
                "is_valid": len(errors) == 0,
                "errors": errors,
            },
        }

        if errors:
            next_agent = "writeup_agent" if missing_writeup else "proofreading_agent"
            return {
                "validation_results": validation_results,
                "current_agent": next_agent,
                "agent_task": (
                    "PROOFREAD GATE FAILURE.\n\n"
                    "You must preserve the canonical paper and produce the copy-edit artifacts "
                    "before review.\n"
                    + "\n".join(f"- {error}" for error in errors)
                ),
            }

        validation_results.pop("proofread_gate", None)
        return {
            "validation_results": validation_results,
            "current_agent": "reviewer_agent",
            "agent_task": None,
        }

    proofread_gate_node.__name__ = "proofread_gate"
    return proofread_gate_node


def proofread_gate_router(state: ResearchState) -> str:
    return state.get("current_agent") or "reviewer_agent"


def build_review_gate_node(workspace_dir: str) -> Any:
    def review_gate_node(state: dict) -> dict:
        errors: list[str] = []
        for rel_path in (REVIEW_REPORT_TEX, REVIEW_REPORT_PDF, REVIEW_VERDICT_JSON):
            if not os.path.exists(os.path.join(workspace_dir, rel_path)):
                errors.append(rel_path)

        verdict_path = os.path.join(workspace_dir, REVIEW_VERDICT_JSON)
        if os.path.exists(verdict_path):
            try:
                with open(verdict_path, "r", encoding="utf-8") as fh:
                    verdict_payload = json.load(fh)
                if not isinstance(verdict_payload, dict):
                    errors.append("paper_workspace/review_verdict.json must be a JSON object")
                elif "overall_score" not in verdict_payload:
                    errors.append("paper_workspace/review_verdict.json missing overall_score")
            except Exception as exc:
                errors.append(f"failed to parse paper_workspace/review_verdict.json: {exc}")

        validation_results = {
            **state.get("validation_results", {}),
            "review_gate": {
                "is_valid": len(errors) == 0,
                "errors": errors,
            },
        }

        if errors:
            return {
                "validation_results": validation_results,
                "current_agent": "reviewer_agent",
                "agent_task": (
                    "REVIEW GATE FAILURE.\n\n"
                    "Produce the canonical reviewer artifacts before milestone review.\n"
                    + "\n".join(f"- {error}" for error in errors)
                ),
            }

        validation_results.pop("review_gate", None)
        return {
            "validation_results": validation_results,
            "current_agent": "milestone_review",
            "agent_task": None,
        }

    review_gate_node.__name__ = "review_gate"
    return review_gate_node


def review_gate_router(state: ResearchState) -> str:
    return state.get("current_agent") or "milestone_review"


def _formalize_results_state_mapper(inner_node: Callable) -> Callable:
    """Copy agent output into the top-level formalized_results state key."""
    def wrapped(state: dict) -> dict:
        result = inner_node(state)
        agent_output = (result or {}).get("agent_outputs", {}).get("formalize_results_agent")
        if agent_output is not None:
            result["formalized_results"] = agent_output
        else:
            import logging
            logging.getLogger(__name__).warning(
                "formalize_results_agent produced no output — "
                "writing sentinel to formalized_results"
            )
            result["formalized_results"] = "[formalize_results_agent produced no output]"
        return result
    wrapped.__name__ = getattr(inner_node, "__name__", "formalize_results_agent")
    return wrapped


def followup_router(state: ResearchState) -> str:
    # Routing decision was already made by followup_gate_node which sets current_agent
    # to "research_planner_agent" (loop) or "resource_preparation_agent" (continue).
    return state.get("current_agent") or "resource_preparation_agent"


def validation_router(state: ResearchState) -> str:
    if state.get("finished"):
        return END
    # Cap retries to prevent unbounded loops
    retry_count = safe_int(state.get("validation_retry_count", 0), 0)
    max_retries = max(1, safe_int(state.get("max_validation_retries", 3), 3))
    if retry_count >= max_retries:
        return END
    # Route based on review verdict fix type classification
    workspace = state.get("workspace_dir") or "."
    fix_type = classify_review_fixes(workspace)
    if fix_type == "experiment":
        return "experiment_track"
    if fix_type == "theory":
        return "theory_track"
    return "writeup_agent"


def build_followup_gate_node(workspace_dir: str) -> Any:
    def followup_gate_node(state: dict) -> dict:
        required, reason = followup_decision_requires_loop(workspace_dir)
        research_cycle = safe_int(state.get("research_cycle", 0), 0)
        max_cycles = max(0, safe_int(state.get("max_research_cycles", 3), 3))

        if required and research_cycle < max_cycles:
            return {
                "current_agent": "research_planner_agent",
                "research_cycle": research_cycle + 1,
                "followup_iteration": safe_int(state.get("followup_iteration", 0), 0) + 1,
                "finished": False,
                "agent_task": (
                    "Prepare a focused follow-up research plan based on "
                    f"results analysis. Reason: {reason}"
                ),
            }

        return {
            "current_agent": "resource_preparation_agent",
            "finished": False,
            "agent_task": None,
        }

    followup_gate_node.__name__ = "followup_gate"
    return followup_gate_node


def build_validation_gate_node() -> Any:
    def validation_gate_node(state: dict) -> dict:
        validation = run_validation_gates(state)
        retry_count = safe_int(state.get("validation_retry_count", 0), 0)
        max_retries = max(1, safe_int(state.get("max_validation_retries", 3), 3))

        if validation["gate_passed"]:
            return {
                "validation_results": validation["validation_results"],
                "finished": True,
                "agent_task": None,
            }

        # Force completion when retry cap is reached
        if retry_count >= max_retries:
            return {
                "validation_results": validation["validation_results"],
                "finished": True,
                "validation_retry_count": retry_count,
                "agent_task": None,
            }

        error_lines = [
            f"- {gate}: {'; '.join(result['errors'])}"
            for gate, result in validation["validation_results"].items()
            if not result.get("is_valid")
        ]
        # Only increment retry count when review_verdict gate has actually
        # run (i.e., the reviewer produced a verdict).  Artifact-missing
        # failures should not consume retry slots.
        has_review_verdict = "review_verdict" in validation["validation_results"]

        # In iterate mode, preserve revision context so retry agents can
        # reference the original feedback and persona council's plan.
        if state.get("iterate_mode"):
            feedback_path = state.get("iterate_feedback_path", "")
            revision_plan = state.get("research_proposal", "")
            task = (
                "ITERATE MODE REVISION — RETRY\n\n"
                "The previous revision attempt did not pass validation. "
                "Review the original feedback, the revision plan, and the specific "
                "failures below, then revise the paper to address ALL issues.\n\n"
                f"## Original Reviewer Feedback\nRead the full feedback at: {feedback_path}\n\n"
                f"## Revision Plan (from persona council)\n{revision_plan[:10_000]}\n\n"
                "## Validation Failures (this attempt)\n" + "\n".join(error_lines) + "\n\n"
                "Focus on the validation failures while keeping the revision plan's "
                "priorities in mind. Do not regress on previously fixed issues."
            )
        else:
            task = (
                "Revise the paper to satisfy validation gates before finalization.\n"
                "Validation failures:\n" + "\n".join(error_lines)
            )

        return {
            "validation_results": validation["validation_results"],
            "finished": False,
            "validation_retry_count": retry_count + (1 if has_review_verdict else 0),
            "agent_task": task,
        }

    validation_gate_node.__name__ = "validation_gate"
    return validation_gate_node


def build_novelty_gate_node(workspace_dir: str) -> Any:
    """Gate between ideation and literature review that checks novelty assessment."""

    def novelty_gate_node(state: dict) -> dict:
        assessment_path = os.path.join(
            workspace_dir, "paper_workspace", "novelty_assessment.json"
        )
        max_attempts = 3
        attempts = safe_int(state.get("novelty_check_attempts", 0), 0)

        if not os.path.exists(assessment_path):
            # No assessment file -- pass through (backward compat)
            return {"current_agent": "literature_review_agent", "agent_task": None}

        try:
            with open(assessment_path) as f:
                assessment = json.load(f)
        except Exception:
            return {"current_agent": "literature_review_agent", "agent_task": None}

        if assessment.get("novel", True):
            return {"current_agent": "literature_review_agent", "agent_task": None}

        if attempts >= max_attempts:
            # After max attempts, proceed anyway with a warning
            logger.warning(
                "novelty gate failed after %s attempts, "
                "proceeding to literature review.", max_attempts
            )
            return {"current_agent": "literature_review_agent", "agent_task": None}

        justification = assessment.get("novelty_justification", "N/A")
        closest = assessment.get("closest_existing_work", "N/A")
        return {
            "current_agent": "ideation_agent",
            "novelty_check_attempts": attempts + 1,
            "agent_task": (
                f"NOVELTY GATE REJECTION (attempt {attempts + 1}/{max_attempts}): "
                f"Your previous idea was assessed as NOT NOVEL.\n"
                f"Justification: {justification}\n"
                f"Closest existing work: {closest}\n\n"
                "Generate a substantially different idea that addresses a genuine "
                "gap in the literature. Delete the old novelty_assessment.json and "
                "run CheckIdeaNoveltyTool again on your new idea."
            ),
        }

    novelty_gate_node.__name__ = "novelty_gate"
    return novelty_gate_node


def build_milestone_gate_node(phase_name: str, workspace_dir: str) -> Any:
    """Build a milestone gate node that generates a report and optionally pauses.

    When ``enable_milestone_gates`` is True in state, the node blocks until a
    human responds via ``POST /milestone_response`` or the timeout expires.
    When disabled, it generates the PDF report but does not pause.
    """

    # Map milestone phase names to intermediate validation checkpoint names
    _PHASE_TO_CHECKPOINT = {
        "research_plan": None,           # no validation yet at planning stage
        "track_results": "post_merge",   # cross-track consistency
        "analysis": "post_analysis",     # early claim traceability
        "review": None,                  # final validation handles this
    }

    def milestone_gate_node(state: dict) -> dict:
        # Run intermediate validation if applicable for this phase
        checkpoint = _PHASE_TO_CHECKPOINT.get(phase_name)
        validation_update = {}
        if checkpoint:
            validation_update = run_intermediate_validation(state, checkpoint)

        budget_status = None
        budget_path = os.path.join(workspace_dir, "budget_state.json")
        if os.path.exists(budget_path):
            try:
                with open(budget_path) as f:
                    import json as _json
                    bd = _json.load(f)
                total = bd.get("total_usd", bd.get("total_cost_usd"))
                limit = bd.get("usd_limit")
                if total is not None and limit is not None:
                    budget_status = f"Budget: ${total:.2f} / ${limit:.2f} ({total / limit * 100:.0f}%)"
                elif total is not None:
                    budget_status = f"Budget spent: ${total:.2f}"
            except Exception:
                pass

        # Merge validation log into state before generating report
        # so the milestone report can display intermediate results
        merged_state = {**state, **validation_update}

        report_path = generate_milestone_report(
            phase=phase_name,
            state=merged_state,
            workspace_dir=workspace_dir,
            budget_status=budget_status,
        )

        update: dict = {"milestone_reports": [report_path] if report_path else []}
        update.update(validation_update)

        if state.get("enable_milestone_gates") and not state.get("autonomous_mode"):
            timeout = state.get("milestone_timeout", 3600)
            feedback = wait_for_human_response(timeout=timeout)
            if feedback:
                from datetime import datetime, timezone as _tz
                update["human_feedback_history"] = [{
                    "phase": phase_name,
                    "action": feedback.get("action", "approve"),
                    "feedback": feedback.get("feedback", ""),
                    "timestamp": datetime.now(_tz.utc).isoformat(),
                }]
                action = feedback.get("action", "approve")
                if action == "modify":
                    update["agent_task"] = feedback.get("feedback", "")
                elif action == "abort":
                    update["finished"] = True

        return update

    milestone_gate_node.__name__ = f"milestone_{phase_name}"
    return milestone_gate_node


def novelty_router(state: ResearchState) -> str:
    """Route based on novelty gate decision."""
    return state.get("current_agent") or "literature_review_agent"


def build_theory_track_subgraph(
    model: Any,
    workspace_dir: str,
    authorized_imports: Optional[List[str]] = None,
    counsel_models: Optional[List[Any]] = None,
    summary_model_id: Optional[str] = "claude-sonnet-4-6",
    model_registry: Optional[Any] = None,
    adversarial_verification: bool = False,
    theory_repair_max_attempts: int = 2,
):
    graph = StateGraph(ResearchState)
    counsel_kwargs = {"counsel_models": counsel_models} if counsel_models is not None else {}

    def _m(agent_name: str) -> Any:
        if model_registry is not None:
            return model_registry.get(agent_name)
        return model

    def _wrap(node, name):
        summarized = with_pdf_summary(node, name, workspace_dir, summary_model_id)

        def contract_materializing_node(state: dict) -> dict:
            result = summarized(state) or {}
            try:
                return materialize_stage_outputs(name, state, result)
            except Exception as exc:
                logger.exception("Failed to materialize contract artifacts for %s", name)
                return {
                    **result,
                    "critical_failure": f"{name} failed to write contract artifacts: {exc}",
                    "agent_task": None,
                }

        contract_materializing_node.__name__ = getattr(summarized, "__name__", name)
        return contract_materializing_node

    graph.add_node(
        "math_literature_agent",
        _wrap(build_math_literature_node(_m("math_literature_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "math_literature_agent"),
    )
    graph.add_node(
        "math_proposer_agent",
        _wrap(build_math_proposer_node(_m("math_proposer_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "math_proposer_agent"),
    )
    graph.add_node(
        "math_prover_agent",
        _wrap(build_math_prover_node(_m("math_prover_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "math_prover_agent"),
    )
    graph.add_node(
        "math_rigorous_verifier_agent",
        _wrap(build_math_rigorous_verifier_node(_m("math_rigorous_verifier_agent"), workspace_dir, authorized_imports, adversarial=adversarial_verification, **counsel_kwargs), "math_rigorous_verifier_agent"),
    )
    graph.add_node(
        "math_empirical_verifier_agent",
        _wrap(build_math_empirical_verifier_node(_m("math_empirical_verifier_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "math_empirical_verifier_agent"),
    )
    graph.add_node(
        "proof_transcription_agent",
        _wrap(build_proof_transcription_node(_m("proof_transcription_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "proof_transcription_agent"),
    )
    # -- Issue 3: goal tag validation gate (warn-only, no LLM) --
    def goal_tag_validation_gate(state: dict) -> dict:
        """Check that all must_accept claims have goal:<id> tags."""
        math_ws = os.path.join(workspace_dir, "math_workspace")
        cg_path = os.path.join(math_ws, "claim_graph.json")
        warnings = []
        try:
            with open(cg_path) as f:
                cg = json.load(f)
            for claim in cg.get("claims", []):
                if claim.get("must_accept") and claim.get("status") != "rejected":
                    tags = [str(t) for t in claim.get("tags", [])]
                    has_goal_tag = any(t.startswith("goal:") for t in tags)
                    if not has_goal_tag:
                        warnings.append(
                            f"- {claim.get('id')}: must_accept=true but no goal:<id> tag"
                        )
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # claim graph may not exist yet if proposer failed
        if warnings:
            warn_path = os.path.join(math_ws, "goal_tag_warnings.md")
            content = "# Goal Tag Warnings\n\nThe following must_accept claims are missing goal:<id> tags.\nThis may cause verify_completion to under-count goal progress.\n\n" + "\n".join(warnings) + "\n"
            os.makedirs(math_ws, exist_ok=True)
            with open(warn_path, "w") as f:
                f.write(content)
            logger.warning("[goal_tag_validation_gate] %s must_accept claims missing goal tags. See %s", len(warnings), warn_path)
        return {"agent_task": None}

    # -- Issue 6: human review gate (scan checks for human_review_needed) --
    def human_review_gate(state: dict) -> dict:
        """Scan checks/*.jsonl for human_review_needed flags and write summary."""
        math_ws = os.path.join(workspace_dir, "math_workspace")
        checks_dir = os.path.join(math_ws, "checks")
        flags = []
        if os.path.isdir(checks_dir):
            for fname in os.listdir(checks_dir):
                if fname.endswith(".jsonl"):
                    fpath = os.path.join(checks_dir, fname)
                    try:
                        with open(fpath) as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    entry = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                if entry.get("next_action") == "human_review_needed":
                                    claim_id = fname.replace(".jsonl", "")
                                    reason = entry.get("reason", entry.get("message", "tool-vs-manual conflict"))
                                    flags.append(f"- {claim_id}: {reason}")
                    except OSError:
                        continue
        if flags:
            flag_path = os.path.join(math_ws, "human_review_flags.md")
            content = "# Human Review Flags\n\nThe following claims have unresolved tool-vs-manual verification conflicts.\n\n" + "\n".join(flags) + "\n"
            with open(flag_path, "w") as f:
                f.write(content)
            logger.info("[human_review_gate] %s claims flagged for human review. See %s", len(flags), flag_path)
        return {"agent_task": None}

    # -- Issue 10: intra-track repair gate (configurable retries) --
    REPAIR_THRESHOLD = 0.7  # must_accept completion ratio below which we retry
    MAX_THEORY_REPAIRS = theory_repair_max_attempts

    def theory_track_repair_gate(state: dict) -> dict:
        """Check must_accept claim completion; route back to prover if below threshold."""
        repair_count = state.get("theory_repair_count", 0)
        math_ws = os.path.join(workspace_dir, "math_workspace")
        summary_path = os.path.join(workspace_dir, "paper_workspace", "theory_track_summary.json")

        # Try to read the structured summary
        must_accept_total = 0
        must_accept_ok = 0
        try:
            with open(summary_path) as f:
                summary = json.load(f)
            verified = set(summary.get("verified_numeric_claims", []))
            verified |= set(summary.get("verified_symbolic_claims", []))
            # Count must_accept claims from the claim graph
            cg_path = os.path.join(math_ws, "claim_graph.json")
            with open(cg_path) as f:
                cg = json.load(f)
            for claim in cg.get("claims", []):
                if claim.get("must_accept"):
                    must_accept_total += 1
                    if claim.get("status") in ("verified_symbolic", "verified_numeric", "accepted"):
                        must_accept_ok += 1
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            # Can't assess — don't retry
            return {"agent_task": None}

        ratio = must_accept_ok / must_accept_total if must_accept_total > 0 else 1.0
        if ratio < REPAIR_THRESHOLD and repair_count < MAX_THEORY_REPAIRS:
            logger.info("[theory_track_repair_gate] must_accept ratio %.2f < %s, "
                        "retry %s/%s — routing back to prover",
                        ratio, REPAIR_THRESHOLD, repair_count + 1, MAX_THEORY_REPAIRS)
            # Write a targeted repair task for the prover
            failed_claims = []
            try:
                with open(os.path.join(math_ws, "claim_graph.json")) as f:
                    cg = json.load(f)
                for claim in cg.get("claims", []):
                    if claim.get("must_accept") and claim.get("status") in ("proved_draft", "proposed"):
                        failed_claims.append(claim.get("id", "unknown"))
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            repair_task = (
                f"REPAIR PASS {repair_count + 1}: Focus on these blocked must_accept claims: "
                + ", ".join(failed_claims[:10])
                + ". Read their checks/*.jsonl audit logs for specific failure reasons and fix the proofs."
            )
            return {
                "agent_task": repair_task,
                "theory_repair_count": repair_count + 1,
            }
        if ratio < REPAIR_THRESHOLD:
            logger.warning("[theory_track_repair_gate] must_accept ratio %.2f still below threshold "
                          "after %s retries — proceeding to END", ratio, MAX_THEORY_REPAIRS)
        return {"agent_task": None}

    def theory_repair_router(state: dict) -> str:
        """Route to prover for retry or END."""
        task = state.get("agent_task")
        if task and task.startswith("REPAIR PASS"):
            return "math_prover_agent"
        return END

    graph.add_node("goal_tag_validation_gate", goal_tag_validation_gate)
    graph.add_node("human_review_gate", human_review_gate)
    graph.add_node("theory_track_repair_gate", theory_track_repair_gate)

    graph.set_entry_point("math_literature_agent")
    graph.add_edge("math_literature_agent", "math_proposer_agent")
    graph.add_edge("math_proposer_agent", "goal_tag_validation_gate")
    graph.add_edge("goal_tag_validation_gate", "math_prover_agent")
    graph.add_edge("math_prover_agent", "math_rigorous_verifier_agent")
    graph.add_edge("math_rigorous_verifier_agent", "human_review_gate")
    graph.add_edge("human_review_gate", "math_empirical_verifier_agent")
    graph.add_edge("math_empirical_verifier_agent", "proof_transcription_agent")
    graph.add_edge("proof_transcription_agent", "theory_track_repair_gate")
    graph.add_conditional_edges(
        "theory_track_repair_gate",
        theory_repair_router,
        {"math_prover_agent": "math_prover_agent", END: END},
    )
    return graph.compile()


def build_experiment_track_subgraph(
    model: Any,
    workspace_dir: str,
    authorized_imports: Optional[List[str]] = None,
    counsel_models: Optional[List[Any]] = None,
    summary_model_id: Optional[str] = "claude-sonnet-4-6",
    model_registry: Optional[Any] = None,
):
    graph = StateGraph(ResearchState)
    counsel_kwargs = {"counsel_models": counsel_models} if counsel_models is not None else {}

    def _m(agent_name: str) -> Any:
        if model_registry is not None:
            return model_registry.get(agent_name)
        return model

    def _wrap(node, name):
        summarized = with_pdf_summary(node, name, workspace_dir, summary_model_id)

        def contract_materializing_node(state: dict) -> dict:
            result = summarized(state) or {}
            try:
                return materialize_stage_outputs(name, state, result)
            except Exception as exc:
                logger.exception("Failed to materialize contract artifacts for %s", name)
                return {
                    **result,
                    "critical_failure": f"{name} failed to write contract artifacts: {exc}",
                    "agent_task": None,
                }

        contract_materializing_node.__name__ = getattr(summarized, "__name__", name)
        return contract_materializing_node

    graph.add_node(
        "experiment_literature_agent",
        _wrap(build_experiment_literature_node(_m("experiment_literature_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "experiment_literature_agent"),
    )
    graph.add_node(
        "experiment_design_agent",
        _wrap(build_experiment_design_node(_m("experiment_design_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "experiment_design_agent"),
    )
    graph.add_node(
        "experimentation_agent",
        _wrap(build_experimentation_node(_m("experimentation_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "experimentation_agent"),
    )
    graph.add_node(
        "experiment_verification_agent",
        _wrap(build_experiment_verification_node(_m("experiment_verification_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "experiment_verification_agent"),
    )
    graph.add_node(
        "experiment_transcription_agent",
        _wrap(build_experiment_transcription_node(_m("experiment_transcription_agent"), workspace_dir, authorized_imports, **counsel_kwargs), "experiment_transcription_agent"),
    )
    graph.set_entry_point("experiment_literature_agent")
    graph.add_edge("experiment_literature_agent", "experiment_design_agent")
    graph.add_edge("experiment_design_agent", "experimentation_agent")
    graph.add_edge("experimentation_agent", "experiment_verification_agent")
    graph.add_edge("experiment_verification_agent", "experiment_transcription_agent")
    graph.add_edge("experiment_transcription_agent", END)
    return graph.compile()


def build_track_subgraph_node(
    subgraph: Any,
    status_field: str,
    status_value: str = "completed",
    workspace_dir: Optional[str] = None,
) -> Any:
    def node(state: dict) -> dict:
        final_state = subgraph.invoke(state)
        result = {
            "agent_outputs": final_state.get("agent_outputs", {}),
            status_field: status_value,
        }
        # Forward structured track summaries if available on disk
        if workspace_dir:
            summary_path = os.path.join(workspace_dir, "paper_workspace", "theory_track_summary.json")
            if os.path.isfile(summary_path):
                try:
                    with open(summary_path) as f:
                        result["theory_track_summary"] = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
        return result

    node.__name__ = status_field.removesuffix("_status")
    return node


def build_noop_track_node(status_field: str) -> Any:
    def node(state: dict) -> dict:
        return {
            status_field: state.get(status_field),
        }

    node.__name__ = status_field.removesuffix("_status")
    return node


def build_synthesis_literature_node(
    model: Any,
    workspace_dir: str,
    authorized_imports: Optional[List[str]] = None,
    counsel_models: Optional[List[Any]] = None,
) -> Any:
    base_node = build_literature_review_node(
        model,
        workspace_dir,
        authorized_imports,
        **({"counsel_models": counsel_models} if counsel_models is not None else {}),
    )

    def synthesis_node(state: dict) -> dict:
        previous_output = state.get("agent_outputs", {}).get("literature_review_agent")
        result = base_node(state)
        outputs = dict(result.get("agent_outputs", {}))
        new_output = outputs.pop("literature_review_agent", previous_output)
        if previous_output is not None:
            outputs["literature_review_agent"] = previous_output
        outputs["synthesis_literature_review_agent"] = new_output
        return {
            **result,
            "agent_outputs": outputs,
        }

    synthesis_node.__name__ = "synthesis_literature_review_agent"
    return synthesis_node


# ---------------------------------------------------------------------------
# Pipeline — gate nodes and routers
# ---------------------------------------------------------------------------


# Lightweight mtime-based file cache for gate reads — avoids redundant I/O
# when multiple gates read the same JSON files within a single graph run.
_file_cache: dict[str, tuple[float, str]] = {}


def _read_file_safe(path: str, max_chars: int = 20000) -> str:
    """Read a file, returning empty string on failure. Uses mtime cache."""
    if not os.path.exists(path):
        return ""
    try:
        mtime = os.path.getmtime(path)
        cached = _file_cache.get(path)
        if cached is not None:
            cached_mtime, cached_content = cached
            if cached_mtime == mtime:
                return cached_content[:max_chars]
        with open(path, "r", encoding="utf-8") as f:
            content = f.read(max_chars)
        _file_cache[path] = (mtime, content)
        return content
    except Exception:
        return ""


def _build_brainstorm_novelty_directive(novelty_data: dict) -> str:
    """Build a structured novelty directive for the brainstorm agent.

    Extracts OPEN and PARTIAL claims from novelty_flags.json data and
    formats them into an actionable directive so the brainstorm agent
    starts with rich context rather than a single summary sentence.
    """
    lines = ["NOVELTY GATE PASSED — structured claim summary from literature review:\n"]

    claims = novelty_data.get("claims", [])
    open_claims = [c for c in claims if c.get("status") == "OPEN"]
    partial_claims = [c for c in claims if c.get("status") == "PARTIAL"]

    if open_claims:
        lines.append("CONFIRMED OPEN CLAIMS (prioritize these):")
        for c in open_claims:
            cid = c.get("claim_id", "?")
            text = c.get("claim_text", "")[:200]
            conf = c.get("confidence", "?")
            rec = c.get("recommendation", "?")
            lines.append(f"  [{cid}] {text} (confidence: {conf}, recommendation: {rec})")
        lines.append("")

    if partial_claims:
        lines.append("PARTIALLY RESOLVED CLAIMS (extend beyond existing partial results):")
        for c in partial_claims:
            cid = c.get("claim_id", "?")
            text = c.get("claim_text", "")[:200]
            conf = c.get("confidence", "?")
            rec = c.get("recommendation", "?")
            ev_count = len(c.get("evidence", []))
            lines.append(
                f"  [{cid}] {text} (confidence: {conf}, recommendation: {rec}, "
                f"{ev_count} evidence sources)"
            )
        lines.append("")

    overall = novelty_data.get("overall_novelty_assessment", "N/A")
    lines.append(f"OVERALL ASSESSMENT: {overall}")
    lines.append(
        "\nRead `paper_workspace/novelty_flags.json` for full evidence on each claim."
    )

    return "\n".join(lines)


def build_lit_review_gate_node(workspace_dir: str, max_attempts: int = 2) -> Any:
    """Gate after lit review: checks novelty flags then feasibility.

    Routes to:
      - ``persona_council`` if novelty-blocked or infeasible (retries remain)
      - ``brainstorm_agent`` if feasible or retries exhausted
    """

    def lit_review_gate_node(state: dict) -> dict:
        import re as _re
        attempts = safe_int(state.get("lit_review_attempts", 0), 0)
        gate_ctx = StageRunContext.from_env("lit_review_gate")
        lit_ctx = StageRunContext.from_env("literature_review_agent")

        if gate_ctx is not None and lit_ctx is not None:
            feasibility_path = lit_ctx.artifact_path("artifacts/lit_review_feasibility.json")
            feasible = True
            reason = "Contract-native literature review artifacts are present."
            if feasibility_path.exists():
                try:
                    feasibility = json.loads(feasibility_path.read_text(encoding="utf-8"))
                    feasible = bool(feasibility.get("feasible", True))
                    reason = str(feasibility.get("rationale") or feasibility.get("reason") or reason)
                except Exception as exc:
                    feasible = False
                    reason = f"failed to parse {feasibility_path.name}: {exc}"
            else:
                feasible = False
                reason = "missing artifacts/lit_review_feasibility.json"

            gate_ctx.write_required(
                "artifacts/lit_review_gate_decision.json",
                {
                    "feasible": feasible,
                    "reason": reason,
                    "next": "brainstorm_agent" if feasible else "persona_council",
                },
                kind="json",
                metadata={"run_id": gate_ctx.run_id},
            )
            if feasible:
                return {
                    "current_agent": "brainstorm_agent",
                    "lit_review_feasibility": {"feasible": True, "reason": reason},
                    "agent_task": None,
                }
            return {
                "current_agent": "persona_council",
                "lit_review_feasibility": {"feasible": False, "reason": reason},
                "agent_task": f"LITERATURE FEASIBILITY GATE FAILED.\n\n{reason}",
                "lit_review_attempts": attempts + 1,
            }

        paper_ws = os.path.join(workspace_dir, "paper_workspace")
        lit_text = _read_file_safe(os.path.join(paper_ws, "literature_review.tex"))
        sources_text = _read_file_safe(
            os.path.join(paper_ws, "literature_review_sources.json"), 10000
        )

        if not lit_text:
            # No lit review output — pass through
            return {
                "current_agent": "brainstorm_agent",
                "lit_review_feasibility": {"feasible": True, "reason": "no lit review found"},
                "agent_task": None,
            }

        # ------------------------------------------------------------------
        # Novelty check — read novelty_flags.json (produced by Step 3b)
        # ------------------------------------------------------------------
        novelty_path = os.path.join(paper_ws, "novelty_flags.json")
        novelty_text = _read_file_safe(novelty_path, 10000)
        novelty_data = None
        blocking_claims = []

        if novelty_text:
            try:
                novelty_data = json.loads(novelty_text)
                blocking_claims = [
                    c for c in novelty_data.get("claims", [])
                    if c.get("blocking", False)
                ]
            except (json.JSONDecodeError, TypeError) as e:
                logger.error("[lit_review_gate] Failed to parse novelty_flags.json: %s", e)

        # Gate on blocking novelty claims BEFORE the LLM feasibility call
        if blocking_claims:
            blocking_summary = "; ".join(
                f"[{c.get('claim_id', '?')}] "
                f"{c.get('claim_text', '')[:150]}... "
                f"(status: {c.get('status', '?')}, "
                f"evidence: {len(c.get('evidence', []))} sources)"
                for c in blocking_claims
            )

            if attempts >= max_attempts:
                logger.warning(
                    "lit_review_gate — blocking novelty claims after "
                    "%s attempts, proceeding to brainstorm_agent.", max_attempts
                )
                return {
                    "current_agent": "brainstorm_agent",
                    "lit_review_feasibility": {
                        "feasible": False,
                        "reason": f"NOVELTY BLOCKED: {blocking_summary}",
                    },
                    "agent_task": (
                        f"NOVELTY WARNING (max retries reached): Some proposed claims "
                        f"may not be novel. Proceed but explicitly acknowledge and "
                        f"work around: {blocking_summary}"
                    ),
                }

            return {
                "current_agent": "persona_council",
                "lit_review_attempts": attempts + 1,
                "lit_review_feasibility": {
                    "feasible": False,
                    "reason": f"NOVELTY BLOCKED: {blocking_summary}",
                },
                "agent_task": (
                    f"NOVELTY GATE REJECTION (attempt {attempts + 1}/{max_attempts}):\n"
                    f"The literature review found that one or more core claims in your "
                    f"research proposal are NOT novel — they have already been proven or "
                    f"established in the existing literature.\n\n"
                    f"Blocking claims:\n{blocking_summary}\n\n"
                    f"You MUST substantially reformulate or replace the blocking claims. "
                    f"Options:\n"
                    f"1. Strengthen the claim (generalize, relax assumptions, extend to "
                    f"new settings)\n"
                    f"2. Replace the claim with a genuinely open related problem\n"
                    f"3. Reframe the contribution as a new proof technique for a known "
                    f"result (only if the technique itself is novel)\n"
                    f"4. Pivot the research direction entirely\n\n"
                    f"Read `paper_workspace/novelty_flags.json` for full evidence."
                ),
            }

        # ------------------------------------------------------------------
        # LLM feasibility assessment
        # ------------------------------------------------------------------
        import litellm as _litellm

        # Build novelty context for the feasibility prompt
        novelty_context = ""
        if novelty_data:
            open_count = sum(
                1 for c in novelty_data.get("claims", [])
                if c.get("status") == "OPEN"
            )
            partial_count = sum(
                1 for c in novelty_data.get("claims", [])
                if c.get("status") == "PARTIAL"
            )
            novelty_context = (
                f"\n\nNOVELTY ASSESSMENT SUMMARY:\n"
                f"- Fully open claims: {open_count}\n"
                f"- Partially resolved claims: {partial_count}\n"
                f"- Blocking (known/equivalent) claims: {len(blocking_claims)}\n"
                f"Overall: {novelty_data.get('overall_novelty_assessment', 'N/A')}"
            )

        prompt = (
            "You are a research feasibility assessor. Given the literature review below, "
            "determine whether the proposed research direction is FEASIBLE.\n\n"
            "A direction is INFEASIBLE if:\n"
            "1. The core idea has already been thoroughly explored (no room for contribution)\n"
            "2. The required methods/data are fundamentally unavailable\n"
            "3. The theoretical foundations have known fatal flaws\n"
            "4. The scope is impossibly broad for a single research effort\n"
            "5. The core proposed claims are already proven results with no novel angle "
            "(check the NOVELTY ASSESSMENT SUMMARY below if available)\n\n"
            f"LITERATURE REVIEW:\n{lit_text[:15000]}\n\n"
            f"SOURCES:\n{sources_text[:5000]}"
            f"{novelty_context}\n\n"
            'Respond in JSON (no markdown fences): {"feasible": true/false, "reason": "one paragraph explanation"}'
        )
        try:
            resp = _litellm.completion(
                model=resolve_or_model("claude-sonnet-4-6"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            raw = resp.choices[0].message.content or ""
            raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip())
            raw = _re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
        except Exception as e:
            logger.error("[lit_review_gate] LLM assessment failed: %s, passing through", e)
            return {
                "current_agent": "brainstorm_agent",
                "lit_review_feasibility": {"feasible": True, "reason": f"assessment failed: {e}"},
                "agent_task": None,
            }

        feasible = result.get("feasible", True)
        reason = result.get("reason", "N/A")

        if feasible:
            return {
                "current_agent": "brainstorm_agent",
                "lit_review_feasibility": {"feasible": True, "reason": reason},
                "agent_task": _build_brainstorm_novelty_directive(novelty_data)
                if novelty_data else None,
            }

        # Infeasible
        if attempts >= max_attempts:
            logger.warning(
                "lit_review_gate failed after %s attempts, "
                "proceeding to brainstorm_agent anyway.", max_attempts
            )
            return {
                "current_agent": "brainstorm_agent",
                "lit_review_feasibility": {"feasible": False, "reason": reason},
                "agent_task": None,
            }

        return {
            "current_agent": "persona_council",
            "lit_review_attempts": attempts + 1,
            "lit_review_feasibility": {"feasible": False, "reason": reason},
            "agent_task": (
                f"LIT REVIEW FEASIBILITY REJECTION (attempt {attempts + 1}/{max_attempts}): "
                f"The literature review found the research direction INFEASIBLE.\n"
                f"Reason: {reason}\n\n"
                "Re-evaluate the research direction. Consider pivoting the angle, "
                "narrowing scope, or identifying an unexplored niche."
            ),
        }

    lit_review_gate_node.__name__ = "lit_review_gate"
    return lit_review_gate_node


def build_verify_completion_node(workspace_dir: str) -> Any:
    """Verify whether formalized research goals have been met by track execution.

    Three-way routing with progress vetting on re-entry:
      - ``formalize_results_agent``: all/nearly all goals met (ratio >= 0.8)
      - ``formalize_goals_agent``: some goals not met (incomplete, ratio 0.5-0.8)
      - ``brainstorm_agent``: fundamental rethink needed (ratio < 0.5)

    On 2nd+ cycle, if goals_met delta <= 0 since last check, forces forward.
    """

    def verify_completion_node(state: dict) -> dict:
        import re as _re
        from .state import prune_messages

        # Prune message history on re-entry to prevent unbounded growth
        pruned = prune_messages(state)
        if pruned:
            state = {**state, **pruned}

        research_goals = state.get("research_goals") or {}
        goals = research_goals.get("goals", [])
        total_goals = len(goals)

        if total_goals == 0:
            return {
                "current_agent": "formalize_results_agent",
                "verify_completion_result": {
                    "goals_met": 0, "goals_total": 0, "ratio": 1.0,
                    "verdict": "complete", "goal_verdicts": [],
                },
                "agent_task": None,
            }

        # Collect evidence from workspace
        evidence_parts = []
        math_ws = os.path.join(workspace_dir, "math_workspace")
        paper_ws = os.path.join(workspace_dir, "paper_workspace")
        exp_ws = os.path.join(workspace_dir, "experiment_workspace")

        # Parse claim graph for tag-filtered goal matching
        all_claims = []
        cg_content = _read_file_safe(os.path.join(math_ws, "claim_graph.json"), 8000)
        if cg_content:
            try:
                cg = json.loads(cg_content)
                all_claims = cg.get("claims", [])
            except Exception:
                pass

        # Build per-goal claim map using "goal:<id>" tags
        goal_claim_map = {}
        for g in goals:
            gid = g.get("id", "")
            if g.get("track") in ("theory", "both") and gid:
                relevant = [
                    c for c in all_claims
                    if any(f"goal:{gid}" in str(t) for t in c.get("tags", []))
                ]
                goal_claim_map[gid] = relevant

        has_tagged_claims = any(bool(v) for v in goal_claim_map.values())

        if has_tagged_claims:
            # Use tag-filtered evidence: present each goal with only its tagged claims
            for g in goals:
                gid = g.get("id", "")
                if gid in goal_claim_map and goal_claim_map[gid]:
                    claims_summary = json.dumps(
                        [{"id": c.get("id"), "statement": str(c.get("statement", ""))[:200],
                          "status": c.get("status"), "must_accept": c.get("must_accept")}
                         for c in goal_claim_map[gid]],
                        indent=2
                    )
                    evidence_parts.append(
                        f"--- THEORY CLAIMS FOR GOAL {gid} ---\n{claims_summary}"
                    )
            # Include untagged claims as supplementary context
            tagged_ids = {c.get("id") for cs in goal_claim_map.values() for c in cs}
            untagged = [c for c in all_claims if c.get("id") not in tagged_ids]
            if untagged:
                untagged_summary = json.dumps(
                    [{"id": c.get("id"), "status": c.get("status")} for c in untagged],
                    indent=2
                )
                evidence_parts.append(
                    f"--- THEORY: untagged claims (supplementary) ---\n{untagged_summary}"
                )
        elif cg_content:
            # Fallback: no tags found, use raw claim graph as before
            evidence_parts.append(f"--- THEORY: claim_graph.json ---\n{cg_content}")

        for name in ["experiment_results.json", "experiment_design.json"]:
            content = _read_file_safe(os.path.join(paper_ws, name), 8000)
            if content:
                evidence_parts.append(f"--- EXPERIMENT: {name} ---\n{content}")
        for name in ["results_summary.json", "experiment_report.md"]:
            content = _read_file_safe(os.path.join(exp_ws, name), 5000)
            if content:
                evidence_parts.append(f"--- EXPERIMENT: {name} ---\n{content}")

        agent_outputs = state.get("agent_outputs", {})
        for agent_name in ["proof_transcription_agent", "experiment_transcription_agent",
                           "experiment_verification_agent", "track_merge"]:
            output = str(agent_outputs.get(agent_name, "")).strip()
            if output:
                evidence_parts.append(f"--- AGENT: {agent_name} ---\n{output[:3000]}")

        evidence = "\n\n".join(evidence_parts) if evidence_parts else "No execution evidence found."

        goal_descriptions = "\n".join(
            f"Goal {i+1} (ID: {g.get('id', i)}):\n"
            f"  Description: {g.get('description', 'N/A')}\n"
            f"  Success Criteria: {g.get('success_criteria', 'N/A')}\n"
            f"  Track: {g.get('track', 'both')}"
            for i, g in enumerate(goals)
        )

        import litellm as _litellm

        prompt = (
            "You are a rigorous research goal completion assessor.\n\n"
            f"RESEARCH GOALS:\n{goal_descriptions}\n\n"
            f"EXECUTION EVIDENCE:\n{evidence}\n\n"
            "For EACH goal, assess whether it has been MET based on the evidence.\n"
            "A goal is MET if concrete evidence (data, proofs, results) supports completion.\n"
            "A goal is NOT MET if no evidence exists or evidence contradicts it.\n\n"
            "Respond in JSON (no markdown fences):\n"
            '{"goal_verdicts": [{"goal_id": "...", "met": true/false, "evidence_summary": "..."}], '
            '"overall_assessment": "one paragraph"}'
        )

        try:
            resp = _litellm.completion(
                model=resolve_or_model("claude-sonnet-4-6"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            raw = resp.choices[0].message.content or ""
            raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip())
            raw = _re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
        except Exception as e:
            logger.error("[verify_completion] LLM assessment failed: %s, passing through", e)
            return {
                "current_agent": "formalize_results_agent",
                "verify_completion_result": {
                    "goals_met": total_goals, "goals_total": total_goals,
                    "ratio": 1.0, "verdict": "complete",
                    "goal_verdicts": [], "error": str(e),
                },
                "agent_task": None,
            }

        goal_verdicts = result.get("goal_verdicts", [])
        goals_met = sum(1 for v in goal_verdicts if v.get("met", False))
        ratio = goals_met / total_goals if total_goals > 0 else 1.0

        verify_rework = safe_int(state.get("verify_rework_attempts", 0), 0)
        brainstorm_cyc = safe_int(state.get("brainstorm_cycle", 0), 0)

        # Progress vetting: compare against previous result
        prev_result = state.get("verify_completion_result")
        prev_history = list(state.get("verify_completion_history") or [])
        if prev_result is not None:
            prev_history.append(prev_result)
            prev_goals_met = prev_result.get("goals_met", 0)
            delta = goals_met - prev_goals_met
            if delta <= 0:
                logger.warning(
                    "[verify_completion] Progress stalled: %s goals met "
                    "(prev %s, delta %s). Forcing forward.",
                    goals_met, prev_goals_met, delta
                )
                return {
                    "current_agent": "formalize_results_agent",
                    "verify_completion_result": {
                        "goals_met": goals_met, "goals_total": total_goals,
                        "ratio": ratio, "verdict": "complete_forced_stalled",
                        "goal_verdicts": goal_verdicts,
                    },
                    "verify_completion_history": prev_history,
                    "agent_task": None,
                }

        new_result = {
            "goals_met": goals_met, "goals_total": total_goals,
            "ratio": ratio, "goal_verdicts": goal_verdicts,
        }

        # Three-way decision
        if ratio >= 0.8:
            new_result["verdict"] = "complete"
            return {
                "current_agent": "formalize_results_agent",
                "verify_completion_result": new_result,
                "verify_completion_history": prev_history,
                "agent_task": None,
            }

        if ratio >= 0.5:
            # Incomplete — rework goals
            if verify_rework >= 3:
                logger.warning(
                    "verify_completion 'incomplete' after %s "
                    "rework attempts, forcing forward.", verify_rework
                )
                new_result["verdict"] = "complete_forced"
                return {
                    "current_agent": "formalize_results_agent",
                    "verify_completion_result": new_result,
                    "verify_completion_history": prev_history,
                    "agent_task": None,
                }

            new_result["verdict"] = "incomplete"
            failed_goals = [v for v in goal_verdicts if not v.get("met", False)]
            failed_summary = "\n".join(
                f"  - {v.get('goal_id', '?')}: {v.get('evidence_summary', 'no evidence')}"
                for v in failed_goals
            )
            return {
                "current_agent": "formalize_goals_agent",
                "verify_rework_attempts": verify_rework + 1,
                "verify_completion_result": new_result,
                "verify_completion_history": prev_history,
                "agent_task": (
                    f"VERIFY COMPLETION: INCOMPLETE (attempt {verify_rework + 1}/3)\n"
                    f"{goals_met}/{total_goals} goals met ({ratio:.0%}).\n"
                    f"Failed goals:\n{failed_summary}\n\n"
                    "Revise the research goals to address the unmet criteria."
                ),
            }

        # < 50% — fundamental rethink
        if brainstorm_cyc >= 3:
            logger.warning(
                "verify_completion 'no_half' after %s "
                "brainstorm cycles, forcing forward.", brainstorm_cyc
            )
            new_result["verdict"] = "complete_forced"
            return {
                "current_agent": "formalize_results_agent",
                "verify_completion_result": new_result,
                "verify_completion_history": prev_history,
                "agent_task": None,
            }

        new_result["verdict"] = "no_half"
        return {
            "current_agent": "brainstorm_agent",
            "brainstorm_cycle": brainstorm_cyc + 1,
            "verify_completion_result": new_result,
            "verify_completion_history": prev_history,
            "agent_task": (
                f"VERIFY COMPLETION: FUNDAMENTAL RETHINK NEEDED (cycle {brainstorm_cyc + 1}/3)\n"
                f"Only {goals_met}/{total_goals} goals met ({ratio:.0%}).\n"
                f"Assessment: {result.get('overall_assessment', 'N/A')}\n\n"
                "The current approach is not working. Brainstorm substantially "
                "different approaches to the research question."
            ),
        }

    verify_completion_node.__name__ = "verify_completion"
    return verify_completion_node


def build_duality_gate_node(duality_max_attempts: int = 2) -> Any:
    """Gate after duality check: routes based on Check A + B results.

    Routes to:
      - ``resource_preparation_agent`` if both checks pass
      - ``followup_lit_review`` if either check fails (retries remaining)
    """

    def duality_gate_node(state: dict) -> dict:
        duality_result = state.get("duality_check_result") or {}
        both_passed = duality_result.get("both_passed", False)
        duality_rework = safe_int(state.get("duality_rework_attempts", 0), 0)
        max_attempts = duality_max_attempts

        if not duality_result:
            logger.warning(
                "duality_check_result is missing or empty, "
                "proceeding to resource_preparation anyway."
            )
            return {
                "current_agent": "resource_preparation_agent",
                "agent_task": None,
            }

        if both_passed:
            check_a = duality_result.get("check_a", {})
            check_b = duality_result.get("check_b", {})
            summary_parts = []
            for label, check in [("Practice", check_a), ("Rigor", check_b)]:
                score = check.get("score", "?")
                summary_parts.append(f"Check {label}: {score}/10")
                for s in check.get("suggestions", []):
                    summary_parts.append(f"  - {s}")
            return {
                "current_agent": "resource_preparation_agent",
                "agent_task": (
                    "DUALITY CHECK PASSED. Summary of findings for paper production:\n"
                    + "\n".join(summary_parts)
                    + "\n\nFull results: paper_workspace/duality_check.json"
                ),
            }

        if duality_rework >= max_attempts:
            logger.warning(
                "duality_gate failed after %s attempts, "
                "proceeding to resource_preparation anyway.", max_attempts
            )
            check_a = duality_result.get("check_a", {})
            check_b = duality_result.get("check_b", {})
            failures = []
            if not check_a.get("passed", True):
                failures.append(f"Practice: {check_a.get('reasoning', 'Failed')} (score {check_a.get('score', '?')}/10)")
            if not check_b.get("passed", True):
                failures.append(f"Rigor: {check_b.get('reasoning', 'Failed')} (score {check_b.get('score', '?')}/10)")
            return {
                "current_agent": "resource_preparation_agent",
                "agent_task": (
                    f"DUALITY CHECK FORCED FORWARD after {max_attempts} attempts.\n"
                    "Unresolved issues (address in paper where possible):\n"
                    + "\n".join(f"- {f}" for f in failures)
                    + "\n\nFull results: paper_workspace/duality_check.json"
                ) if failures else None,
            }

        check_a = duality_result.get("check_a", {})
        check_b = duality_result.get("check_b", {})
        failures = []
        suggestions = []
        if not check_a.get("passed", True):
            failures.append(f"Check A (Practice): {check_a.get('reasoning', 'Failed')}")
            suggestions.extend(check_a.get("suggestions", []))
        if not check_b.get("passed", True):
            failures.append(f"Check B (Rigor): {check_b.get('reasoning', 'Failed')}")
            suggestions.extend(check_b.get("suggestions", []))

        return {
            "current_agent": "followup_lit_review",
            "duality_rework_attempts": duality_rework + 1,
            "agent_task": (
                f"DUALITY CHECK FAILURE (attempt {duality_rework + 1}/{max_attempts}):\n"
                + "\n".join(f"- {f}" for f in failures)
                + "\n\nSuggestions:\n"
                + "\n".join(f"- {s}" for s in suggestions)
                + "\n\nConduct a targeted follow-up literature review addressing "
                "these gaps, then re-enter the brainstorm phase."
                "\n\nFull structured results: paper_workspace/duality_check.json"
            ),
        }

    duality_gate_node.__name__ = "duality_gate"
    return duality_gate_node


# V2 router functions

def lit_review_gate_router(state: ResearchState) -> str:
    return state.get("current_agent") or "brainstorm_agent"


def brainstorm_artifact_gate_router(state: ResearchState) -> str:
    if state.get("critical_failure"):
        return END
    return state.get("current_agent") or "formalize_goals_entry"


def verify_completion_router(state: ResearchState) -> str:
    return state.get("current_agent") or "formalize_results_agent"


def duality_gate_router(state: ResearchState) -> str:
    target = state.get("current_agent")
    if target in {"resource_preparation_agent", "followup_lit_review"}:
        return target
    return "resource_preparation_agent"


def iterate_persona_exit_router(state: ResearchState) -> str:
    override = state.get("iterate_start_stage_override")
    if override:
        return str(override)
    return "iterate_router"


def _critical_failure_check(next_node: str):
    """Return a router function that checks for critical_failure before proceeding.

    If ``state["critical_failure"]`` is set, route to END to halt the pipeline
    instead of continuing with empty/broken state.
    """
    def _router(state: dict) -> str:
        if state.get("critical_failure"):
            logger.critical(
                "Pipeline halted due to critical failure: %s",
                state["critical_failure"],
            )
            return END
        return next_node
    return _router


def build_followup_lit_review_node(
    model: Any,
    workspace_dir: str,
    authorized_imports: Optional[List[str]] = None,
    counsel_models: Optional[List[Any]] = None,
) -> Any:
    """Followup lit review: reuses literature_review_agent under a different output key."""
    base_node = build_literature_review_node(
        model,
        workspace_dir,
        authorized_imports,
        **({"counsel_models": counsel_models} if counsel_models is not None else {}),
    )

    def followup_node(state: dict) -> dict:
        previous_output = state.get("agent_outputs", {}).get("literature_review_agent")
        result = base_node(state)
        outputs = dict(result.get("agent_outputs", {}))
        new_output = outputs.pop("literature_review_agent", previous_output)
        if previous_output is not None:
            outputs["literature_review_agent"] = previous_output
        outputs["followup_lit_review_agent"] = new_output
        return {
            **result,
            "agent_outputs": outputs,
        }

    followup_node.__name__ = "followup_lit_review"
    return followup_node


# ---------------------------------------------------------------------------
# V2 graph builder
# ---------------------------------------------------------------------------

def build_research_graph_v2(config: "ResearchGraphConfig"):
    """
    Build the V2 persona-council-driven research pipeline.

    Args:
        config: A :class:`~consortium.graph_config.ResearchGraphConfig`
            bundling all graph-construction knobs.

    Flow:
      persona_council → lit_review → lit_review_gate → brainstorm → formalize_goals
      → {theory | experiment} → track_merge → verify_completion → formalize_results
      → duality_check → duality_gate → resource_prep → writeup → proofreading
      → reviewer → validation_gate

    With feedback loops:
      - lit_review_gate → persona_council (infeasible)
      - verify_completion → formalize_goals (incomplete)
      - verify_completion → brainstorm (fundamental rethink)
      - duality_gate → followup_lit_review → brainstorm (quality failure)
    """
    from .graph_config import ResearchGraphConfig  # noqa: F811 (type hint above)

    # Unpack config to local variables (preserves existing closures and
    # sub-builder call sites unchanged).
    model = config.model
    workspace_dir = config.workspace_dir
    pipeline_mode = config.pipeline_mode
    enable_math_agents = config.enable_math_agents
    enable_milestone_gates = config.enable_milestone_gates
    adversarial_verification = config.adversarial_verification
    min_review_score = config.min_review_score
    followup_max_iterations = config.followup_max_iterations
    manager_max_steps = config.manager_max_steps
    authorized_imports = config.authorized_imports
    summary_model_id = config.summary_model_id
    checkpointer = config.checkpointer
    counsel_models = config.counsel_models
    budget_manager = config.budget_manager
    model_registry = config.model_registry
    tree_search_config = config.tree_search
    iterate_mode = config.iterate_mode

    # Sub-config unpacking
    enforce_paper_artifacts = config.artifacts.enforce_paper_artifacts
    enforce_editorial_artifacts = config.artifacts.enforce_editorial_artifacts
    require_pdf = config.artifacts.require_pdf
    require_experiment_plan = config.artifacts.require_experiment_plan
    lit_review_max_attempts = config.artifacts.lit_review_max_attempts

    persona_council_specs = config.persona_council.specs
    persona_debate_rounds = config.persona_council.debate_rounds
    persona_synthesis_model = config.persona_council.synthesis_model
    persona_max_post_vote_retries = config.persona_council.max_post_vote_retries

    duality_check_model = config.duality_check.model
    enable_duality_check = config.duality_check.enabled
    from .persona_council import create_persona_council_node, create_duality_check_node

    counsel_kwargs = {"counsel_models": counsel_models} if counsel_models is not None else {}

    def _m(agent_name: str) -> Any:
        """Resolve the model for *agent_name* from the registry or fallback."""
        if model_registry is not None:
            return model_registry.get(agent_name)
        return model

    tracked_stage_order = build_pipeline_stages_v2(enable_math_agents)
    tracked_stage_index = {
        stage_name: idx + 1 for idx, stage_name in enumerate(tracked_stage_order)
    }

    def _wrap(node, name):
        summarized = with_pdf_summary(node, name, workspace_dir, summary_model_id)

        def contract_materializing_node(state: dict) -> dict:
            result = summarized(state) or {}
            try:
                return materialize_stage_outputs(name, state, result)
            except Exception as exc:
                logger.exception("Failed to materialize contract artifacts for %s", name)
                return {
                    **result,
                    "critical_failure": f"{name} failed to write contract artifacts: {exc}",
                    "agent_task": None,
                }

        contract_materializing_node.__name__ = getattr(summarized, "__name__", name)
        return contract_materializing_node

    def _track_stage_execution(node, name):
        def wrapped(state: dict) -> dict:
            status_payload = {}
            if name in tracked_stage_index:
                write_run_status(
                    workspace_dir,
                    status="running",
                    current_stage=name,
                    pid=os.getpid(),
                )
                status_payload["pipeline_stage_index"] = tracked_stage_index[name]
            _emit_campaign_node_status(
                workspace_dir,
                name,
                "running",
                status_payload,
            )
            try:
                result = node(state) or {}
            except Exception as exc:
                _emit_campaign_node_status(
                    workspace_dir,
                    name,
                    "failed",
                    {"error": str(exc), "human_decision_required": True, **status_payload},
                )
                raise
            if result.get("critical_failure"):
                _emit_campaign_node_status(
                    workspace_dir,
                    name,
                    "failed",
                    {"error": str(result.get("critical_failure")), "human_decision_required": True, **status_payload},
                )
                return result
            if name not in tracked_stage_index:
                _emit_campaign_node_status(workspace_dir, name, "completed", status_payload)
                return result
            update = dict(result)
            executed = list(update.get("executed_stages") or [])
            executed.append(name)
            update["executed_stages"] = executed
            prior_index = safe_int(state.get("pipeline_stage_index", 0), 0)
            update["pipeline_stage_index"] = max(prior_index, tracked_stage_index[name])
            _emit_campaign_node_status(
                workspace_dir,
                name,
                "completed",
                {"pipeline_stage_index": tracked_stage_index[name]},
            )
            return update

        wrapped.__name__ = getattr(node, "__name__", name)
        return wrapped

    # Build track subgraphs (same as v1)
    theory_track_node = build_noop_track_node("theory_track_status")
    if enable_math_agents:
        if tree_search_config and getattr(tree_search_config, "enabled", False):
            from consortium.tree_search.graph_integration import (
                build_tree_search_theory_track,
            )
            theory_subgraph = build_tree_search_theory_track(
                model=model,
                workspace_dir=workspace_dir,
                authorized_imports=authorized_imports,
                counsel_models=counsel_models,
                summary_model_id=summary_model_id,
                tree_config=tree_search_config,
                adversarial_verification=adversarial_verification,
            )
        else:
            theory_subgraph = build_theory_track_subgraph(
                model=model,
                workspace_dir=workspace_dir,
                authorized_imports=authorized_imports,
                counsel_models=counsel_models,
                summary_model_id=summary_model_id,
                model_registry=model_registry,
                adversarial_verification=adversarial_verification,
                theory_repair_max_attempts=config.theory_repair_max_attempts,
            )
        theory_track_node = build_track_subgraph_node(theory_subgraph, "theory_track_status", workspace_dir=workspace_dir)

    if tree_search_config and getattr(tree_search_config, "enabled", False):
        from consortium.tree_search.experiment_tree_integration import (
            build_tree_search_experiment_track,
        )
        experiment_subgraph = build_tree_search_experiment_track(
            model=model,
            workspace_dir=workspace_dir,
            authorized_imports=authorized_imports,
            counsel_models=counsel_models,
            summary_model_id=summary_model_id,
            tree_config=tree_search_config,
            adversarial_verification=adversarial_verification,
        )
    else:
        experiment_subgraph = build_experiment_track_subgraph(
            model=model,
            workspace_dir=workspace_dir,
            authorized_imports=authorized_imports,
            counsel_models=counsel_models,
            summary_model_id=summary_model_id,
            model_registry=model_registry,
        )

    # ── Ensemble reviewer (5 parallel reviewers with different biases) ──
    REVIEWER_BIASES = [
        ("soundness", "REVIEW FOCUS: Focus primarily on mathematical correctness, proof validity, logical consistency, and theoretical soundness. Weight your scoring heavily toward rigor.\n\n"),
        ("novelty", "REVIEW FOCUS: Focus primarily on novelty of contributions, positioning against prior work, significance of results, and whether claims of novelty are adequately supported by literature evidence.\n\n"),
        ("clarity", "REVIEW FOCUS: Focus primarily on writing quality, notation consistency, figure readability, document structure, and whether the paper is accessible to the target audience.\n\n"),
        ("experimental", "REVIEW FOCUS: Focus primarily on experimental methodology, baseline fairness, statistical rigor, reproducibility, ablation sufficiency, and whether experiments adequately support the claims.\n\n"),
        ("impact", "REVIEW FOCUS: Focus primarily on broader significance, practical implications, limitations disclosure, future work directions, and whether this work meaningfully advances the field.\n\n"),
    ]

    def build_ensemble_reviewer_node():
        """Build a node that runs 5 parallel reviewer agents, each with a different bias."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from .agents.reviewer_agent import get_tools as get_reviewer_tools
        from .agents.base_agent import create_specialist_agent
        from .prompts.reviewer_instructions import get_reviewer_system_prompt
        from .toolkits.model_utils import get_raw_model

        def ensemble_reviewer_node(state: dict) -> dict:
            reviewer_model = _m("reviewer_agent")
            model_id = get_raw_model(reviewer_model)
            base_tools = get_reviewer_tools(workspace_dir, model_id)
            base_prompt = get_reviewer_system_prompt(tools=base_tools, managed_agents=None)

            def _run_single_reviewer(bias_name: str, bias_prefix: str) -> dict:
                biased_prompt = bias_prefix + base_prompt
                agent = create_specialist_agent(
                    model=reviewer_model,
                    tools=base_tools,
                    system_prompt=biased_prompt,
                    agent_name=f"reviewer_{bias_name}",
                    workspace_dir=workspace_dir,
                )
                try:
                    result = agent(state)
                    # Try to read the verdict file this reviewer wrote
                    verdict_path = os.path.join(workspace_dir, "paper_workspace", "review_verdict.json")
                    if os.path.exists(verdict_path):
                        with open(verdict_path) as f:
                            verdict = json.load(f)
                        # Save bias-specific copy
                        bias_path = os.path.join(workspace_dir, "paper_workspace", f"review_verdict_{bias_name}.json")
                        with open(bias_path, "w") as f:
                            json.dump(verdict, f, indent=2)
                        return verdict
                except Exception as exc:
                    logger.warning("[ensemble_reviewer] %s reviewer failed: %s", bias_name, exc)
                return {}

            # Run all 5 reviewers in parallel
            verdicts: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {
                    pool.submit(_run_single_reviewer, name, prefix): name
                    for name, prefix in REVIEWER_BIASES
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        verdicts[name] = future.result()
                    except Exception as exc:
                        logger.warning("[ensemble_reviewer] %s failed: %s", name, exc)
                        verdicts[name] = {}

            # Merge verdicts: min score, union of blockers/actions, max ai_voice_risk
            valid_verdicts = [v for v in verdicts.values() if v.get("overall_score") is not None]
            if not valid_verdicts:
                logger.warning("[ensemble_reviewer] No valid verdicts from ensemble, falling back to empty")
                return state

            merged = {
                "overall_score": min(v["overall_score"] for v in valid_verdicts),
                "hard_blockers": [],
                "must_fix_actions": [],
                "ai_voice_risk": "low",
                "ensemble_scores": {name: v.get("overall_score") for name, v in verdicts.items()},
            }
            seen_blockers = set()
            seen_actions = set()
            risk_order = {"low": 0, "medium": 1, "high": 2}
            max_risk = 0
            for v in valid_verdicts:
                for b in v.get("hard_blockers", []):
                    b_key = str(b)
                    if b_key not in seen_blockers:
                        seen_blockers.add(b_key)
                        merged["hard_blockers"].append(b)
                for a in v.get("must_fix_actions", []):
                    a_key = str(a)
                    if a_key not in seen_actions:
                        seen_actions.add(a_key)
                        merged["must_fix_actions"].append(a)
                risk = risk_order.get(v.get("ai_voice_risk", "low"), 0)
                if risk > max_risk:
                    max_risk = risk
                    merged["ai_voice_risk"] = v.get("ai_voice_risk", "low")

            # Write merged verdict
            merged_path = os.path.join(workspace_dir, "paper_workspace", "review_verdict.json")
            os.makedirs(os.path.dirname(merged_path), exist_ok=True)
            with open(merged_path, "w") as f:
                json.dump(merged, f, indent=2)

            logger.info(
                "[ensemble_reviewer] Merged %d reviewer verdicts: min_score=%s, blockers=%d, actions=%d",
                len(valid_verdicts), merged["overall_score"],
                len(merged["hard_blockers"]), len(merged["must_fix_actions"]),
            )
            return {"agent_task": None}

        ensemble_reviewer_node.__name__ = "reviewer_agent"
        return ensemble_reviewer_node

    # Build all nodes
    nodes: dict[str, Any] = {
        # Pre-track (new v2 flow)
        "persona_council": create_persona_council_node(
            workspace_dir=workspace_dir,
            persona_specs=persona_council_specs,
            max_debate_rounds=persona_debate_rounds,
            synthesis_model=persona_synthesis_model,
            max_post_vote_retries=persona_max_post_vote_retries,
            budget_manager=budget_manager,
        ),
        "literature_review_agent": _wrap(
            build_literature_review_node(_m("literature_review_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "literature_review_agent",
        ),
        "lit_review_gate": build_lit_review_gate_node(workspace_dir, max_attempts=lit_review_max_attempts),
        "brainstorm_agent": _wrap(
            build_brainstorm_node(_m("brainstorm_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "brainstorm_agent",
        ),
        "brainstorm_artifact_gate": build_brainstorm_artifact_gate_node(workspace_dir),
        "formalize_goals_entry": build_formalize_goals_entry_node(workspace_dir),
        "formalize_goals_agent": _wrap(
            build_formalize_goals_node(_m("formalize_goals_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "formalize_goals_agent",
        ),
        "research_plan_writeup_agent": _wrap(
            build_research_plan_writeup_node(_m("research_plan_writeup_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "research_plan_writeup_agent",
        ),
        "track_decomposition_gate": build_track_decomposition_gate_node(
            workspace_dir, enable_math_agents=enable_math_agents
        ),
        "milestone_goals": build_milestone_gate_node("research_plan", workspace_dir),
        # Execution tracks (reused from v1)
        "theory_track": theory_track_node,
        "experiment_track": build_track_subgraph_node(experiment_subgraph, "experiment_track_status"),
        "track_merge": build_track_merge_node(workspace_dir=workspace_dir),
        # Post-track verification (new v2 gates)
        "verify_completion": build_verify_completion_node(workspace_dir),
        "formalize_results_agent": _wrap(
            _formalize_results_state_mapper(
                build_formalize_results_node(_m("formalize_results_agent"), workspace_dir, authorized_imports, **counsel_kwargs)
            ),
            "formalize_results_agent",
        ),
        "followup_lit_review": _wrap(
            build_followup_lit_review_node(_m("followup_lit_review"), workspace_dir, authorized_imports, counsel_models),
            "followup_lit_review_agent",
        ),
        # Paper production chain (reused from v1)
        "resource_preparation_agent": _wrap(
            build_resource_preparation_node(_m("resource_preparation_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "resource_preparation_agent",
        ),
        "paper_contract_builder": build_paper_contract_node(workspace_dir),
        "writeup_agent": _wrap(
            build_writeup_node(_m("writeup_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "writeup_agent",
        ),
        "writeup_artifact_gate": build_writeup_artifact_gate_node(workspace_dir),
        "proofreading_entry": build_proofreading_entry_node(workspace_dir),
        "proofreading_agent": _wrap(
            build_proofreading_node(_m("proofreading_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
            "proofreading_agent",
        ),
        "proofread_gate": build_proofread_gate_node(workspace_dir),
        "reviewer_agent": (
            build_ensemble_reviewer_node()
            if config.enable_ensemble_review
            else _wrap(
                build_reviewer_node(_m("reviewer_agent"), workspace_dir, authorized_imports, **counsel_kwargs),
                "reviewer_agent",
            )
        ),
        "review_gate": build_review_gate_node(workspace_dir),
        "validation_gate": build_validation_gate_node(),
        "milestone_review": build_milestone_gate_node("review", workspace_dir),
    }

    # Add duality check nodes if enabled
    if enable_duality_check:
        nodes["duality_check"] = create_duality_check_node(
            workspace_dir=workspace_dir,
            check_model=duality_check_model,
            budget_manager=budget_manager,
        )
        nodes["duality_gate"] = build_duality_gate_node(duality_max_attempts=config.duality_max_attempts)

    graph = StateGraph(ResearchState)
    for name, node in nodes.items():
        graph.add_node(name, _track_stage_execution(node, name))

    # --- Iterate-mode entry node (revision from prior paper + feedback) ---
    if iterate_mode:
        def _iterate_entry_node(state: dict) -> dict:
            """Seed the persona council with revision context from prior paper + feedback.

            In iterate mode the persona council runs first, debating what
            revisions are needed before the paper production agents execute.
            The council receives the **complete** prior paper and all feedback
            — no truncation.  Frontier models (Opus 4.6, GPT-5.4, Gemini-3.1)
            all have 200K-2M token context; a typical 40-page paper is ~20K
            tokens, well within limits.
            """
            paper_ws = os.path.join(workspace_dir, "paper_workspace")

            # Read the full feedback — no truncation
            feedback_path = os.path.join(paper_ws, "iteration_feedback.md")
            feedback_content = ""
            if os.path.exists(feedback_path):
                with open(feedback_path, "r", encoding="utf-8") as fh:
                    feedback_content = fh.read()

            # Read the full prior paper — no truncation
            prior_path = state.get("iterate_prior_paper_path", "")
            prior_paper_content = ""
            if prior_path and os.path.exists(prior_path):
                try:
                    with open(prior_path, "r", encoding="utf-8") as fh:
                        prior_paper_content = fh.read()
                except Exception:
                    prior_paper_content = f"[Error reading paper at: {prior_path}]"

            # Binding constraints from the PI (human_directive.md) — these are
            # non-negotiable research decisions that personas must respect.
            binding = state.get("iterate_binding_constraints", "")
            binding_section = ""
            if binding:
                binding_section = (
                    "## BINDING CONSTRAINTS (from Principal Investigator)\n\n"
                    "The following constraints are set by the principal investigator and "
                    "are **NOT subject to your evaluation**. You MUST design the revision "
                    "plan to satisfy these constraints. You may note concerns or suggest "
                    "Discussion-section caveats, but you must NOT reject the revision "
                    "direction based on these decisions. Treat them as given.\n\n"
                    f"{binding}\n\n"
                    "---\n\n"
                )

            task = (
                "REVISION MODE — PERSONA COUNCIL DEBATE\n\n"
                "You are reviewing an existing paper draft and feedback from reviewers. "
                "Your task is to debate and produce a **structured revision plan** that "
                "identifies:\n"
                "1. The most critical issues raised by reviewers (ranked by severity)\n"
                "2. Specific sections/claims that need revision, with concrete directives\n"
                "3. Missing experiments, proofs, or literature that must be added\n"
                "4. Structural changes needed (reordering, splitting, merging sections)\n"
                "5. What should be preserved as-is (strengths to keep)\n\n"
                "The downstream writing agents will use your revision plan to rewrite the paper.\n\n"
                "---\n\n"
                f"{binding_section}"
                "## Prior Paper Draft (complete)\n\n"
                f"{prior_paper_content}\n\n"
                "---\n\n"
                "## Reviewer Feedback (complete)\n\n"
                f"{feedback_content}\n\n"
                "---\n\n"
                "Debate this revision thoroughly. Each persona should evaluate the paper "
                "and feedback from their specific lens, then converge on a unified revision plan. "
                "Remember: the binding constraints above are non-negotiable — work within them."
            )
            return {"agent_task": task}

        _iterate_entry_node.__name__ = "iterate_entry"
        graph.add_node("iterate_entry", _iterate_entry_node)

        def _iterate_router(state: dict) -> dict:
            """Classify the revision plan and route to the appropriate pipeline entry.

            Uses an LLM call to analyze the persona council's revision plan and
            determine whether the revisions require:
            - writing_only: presentation/clarity fixes → resource_prep
            - needs_research: new experiments/theory/lit → literature_review
            - needs_full_rethink: fundamental rethink → brainstorm
            """
            import litellm as _litellm

            revision_plan = state.get("research_proposal", "")
            prior_path = state.get("iterate_prior_paper_path", "")
            feedback_path = os.path.join(workspace_dir, "paper_workspace", "iteration_feedback.md")

            classification_prompt = (
                "You are a routing classifier for a research paper revision pipeline.\n\n"
                "Given the revision plan below, classify the required changes into ONE of:\n\n"
                "- WRITING_ONLY: Changes are limited to writing quality, presentation, "
                "clarity, structure, citations, notation, or minor corrections. "
                "No new experiments, proofs, or literature search needed.\n\n"
                "- NEEDS_RESEARCH: Changes require new experiments, additional baselines, "
                "new or revised proofs, expanded literature review, or new data analysis. "
                "The fundamental approach is sound but execution gaps must be filled.\n\n"
                "- NEEDS_FULL_RETHINK: Changes challenge the fundamental approach, "
                "research questions, or methodology. The paper needs a substantially "
                "different direction or framing.\n\n"
                f"## Revision Plan\n\n{revision_plan[:20_000]}\n\n"
                "Respond with EXACTLY one word: WRITING_ONLY, NEEDS_RESEARCH, or NEEDS_FULL_RETHINK"
            )

            try:
                resp = _litellm.completion(
                    model=resolve_or_model(summary_model_id or "claude-sonnet-4-6"),
                    messages=[{"role": "user", "content": classification_prompt}],
                    max_tokens=50,
                )
                route = (resp.choices[0].message.content or "").strip().upper()
            except Exception as exc:
                logger.warning("[iterate_router] Classification failed (%s), defaulting to WRITING_ONLY", exc)
                route = "WRITING_ONLY"

            # Normalize to canonical route names
            if "FULL_RETHINK" in route:
                route = "needs_full_rethink"
            elif "RESEARCH" in route:
                route = "needs_research"
            else:
                route = "writing_only"

            # Build appropriate agent_task for the downstream entry point
            if route == "writing_only":
                task = (
                    "ITERATE MODE — WRITING REVISION\n\n"
                    f"## Prior Paper\nThe prior paper is available at: {prior_path}\n\n"
                    f"## Revision Plan (from persona council debate)\n{revision_plan}\n\n"
                    f"## Original Feedback\nRead the full feedback at: {feedback_path}\n\n"
                    "Prepare resources for revising the paper. Use the prior paper as the "
                    "starting point. Follow the revision plan. Read the original feedback directly."
                )
            elif route == "needs_research":
                task = (
                    "ITERATE MODE — RESEARCH REQUIRED\n\n"
                    "The persona council's revision plan identifies gaps that require new "
                    "research: additional experiments, new baselines, expanded proofs, or "
                    "deeper literature coverage.\n\n"
                    f"## Revision Plan\n{revision_plan}\n\n"
                    f"## Prior Paper: {prior_path}\n"
                    f"## Original Feedback: {feedback_path}\n\n"
                    "Conduct a targeted literature review to address the identified gaps. "
                    "Focus on finding papers that strengthen the areas reviewers criticized."
                )
            else:  # needs_full_rethink
                task = (
                    "ITERATE MODE — FUNDAMENTAL RETHINK\n\n"
                    "The persona council's revision plan identifies fundamental issues with "
                    "the approach. The research questions, methodology, or framing need "
                    "substantial changes.\n\n"
                    f"## Revision Plan\n{revision_plan}\n\n"
                    f"## Prior Paper: {prior_path}\n"
                    f"## Original Feedback: {feedback_path}\n\n"
                    "Brainstorm a revised approach that addresses the fundamental concerns "
                    "while preserving any strengths identified in the revision plan."
                )

            logger.info("[iterate_router] Classified revision as: %s", route)
            return {"agent_task": task, "iterate_route": route}

        _iterate_router.__name__ = "iterate_router"
        graph.add_node("iterate_router", _iterate_router)

        def _iterate_route_selector(state: dict) -> str:
            """Route based on the iterate_router's classification."""
            route = state.get("iterate_route", "writing_only")
            if route == "needs_research":
                return "literature_review_agent"
            if route == "needs_full_rethink":
                return "brainstorm_agent"
            return "resource_preparation_agent"

    # --- Edge wiring ---

    # Entry point: iterate_entry (revision) or persona_council (fresh run)
    # In iterate mode: iterate_entry seeds context → persona_council debates
    # revision plan → bridge reformats for resource_prep → paper production chain.
    if iterate_mode:
        graph.set_entry_point("iterate_entry")
        graph.add_edge("iterate_entry", "persona_council")
        graph.add_conditional_edges(
            "persona_council",
            iterate_persona_exit_router,
            {
                "iterate_router": "iterate_router",
                "literature_review_agent": "literature_review_agent",
                "brainstorm_agent": "brainstorm_agent",
                "formalize_goals_entry": "formalize_goals_entry",
                "formalize_goals_agent": "formalize_goals_agent",
                "research_plan_writeup_agent": "research_plan_writeup_agent",
                "formalize_results_agent": "formalize_results_agent",
                "resource_preparation_agent": "resource_preparation_agent",
                "writeup_agent": "writeup_agent",
                "proofreading_agent": "proofreading_agent",
                "reviewer_agent": "reviewer_agent",
            },
        )
        graph.add_conditional_edges(
            "iterate_router",
            _iterate_route_selector,
            {
                "resource_preparation_agent": "resource_preparation_agent",
                "literature_review_agent": "literature_review_agent",
                "brainstorm_agent": "brainstorm_agent",
            },
        )
    else:
        graph.set_entry_point("persona_council")
        graph.add_edge("persona_council", "literature_review_agent")

    # Lit review → gate
    graph.add_edge("literature_review_agent", "lit_review_gate")
    graph.add_conditional_edges(
        "lit_review_gate",
        lit_review_gate_router,
        {
            "persona_council": "persona_council",
            "brainstorm_agent": "brainstorm_agent",
        },
    )

    # Brainstorm → artifact gate → entry gate → formalize goals → writeup → milestone → track execution
    graph.add_conditional_edges(
        "brainstorm_agent",
        _critical_failure_check("brainstorm_artifact_gate"),
        {"brainstorm_artifact_gate": "brainstorm_artifact_gate", END: END},
    )
    graph.add_conditional_edges(
        "brainstorm_artifact_gate",
        brainstorm_artifact_gate_router,
        {
            "brainstorm_agent": "brainstorm_agent",
            "formalize_goals_entry": "formalize_goals_entry",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "formalize_goals_entry",
        _critical_failure_check("formalize_goals_agent"),
        {"formalize_goals_agent": "formalize_goals_agent", END: END},
    )
    graph.add_edge("formalize_goals_agent", "research_plan_writeup_agent")
    graph.add_edge("research_plan_writeup_agent", "track_decomposition_gate")
    graph.add_edge("track_decomposition_gate", "milestone_goals")
    graph.add_conditional_edges("milestone_goals", track_router)

    # Track execution → merge → verify
    graph.add_edge("theory_track", "track_merge")
    graph.add_edge("experiment_track", "track_merge")
    graph.add_edge("track_merge", "verify_completion")

    # Verify completion three-way routing
    graph.add_conditional_edges(
        "verify_completion",
        verify_completion_router,
        {
            "formalize_results_agent": "formalize_results_agent",
            "formalize_goals_agent": "formalize_goals_agent",
            "brainstorm_agent": "brainstorm_agent",
        },
    )

    if enable_duality_check:
        # Formalize results → duality check → duality gate
        graph.add_edge("formalize_results_agent", "duality_check")
        graph.add_edge("duality_check", "duality_gate")
        graph.add_conditional_edges(
            "duality_gate",
            duality_gate_router,
            {
                "resource_preparation_agent": "resource_preparation_agent",
                "followup_lit_review": "followup_lit_review",
            },
        )
        # Followup lit review → brainstorm (duality failure path)
        graph.add_edge("followup_lit_review", "brainstorm_agent")
    else:
        # No duality check — go straight to paper production
        graph.add_edge("formalize_results_agent", "resource_preparation_agent")

    # Paper production chain (same as v1)
    graph.add_edge("resource_preparation_agent", "paper_contract_builder")
    graph.add_edge("paper_contract_builder", "writeup_agent")
    graph.add_edge("writeup_agent", "writeup_artifact_gate")
    graph.add_conditional_edges(
        "writeup_artifact_gate",
        writeup_artifact_gate_router,
        {
            "writeup_agent": "writeup_agent",
            "proofreading_entry": "proofreading_entry",
        },
    )
    graph.add_edge("proofreading_entry", "proofreading_agent")
    graph.add_edge("proofreading_agent", "proofread_gate")
    graph.add_conditional_edges(
        "proofread_gate",
        proofread_gate_router,
        {
            "proofreading_agent": "proofreading_agent",
            "reviewer_agent": "reviewer_agent",
        },
    )
    graph.add_edge("reviewer_agent", "review_gate")
    graph.add_conditional_edges(
        "review_gate",
        review_gate_router,
        {
            "reviewer_agent": "reviewer_agent",
            "milestone_review": "milestone_review",
        },
    )
    graph.add_edge("milestone_review", "validation_gate")
    graph.add_conditional_edges(
        "validation_gate",
        validation_router,
        {
            END: END,
            "writeup_agent": "writeup_agent",
            "experiment_track": "experiment_track",
            "theory_track": "theory_track",
        },
    )

    compile_kwargs: dict = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    return graph.compile(**compile_kwargs)


def get_default_checkpointer(workspace_dir: str):
    """Return a SqliteSaver checkpointer scoped to the workspace directory."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3
        db_path = os.path.join(workspace_dir, "checkpoints.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        return SqliteSaver(conn)
    except ImportError as e:
        logger.warning(
            "SQLite checkpoint support unavailable (%s). "
            "Install 'langgraph-checkpoint-sqlite' to enable resumability.",
            e,
        )
        return None
    except Exception as e:
        logger.warning("Checkpointer unavailable (%s); resumability disabled.", e)
        return None
