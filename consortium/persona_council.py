"""
Persona council — 3-persona debate for research proposal synthesis, plus
dual-lens evaluation of formalized results.

The persona council runs a structured multi-phase debate:
  1. Independent evaluation — each persona assesses the task from its unique lens.
  2. Debate rounds       — personas critique each other's evaluations (parallel per round).
  3. Synthesis            — a synthesis model integrates the debate into a 1-2 page proposal.

The duality check performs two parallel evaluations of formalized results from
complementary analytical lenses (Check A and Check B), returning structured
pass/fail verdicts with scores and suggestions.

Both integrate with BudgetManager for cost tracking and use ThreadPoolExecutor
for parallel execution, matching the patterns in counsel.py.

Usage (via graph.py):
    from .persona_council import create_persona_council_node, create_duality_check_node
    council_node = create_persona_council_node(workspace_dir, ...)
    duality_node = create_duality_check_node(workspace_dir, ...)
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import litellm

from .utils import resolve_or_model
from .prompts.persona_instructions import (
    PERSONA_POST_SYNTHESIS_VOTE_PROMPT,
    PERSONA_SYSTEM_PROMPTS,
    PERSONA_SYNTHESIS_PROMPT,
)
from .prompts.duality_check_instructions import (
    DUALITY_CHECK_A_PROMPT,
    DUALITY_CHECK_B_PROMPT,
)


# ---------------------------------------------------------------------------
# Default persona model specs
# ---------------------------------------------------------------------------

DEFAULT_PERSONA_MODEL_SPECS: List[Dict[str, Any]] = [
    {"persona": "practical_compass",   "model": "claude-opus-4-6",      "reasoning_effort": "high"},
    {"persona": "rigor_novelty",       "model": "gpt-5.4",              "reasoning_effort": "high"},
    {"persona": "narrative_architect",  "model": "gemini-3-pro-preview", "thinking_budget": 32768},
]

# Extended persona list for ultra tier — includes empiricist for experimental grounding
EXTENDED_PERSONA_MODEL_SPECS: List[Dict[str, Any]] = [
    {"persona": "practical_compass",    "model": "claude-opus-4-6",           "reasoning_effort": "high"},
    {"persona": "rigor_novelty",        "model": "gpt-5.4",                  "reasoning_effort": "high"},
    {"persona": "narrative_architect",   "model": "gemini-3.1-pro-preview",   "thinking_budget": 65536},
    {"persona": "empirical_grounding",   "model": "claude-opus-4-6",          "reasoning_effort": "high"},
]

DEFAULT_SYNTHESIS_MODEL = "claude-opus-4-6"
DEFAULT_DUALITY_CHECK_MODEL = "claude-opus-4-6"

# Max chars to read from each workspace file for duality check context.
_DUALITY_FILE_TRUNCATE = 8000

# False-positive patterns stripped before scanning for ACCEPT/REJECT verdicts.
_FALSE_POSITIVE_PATTERNS = [
    r"REJECT THE (?:PREMISE|CLAIM|ASSUMPTION|FRAMING)",
    r"WOULD REJECT THE",
    r"CANNOT ACCEPT",
    r"NOT ACCEPT",
    r"REFUSE TO ACCEPT",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_verdict(text: str) -> str:
    """Extract ACCEPT/REJECT verdict from persona output.

    Uses a two-pass approach:
    1. Look for a structured ``VERDICT: ACCEPT/REJECT`` marker near the end
       of the text (preferred — reliable and unambiguous).
    2. Fall back to a full-text scan with false-positive filtering.

    Returns ``"ACCEPT"``, ``"REJECT"``, or ``"UNKNOWN"`` if neither is found.
    """
    if not text:
        return "UNKNOWN"

    # Pass 1: structured marker in the last 500 chars
    tail = text[-500:].upper()
    structured = re.search(r"(?:FINAL\s+)?VERDICT\s*:\s*(ACCEPT|REJECT)", tail)
    if structured:
        return structured.group(1)

    # Pass 2: full-text scan with false-positive pattern removal
    scan_text = text.upper()
    for pattern in _FALSE_POSITIVE_PATTERNS:
        scan_text = re.sub(pattern, "", scan_text)

    if re.search(r"\bACCEPT\b", scan_text):
        return "ACCEPT"
    if re.search(r"\bREJECT\b", scan_text):
        return "REJECT"
    return "UNKNOWN"


def _read_file_truncated(path: str, max_chars: int = _DUALITY_FILE_TRUNCATE) -> str:
    """Read a file and truncate to *max_chars*.  Returns empty string on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read(max_chars)
        if os.path.getsize(path) > max_chars:
            content += "\n... [truncated]"
        return content
    except Exception:
        return ""


