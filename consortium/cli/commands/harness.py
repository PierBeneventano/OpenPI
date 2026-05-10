"""Machine-readable orchestrator harness commands."""

from __future__ import annotations

import json

import click

from msc_sdk.harness import OrchestratorHarness


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
@click.option("--root", type=click.Path(), default=".", help="Project root.")
@click.option("--state-dir", type=click.Path(), default=".msc", help="Product-shell state directory.")
@click.option("--index-dir", type=click.Path(), default=".msc_index", help="Derived index directory.")
@click.option("--profile", default="default", help="Capability profile.")
@click.pass_context
def harness(
    ctx: click.Context,
    root: str,
    state_dir: str,
    index_dir: str,
    profile: str,
) -> None:
    """Use the single product-shell orchestrator harness."""
    ctx.obj = ctx.obj or {}
    ctx.obj["harness_kwargs"] = {
        "root": root,
        "state_dir": state_dir,
        "index_dir": index_dir,
        "profile": profile,
    }


def _harness(ctx: click.Context) -> OrchestratorHarness:
    return OrchestratorHarness(**ctx.obj["harness_kwargs"])


@harness.command("inspect-project")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def harness_inspect_project(ctx: click.Context, as_json: bool) -> None:
    """Inspect project state through the harness."""
    data = _harness(ctx).inspect_project()
    if as_json:
        _emit_json(data)
        return
    click.echo(data["project"].get("project_root") or "-")


@harness.command("refresh-manifest")
@click.argument("path", type=click.Path(exists=True))
@click.option("--actor", default="user", help="Actor recorded in the event log.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def harness_refresh_manifest(ctx: click.Context, path: str, actor: str, as_json: bool) -> None:
    """Refresh a derived manifest through the harness."""
    data = _harness(ctx).refresh_manifest(path, actor=actor)
    if as_json:
        _emit_json(data)
        return
    click.echo(data.get("manifest_path") or data.get("error_category"))


@harness.command("request-action")
@click.argument("operation")
@click.option("--target", required=True, help="Action target.")
@click.option("--capability", required=True, help="Required capability.")
@click.option("--actor", default="user", help="Actor recorded in the event log.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def harness_request_action(
    ctx: click.Context,
    operation: str,
    target: str,
    capability: str,
    actor: str,
    as_json: bool,
) -> None:
    """Create a confirmation request without executing a mutation."""
    data = _harness(ctx).request_action(
        operation=operation,
        target=target,
        capability=capability,
        actor=actor,
    )
    if as_json:
        _emit_json(data)
        return
    request = data.get("request") or {}
    click.echo(request.get("confirmation_token") or data.get("error_category"))


@harness.command("execute-action")
@click.argument("request_id")
@click.option("--confirm", "confirmation", required=True, help="Confirmation token.")
@click.option("--actor", default="user", help="Actor recorded in the event log.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def harness_execute_action(
    ctx: click.Context,
    request_id: str,
    confirmation: str,
    actor: str,
    as_json: bool,
) -> None:
    """Validate a confirmation token; mutation execution is still deferred."""
    data = _harness(ctx).execute_action(request_id, confirmation, actor=actor)
    if as_json:
        _emit_json(data)
        return
    click.echo(data.get("message") or data.get("error_category"))
