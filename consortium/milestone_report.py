"""Structured milestone reports for human review at strategic pipeline points.

Each milestone report is a standalone PDF with fixed sections:
1. Executive Summary
2. Key Decisions Made
3. Artifacts Produced
4. Quality Signals
5. Open Questions / Risks
6. Recommended Next Steps
7. Human Action Required

Reports are generated at 4 strategic points in the pipeline:
- After research_planner (before track execution)
- After track_merge (before synthesis)
- After results_analysis (before follow-up decision)
- After reviewer (before final validation)

When ``enable_milestone_gates`` is True, the pipeline pauses after
generating the report and waits for human input via HTTP.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .pdf_summary import _build_summary_latex, _compile_tex_to_pdf


# ---------------------------------------------------------------------------
# Milestone waiting infrastructure
# ---------------------------------------------------------------------------

# Shared state for milestone gate blocking.
# The HTTP handler writes to _milestone_response; the gate node reads it.
_milestone_lock = threading.Lock()
_milestone_event = threading.Event()
_milestone_response: Optional[dict] = None
_milestone_latest_path: Optional[str] = None
_milestone_latest_phase: Optional[str] = None
_milestone_waiting: bool = False


def set_milestone_response(response: dict) -> None:
    """Called by the HTTP handler when a human responds to a milestone."""
    global _milestone_response
    with _milestone_lock:
        _milestone_response = response
    _milestone_event.set()


def get_milestone_status() -> dict:
    """Called by the HTTP handler for GET /milestone."""
    return {
        "waiting": _milestone_waiting,
        "phase": _milestone_latest_phase,
        "latest_report_path": _milestone_latest_path,
    }


def wait_for_human_response(timeout: int = 3600) -> Optional[dict]:
    """Block until human responds via HTTP or timeout expires.

    Returns the response dict or None on timeout.
    """
    global _milestone_waiting, _milestone_response
    _milestone_event.clear()
    with _milestone_lock:
        _milestone_response = None
        _milestone_waiting = True

    print(f"[milestone] Waiting for human response (timeout={timeout}s)...")
    print(f"[milestone]   POST /milestone_response with:")
    print(f'[milestone]   {{"action": "approve"|"modify"|"abort", "feedback": "..."}}')
    _milestone_event.wait(timeout=timeout)

    with _milestone_lock:
        _milestone_waiting = False
        resp = _milestone_response
        _milestone_response = None
    if resp:
        print(f"[milestone] Human response: {resp.get('action', 'unknown')}")
    else:
        print("[milestone] Timeout — proceeding automatically.")
    return resp


# ---------------------------------------------------------------------------
# Milestone content sections
# ---------------------------------------------------------------------------

# Phase-specific context prompts for the LLM formatter
PHASE_CONTEXTS = {
    "research_plan": {
        "title": "Research Plan Milestone",
        "description": "Research planning is complete. Review the plan before execution begins.",
        "key_artifacts": [
            "paper_workspace/track_decomposition.json",
            "paper_workspace/research_plan.pdf",
        ],
        "what_to_review": [
            "Is the research question well-formulated?",
            "Is the track decomposition (theory vs experiment) appropriate?",
            "Are the assigned questions to each track correct?",
        ],
    },
    "track_results": {
        "title": "Track Results Milestone",
        "description": "Theory and experiment tracks are complete. Review results before synthesis.",
        "key_artifacts": [
            "math_workspace/claim_graph.json",
            "paper_workspace/experiment_results.json",
        ],
        "what_to_review": [
            "Are the theoretical results sound and complete?",
            "Do experimental results support the theory?",
            "Are there inconsistencies between tracks?",
        ],
    },
    "analysis": {
        "title": "Results Analysis Milestone",
        "description": "Results analysis is complete. Review before follow-up decision.",
        "key_artifacts": [
            "paper_workspace/results_assessment.pdf",
            "paper_workspace/followup_decision.json",
        ],
        "what_to_review": [
            "Does the analysis identify all significant findings?",
            "Is the follow-up recommendation appropriate?",
            "Are there gaps that need addressing?",
        ],
    },
    "review": {
        "title": "Peer Review Milestone",
        "description": "Internal review is complete. Review before final validation.",
        "key_artifacts": [
            "paper_workspace/review_report.tex",
            "paper_workspace/review_verdict.json",
            "paper_workspace/copyedit_report.tex",
            "final_paper.tex",
        ],
        "what_to_review": [
            "Are the reviewer's criticisms valid?",
            "Is the paper quality sufficient?",
            "Should the paper be revised before submission?",
        ],
    },
}


def _collect_recent_artifacts(workspace_dir: str, phase: str) -> list[dict]:
    """Scan workspace for artifacts relevant to the current phase."""
    artifacts = []
    phase_ctx = PHASE_CONTEXTS.get(phase, {})
    for rel_path in phase_ctx.get("key_artifacts", []):
        full_path = os.path.join(workspace_dir, rel_path)
        exists = os.path.exists(full_path)
        size = os.path.getsize(full_path) if exists else 0
        artifacts.append({
            "path": rel_path,
            "exists": exists,
            "size_bytes": size,
        })
    return artifacts


def _collect_validation_signals(state: dict) -> list[str]:
    """Extract quality signals from state."""
    signals = []
    validation = state.get("validation_results", {})
    for gate, result in validation.items():
        status = "PASS" if result.get("is_valid") else "FAIL"
        errors = result.get("errors", [])
        signals.append(f"{gate}: {status}" + (f" ({'; '.join(errors)})" if errors else ""))

    intermediate = list(state.get("intermediate_validation_log") or [])
    for entry in intermediate[-5:]:  # last 5 entries
        checkpoint = entry.get("checkpoint", "unknown")
        results = entry.get("results", {})
        for gate, result in results.items():
            status = "PASS" if result.get("is_valid") else "FAIL"
            signals.append(f"[intermediate:{checkpoint}] {gate}: {status}")

    return signals


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _format_milestone_latex(
    phase: str,
    state: dict,
    workspace_dir: str,
    budget_status: Optional[str] = None,
) -> str:
    """Build LaTeX body content for a milestone report."""
    phase_ctx = PHASE_CONTEXTS.get(phase, {
        "title": phase.replace("_", " ").title(),
        "description": f"Milestone report for {phase}.",
        "what_to_review": [],
    })

    cycle = state.get("research_cycle", 0)
    iteration = state.get("iteration_count", 0)

    # Header with budget status
    lines = []
    if budget_status:
        lines.append(f"\\begin{{center}}\\textbf{{{budget_status}}}\\end{{center}}")
        lines.append("")

    # 1. Executive Summary
    lines.append("\\section{Executive Summary}")
    lines.append(f"{phase_ctx['description']}")
    lines.append(f"Research cycle: {cycle}. Pipeline iteration: {iteration}.")
    lines.append("")

    # 2. Key Decisions
    lines.append("\\section{Key Decisions Made}")
    agent_outputs = state.get("agent_outputs", {})
    if agent_outputs:
        lines.append("The following agents have produced outputs in this phase:")
        lines.append("\\begin{itemize}")
        for agent_name in sorted(agent_outputs.keys()):
            output = agent_outputs[agent_name]
            preview = str(output)[:200].replace("&", "\\&").replace("_", "\\_").replace("#", "\\#").replace("%", "\\%")
            lines.append(f"  \\item \\textbf{{{agent_name.replace('_', ' ').title()}}}: {preview}...")
        lines.append("\\end{itemize}")
    else:
        lines.append("No agent outputs recorded for this phase.")
    lines.append("")

    # 3. Artifacts Produced
    lines.append("\\section{Artifacts Produced}")
    artifacts = _collect_recent_artifacts(workspace_dir, phase)
    if artifacts:
        lines.append("\\begin{longtable}{lll}")
        lines.append("\\toprule")
        lines.append("Path & Status & Size \\\\")
        lines.append("\\midrule")
        lines.append("\\endhead")
        for a in artifacts:
            path = a["path"].replace("_", "\\_")
            status = "EXISTS" if a["exists"] else "MISSING"
            size = f"{a['size_bytes']:,} B" if a["exists"] else "---"
            lines.append(f"{path} & {status} & {size} \\\\")
        lines.append("\\bottomrule")
        lines.append("\\end{longtable}")
    else:
        lines.append("No phase-specific artifacts to report.")
    lines.append("")

    # 4. Quality Signals
    lines.append("\\section{Quality Signals}")
    signals = _collect_validation_signals(state)
    if signals:
        lines.append("\\begin{itemize}")
        for s in signals:
            safe_s = s.replace("&", "\\&").replace("_", "\\_").replace("#", "\\#").replace("%", "\\%")
            lines.append(f"  \\item {safe_s}")
        lines.append("\\end{itemize}")
    else:
        lines.append("No validation signals available yet.")
    lines.append("")

    # 5. Open Questions / Risks
    lines.append("\\section{Open Questions / Risks}")
    review_items = phase_ctx.get("what_to_review", [])
    if review_items:
        lines.append("Please consider the following:")
        lines.append("\\begin{enumerate}")
        for item in review_items:
            safe_item = item.replace("&", "\\&").replace("_", "\\_").replace("#", "\\#").replace("%", "\\%")
            lines.append(f"  \\item {safe_item}")
        lines.append("\\end{enumerate}")
    lines.append("")

    # 6. Recommended Next Steps
    lines.append("\\section{Recommended Next Steps}")
    next_agent = state.get("current_agent", "unknown")
    next_task = state.get("agent_task") or "Continue with the next pipeline stage."
    safe_task = next_task[:300].replace("&", "\\&").replace("_", "\\_").replace("#", "\\#").replace("%", "\\%")
    lines.append(f"Next agent: \\textbf{{{next_agent.replace('_', ' ').title()}}}")
    lines.append(f"\\\\Task: {safe_task}")
    lines.append("")

    # 7. Human Action Required
    lines.append("\\section{Human Action Required}")
    if state.get("enable_milestone_gates"):
        lines.append("\\textbf{YES} --- The pipeline is paused at this milestone.")
        lines.append("Submit your response via the HTTP API:")
        lines.append("\\begin{verbatim}")
        lines.append('POST /milestone_response')
        lines.append('{"action": "approve"|"modify"|"abort", "feedback": "..."}')
        lines.append("\\end{verbatim}")
    else:
        lines.append("No --- milestone gates are disabled. This report is informational only.")
    lines.append("")

    return "\n".join(lines)


def generate_milestone_report(
    phase: str,
    state: dict,
    workspace_dir: str,
    budget_status: Optional[str] = None,
) -> Optional[str]:
    """Generate a structured milestone report PDF.

    Returns the PDF path on success, or the .tex path if PDF compilation
    fails, or None on total failure.  Never raises.
    """
    global _milestone_latest_path, _milestone_latest_phase
    try:
        report_dir = os.path.join(workspace_dir, "milestone_reports")
        os.makedirs(report_dir, exist_ok=True)

        cycle = state.get("research_cycle", 0)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        basename = f"{phase}_cycle{cycle}_{timestamp}"

        # Build LaTeX content
        formatted_content = _format_milestone_latex(phase, state, workspace_dir, budget_status)

        # Wrap in full document
        phase_ctx = PHASE_CONTEXTS.get(phase, {})
        title = phase_ctx.get("title", phase.replace("_", " ").title())
        ts_display = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        full_latex = _build_summary_latex(phase, formatted_content, ts_display)
        # Override the title
        full_latex = full_latex.replace(
            f"{phase.replace('_', ' ').title()} — Stage Summary",
            f"{title} — Milestone Report",
        )

        # Write .tex
        tex_path = os.path.join(report_dir, f"{basename}.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(full_latex)

        # Write machine-readable JSON alongside
        json_path = os.path.join(report_dir, f"{basename}.json")
        json_data = {
            "phase": phase,
            "timestamp": ts_display,
            "research_cycle": cycle,
            "budget_status": budget_status,
            "artifacts": _collect_recent_artifacts(workspace_dir, phase),
            "validation_signals": _collect_validation_signals(state),
            "enable_milestone_gates": state.get("enable_milestone_gates", False),
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2)

        # Compile to PDF
        pdf_path = _compile_tex_to_pdf(tex_path)
        result_path = pdf_path or tex_path
        _milestone_latest_path = result_path
        _milestone_latest_phase = phase
        print(f"[milestone] Report generated: {result_path}")
        return result_path

    except Exception as e:
        print(f"[milestone] Report generation failed for {phase}: {e}")
        return None
