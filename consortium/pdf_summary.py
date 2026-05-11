"""
Automatic PDF summary generation for each pipeline stage.

After every agent/counsel stage completes, generates a standalone LaTeX PDF
summarising the stage's converged output.  The wrapper function
``with_pdf_summary`` decorates LangGraph node callables so that PDF generation
is a transparent, fail-safe side effect that never crashes the pipeline.

Output location: ``{workspace_dir}/stage_summaries/{agent_name}_summary.pdf``
"""

from __future__ import annotations

import os
import re
import subprocess
import shutil
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .models import get_openrouter_name


# ---------------------------------------------------------------------------
# Human-readable display names for each agent
# ---------------------------------------------------------------------------

AGENT_DISPLAY_NAMES: dict[str, str] = {
    "ideation_agent": "Research Ideation",
    "literature_review_agent": "Literature Review",
    "research_planner_agent": "Research Plan",
    "math_literature_agent": "Mathematical Literature Review",
    "math_proposer_agent": "Mathematical Claim Proposal",
    "math_prover_agent": "Mathematical Proof Development",
    "math_rigorous_verifier_agent": "Rigorous Proof Verification",
    "math_empirical_verifier_agent": "Empirical Verification",
    "proof_transcription_agent": "Proof Transcription",
    "experiment_literature_agent": "Experimental Literature Review",
    "experiment_design_agent": "Experiment Design",
    "experimentation_agent": "Experimentation",
    "experiment_verification_agent": "Experiment Verification",
    "experiment_transcription_agent": "Experiment Transcription",
    "synthesis_literature_review_agent": "Synthesis Literature Review",
    "results_analysis_agent": "Results Analysis",
    "resource_preparation_agent": "Resource Preparation",
    "writeup_agent": "Paper Writeup",
    "proofreading_agent": "Proofreading",
    "reviewer_agent": "Peer Review",
}

# Maximum characters of agent output sent to the formatting LLM.
_MAX_OUTPUT_CHARS = 50_000


def _normalize_model_for_litellm(model_id: str) -> str:
    """Normalize internal model names for direct LiteLLM/OpenRouter calls."""
    if model_id.startswith("openrouter/"):
        return model_id
    return f"openrouter/{get_openrouter_name(model_id)}"

# ---------------------------------------------------------------------------
# pdflatex discovery (mirrors LaTeXCompilerTool logic)
# ---------------------------------------------------------------------------


