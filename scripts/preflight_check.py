#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def check_import(module_name: str) -> str | None:
    try:
        importlib.import_module(module_name)
        return None
    except Exception as exc:
        return f"{module_name}: {exc}"


def check_playwright_chromium() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return f"playwright import failed: {exc}"

    try:
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
            if not exe.exists():
                return (
                    "Chromium browser binary is missing. "
                    "Run: python -m playwright install chromium"
                )
    except Exception as exc:
        return f"playwright chromium check failed: {exc}"
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate consortium runtime dependencies and optional capabilities."
    )
    parser.add_argument(
        "--with-docs",
        action="store_true",
        help="Treat document parser stack as required.",
    )
    parser.add_argument(
        "--with-web",
        action="store_true",
        help="Treat web crawling stack as required.",
    )
    parser.add_argument(
        "--with-experiment",
        action="store_true",
        help="Treat experiment tool stack as required.",
    )
    parser.add_argument(
        "--with-latex",
        action="store_true",
        help="Treat LaTeX toolchain (pdflatex/bibtex) as required.",
    )
    return parser.parse_args()


def _check_modules(modules: list[str], errors: list[str], warnings: list[str], required: bool) -> None:
    for mod in modules:
        err = check_import(mod)
        if err:
            if required:
                errors.append(err)
            else:
                warnings.append(err)


def _resolve_latex_binary(
    tool_name: str,
    env_var: str,
    extra_candidates: list[str] | None = None,
) -> tuple[str | None, str | None]:
    override = os.getenv(env_var, "").strip()
    if override:
        override_path = Path(override).expanduser()
        if override_path.exists() and os.access(override_path, os.X_OK):
            return str(override_path), None
        return None, f"{env_var} points to missing/non-executable file: {override}"

    for candidate in extra_candidates or []:
        candidate_path = Path(candidate)
        if candidate_path.exists() and os.access(candidate_path, os.X_OK):
            return str(candidate_path), None

    found = shutil.which(tool_name)
    if found:
        return found, None
    return None, f"{tool_name} not found on PATH"


def _latex_install_hint() -> str:
    env_name = os.getenv("CONDA_DEFAULT_ENV", "consortium")
    return (
        "Install/repair options:\n"
        f"  - Conda route: ./scripts/bootstrap.sh {env_name} latex\n"
        f"  - Conda repair: ./scripts/fix_pdflatex_conda.sh {env_name}\n"
        "  - macOS MacTeX route: brew install --cask mactex\n"
        f"    and set persistent env vars:\n"
        f"    conda env config vars set -n {env_name} "
        "CONSORTIUM_PDFLATEX_PATH=/Library/TeX/texbin/pdflatex "
        "CONSORTIUM_BIBTEX_PATH=/Library/TeX/texbin/bibtex"
    )


def check_latex_toolchain() -> str | None:
    pdflatex, pdflatex_err = _resolve_latex_binary(
        tool_name="pdflatex",
        env_var="CONSORTIUM_PDFLATEX_PATH",
        extra_candidates=["/Library/TeX/texbin/pdflatex"],
    )
    if pdflatex_err:
        return f"{pdflatex_err}\n{_latex_install_hint()}"

    _bibtex, bibtex_err = _resolve_latex_binary(
        tool_name="bibtex",
        env_var="CONSORTIUM_BIBTEX_PATH",
        extra_candidates=["/Library/TeX/texbin/bibtex"],
    )
    if bibtex_err:
        return f"{bibtex_err}\n{_latex_install_hint()}"

    try:
        with tempfile.TemporaryDirectory() as td:
            tex_path = Path(td) / "preflight_latex_test.tex"
            tex_path.write_text(
                textwrap.dedent(
                    r"""
                    \documentclass{article}
                    \begin{document}
                    preflight latex check
                    \end{document}
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    pdflatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    str(tex_path.name),
                ],
                cwd=td,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                combined = f"{result.stdout}\n{result.stderr}"
                if "pdflatex.fmt" in combined:
                    return (
                        "pdflatex found but missing format file pdflatex.fmt. "
                        f"Run scripts/fix_pdflatex_conda.sh <env_name> or use MacTeX.\n{_latex_install_hint()}"
                    )
                return (
                    "pdflatex invocation failed during smoke compile. "
                    f"Return code: {result.returncode}.\n{_latex_install_hint()}"
                )
    except Exception as exc:
        return f"latex toolchain check failed: {exc}\n{_latex_install_hint()}"

    return None


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    warnings: list[str] = []

    # Load repo-local .env so API key checks reflect real runtime behavior.
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=env_path, override=False)
        except Exception as exc:
            warnings.append(f"Could not load .env: {exc}")

    required_modules = [
        "smolagents",
        "litellm",
        "yaml",
        "dotenv",
        "fitz",
        "pdfminer",
        "requests",
        "bs4",
        "pypdf",
    ]
    docs_modules = [
        "mammoth",
        "markdownify",
        "pptx",
        "puremagic",
        "pydub",
        "speech_recognition",
        "youtube_transcript_api",
    ]
    web_modules = [
        "crawl4ai",
        "playwright",
    ]
    experiment_modules = [
        "transformers",
        "datasets",
    ]

    _check_modules(required_modules, errors, warnings, required=True)
    _check_modules(docs_modules, errors, warnings, required=args.with_docs)
    _check_modules(web_modules, errors, warnings, required=args.with_web)
    _check_modules(experiment_modules, errors, warnings, required=args.with_experiment)

    latex_err = check_latex_toolchain()
    if latex_err:
        if args.with_latex:
            errors.append(latex_err)
        else:
            warnings.append(latex_err)

    # Validate chromium only if web capability is requested/installed.
    if args.with_web or check_import("playwright") is None:
        err = check_playwright_chromium()
        if err:
            if args.with_web:
                errors.append(err)
            else:
                warnings.append(err)

    # Warn if pydub is installed but ffmpeg is not available.
    if check_import("pydub") is None and shutil.which("ffmpeg") is None:
        warnings.append(
            "pydub is installed but ffmpeg is not on PATH. "
            "Install ffmpeg for audio transcription features."
        )

    if not os.getenv("OPENROUTER_API_KEY"):
        warnings.append(
            "OPENROUTER_API_KEY not set. All LLM calls are routed through OpenRouter.\n"
            "  Add it to your .env file:  OPENROUTER_API_KEY=your_key_here"
        )

    llm_cfg = REPO_ROOT / ".llm_config.yaml"
    if not llm_cfg.exists():
        warnings.append(".llm_config.yaml not found at repo root.")

    print("=== consortium preflight ===")
    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("No dependency errors found.")

    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print("\nPreflight failed.")
        return 1

    print("\nPreflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
