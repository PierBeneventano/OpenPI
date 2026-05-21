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

def _emit_council_event(event_type: str, payload: Dict[str, Any]) -> None:
    """Append a ``PersonaCouncil*`` event to the campaign event log.

    Best-effort: silently no-ops outside a campaign context (when there's no
    ``MSC_CAMPAIGN_ID`` set) so the council still works in standalone tests.
    """
    campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
    campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
    if not campaign_id or not campaign_root:
        return
    try:
        from msc_sdk.campaign_store import CampaignStore

        CampaignStore(campaign_root).append_event(
            campaign_id, event_type, actor="persona_council", payload=payload
        )
    except Exception as exc:
        # Never let event-bookkeeping kill the council itself.
        print(f"[persona_council] Could not emit {event_type}: {exc}")


def _synthesize_proposal(
    model: str,
    user_content: str,
    synthesis_extra: Dict[str, Any],
    *,
    fallback_text: str = "",
) -> str:
    """Call the synthesizer with enough budget for a real proposal.

    Two failure modes the original code hit on muon-test-v5:

    1. ``max_tokens=8192`` was too tight. With ``reasoning_effort="high"`` on
       Sonnet/GPT models, internal reasoning tokens consume most of the cap
       and ``content`` comes back empty. We raise to 32768 so reasoning has
       room *and* there's a real proposal left over.

    2. When the response *is* empty (truncation, finish_reason=length,
       safety filter, etc.) the original code silently used ``""``, the
       personas unanimously rejected the empty string, and the loop spun
       through 5 attempts producing nothing. We now detect that and retry
       once with ``reasoning_effort`` dropped so all tokens go to content.

    Returns the proposal text, or ``fallback_text`` if both attempts come
    back empty.
    """
    def _do_call(extra: Dict[str, Any], max_tokens: int) -> tuple[str, str]:
        resp = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": PERSONA_SYNTHESIS_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
            **extra,
        )
        text = (resp.choices[0].message.content or "").strip()
        finish_reason = ""
        try:
            finish_reason = str(getattr(resp.choices[0], "finish_reason", "") or "")
        except (AttributeError, IndexError):
            pass
        return text, finish_reason

    try:
        text, finish = _do_call(synthesis_extra, max_tokens=32768)
    except Exception as e:
        print(f"[persona_council] Synthesis call failed ({e}); using fallback.")
        return fallback_text

    if text:
        if finish == "length":
            print(
                f"[persona_council] WARNING: synthesis hit max_tokens "
                f"(finish_reason=length, {len(text)} chars produced). "
                f"Proposal may be truncated."
            )
        return text

    # Empty response. Most common cause: reasoning_effort=high consumed the
    # whole token budget. Retry once without it so all tokens are output.
    print(
        f"[persona_council] WARNING: synthesis returned empty content "
        f"(finish_reason={finish!r}). Retrying without reasoning_effort=high."
    )
    fallback_extra = {k: v for k, v in synthesis_extra.items() if k != "reasoning_effort"}
    try:
        text, _ = _do_call(fallback_extra, max_tokens=32768)
    except Exception as e:
        print(f"[persona_council] Fallback synthesis failed ({e}); using prior draft.")
        return fallback_text
    if text:
        return text
    print("[persona_council] WARNING: fallback synthesis also returned empty; using prior draft.")
    return fallback_text


