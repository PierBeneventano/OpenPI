"""msc runs — list and inspect past research runs."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from consortium.cli.core.paths import find_results_dir as _find_results_dir
from consortium.cli.core.run_inspector import inspect_run
from msc_sdk.runs import RunClient

console = Console()


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group(invoke_without_command=True)
@click.option("--limit", "-n", type=int, default=10, help="Number of recent runs to show.")
@click.option("--results-dir", type=click.Path(exists=True), default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def runs(ctx: click.Context, limit: int, results_dir: str | None, as_json: bool) -> None:
    """List past research runs with status and cost.

    \b
    Examples:
      msc runs           # Show last 10 runs
      msc runs -n 20     # Show last 20 runs
      msc runs list --json
      msc runs inspect consortium_20260409_010203 --json
    """
    ctx.obj = ctx.obj or {}
    rdir = Path(results_dir) if results_dir else _find_results_dir()
    ctx.obj["results_dir"] = str(rdir) if rdir else None
    if ctx.invoked_subcommand is not None:
        return
    _list_runs(limit, rdir, as_json=as_json)


@runs.command("list")
@click.option("--limit", "-n", type=int, default=10, help="Number of recent runs to show.")
@click.option("--results-dir", type=click.Path(exists=True), default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def runs_list(ctx: click.Context, limit: int, results_dir: str | None, as_json: bool) -> None:
    """List past runs."""
    rdir = Path(results_dir) if results_dir else _find_results_dir()
    ctx.obj = ctx.obj or {}
    ctx.obj["results_dir"] = str(rdir) if rdir else None
    _list_runs(limit, rdir, as_json=as_json)


@runs.command("inspect")
@click.argument("run_ref")
@click.option("--results-dir", type=click.Path(exists=True), default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runs_inspect(run_ref: str, results_dir: str | None, as_json: bool) -> None:
    """Inspect a run workspace."""
    rdir = Path(results_dir) if results_dir else _find_results_dir() or Path("results")
    data = RunClient(rdir).inspect(run_ref).to_dict()
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['run_id']}: {data['status']}")


@runs.command("logs")
@click.argument("run_ref")
@click.option("--stage", default=None, help="Optional stage name/path filter.")
@click.option("--results-dir", type=click.Path(exists=True), default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runs_logs(run_ref: str, stage: str | None, results_dir: str | None, as_json: bool) -> None:
    """List run log files."""
    rdir = Path(results_dir) if results_dir else _find_results_dir() or Path("results")
    data = RunClient(rdir).logs(run_ref, stage=stage)
    if as_json:
        _emit_json(data)
        return
    for log in data["logs"]:
        click.echo(log["path"])


@runs.command("budget")
@click.argument("run_ref")
@click.option("--results-dir", type=click.Path(exists=True), default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runs_budget(run_ref: str, results_dir: str | None, as_json: bool) -> None:
    """Inspect run budget state."""
    rdir = Path(results_dir) if results_dir else _find_results_dir() or Path("results")
    data = RunClient(rdir).budget(run_ref)
    if as_json:
        _emit_json(data)
        return
    total = data["budget"].get("total_usd")
    click.echo(f"${total:.2f}" if total is not None else "-")


@runs.command("dry-run")
@click.option("--task-file", type=click.Path(), default=None)
@click.option("--tier", default=None)
@click.option("--model", default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runs_dry_run(task_file: str | None, tier: str | None, model: str | None, as_json: bool) -> None:
    """Prepare a run command without executing it."""
    data = RunClient().dry_run(task_file=task_file, tier=tier, model=model)
    if as_json:
        _emit_json(data)
        return
    click.echo(" ".join(data["would_execute"]))


@runs.command("resume-request")
@click.argument("run_ref")
@click.option("--results-dir", type=click.Path(exists=True), default=None)
@click.option("--confirm", "confirmation", default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def runs_resume_request(
    run_ref: str,
    results_dir: str | None,
    confirmation: str | None,
    as_json: bool,
) -> None:
    """Prepare a resume request without executing it."""
    rdir = Path(results_dir) if results_dir else _find_results_dir() or Path("results")
    data = RunClient(rdir).resume_request(run_ref, confirmation=confirmation)
    if as_json:
        _emit_json(data)
        return
    click.echo(data.get("confirmation_token") or ("confirmed" if data.get("confirmed") else "denied"))


def _list_runs(limit: int, rdir: Path | None, *, as_json: bool = False) -> None:
    if not rdir:
        if as_json:
            _emit_json({"ok": True, "runs": [], "warnings": ["results_dir_not_found"]})
            return
        console.print("[yellow]No results/ directory found.[/] Run a pipeline first.")
        return

    if as_json:
        runs_data = [run.to_dict() for run in RunClient(rdir).list(limit=limit)]
        _emit_json({"ok": True, "results_dir": str(rdir), "runs": runs_data})
        return

    # Find run directories (sorted newest first)
    # Include all subdirectories — runs may have custom names or consortium_ prefix
    run_dirs = sorted(
        [d for d in rdir.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[:limit]

    if not run_dirs:
        console.print("[dim]No runs found.[/]")
        return

    table = Table(title=f"Recent Runs ({len(run_dirs)} of {len(list(rdir.iterdir()))})")
    table.add_column("Run ID", style="bold")
    table.add_column("Status")
    table.add_column("Cost")
    table.add_column("Model")
    table.add_column("Task")

    for d in run_dirs:
        info = inspect_run(d)
        status_map = {
            "active": "[green]active[/]",
            "stalled": "[yellow]stalled[/]",
            "completed": "[blue]completed[/]",
            "failed": "[red]failed[/]",
            "partial": "[yellow]partial[/]",
            "unknown": "[dim]unknown[/]",
        }
        cost = f"${info['budget_usd']:.2f}" if info["budget_usd"] is not None else "-"
        task = (info["task"] or "-")[:50]
        table.add_row(
            info["run_id"],
            status_map.get(info["status"], f"[dim]{info['status']}[/]"),
            cost,
            info["model"],
            task,
        )

    console.print(table)
