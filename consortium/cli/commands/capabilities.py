"""Machine-readable capability commands."""

from __future__ import annotations

import json

import click

from msc_sdk.capabilities import CapabilityClient


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
@click.option("--profile", default="default", help="Capability profile to inspect.")
@click.pass_context
def capabilities(ctx: click.Context, profile: str) -> None:
    """Inspect product-shell capability profiles."""
    ctx.obj = ctx.obj or {}
    ctx.obj["capability_profile"] = profile


@capabilities.command("current")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def capabilities_current(ctx: click.Context, as_json: bool) -> None:
    """Show current capabilities."""
    data = CapabilityClient(ctx.obj["capability_profile"]).current()
    if as_json:
        _emit_json(data)
        return
    for capability in data["capabilities"]:
        click.echo(capability)


@capabilities.command("explain")
@click.argument("capability")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def capabilities_explain(ctx: click.Context, capability: str, as_json: bool) -> None:
    """Explain a capability."""
    data = CapabilityClient(ctx.obj["capability_profile"]).explain(capability)
    if as_json:
        _emit_json(data)
        return
    click.echo(data.get("description") or "unknown capability")
