"""Read-only budget normalization for product-shell consumers.

This module intentionally does not participate in runtime budget enforcement.
It only interprets existing budget artifacts for SDK, CLI, and dashboard views.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .read_models import BudgetReadModel

CUMULATIVE_TOTAL_KEYS = (
    "total_usd",
    "total_cost_usd",
    "total_spent_usd",
    "spent_usd",
)
LIMIT_KEYS = (
    "limit_usd",
    "budget_usd",
    "campaign_limit_usd",
    "usd_limit",
)
PER_CALL_COST_KEYS = (
    "cost_usd",
    "call_cost_usd",
    "incremental_usd",
    "delta_usd",
)
MODEL_KEYS = (
    "model_id",
    "model",
    "model_name",
)
CALL_LIKE_KEYS = (
    "call_id",
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "timestamp",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _first_number(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(source.get(key))
        if value is not None:
            return value
    return None


def _model_id(row: dict[str, Any]) -> str | None:
    for key in MODEL_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_call_like(row: dict[str, Any]) -> bool:
    return any(key in row for key in CALL_LIKE_KEYS)


def _per_call_cost(row: dict[str, Any]) -> float | None:
    for key in PER_CALL_COST_KEYS:
        value = _number(row.get(key))
        if value is not None:
            return value
    # Legacy/third-party rows sometimes use `usd` for per-call cost, but it is
    # ambiguous. Only accept it on call-shaped rows that do not also carry an
    # aggregate total.
    ambiguous = _number(row.get("usd"))
    if ambiguous is not None and _is_call_like(row):
        if all(row.get(key) is None for key in CUMULATIVE_TOTAL_KEYS):
            return ambiguous
    return None


def _ledger_cumulative_totals(rows: list[dict[str, Any]]) -> list[float]:
    totals: list[float] = []
    for row in rows:
        total = _first_number(row, CUMULATIVE_TOTAL_KEYS)
        if total is not None:
            totals.append(total)
    return totals


def _state_by_model(state: dict[str, Any]) -> dict[str, float]:
    raw = state.get("by_model") or {}
    if not isinstance(raw, dict):
        return {}
    by_model: dict[str, float] = {}
    for model, value in raw.items():
        cost = _number(value)
        if isinstance(model, str) and model and cost is not None:
            by_model[model] = round(cost, 6)
    return by_model


def _ledger_by_model(rows: list[dict[str, Any]]) -> tuple[dict[str, float], int, float]:
    by_model: dict[str, float] = {}
    cost_rows = 0
    total = 0.0
    for row in rows:
        cost = _per_call_cost(row)
        model = _model_id(row)
        if cost is None:
            continue
        cost_rows += 1
        total += cost
        if model:
            by_model[model] = by_model.get(model, 0.0) + cost
    return {model: round(cost, 6) for model, cost in by_model.items()}, cost_rows, total


def read_budget_workspace(
    root: str | Path,
    summary: dict[str, Any] | None = None,
    *,
    metadata: dict[str, Any] | None = None,
) -> BudgetReadModel:
    """Normalize a workspace's budget artifacts without mutating them."""

    workspace = Path(root)
    state_path = workspace / "budget_state.json"
    ledger_path = workspace / "budget_ledger.jsonl"
    state = _read_json(state_path)
    rows = _read_jsonl(ledger_path)
    summary = summary or {}
    warnings: list[str] = []

    state_total = _first_number(state, CUMULATIVE_TOTAL_KEYS)
    summary_total = _first_number(summary, CUMULATIVE_TOTAL_KEYS)
    ledger_totals = _ledger_cumulative_totals(rows)
    ledger_latest_total = ledger_totals[-1] if ledger_totals else None
    ledger_max_total = max(ledger_totals) if ledger_totals else None

    by_model_from_ledger, ledger_cost_rows, ledger_cost_total = _ledger_by_model(rows)
    incremental_total = round(ledger_cost_total, 6) if ledger_cost_rows else None

    total_candidates: dict[str, float] = {}
    for key, value in (
        ("state", state_total),
        ("ledger_latest", ledger_latest_total),
        ("ledger_max", ledger_max_total),
        ("summary", summary_total),
    ):
        if value is not None:
            total_candidates[key] = round(value, 6)

    had_cumulative_candidates = bool(total_candidates)
    if had_cumulative_candidates:
        total_usd = max(total_candidates.values())
    else:
        total_usd = incremental_total
    if ledger_totals and any(
        later + 1e-9 < earlier for earlier, later in zip(ledger_totals, ledger_totals[1:])
    ):
        warnings.append("ledger_cumulative_total_decreased")
    if total_candidates:
        spread = max(total_candidates.values()) - min(total_candidates.values())
        if spread > max(0.0001, abs(max(total_candidates.values())) * 0.001):
            warnings.append("budget_total_sources_diverged")
    if incremental_total is not None:
        total_candidates["ledger_incremental_sum"] = incremental_total
    if total_usd is not None and incremental_total is not None:
        spread = abs(incremental_total - total_usd)
        if spread > max(0.01, abs(total_usd) * 0.02):
            warnings.append("ledger_incremental_sum_diverged")

    limit = None
    for source in (state, summary):
        limit = _first_number(source, LIMIT_KEYS)
        if limit is not None:
            break

    state_models = _state_by_model(state)
    if by_model_from_ledger:
        by_model = by_model_from_ledger
        by_model_source = "ledger_per_call_costs"
    else:
        by_model = state_models
        by_model_source = "state_by_model" if by_model else None

    by_model_total = round(sum(by_model.values()), 6) if by_model else None
    if total_usd is not None and by_model_total is not None:
        tolerance = max(0.01, abs(total_usd) * 0.02)
        if by_model_total > total_usd + tolerance:
            warnings.append("by_model_total_exceeds_total")

    read_metadata = {
        "reader": "msc_sdk.budget.read_budget_workspace",
        "total_source": (
            "observed_max"
            if had_cumulative_candidates
            else ("ledger_incremental_sum" if incremental_total is not None else None)
        ),
        "total_candidates": total_candidates,
        "by_model_source": by_model_source,
        "ledger_rows": len(rows),
        "ledger_cost_rows": ledger_cost_rows,
        "warnings": warnings,
    }
    if metadata:
        read_metadata.update(metadata)

    return BudgetReadModel(
        total_usd=round(total_usd, 6) if total_usd is not None else None,
        limit_usd=round(limit, 6) if limit is not None else None,
        ledger_path="budget_ledger.jsonl" if ledger_path.exists() else None,
        state_path="budget_state.json" if state_path.exists() else None,
        by_model=by_model,
        metadata=read_metadata,
    )
