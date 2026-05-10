#!/usr/bin/env python3
"""Heuristic analyzer for a cheap full MSc smoke-test workspace.

This script intentionally lives outside the research kernel. It reads artifacts
after a run and reports whether the workspace is useful as a canary for product
work: did the run complete, did it stay on cheap model surfaces, did core
metadata appear, and did the paper artifact look non-placeholder?
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_ALLOWED_MODELS = {
    "gpt-5-mini",
    "openrouter/openai/gpt-5-mini",
    "openai/gpt-5-mini",
    "openrouter/gpt-5-mini",
    "openrouter/perplexity/sonar-pro",
    "perplexity/sonar-pro",
}

PLACEHOLDER_PATTERNS = (
    "TODO",
    "TBD",
    "Research Paper Title",
    "Author Names",
    "Lorem ipsum",
    "[Insert",
    "<insert",
)


def _load_json(path: Path) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text()), None
    except Exception as exc:  # noqa: BLE001 - diagnostics should keep going.
        return None, f"invalid JSON: {exc}"


def _walk_model_values(value: Any) -> list[str]:
    models: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if "model" in str(key).lower() and isinstance(child, str):
                models.append(child)
            else:
                models.extend(_walk_model_values(child))
    elif isinstance(value, list):
        for child in value:
            models.extend(_walk_model_values(child))
    return models


def _allowed(model: str, allowed: set[str]) -> bool:
    normalized = model.strip()
    return normalized in allowed or any(normalized.endswith(f"/{item}") for item in allowed)


def _find_final_paper(workspace: Path, summary: dict[str, Any] | None) -> Path | None:
    if summary:
        rel = summary.get("final_paper")
        if isinstance(rel, str) and rel:
            candidate = workspace / rel
            if candidate.exists():
                return candidate

    candidates = (
        workspace / "paper_workspace" / "final_paper.md",
        workspace / "paper_workspace" / "final_paper.tex",
        workspace / "paper_workspace" / "final_paper.pdf",
        workspace / "final_paper.md",
        workspace / "final_paper.tex",
        workspace / "final_paper.pdf",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _text_metrics(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False}
    if path.suffix.lower() == ".pdf":
        return {"path": str(path), "exists": True, "kind": "pdf", "bytes": path.stat().st_size}

    text = path.read_text(errors="ignore")
    headings = len(re.findall(r"(?m)^(#{1,6}\s+|\\section\{|\\subsection\{)", text))
    words = len(re.findall(r"\b\w+\b", text))
    placeholders = [pattern for pattern in PLACEHOLDER_PATTERNS if pattern.lower() in text.lower()]
    citations = len(re.findall(r"(?i)(arxiv|doi:|et al\.|\[[0-9]+\]|\\cite\{)", text))
    return {
        "path": str(path),
        "exists": True,
        "kind": path.suffix.lower().lstrip(".") or "text",
        "bytes": path.stat().st_size,
        "words": words,
        "headings": headings,
        "citation_markers": citations,
        "placeholders": placeholders,
    }


def _budget_total(budget_state: dict[str, Any] | None) -> float | None:
    if not budget_state:
        return None
    for key in ("total_usd", "total_spent_usd", "spent_usd", "cost_usd"):
        value = budget_state.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def analyze(workspace: Path, allowed_models: set[str]) -> tuple[dict[str, Any], int]:
    checks: list[dict[str, Any]] = []
    require_citations = os.getenv("MSC_SMOKE_REQUIRE_CITATIONS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    accept_noncompleted = os.getenv("MSC_SMOKE_ACCEPT_NONCOMPLETED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    require_final_paper = os.getenv("MSC_SMOKE_REQUIRE_FINAL_PAPER", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("workspace_exists", workspace.is_dir(), str(workspace))
    if not workspace.is_dir():
        return {"workspace": str(workspace), "checks": checks}, 1

    status, status_error = _load_json(workspace / "run_status.json")
    summary, summary_error = _load_json(workspace / "run_summary.json")
    metadata, metadata_error = _load_json(workspace / "experiment_metadata.json")
    models, models_error = _load_json(workspace / "effective_models.json")
    budget_state, budget_error = _load_json(workspace / "budget_state.json")

    check("run_status_json", status_error is None, status_error or "present")
    check("run_summary_json", summary_error is None, summary_error or "present")
    check("experiment_metadata_json", metadata_error is None, metadata_error or "present")
    check("effective_models_json", models_error is None, models_error or "present")
    check("budget_state_json", budget_error is None, budget_error or "present")
    check("budget_ledger_jsonl", (workspace / "budget_ledger.jsonl").exists(), "budget_ledger.jsonl")
    check("paper_workspace", (workspace / "paper_workspace").is_dir(), "paper_workspace/")

    status_value = ""
    if isinstance(status, dict):
        status_value = str(status.get("status", ""))
    if isinstance(summary, dict) and not status_value:
        status_value = str(summary.get("status", ""))
    terminal_or_completed = status_value == "completed" or (
        accept_noncompleted
        and status_value in {"failed", "partial"}
        and isinstance(summary, dict)
    )
    check("terminal_or_completed_status", terminal_or_completed, status_value or "unknown")

    observed_models = sorted(set(_walk_model_values(models) if models is not None else []))
    unexpected_models = [model for model in observed_models if not _allowed(model, allowed_models)]
    check(
        "cheap_model_surfaces",
        bool(observed_models) and not unexpected_models,
        f"observed={observed_models}; unexpected={unexpected_models}",
    )

    budget_total = _budget_total(budget_state if isinstance(budget_state, dict) else None)
    budget_limit = float(os.getenv("MSC_SMOKE_BUDGET_USD", "5"))
    check(
        "budget_within_limit",
        budget_total is None or budget_total <= budget_limit,
        f"spent={budget_total}; limit={budget_limit}",
    )

    final_paper = _find_final_paper(workspace, summary if isinstance(summary, dict) else None)
    metrics = _text_metrics(final_paper)
    if status_value == "completed" or require_final_paper:
        check("final_paper_exists", bool(metrics.get("exists")), str(metrics.get("path")))
    if metrics.get("exists") and metrics.get("kind") != "pdf":
        check("final_paper_has_body", int(metrics.get("words") or 0) >= 500, f"words={metrics.get('words')}")
        check("final_paper_has_sections", int(metrics.get("headings") or 0) >= 3, f"headings={metrics.get('headings')}")
        check("final_paper_no_placeholders", not metrics.get("placeholders"), str(metrics.get("placeholders")))
        if require_citations:
            check(
                "final_paper_has_citation_markers",
                int(metrics.get("citation_markers") or 0) >= 3,
                f"citation_markers={metrics.get('citation_markers')}",
            )

    report = {
        "workspace": str(workspace),
        "status": status_value or None,
        "allowed_models": sorted(allowed_models),
        "observed_models": observed_models,
        "budget_total_usd": budget_total,
        "accept_noncompleted": accept_noncompleted,
        "citation_check_required": require_citations,
        "final_paper_required": require_final_paper,
        "final_paper": metrics,
        "checks": checks,
    }
    failed = [item for item in checks if not item["passed"]]
    return report, 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Result workspace to inspect.")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path for JSON report.")
    parser.add_argument(
        "--allowed-model",
        action="append",
        default=[],
        help="Allowed model id. May be passed multiple times.",
    )
    args = parser.parse_args(argv)

    allowed = set(DEFAULT_ALLOWED_MODELS)
    allowed.update(args.allowed_model)
    env_allowed = os.getenv("MSC_SMOKE_ALLOWED_MODELS")
    if env_allowed:
        allowed.update(item.strip() for item in env_allowed.split(",") if item.strip())

    report, rc = analyze(args.workspace.resolve(), allowed)
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