def _parse_json_response(text: str) -> Optional[dict]:
    """Parse a JSON object from an LLM response, handling markdown code fences."""
    # Try to extract from code fences first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    candidate = fence_match.group(1).strip() if fence_match else text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Fallback: find the first { ... } block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    return None


## _record_budget removed — budget is now recorded automatically by the
# monkey-patched litellm.completion() in config.py.


# ---------------------------------------------------------------------------
# Core: run_persona_council
# ---------------------------------------------------------------------------

def run_persona_council(
    task: str,
    persona_specs: Optional[List[Dict[str, Any]]] = None,
    max_debate_rounds: int = 3,
    synthesis_model: str = DEFAULT_SYNTHESIS_MODEL,
    budget_manager: Optional[Any] = None,
    timeout_seconds: int = 600,
    max_post_vote_retries: int = 1,
) -> Tuple[str, Dict[str, str]]:
    """
    Run a 3-persona debate to synthesize a research proposal.

    Parameters
    ----------
    task : str
        The research task / question that the personas evaluate.
    persona_specs : list[dict], optional
        Per-persona specs with keys ``persona``, ``model``, and optional
        provider-specific params (e.g. ``reasoning_effort``).  Defaults to
        :data:`DEFAULT_PERSONA_MODEL_SPECS`.
    max_debate_rounds : int
        Number of debate rounds (default 3).
    synthesis_model : str
        Model used for the final synthesis step.
    budget_manager : BudgetManager or None
        If provided, token usage is recorded for every LLM call.
    timeout_seconds : int
        Per-call timeout for ThreadPoolExecutor futures (default 600).
    max_post_vote_retries : int
        Max re-synthesis attempts if post-synthesis vote rejects (default 1).

    Returns
    -------
    (proposal_text, verdicts) : tuple[str, dict[str, str]]
        *proposal_text* is the 1-2 page synthesized proposal.
        *verdicts* maps persona name -> "ACCEPT" | "REJECT" | "UNKNOWN".
    """
    specs = persona_specs or DEFAULT_PERSONA_MODEL_SPECS

    # ------------------------------------------------------------------
    # Phase 1 — Independent evaluations (parallel)
    # ------------------------------------------------------------------

    def _evaluate(idx: int) -> Tuple[int, str]:
        spec = specs[idx]
        persona_name = spec["persona"]
        model_id = resolve_or_model(spec["model"])
        extra_params = {k: v for k, v in spec.items() if k not in ("persona", "model")}

        system_prompt = PERSONA_SYSTEM_PROMPTS.get(persona_name, "")
        try:
            resp = litellm.completion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": task},
                ],
                max_tokens=4096,
                **extra_params,
            )
            output = resp.choices[0].message.content or ""
            # Budget recorded automatically via litellm.completion monkey-patch
        except Exception as e:
            output = f"[{persona_name} error: {e}]"
        print(f"[persona_council] Phase 1 — {persona_name} evaluation complete.")
        return idx, output

    evaluations: List[str] = [""] * len(specs)
    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        futures = {pool.submit(_evaluate, i): i for i in range(len(specs))}
        try:
            for future in as_completed(futures, timeout=timeout_seconds + 60):
                try:
                    idx, output = future.result(timeout=timeout_seconds)
                    evaluations[idx] = output
                except TimeoutError:
                    for f, i in futures.items():
                        if f is future:
                            name = specs[i]["persona"]
                            evaluations[i] = f"[{name} error: timed out after {timeout_seconds}s]"
                            print(f"[persona_council] Phase 1 — {name} TIMED OUT.")
                            break
                except Exception as e:
                    for f, i in futures.items():
                        if f is future:
                            name = specs[i]["persona"]
                            evaluations[i] = f"[{name} error: {e}]"
                            print(f"[persona_council] Phase 1 — {name} error: {e}")
                            break
        except TimeoutError:
            print(f"[persona_council] Phase 1 evaluation timeout — some personas did not complete within {timeout_seconds + 60}s")
            for f, i in futures.items():
                if not f.done():
                    name = specs[i]["persona"]
                    evaluations[i] = f"[{name} error: timed out after {timeout_seconds}s]"
                    f.cancel()

    # Format evaluations for debate context
    formatted_evals = "\n\n".join(
        f"=== Evaluation by {specs[i]['persona']} ===\n{text}"
        for i, text in enumerate(evaluations)
    )

    # ------------------------------------------------------------------
    # Phase 2 — Debate rounds (parallel per round)
    # ------------------------------------------------------------------
    debate_history: List[str] = []

    for rnd in range(max_debate_rounds):
        debate_prompt = (
            f"Original task:\n{task}\n\n"
            f"Initial evaluations from all personas:\n\n{formatted_evals}\n\n"
        )
        if debate_history:
            debate_prompt += "Prior debate rounds:\n" + "\n---\n".join(debate_history) + "\n\n"
        debate_prompt += (
            "Your job in this round is to argue that this proposal should be REJECTED from your lens. "
            "Find the single strongest reason it should not proceed as written. "
            "Be a harsh critic, not a helpful colleague. "
            "Only concede a point if the evidence from another persona's evaluation is overwhelming. "
            "State clearly whether you maintain or change your verdict (ACCEPT/REJECT) and why."
        )

        def _one_critique(i: int) -> Tuple[int, str]:
            spec = specs[i]
            persona_name = spec["persona"]
            model_id = resolve_or_model(spec["model"])
            extra_params = {k: v for k, v in spec.items() if k not in ("persona", "model")}
            system_prompt = PERSONA_SYSTEM_PROMPTS.get(persona_name, "")
            try:
                resp = litellm.completion(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": debate_prompt},
                    ],
                    max_tokens=3072,
                    **extra_params,
                )
                critique = resp.choices[0].message.content or ""
                # Budget recorded automatically via litellm.completion monkey-patch
            except Exception as e:
                critique = f"[{persona_name} error: {e}]"
            return i, f"{persona_name}:\n{critique}"

        critiques: List[str] = [""] * len(specs)
        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            futures = {pool.submit(_one_critique, i): i for i in range(len(specs))}
            try:
                for future in as_completed(futures, timeout=timeout_seconds + 60):
                    try:
                        i, text = future.result(timeout=timeout_seconds)
                        critiques[i] = text
                    except TimeoutError:
                        for f, idx in futures.items():
                            if f is future:
                                name = specs[idx]["persona"]
                                critiques[idx] = f"{name}:\n[debate timed out after {timeout_seconds}s]"
                                print(f"[persona_council] Debate round {rnd + 1} — {name} TIMED OUT.")
                                break
                    except Exception as e:
                        for f, idx in futures.items():
                            if f is future:
                                name = specs[idx]["persona"]
                                critiques[idx] = f"{name}:\n[debate error: {e}]"
                                break
            except TimeoutError:
                print(f"[persona_council] Debate round {rnd + 1} timeout — some personas did not complete within {timeout_seconds + 60}s")
                for f, idx in futures.items():
                    if not f.done():
                        name = specs[idx]["persona"]
                        critiques[idx] = f"{name}:\n[debate timed out after {timeout_seconds}s]"
                        f.cancel()

        debate_history.append(f"[Round {rnd + 1}]\n" + "\n\n".join(critiques))
        print(f"[persona_council] Phase 2 — debate round {rnd + 1}/{max_debate_rounds} complete.")

    # ------------------------------------------------------------------
    # Phase 3 — Synthesis
    # ------------------------------------------------------------------

    # Extract verdicts before synthesis so we can pass them explicitly
    verdicts: Dict[str, str] = {}
    for i, spec in enumerate(specs):
        verdicts[spec["persona"]] = _extract_verdict(evaluations[i])

    verdict_summary = ", ".join(f"{k}={v}" for k, v in verdicts.items())
    accept_count = sum(1 for v in verdicts.values() if v == "ACCEPT")
    reject_count = sum(1 for v in verdicts.values() if v == "REJECT")

    synthesis_input = (
        f"VERDICT SUMMARY: {verdict_summary} "
        f"({accept_count} ACCEPT, {reject_count} REJECT)\n\n"
        f"Original task:\n{task}\n\n"
        f"Persona evaluations:\n\n{formatted_evals}\n\n"
        f"Debate ({len(debate_history)} rounds):\n" + "\n---\n".join(debate_history)
    )

    _routed_synthesis = resolve_or_model(synthesis_model)
    _synthesis_extra = {"reasoning_effort": "high"} if any(p in synthesis_model for p in ("claude", "gpt")) else {}
    try:
        resp = litellm.completion(
            model=_routed_synthesis,
            messages=[
                {"role": "system", "content": PERSONA_SYNTHESIS_PROMPT},
                {"role": "user", "content": synthesis_input},
            ],
            max_tokens=8192,
            **_synthesis_extra,
        )
        proposal_text = resp.choices[0].message.content or ""
        # Budget recorded automatically via litellm.completion monkey-patch
    except Exception as e:
        print(f"[persona_council] Synthesis failed ({e}), using first evaluation as fallback.")
        proposal_text = evaluations[0] if evaluations else f"[synthesis error: {e}]"

    # ------------------------------------------------------------------
    # Phase 4 — Post-synthesis accountability vote (parallel)
    # ------------------------------------------------------------------

    def _post_vote(idx: int, proposal: str) -> Tuple[int, str, str]:
        """Ask one persona to vote ACCEPT/REJECT on the synthesized proposal."""
        spec = specs[idx]
        persona_name = spec["persona"]
        model_id = resolve_or_model(spec["model"])
        extra_params = {k: v for k, v in spec.items() if k not in ("persona", "model")}

        system_prompt = PERSONA_SYSTEM_PROMPTS.get(persona_name, "")
        user_content = (
            f"{PERSONA_POST_SYNTHESIS_VOTE_PROMPT}\n\n"
            f"YOUR PERSONA: {persona_name}\n\n"
            f"YOUR INITIAL EVALUATION:\n{evaluations[idx]}\n\n"
            f"SYNTHESIZED PROPOSAL:\n{proposal}"
        )
        try:
            resp = litellm.completion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=1024,
                **extra_params,
            )
            vote_text = resp.choices[0].message.content or ""
        except Exception as e:
            vote_text = f"[{persona_name} vote error: {e}]"
        vote_verdict = _extract_verdict(vote_text)
        return idx, vote_verdict, vote_text

    for post_vote_attempt in range(max_post_vote_retries + 1):
        post_verdicts: Dict[str, str] = {}
        post_vote_texts: Dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            futures = {pool.submit(_post_vote, i, proposal_text): i for i in range(len(specs))}
            try:
                for future in as_completed(futures, timeout=timeout_seconds + 60):
                    try:
                        idx, vote_verdict, vote_text = future.result(timeout=timeout_seconds)
                        name = specs[idx]["persona"]
                        post_verdicts[name] = vote_verdict
                        post_vote_texts[name] = vote_text
                    except (TimeoutError, Exception) as e:
                        for f, i in futures.items():
                            if f is future:
                                name = specs[i]["persona"]
                                post_verdicts[name] = "UNKNOWN"
                                post_vote_texts[name] = f"[vote error: {e}]"
                                break
            except TimeoutError:
                for f, i in futures.items():
                    if not f.done():
                        name = specs[i]["persona"]
                        post_verdicts[name] = "UNKNOWN"
                        post_vote_texts[name] = "[vote timed out]"
                        f.cancel()

        post_reject_count = sum(1 for v in post_verdicts.values() if v == "REJECT")
        print(
            f"[persona_council] Phase 4 — post-synthesis vote "
            f"(attempt {post_vote_attempt + 1}): {post_verdicts}"
        )

        if post_reject_count < 2 or post_vote_attempt >= max_post_vote_retries:
            # Accept or retries exhausted — use current proposal
            verdicts = post_verdicts
            break

        # 2+ rejected — re-synthesize with objections appended
        objections = "\n\n".join(
            f"=== {name} POST-SYNTHESIS REJECTION ===\n{post_vote_texts[name]}"
            for name, v in post_verdicts.items() if v == "REJECT"
        )
        synthesis_input_retry = (
            synthesis_input + "\n\n"
            f"POST-SYNTHESIS VOTE: {post_reject_count} of {len(specs)} personas REJECTED "
            f"the synthesized proposal. Their objections:\n\n{objections}\n\n"
            "You MUST address these objections in a revised proposal."
        )
        try:
            resp = litellm.completion(
                model=_routed_synthesis,
                messages=[
                    {"role": "system", "content": PERSONA_SYNTHESIS_PROMPT},
                    {"role": "user", "content": synthesis_input_retry},
                ],
                max_tokens=8192,
                **_synthesis_extra,
            )
            proposal_text = resp.choices[0].message.content or ""
            print("[persona_council] Phase 4 — re-synthesis complete after post-vote rejection.")
        except Exception as e:
            print(f"[persona_council] Re-synthesis failed ({e}), keeping original proposal.")
            verdicts = post_verdicts
            break

    # Warn about UNKNOWN verdicts (parse failures or errors)
    unknown_personas = [name for name, v in verdicts.items() if v == "UNKNOWN"]
    if unknown_personas:
        print(
            f"[persona_council] WARNING: Phase 4 — {len(unknown_personas)} persona(s) "
            f"returned UNKNOWN verdict (parse failure or error): {unknown_personas}. "
            f"These were not counted toward re-synthesis threshold."
        )

    print(f"[persona_council] Complete. Final verdicts: {verdicts}")
    return proposal_text, verdicts