def run_persona_council(
    task: str,
    persona_specs: Optional[List[Dict[str, Any]]] = None,
    max_debate_rounds: int = 3,
    synthesis_model: str = DEFAULT_SYNTHESIS_MODEL,
    budget_manager: Optional[Any] = None,
    timeout_seconds: int = 600,
    max_synthesis_attempts: int = 5,
    max_post_vote_retries: Optional[int] = None,
) -> Tuple[str, Dict[str, str], bool]:
    """
    Run a 3-persona debate to synthesize a research proposal.

    The council loops: synthesize → vote → (on consensus) return; (on rejection)
    re-synthesize with the rejection rationale appended. It only gives up when
    ``max_synthesis_attempts`` is exhausted, and the third return value flags
    that case so the caller can stop the graph instead of advancing.

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
    max_synthesis_attempts : int
        Safety cap on the synthesize-vote-revise loop (default 5). Above this,
        the council halts with ``deadlocked=True``; the caller should park the
        graph and write a decision rather than silently advancing.
    max_post_vote_retries : int, optional
        Deprecated. Back-compat alias: if set, becomes
        ``max_synthesis_attempts = max_post_vote_retries + 1``.

    Returns
    -------
    (proposal_text, verdicts, deadlocked) : tuple[str, dict[str, str], bool]
        *proposal_text* is the latest synthesized 1-2 page proposal.
        *verdicts* maps persona name -> "ACCEPT" | "REJECT" | "UNKNOWN".
        *deadlocked* is True iff the cap was hit without 2-of-3 ACCEPT.
    """
    # Back-compat for old call sites that still pass max_post_vote_retries.
    if max_post_vote_retries is not None:
        max_synthesis_attempts = max(1, int(max_post_vote_retries) + 1)
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
    proposal_text = _synthesize_proposal(
        _routed_synthesis, synthesis_input, _synthesis_extra,
        fallback_text=evaluations[0] if evaluations else "",
    )

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

    # Loop the synthesize -> vote -> revise cycle until 2-of-3 ACCEPT or we hit
    # the safety cap. Each attempt's verdicts + proposal preview is emitted as
    # a PersonaCouncilAttempt event so the VSCode UI can render progress live.
    consensus = False
    attempt = 0
    post_verdicts: Dict[str, str] = {}
    post_vote_texts: Dict[str, str] = {}

    # Guard: if the initial synthesis came back empty even after the
    # fallback retry, voting on it is pure waste. Bail to the deadlock
    # path immediately so the human can intervene rather than spinning
    # the loop on an empty string (this was the v5 failure mode before
    # _synthesize_proposal landed).
    if not proposal_text.strip():
        print(
            "[persona_council] DEADLOCKED before voting: synthesis returned "
            "empty content; cannot ask personas to evaluate an empty plan."
        )
        return proposal_text, {spec["persona"]: "UNKNOWN" for spec in specs}, True, {}

    while attempt < max_synthesis_attempts:
        attempt += 1
        post_verdicts = {}
        post_vote_texts = {}

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

        accept_count = sum(1 for v in post_verdicts.values() if v == "ACCEPT")
        post_reject_count = sum(1 for v in post_verdicts.values() if v == "REJECT")
        print(
            f"[persona_council] Phase 4 — post-synthesis vote "
            f"(attempt {attempt}/{max_synthesis_attempts}): {post_verdicts}"
        )

        # Emit a per-attempt event so the VSCode node-detail panel can show
        # the evolving verdicts and the latest proposal preview live.
        _emit_council_event(
            "PersonaCouncilAttempt",
            {
                "attempt": attempt,
                "max_attempts": max_synthesis_attempts,
                "verdicts": post_verdicts,
                "accept_count": accept_count,
                "reject_count": post_reject_count,
                "proposal_preview": proposal_text[:400],
            },
        )

        # Consensus is 2-of-3 ACCEPT. The remaining persona is allowed to be
        # REJECT or UNKNOWN; this matches the existing threshold.
        if accept_count >= 2:
            consensus = True
            break

        # Below the cap and no consensus — revise with the rejection rationale.
        # The rejection text is THREADED INTO the next synthesis prompt so the
        # next attempt isn't just a coin-flip retry but a genuine evolution.
        if attempt >= max_synthesis_attempts:
            break  # Out of attempts; fall through to deadlock handling.

        objections = "\n\n".join(
            f"=== {name} POST-SYNTHESIS REJECTION ===\n{post_vote_texts[name]}"
            for name, v in post_verdicts.items() if v == "REJECT"
        )
        synthesis_input_retry = (
            synthesis_input + "\n\n"
            f"POST-SYNTHESIS VOTE (attempt {attempt}/{max_synthesis_attempts}): "
            f"{post_reject_count} of {len(specs)} personas REJECTED the synthesized "
            f"proposal. Their objections:\n\n{objections}\n\n"
            "You MUST evolve the proposal to address these specific objections. "
            "Do not merely rephrase; produce a revised plan that genuinely "
            "responds to the criticisms."
        )
        proposal_text = _synthesize_proposal(
            _routed_synthesis, synthesis_input_retry, _synthesis_extra,
            fallback_text=proposal_text,  # keep previous draft if re-synth fails
        )
        if not proposal_text.strip():
            print(f"[persona_council] Re-synthesis returned empty on attempt {attempt}; halting.")
            break
        print(
            f"[persona_council] Phase 4 — re-synthesis complete after "
            f"attempt {attempt} rejection."
        )

    verdicts = post_verdicts
    deadlocked = not consensus

    # Warn about UNKNOWN verdicts (parse failures or errors)
    unknown_personas = [name for name, v in verdicts.items() if v == "UNKNOWN"]
    if unknown_personas:
        print(
            f"[persona_council] WARNING: Phase 4 — {len(unknown_personas)} persona(s) "
            f"returned UNKNOWN verdict (parse failure or error): {unknown_personas}. "
            f"These were not counted toward consensus threshold."
        )

    if deadlocked:
        print(
            f"[persona_council] DEADLOCKED after {attempt} attempts. "
            f"Final verdicts: {verdicts}. Caller should halt the stage and "
            f"open a human-decision."
        )
    else:
        print(f"[persona_council] CONSENSUS reached on attempt {attempt}. "
              f"Final verdicts: {verdicts}")
    # 4-tuple: includes the final per-persona vote texts so the node wrapper
    # can attach them to the deadlock decision metadata for the UI to render.
    # Callers that unpack a 3-tuple are unaffected — _run_persona_council_compat
    # below preserves the old shape for them.
    return proposal_text, verdicts, deadlocked, post_vote_texts


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
    max_synthesis_attempts: int = 5,
    deadlock_policy: str = "pause",
    max_post_vote_retries: Optional[int] = None,
) -> Callable:
    """
    Return a LangGraph node callable that runs the persona council.

    The node reads ``state["task"]``, invokes :func:`run_persona_council`,
    writes campaign-attached artifacts through the contract runtime when
    available, and returns a state-update dict.

    Parameters
    ----------
    max_synthesis_attempts : int
        Safety cap on the council's evolve-and-vote loop (default 5).
    deadlock_policy : {"pause", "best_effort"}
        On safety-cap exhaustion without consensus: ``"pause"`` (default)
        halts the stage and opens a human-decision; ``"best_effort"`` writes
        the latest proposal and lets the graph advance.
    max_post_vote_retries : int, optional
        Deprecated alias; if set, becomes
        ``max_synthesis_attempts = max_post_vote_retries + 1``.
    """
    # Back-compat for older callers
    if max_post_vote_retries is not None:
        max_synthesis_attempts = max(1, int(max_post_vote_retries) + 1)

    # Allow per-campaign overrides via metadata. The campaign client wrote
    # `persona_max_synthesis_attempts` / `persona_deadlock_policy` when the
    # user adjusted the Advanced section of the New Campaign modal.
    campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
    campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
    if campaign_id and campaign_root:
        try:
            from msc_sdk.campaign_store import CampaignStore

            meta = CampaignStore(campaign_root).get_campaign_metadata(campaign_id)
            override_attempts = meta.get("persona_max_synthesis_attempts")
            if override_attempts is not None:
                max_synthesis_attempts = max(1, int(override_attempts))
            override_policy = meta.get("persona_deadlock_policy")
            if override_policy in ("pause", "best_effort"):
                deadlock_policy = override_policy
            override_rounds = meta.get("persona_debate_rounds")
            if override_rounds is not None:
                max_debate_rounds = max(1, int(override_rounds))
        except Exception:
            pass  # silent fallback to args defaults

    def persona_council_node(state: dict) -> dict:
        # Pre-check: if a previous deadlock has been resolved by a human
        # (either accept_as_is or edited), skip the council entirely and use
        # the resolved proposal as our output. This is what makes the
        # 'Accept as-is' / 'Edit plan and proceed' UI buttons actually take
        # effect on the next `hpc submit` — without it, the council would
        # just deadlock the same way again.
        resolved = _check_resolved_deadlock()
        if resolved:
            print(
                f"[persona_council] Honoring human-resolved deadlock: "
                f"status={resolved['status']} proposal_path={resolved['proposal_path']}"
            )
            return {
                "agent_outputs": {
                    **state.get("agent_outputs", {}),
                    "persona_council": resolved["proposal_text"],
                },
                "research_proposal": resolved["proposal_text"],
                "persona_council_consensus": True,
                "persona_council_human_resolved": True,
                "persona_council_resolution": resolved["status"],
                "artifacts": {
                    **state.get("artifacts", {}),
                    "research_proposal": resolved["proposal_path"],
                },
            }

        task = state.get("agent_task") or state.get("task", "")

        proposal, verdicts, deadlocked, rationales = run_persona_council(
            task=task,
            persona_specs=persona_specs,
            max_debate_rounds=max_debate_rounds,
            synthesis_model=synthesis_model,
            budget_manager=budget_manager,
            timeout_seconds=timeout_seconds,
            max_synthesis_attempts=max_synthesis_attempts,
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

        state_update: Dict[str, Any] = {
            "agent_outputs": {
                **state.get("agent_outputs", {}),
                "persona_council": proposal,
            },
            "research_proposal": proposal,
            "persona_council_consensus": not deadlocked,
            "persona_council_verdicts": verdicts,
            "artifacts": {
                **state.get("artifacts", {}),
                "research_proposal": proposal_path,
                "persona_verdicts": verdicts_path,
            },
        }

        if deadlocked and deadlock_policy == "pause":
            # Halt the stage: open a human-decision and signal the graph router
            # to park rather than advance. The proposal is still written above
            # so the user can read what the personas couldn't agree on.
            _open_deadlock_decision(
                verdicts=verdicts,
                rationales=rationales,
                proposal_path=proposal_path,
                attempts=max_synthesis_attempts,
            )
            state_update["critical_failure"] = "persona_council_deadlock"
            state_update["human_decision_required"] = True

        return state_update

    persona_council_node.__name__ = "persona_council"
    return persona_council_node


def _check_resolved_deadlock() -> Optional[Dict[str, Any]]:
    """Look for a previously-resolved persona_council_deadlock in campaign metadata.

    Returns ``None`` outside a campaign context or when no resolved blob
    exists. On a hit, reads the proposal file from disk and returns
    ``{"proposal_text", "proposal_path", "status"}`` so the node can return
    the resolved proposal as its output instead of running the council.

    Also *clears* the blob after consumption — a fresh deadlock in a later
    attempt opens its own decision, rather than silently honoring stale
    resolution.
    """
    campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
    campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
    if not campaign_id or not campaign_root:
        return None
    try:
        from msc_sdk.campaign_store import CampaignStore

        store = CampaignStore(campaign_root)
        meta = store.get_campaign_metadata(campaign_id)
        blob = meta.get("persona_council_deadlock") or {}
        status = blob.get("status")
        if status not in ("accepted_as_is", "edited"):
            return None
        proposal_path = blob.get("proposal_path") or ""
        if not proposal_path or not os.path.exists(proposal_path):
            print(
                f"[persona_council] WARNING: deadlock resolution found but proposal "
                f"path missing or unreadable: {proposal_path!r}; falling back to "
                f"running the council."
            )
            return None
        with open(proposal_path, "r", encoding="utf-8") as handle:
            proposal_text = handle.read()
        # Clear the blob so a later deadlock isn't silently auto-resolved.
        store.update_campaign_metadata(
            campaign_id, {"persona_council_deadlock": None}, actor="persona_council"
        )
        # Audit event so the run-history drawer shows the boundary.
        store.append_event(
            campaign_id,
            "PersonaCouncilHumanResolved",
            actor="persona_council",
            payload={
                "decision_id": blob.get("decision_id"),
                "status": status,
                "proposal_path": proposal_path,
                "char_count": len(proposal_text),
            },
        )
        return {
            "proposal_text": proposal_text,
            "proposal_path": proposal_path,
            "status": status,
        }
    except Exception as exc:
        print(f"[persona_council] _check_resolved_deadlock error: {exc}")
        return None


def _open_deadlock_decision(
    *,
    verdicts: Dict[str, str],
    rationales: Dict[str, str],
    proposal_path: str,
    attempts: int,
) -> None:
    """Open a ``persona_council_deadlock`` decision on the campaign.

    Best-effort: silently no-ops outside a campaign context. The decision
    carries each persona's final verdict + the artifact path so the UI can
    surface 'approve current proposal / send guidance / abort' actions.

    Also attaches a structured ``persona_council_deadlock`` blob to the
    campaign metadata so the VSCode DecisionsTab can render verdicts +
    per-persona rationales + the latest proposal without having to parse
    the free-form reason string.
    """
    campaign_id = os.environ.get("MSC_CAMPAIGN_ID")
    campaign_root = os.environ.get("MSC_CAMPAIGN_ROOT")
    if not campaign_id or not campaign_root:
        return
    try:
        from msc_sdk.campaign_store import CampaignStore

        rejection_summary = ", ".join(f"{k}={v}" for k, v in verdicts.items())
        store = CampaignStore(campaign_root)
        proposal = store.propose_repair(
            campaign_id,
            node_id="persona_council",
            reason=(
                f"Persona council could not reach consensus after the safety cap. "
                f"Final verdicts: {rejection_summary}. Latest proposal written to "
                f"{proposal_path}. Choose: edit the plan, accept as-is, or abort."
            ),
            actor="persona_council",
        )
        # Pull the approval id out of the proposal dict (created by
        # propose_graph_change). It's nested under proposal["approval"]["id"].
        decision_id = ""
        try:
            decision_id = (proposal.get("approval") or {}).get("id") or ""
        except Exception:
            pass
        # Stash structured details on campaign metadata. The UI's DecisionsTab
        # banner reads this blob directly to render the "why it failed"
        # summary; `_check_resolved_deadlock` reads `status` on the next run
        # to know whether to skip the council.
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store.update_campaign_metadata(
            campaign_id,
            {
                "persona_council_deadlock": {
                    "decision_id": decision_id,
                    "node_id": "persona_council",
                    "verdicts": verdicts,
                    "rationales": rationales,
                    "proposal_path": str(proposal_path),
                    "attempts": int(attempts),
                    "opened_at": now_iso,
                    "status": "open",
                }
            },
            actor="persona_council",
        )
    except Exception as exc:
        print(f"[persona_council] Could not open deadlock decision: {exc}")


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
