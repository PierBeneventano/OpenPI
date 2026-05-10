"""Machine-readable project inspection commands."""

from __future__ import annotations

import json

import click

from msc_sdk.project import ProjectClient


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
def project() -> None:
    """Inspect local project and setup state."""


@project.command("inspect")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def project_inspect(as_json: bool) -> None:
    """Inspect repository context without mutating it."""
    data = ProjectClient().inspect().to_dict()
    if as_json:
        _emit_json(data)
        return
    click.echo(f"Project root: {data.get('project_root') or '-'}")
    click.echo(f"Results dir: {data.get('results_dir') or '-'}")


@project.command("readiness")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def project_readiness(as_json: bool) -> None:
    """Report readiness checks for product-shell clients."""
    data = ProjectClient().readiness()
    if as_json:
        _emit_json(data)
        return
    click.echo("ready" if data["ok"] else "not ready")
