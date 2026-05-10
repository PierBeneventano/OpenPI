"""Machine-readable project inspection commands."""

from __future__ import annotations

import json

import click

from consortium.cli.core.config_manager import get_config_dir
from consortium.cli.core.env_manager import build_runtime_env, get_runtime_env_sources
from consortium.cli.core.paths import find_project_root, find_results_dir
from msc_sdk.openclaude import openclaude_readiness
from msc_sdk.openclaw import openclaw_readiness
from msc_sdk.project import ProjectClient
from msc_sdk.setup_state import build_setup_state, tutorial_plan


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


@project.command("setup-state")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def project_setup_state(ctx: click.Context, as_json: bool) -> None:
    """Report guided setup state without secrets or mutations."""
    project_root = find_project_root()
    config_dir_override = ctx.obj.get("config_dir")
    env = build_runtime_env(config_dir_override=config_dir_override, repo_root=project_root)
    sources = get_runtime_env_sources(config_dir_override=config_dir_override, repo_root=project_root)
    openclaude = openclaude_readiness(
        project_root=project_root,
        openrouter_configured=bool(env.get("OPENROUTER_API_KEY")),
        openrouter_source=sources.get("OPENROUTER_API_KEY"),
    )
    openclaw = openclaw_readiness()
    state = build_setup_state(
        project_root=project_root,
        config_dir=get_config_dir(config_dir_override),
        credential_source=sources.get("OPENROUTER_API_KEY"),
        openrouter_configured=bool(env.get("OPENROUTER_API_KEY")),
        results_dir=find_results_dir(),
        openclaude_available=openclaude.openclaude_available,
        openclaude_launch_ready=openclaude.launch_ready,
        telegram_enabled=bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
        openclaw_enabled=openclaw.configured,
    )
    data = {"ok": True, "setup": state.to_dict()}
    if as_json:
        _emit_json(data)
        return
    click.echo("ready" if not state.warnings else "needs attention")


@project.command("tutorial-plan")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def project_tutorial_plan(as_json: bool) -> None:
    """Show the no-cost guided tutorial plan."""
    data = tutorial_plan()
    if as_json:
        _emit_json(data)
        return
    for step in data["steps"]:
        click.echo(step["command"])