# ---------------------------------------------------------------------------
# Core: run_duality_check
# ---------------------------------------------------------------------------

def run_duality_check(
    workspace_dir: str,
    check_model: str = DEFAULT_DUALITY_CHECK_MODEL,
    budget_manager: Optional[Any] = None,
    timeout_seconds: int = 600,
) -> Dict[str, Any]:
    """
    Run parallel dual-lens evaluation of formalized results.

    Reads key workspace artifacts, then runs Check A and Check B in parallel.
    Each check produces a JSON verdict with ``passed``, ``reasoning``,
    ``score`` (1-10), and ``suggestions``.

    Parameters
    ----------
    workspace_dir : str
        Root workspace directory containing math_workspace / paper_workspace.
    check_model : str
        Model used for both checks.
    budget_manager : BudgetManager or None
        If provided, token usage is recorded.
    timeout_seconds : int
        Per-call timeout (default 600).

    Returns
    -------
    dict
        ``{both_passed: bool, check_a: {...}, check_b: {...}}``
    """
    # ------------------------------------------------------------------
    # Gather workspace context
    # ------------------------------------------------------------------
    context_files = {
        "research_proposal.md": os.path.join(workspace_dir, "paper_workspace", "research_proposal.md"),
        "formalized_results.md": os.path.join(workspace_dir, "math_workspace", "formalized_results.md"),
        "formalized_results.json": os.path.join(workspace_dir, "math_workspace", "formalized_results.json"),
        "claim_graph.json": os.path.join(workspace_dir, "math_workspace", "claim_graph.json"),
        "experiment_results.json": next(
            (p for p in [
                os.path.join(workspace_dir, "paper_workspace", "experiment_results.json"),
                os.path.join(workspace_dir, "experiment_workspace", "results_summary.json"),
                os.path.join(workspace_dir, "writeup_agent", "experiment_results.json"),
                os.path.join(workspace_dir, "experiment_results.json"),
            ] if os.path.isfile(p)),
            os.path.join(workspace_dir, "experiment_results.json"),  # fallback (may not exist)
        ),
    }

    # In iterate mode, include prior paper artifacts so the duality check can
    # evaluate existing proofs, experiments, and results from the prior draft
    # (not just the newly generated proposal).
    prior_paper_dir = os.path.join(workspace_dir, "paper_workspace", "prior_paper")
    if os.path.isdir(prior_paper_dir):
        for fname in sorted(os.listdir(prior_paper_dir)):
            fpath = os.path.join(prior_paper_dir, fname)
            if fname.endswith((".tex", ".md")) and os.path.isfile(fpath):
                context_files[f"prior_paper/{fname}"] = fpath

    # Also include the consolidated iteration feedback if present
    feedback_path = os.path.join(workspace_dir, "paper_workspace", "iteration_feedback.md")
    if os.path.isfile(feedback_path):
        context_files["iteration_feedback.md"] = feedback_path

    context_parts: List[str] = []
    for label, path in context_files.items():
        content = _read_file_truncated(path, _DUALITY_FILE_TRUNCATE)
        if content:
            context_parts.append(f"=== {label} ===\n{content}")

    workspace_context = "\n\n".join(context_parts) if context_parts else "[no workspace artifacts found]"

    # ------------------------------------------------------------------
    # Run Check A and Check B in parallel
    # ------------------------------------------------------------------
    default_fail = {"passed": False, "reasoning": "Check did not complete.", "score": 0, "suggestions": []}

    _routed_check = resolve_or_model(check_model)
    _check_extra = {"reasoning_effort": "high"} if any(p in check_model for p in ("claude", "gpt")) else {}

    def _run_check(prompt_template: str, check_label: str) -> Tuple[str, dict]:
        user_content = f"{prompt_template}\n\n--- WORKSPACE ARTIFACTS ---\n\n{workspace_context}"
        try:
            resp = litellm.completion(
                model=_routed_check,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=4096,
                **_check_extra,
            )
            raw = resp.choices[0].message.content or ""
            # Budget recorded automatically via litellm.completion monkey-patch

            parsed = _parse_json_response(raw)
            if parsed is None:
                print(f"[duality_check] {check_label}: failed to parse JSON from response.")
                return check_label, {
                    "passed": False,
                    "reasoning": f"Failed to parse JSON response. Raw: {raw[:500]}",
                    "score": 0,
                    "suggestions": [],
                }
            # Normalize keys
            result = {
                "passed": bool(parsed.get("passed", False)),
                "reasoning": str(parsed.get("reasoning", "")),
                "score": int(parsed.get("score", 0)),
                "suggestions": list(parsed.get("suggestions", [])),
            }
            return check_label, result
        except Exception as e:
            print(f"[duality_check] {check_label} error: {e}")
            return check_label, {
                "passed": False,
                "reasoning": f"Check error: {e}",
                "score": 0,
                "suggestions": [],
            }

    results: Dict[str, dict] = {"check_a": dict(default_fail), "check_b": dict(default_fail)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_run_check, DUALITY_CHECK_A_PROMPT, "check_a")
        future_b = pool.submit(_run_check, DUALITY_CHECK_B_PROMPT, "check_b")
        try:
            for future in as_completed([future_a, future_b], timeout=timeout_seconds + 60):
                try:
                    label, result = future.result(timeout=timeout_seconds)
                    results[label] = result
                except TimeoutError:
                    check_label = "check_a" if future is future_a else "check_b"
                    results[check_label] = {
                        "passed": False,
                        "reasoning": f"Timed out after {timeout_seconds}s",
                        "score": 0,
                        "suggestions": [],
                    }
                    print(f"[duality_check] {check_label} TIMED OUT.")
                except Exception as e:
                    check_label = "check_a" if future is future_a else "check_b"
                    results[check_label] = {
                        "passed": False,
                        "reasoning": f"Execution error: {e}",
                        "score": 0,
                        "suggestions": [],
                    }
        except TimeoutError:
            print(f"[duality_check] outer timeout — one or both checks did not complete within {timeout_seconds + 60}s")
            for future, check_label in [(future_a, "check_a"), (future_b, "check_b")]:
                if not future.done():
                    results[check_label] = {
                        "passed": False,
                        "reasoning": f"Timed out after {timeout_seconds}s",
                        "score": 0,
                        "suggestions": [],
                    }
                    future.cancel()

    both_passed = results["check_a"]["passed"] and results["check_b"]["passed"]
    final = {"both_passed": both_passed, "check_a": results["check_a"], "check_b": results["check_b"]}

    print(
        f"[duality_check] Complete. "
        f"A: {'PASS' if results['check_a']['passed'] else 'FAIL'} (score {results['check_a']['score']}), "
        f"B: {'PASS' if results['check_b']['passed'] else 'FAIL'} (score {results['check_b']['score']}). "
        f"Both passed: {both_passed}"
    )
    return final


