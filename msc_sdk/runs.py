"""Run-level SDK helpers backed by artifact read models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import inspect_run_workspace, list_run_workspaces
from .read_models import RunReadModel


class RunClient:
    """Read and prepare run operations without reaching into graph internals."""

    def __init__(self, results_dir: str | Path = "results"):
        self.results_dir = Path(results_dir)

    def list(self, *, limit: int | None = 10) -> list[RunReadModel]:
        return list_run_workspaces(self.results_dir, limit=limit)

    def inspect(self, ref: str | Path) -> RunReadModel:
        return inspect_run_workspace(resolve_run_ref(ref, self.results_dir))

    def logs(self, ref: str | Path, *, stage: str | None = None) -> dict[str, Any]:
        run = self.inspect(ref)
        logs = run.logs
        if stage:
            logs = [log for log in logs if log.stage == stage or stage in log.path]
        return {"run": run.run_id, "logs": [log.to_dict() for log in logs]}

    def budget(self, ref: str | Path) -> dict[str, Any]:
        run = self.inspect(ref)
        return {"run": run.run_id, "budget": run.budget.to_dict()}

    def dry_run(
        self,
        *,
        task_file: str | Path | None = None,
        tier: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        argv = ["msc", "run"]
        if task_file:
            argv.extend(["--task-file", str(task_file)])
        if tier:
            argv.extend(["--tier", tier])
        if model:
            argv.extend(["--model", model])
        return {
            "ok": True,
            "operation": "runs.dry_run",
            "would_execute": argv,
            "mutates": False,
        }

    def resume_request(self, ref: str | Path, *, confirmation: str | None = None) -> dict[str, Any]:
        run_path = resolve_run_ref(ref, self.results_dir)
        expected = f"resume:{run_path.name}"
        if confirmation is None:
            return {
                "ok": False,
                "error_code": "confirmation_required",
                "error_category": "confirmation_required",
                "operation": "runs.resume",
                "capability": "mutate.resume",
                "target": str(run_path),
                "confirmation_token": expected,
                "would_execute": ["msc", "resume", str(run_path)],
            }
        return {
            "ok": confirmation == expected,
            "operation": "runs.resume",
            "capability": "mutate.resume",
            "target": str(run_path),
            "confirmed": confirmation == expected,
        }


def resolve_run_ref(ref: str | Path, results_dir: str | Path = "results") -> Path:
    """Resolve a run id or path to a concrete workspace path."""
    path = Path(ref)
    if path.exists():
        return path.resolve()
    candidate = Path(results_dir) / str(ref)
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"Run workspace not found: {ref}")
