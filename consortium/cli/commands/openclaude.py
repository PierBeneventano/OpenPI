"""msc openclaude — configure and launch the MSc OpenClaude operator surface."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import click

from consortium.cli.core.env_manager import build_runtime_env, get_runtime_env_sources, load_env_vars
from consortium.cli.core.paths import find_project_root
from msc_sdk.openclaude import (
    DEFAULT_OPENCLAUDE_MODEL,
    OPENCLAUDE_BASE_URL,
    openclaude_campaign_harness,
    openclaude_context_pack,
    openclaude_env_contract,
    openclaude_launch_plan,
    openclaude_models,
    openclaude_readiness,
)


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
@click.option("--model", default=DEFAULT_OPENCLAUDE_MODEL, help="OpenRouter model id for OpenClaude.")
@click.pass_context
def openclaude(ctx: click.Context, model: str) -> None:
    """Inspect and launch the OpenClaude integration without duplicating secrets."""
    ctx.obj = ctx.obj or {}
    ctx.obj["openclaude_model"] = model


@openclaude.command("readiness")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def openclaude_readiness_cmd(ctx: click.Context, as_json: bool) -> None:
    """Check OpenClaude launch readiness."""
    data = _readiness(ctx).to_dict()
    if as_json:
        _emit_json(data)
        return
    click.echo("ready" if data["launch_ready"] else "not ready")


@openclaude.command("env")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def openclaude_env_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show the redacted OpenClaude launch environment contract."""
    readiness = _readiness(ctx)
    data = {
        "ok": readiness.openrouter_configured,
        "env": openclaude_env_contract(
            openrouter_configured=readiness.openrouter_configured,
            model=ctx.obj["openclaude_model"],
        ),
        "openrouter_source": readiness.openrouter_source,
    }
    if as_json:
        _emit_json(data)
        return
    for key, value in data["env"].items():
        click.echo(f"{key}={value}")


@openclaude.command("skill-path")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def openclaude_skill_path_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show the MSc OpenClaude skill path."""
    readiness = _readiness(ctx)
    data = {
        "ok": readiness.skill_exists,
        "skill_path": readiness.skill_path,
        "skill_exists": readiness.skill_exists,
    }
    if as_json:
        _emit_json(data)
        return
    click.echo(readiness.skill_path)


@openclaude.command("campaign-harness")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def openclaude_campaign_harness_cmd(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Return the OpenClaude researcher harness packet for a campaign."""
    project_root = _project_root()
    readiness = _readiness(ctx)
    data = openclaude_campaign_harness(
        campaign_ref,
        project_root=project_root,
        openrouter_configured=readiness.openrouter_configured,
        openrouter_source=readiness.openrouter_source,
        model=ctx.obj["openclaude_model"],
    )
    if as_json:
        _emit_json(data)
        return
    campaign = data["workspace"]["campaign"]
    execution = data["workspace"]["execution"]
    click.echo(f"{campaign['id']}: {execution['status']}")


@openclaude.command("context-pack")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def openclaude_context_pack_cmd(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Return compact campaign context for an OpenClaude chat turn."""
    data = openclaude_context_pack(campaign_ref, project_root=_project_root())
    if as_json:
        _emit_json(data)
        return
    campaign = data["campaign"] or {}
    click.echo(f"{campaign.get('id') or campaign_ref}: {len(data['active_context_links'])} active context links")


@openclaude.command("models")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def openclaude_models_cmd(ctx: click.Context, as_json: bool) -> None:
    """Return model aliases available to the OpenClaude UI."""
    data = openclaude_models(default_model=ctx.obj["openclaude_model"])
    if as_json:
        _emit_json(data)
        return
    for alias in data["aliases"]:
        click.echo(f"{alias['id']}: {alias['model']}")


@openclaude.command("launch")
@click.option("--execute", is_flag=True, help="Actually launch openclaude. Default is dry-run plan only.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.argument("openclaude_args", nargs=-1)
@click.pass_context
def openclaude_launch_cmd(
    ctx: click.Context,
    execute: bool,
    as_json: bool,
    openclaude_args: tuple[str, ...],
) -> None:
    """Return or execute an OpenClaude launch plan."""
    project_root = _project_root()
    readiness = _readiness(ctx)
    plan = openclaude_launch_plan(
        project_root=project_root,
        openrouter_configured=readiness.openrouter_configured,
        model=ctx.obj["openclaude_model"],
        extra_args=list(openclaude_args),
    )
    plan["execute"] = execute
    plan["openclaude_available"] = readiness.openclaude_available
    plan["launch_ready"] = readiness.launch_ready

    if not execute:
        if as_json:
            _emit_json(plan)
        else:
            click.echo(" ".join(plan["command"]))
        return

    if not readiness.launch_ready:
        data = {
            "ok": False,
            "error_code": "openclaude_not_ready",
            "error_category": "unavailable",
            "readiness": readiness.to_dict(),
        }
        if as_json:
            _emit_json(data)
            return
        raise click.ClickException("OpenClaude is not ready. Run `msc openclaude readiness --json`.")

    env = build_runtime_env(
        config_dir_override=ctx.obj.get("config_dir"),
        repo_root=project_root,
        base_env=_openclaude_base_env(ctx),
    )
    env["CLAUDE_CODE_USE_OPENAI"] = "1"
    env["OPENAI_API_KEY"] = env["OPENROUTER_API_KEY"]
    env["OPENAI_BASE_URL"] = OPENCLAUDE_BASE_URL
    env["OPENAI_MODEL"] = ctx.obj["openclaude_model"]
    proc = subprocess.run(plan["command"], cwd=project_root, env=env)
    raise SystemExit(proc.returncode)


def _project_root() -> Path:
    return find_project_root() or Path.cwd()


def _readiness(ctx: click.Context):
    project_root = _project_root()
    base_env = _openclaude_base_env(ctx)
    env = build_runtime_env(
        config_dir_override=ctx.obj.get("config_dir"),
        repo_root=project_root,
        base_env=base_env,
    )
    sources = get_runtime_env_sources(
        config_dir_override=ctx.obj.get("config_dir"),
        repo_root=project_root,
        base_env=base_env,
    )
    return openclaude_readiness(
        project_root=project_root,
        openrouter_configured=bool(env.get("OPENROUTER_API_KEY")),
        openrouter_source=sources.get("OPENROUTER_API_KEY"),
        model=ctx.obj["openclaude_model"],
    )


def _openclaude_base_env(ctx: click.Context) -> dict[str, str]:
    base_env = dict(os.environ)
    config_dir = ctx.obj.get("config_dir")
    if config_dir and load_env_vars(config_dir).get("OPENROUTER_API_KEY"):
        base_env.pop("OPENROUTER_API_KEY", None)
    return base_env