# ---------------------------------------------------------------------------
# LangGraph node factories
# ---------------------------------------------------------------------------

def create_persona_council_node(
    workspace_dir: str,
    persona_specs: Optional[List[Dict[str, Any]]] = None,
    max_debate_rounds: int = 3,
    synthesis_model: str = DEFAULT_SYNTHESIS_MODEL,
    budget_manager: Optional[Any] = None,
    timeout_seconds: int = 600,
    max_post_vote_retries: int = 1,
) -> Callable:
    """
    Return a LangGraph node callable that runs the persona council.

    The node reads ``state["task"]``, invokes :func:`run_persona_council`,
    writes campaign-attached artifacts through the contract runtime when
    available, and returns a state-update dict.
    """

    def persona_council_node(state: dict) -> dict:
        task = state.get("agent_task") or state.get("task", "")

        proposal, verdicts = run_persona_council(
            task=task,
            persona_specs=persona_specs,
            max_debate_rounds=max_debate_rounds,
            synthesis_model=synthesis_model,
            budget_manager=budget_manager,
            timeout_seconds=timeout_seconds,
            max_post_vote_retries=max_post_vote_retries,
        )

        proposal_path = ""
        verdicts_path = ""
        try:
            from msc_sdk.stage_runtime import StageRunContext

            ctx = StageRunContext.from_env("persona_council")
        except Exception:
            ctx = None

        if ctx is not None:
            try:
                proposal_path = str(
                    ctx.write_required(
                        "artifacts/research_proposal.md",
                        proposal,
                        metadata={"run_id": ctx.run_id, "materializer": "persona_council"},
                    )
                )
                debate_text = "# Persona Debate\n\n" + json.dumps(verdicts, indent=2, sort_keys=True)
                ctx.write_required(
                    "artifacts/persona_debate.md",
                    debate_text,
                    metadata={"run_id": ctx.run_id, "materializer": "persona_council"},
                )
                verdicts_path = str(
                    ctx.write_optional(
                        "artifacts/persona_votes.json",
                        verdicts,
                        kind="json",
                        metadata={"run_id": ctx.run_id, "materializer": "persona_council"},
                    )
                )
            except Exception as e:
                return {
                    "critical_failure": f"persona_council failed to write contract artifacts: {e}",
                    "agent_task": None,
                }
        else:
            paper_ws = os.path.join(workspace_dir, "paper_workspace")
            os.makedirs(paper_ws, exist_ok=True)
            proposal_path = os.path.join(paper_ws, "research_proposal.md")
            try:
                with open(proposal_path, "w", encoding="utf-8") as f:
                    f.write(proposal)
            except Exception as e:
                print(f"[persona_council_node] Failed to write proposal: {e}")

            verdicts_path = os.path.join(paper_ws, "persona_verdicts.json")
            try:
                with open(verdicts_path, "w", encoding="utf-8") as f:
                    json.dump(verdicts, f, indent=2)
            except Exception as e:
                print(f"[persona_council_node] Failed to write verdicts: {e}")

        return {
            "agent_outputs": {
                **state.get("agent_outputs", {}),
                "persona_council": proposal,
            },
            "research_proposal": proposal,
            "artifacts": {
                **state.get("artifacts", {}),
                "research_proposal": proposal_path,
                "persona_verdicts": verdicts_path,
            },
        }

    persona_council_node.__name__ = "persona_council"
    return persona_council_node