def _find_pdflatex_path() -> Optional[str]:
    """Locate a usable pdflatex binary on the system."""
    override = os.environ.get("CONSORTIUM_PDFLATEX_PATH", "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override

    # MacTeX default
    mactex = "/Library/TeX/texbin/pdflatex"
    if os.path.isfile(mactex) and os.access(mactex, os.X_OK):
        return mactex

    # which pdflatex
    try:
        result = subprocess.run(
            ["which", "pdflatex"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    except Exception:
        pass

    # Common install locations
    common = [
        "/usr/bin/pdflatex",
        "/usr/local/bin/pdflatex",
        "/Library/TeX/texbin/pdflatex",
        "/opt/texlive/2025/bin/x86_64-linux/pdflatex",
        "/usr/local/texlive/2025/bin/x86_64-linux/pdflatex",
    ]
    for p in common:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    # Walk PATH
    try:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(d, "pdflatex")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# LaTeX template
# ---------------------------------------------------------------------------


def _build_summary_latex(
    agent_name: str,
    formatted_content: str,
    timestamp: str,
) -> str:
    """Return a complete, standalone LaTeX document string."""
    display_name = AGENT_DISPLAY_NAMES.get(agent_name, agent_name.replace("_", " ").title())
    # Escape LaTeX special chars in the title (display_name is safe, but be defensive)
    safe_title = display_name.replace("&", r"\&").replace("_", r"\_")

    return rf"""\documentclass[11pt]{{article}}
\usepackage[margin=1in]{{geometry}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage{{amsmath,amssymb,amsthm}}
\usepackage{{enumitem}}
\usepackage{{hyperref}}
\usepackage{{longtable}}
\usepackage{{booktabs}}
\usepackage{{fancyhdr}}
\usepackage{{xcolor}}
\usepackage{{verbatim}}

\hypersetup{{colorlinks=true,linkcolor=blue,urlcolor=blue,citecolor=blue}}

\pagestyle{{fancy}}
\fancyhf{{}}
\fancyhead[L]{{\small {safe_title}}}
\fancyhead[R]{{\small Stage Summary}}
\fancyfoot[C]{{\thepage}}

\title{{{safe_title} — Stage Summary}}
\author{{AI Researcher Consortium}}
\date{{{timestamp}}}

\begin{{document}}
\maketitle
\tableofcontents
\vspace{{1em}}
\hrule
\vspace{{1em}}

{formatted_content}

\end{{document}}
"""


# ---------------------------------------------------------------------------
# LLM-based output → LaTeX formatting
# ---------------------------------------------------------------------------


def _escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain text."""
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _fallback_format(agent_output: str) -> str:
    """Simple verbatim fallback when the LLM formatter is unavailable."""
    escaped = _escape_latex(agent_output)
    # Wrap long text in a small-font environment for readability
    return (
        "\\begin{small}\n"
        + escaped
        + "\n\\end{small}\n"
    )


def _format_output_as_latex(
    agent_output: str,
    agent_name: str,
    model_id: str,
) -> str:
    """Use an LLM to convert the agent's text/markdown output to LaTeX body content."""
    import litellm

    display_name = AGENT_DISPLAY_NAMES.get(agent_name, agent_name.replace("_", " ").title())

    # Truncate very long outputs
    truncated = False
    text = agent_output
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS]
        truncated = True

    prompt = f"""You are a LaTeX formatting assistant. Convert the following research agent output
into clean, well-structured LaTeX body content.

This is the output of the "{display_name}" stage of an AI research pipeline.

RULES — follow these exactly:
- Output ONLY LaTeX body content. Do NOT include \\documentclass, \\usepackage,
  \\begin{{document}}, \\end{{document}}, or any preamble.
- Convert markdown headers (##, ###, ####) to \\section{{}}, \\subsection{{}}, \\subsubsection{{}}.
- Convert bullet lists (- or *) to \\begin{{itemize}}...\\end{{itemize}}.
- Convert numbered lists (1. 2. 3.) to \\begin{{enumerate}}...\\end{{enumerate}}.
- Preserve all mathematical notation — use $...$ for inline math and \\[...\\] for display math.
  If the original text uses LaTeX math commands, keep them as-is.
- Escape special LaTeX characters in prose text: &, %, #, _, etc.
- Convert **bold** to \\textbf{{}} and *italic* to \\textit{{}}.
- Convert code blocks (```) to \\begin{{verbatim}}...\\end{{verbatim}}.
- Convert inline code (`...`) to \\texttt{{}}.
- Use \\paragraph{{}} for minor headings within subsections.
- Keep citations, references, URLs, and DOIs as-is (wrap URLs in \\url{{}}).
- Structure the content logically with clear sections.
- Do NOT fabricate content — only format what is provided.
- Ensure all LaTeX environments are properly opened and closed.
- Do NOT wrap the output in ```latex``` code fences.

AGENT OUTPUT TO FORMAT:
---
{text}
---
"""
    if truncated:
        prompt += "\n\nNOTE: The original output was truncated for length. Add a note at the end: " \
                  "``\\textit{{(Output truncated — see full pipeline state for complete text.)}}''"

    try:
        litellm_model_id = _normalize_model_for_litellm(model_id)
        resp = litellm.completion(
            model=litellm_model_id,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16384,
        )
        content = resp.choices[0].message.content or ""
        # Strip any accidental code fences the model may have added
        content = content.strip()
        content = re.sub(r"^```(?:latex)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        if not content.strip():
            return _fallback_format(agent_output)
        return content
    except Exception as e:
        print(f"[pdf_summary] LLM formatting failed for {agent_name}: {e}")
        return _fallback_format(agent_output)


# ---------------------------------------------------------------------------
# PDF compilation
# ---------------------------------------------------------------------------


def _compile_tex_to_pdf(tex_path: str) -> Optional[str]:
    """Compile a .tex file to PDF. Returns the PDF path or None."""
    pdflatex = _find_pdflatex_path()
    if not pdflatex:
        print("[pdf_summary] pdflatex not found — .tex file written but PDF skipped.")
        return None

    tex_dir = os.path.dirname(os.path.abspath(tex_path))
    tex_filename = os.path.basename(tex_path)
    pdf_path = tex_path.replace(".tex", ".pdf")

    env = os.environ.copy()
    env["PATH"] = f"{os.path.dirname(pdflatex)}:{env.get('PATH', '')}"

    try:
        # Two passes: first resolves references, second fills them in
        # Use cwd= instead of os.chdir to avoid process-wide race conditions
        for pass_num in range(1, 3):
            result = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", tex_filename],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
                cwd=tex_dir,
            )
            if result.returncode != 0 and pass_num == 1:
                # Log but try second pass anyway (some warnings are non-fatal)
                print(
                    f"[pdf_summary] pdflatex pass {pass_num} returned {result.returncode}"
                )

        if os.path.exists(pdf_path):
            return pdf_path

        print("[pdf_summary] PDF not created despite compilation attempt.")
        return None

    except subprocess.TimeoutExpired:
        print("[pdf_summary] pdflatex timed out.")
        return None
    except Exception as e:
        print(f"[pdf_summary] compilation error: {e}")
        return None


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def generate_stage_summary_pdf(
    agent_name: str,
    agent_output: str,
    workspace_dir: str,
    model_id: str = "claude-sonnet-4-5",
) -> Optional[str]:
    """
    Generate a standalone PDF summarising one pipeline stage's output.

    Returns the PDF path on success, or None on failure.
    Never raises — all errors are caught and logged.
    """
    try:
        summary_dir = os.path.join(workspace_dir, "stage_summaries")
        os.makedirs(summary_dir, exist_ok=True)

        # 1. Format agent output as LaTeX body via LLM
        formatted_content = _format_output_as_latex(agent_output, agent_name, model_id)

        # 2. Wrap in full document template
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        full_latex = _build_summary_latex(agent_name, formatted_content, timestamp)

        # 3. Write .tex file
        tex_path = os.path.join(summary_dir, f"{agent_name}_summary.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(full_latex)

        # 4. Compile to PDF
        pdf_path = _compile_tex_to_pdf(tex_path)
        return pdf_path

    except Exception as e:
        print(f"[pdf_summary] generate_stage_summary_pdf failed for {agent_name}: {e}")
        return None


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def with_pdf_summary(
    node_fn: Callable,
    agent_name: str,
    workspace_dir: str,
    model_id: Optional[str] = "claude-sonnet-4-5",
) -> Callable:
    """
    Wrap a LangGraph node callable to generate a PDF summary after execution.

    The wrapper:
    1. Calls the original node function
    2. Extracts the agent's output from the returned state update
    3. Generates a PDF summary as a side effect (never modifies the state)
    4. Returns the original state update unchanged

    If ``model_id`` is None, the wrapper is a no-op passthrough.
    """
    if model_id is None:
        return node_fn

    def wrapped_node(state: dict) -> dict:
        # Run the original node
        result = node_fn(state)

        # Extract agent output from the result
        agent_output = (result or {}).get("agent_outputs", {}).get(agent_name)

        if agent_output and isinstance(agent_output, str) and agent_output.strip():
            try:
                pdf_path = generate_stage_summary_pdf(
                    agent_name=agent_name,
                    agent_output=agent_output,
                    workspace_dir=workspace_dir,
                    model_id=model_id,
                )
                if pdf_path:
                    print(f"[pdf_summary] Generated: {pdf_path}")
                else:
                    # .tex was still written even if PDF compilation failed
                    tex_path = os.path.join(
                        workspace_dir, "stage_summaries", f"{agent_name}_summary.tex"
                    )
                    if os.path.exists(tex_path):
                        print(f"[pdf_summary] .tex written (PDF compilation failed): {tex_path}")
            except Exception as e:
                print(f"[pdf_summary] Failed for {agent_name}: {e}")

        return result

    # Preserve the original function name for LangGraph introspection
    wrapped_node.__name__ = getattr(node_fn, "__name__", agent_name)
    return wrapped_node
