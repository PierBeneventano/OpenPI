"""Machine-readable product event commands."""

from __future__ import annotations

import json

import click

from msc_sdk.events import EventStore


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
@click.option("--state-dir", type=click.Path(), default=".msc", help="Product-shell state directory.")
@click.pass_context
def events(ctx: click.Context, state_dir: str) -> None:
    """Inspect append-only product-shell events."""
    ctx.obj = ctx.obj or {}
    ctx.obj["event_state_dir"] = state_dir


@events.command("list")
@click.option("--limit", type=int, default=None, help="Limit to the newest N events.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def events_list(ctx: click.Context, limit: int | None, as_json: bool) -> None:
    """List events."""
    store = EventStore(ctx.obj["event_state_dir"])
    data = {"ok": True, "events": [event.to_dict() for event in store.list(limit=limit)]}
    if as_json:
        _emit_json(data)
        return
    for event in data["events"]:
        click.echo(f"{event['timestamp']} {event['kind']} {event['summary']}")


@events.command("show")
@click.argument("event_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def events_show(ctx: click.Context, event_id: str, as_json: bool) -> None:
    """Show one event."""
    event = EventStore(ctx.obj["event_state_dir"]).show(event_id)
    data = {"ok": event is not None, "event": event.to_dict() if event else None}
    if as_json:
        _emit_json(data)
        return
    click.echo(data["event"]["summary"] if event else "event not found")