def create_duality_check_node(
    workspace_dir: str,
    check_model: str = DEFAULT_DUALITY_CHECK_MODEL,
    budget_manager: Optional[Any] = None,
    timeout_seconds: int = 600,
) -> Callable:
    """
    Return a LangGraph node callable that runs the duality check.

    Invokes :func:`run_duality_check`, writes the result to
    ``paper_workspace/duality_check.json``, and returns a state-update dict.
    """

    def duality_check_node(state: dict) -> dict:
        results = run_duality_check(
            workspace_dir=workspace_dir,
            check_model=check_model,
            budget_manager=budget_manager,
            timeout_seconds=timeout_seconds,
        )

        # Write artifact
        paper_ws = os.path.join(workspace_dir, "paper_workspace")
        os.makedirs(paper_ws, exist_ok=True)

        check_path = os.path.join(paper_ws, "duality_check.json")
        try:
            with open(check_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
        except Exception as e:
            print(f"[duality_check_node] Failed to write duality check: {e}")

        # Build human-readable summary for agent_outputs
        summary_parts = []
        for label in ("check_a", "check_b"):
            c = results.get(label, {})
            status = "PASS" if c.get("passed") else "FAIL"
            summary_parts.append(f"{label}: {status} (score {c.get('score', '?')}/10)")
        summary = f"Duality check: {' | '.join(summary_parts)}. Both passed: {results.get('both_passed', False)}"

        return {
            "duality_check_result": results,
            "agent_outputs": {
                **state.get("agent_outputs", {}),
                "duality_check": summary,
            },
            "artifacts": {
                **state.get("artifacts", {}),
                "duality_check": check_path,
            },
        }

    duality_check_node.__name__ = "duality_check"
    return duality_check_node
