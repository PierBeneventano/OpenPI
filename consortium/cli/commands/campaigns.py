"""Machine-readable campaign inspection commands."""

from __future__ import annotations

import json

import click

from msc_sdk.campaigns import CampaignClient


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
@click.option("--root", type=click.Path(), default=".", help="Directory used to resolve campaign refs.")
@click.pass_context
def campaigns(ctx: click.Context, root: str) -> None:
    """Inspect campaign specs and observed state."""
    ctx.obj = ctx.obj or {}
    ctx.obj["campaign_root"] = root


@campaigns.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_list(ctx: click.Context, as_json: bool) -> None:
    """List local campaign specs."""
    data = {"ok": True, "campaigns": CampaignClient(ctx.obj["campaign_root"]).list()}
    if as_json:
        _emit_json(data)
        return
    for campaign in data["campaigns"]:
        click.echo(campaign["name"])


@campaigns.command("inspect")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_inspect(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Inspect a campaign spec."""
    data = CampaignClient(ctx.obj["campaign_root"]).inspect(campaign_ref).to_dict()
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: {data['status']}")


@campaigns.command("status")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_status(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Inspect normalized campaign status."""
    data = CampaignClient(ctx.obj["campaign_root"]).status(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign']}: {data['status']}")


@campaigns.command("graph")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_graph(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Return a stage graph for dashboards."""
    data = CampaignClient(ctx.obj["campaign_root"]).graph(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{len(data['nodes'])} nodes, {len(data['edges'])} edges")


@campaigns.command("artifacts")
@click.argument("campaign_ref")
@click.option("--stage-id", default=None, help="Optional stage id filter.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_artifacts(
    ctx: click.Context,
    campaign_ref: str,
    stage_id: str | None,
    as_json: bool,
) -> None:
    """Return declared campaign artifact contracts."""
    data = CampaignClient(ctx.obj["campaign_root"]).artifacts(campaign_ref, stage_id)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign']}: {len(data['stages'])} stages")
