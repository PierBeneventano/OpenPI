"""Agent-operable SDK/CLI self-test commands."""

from __future__ import annotations

import json

import click

from msc_sdk.validation import ValidationClient


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
def selftest() -> None:
    """Inspect public SDK/CLI command contracts."""


@selftest.command("commands")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def selftest_commands(as_json: bool) -> None:
    """List public operations and their SDK/CLI parity."""
    data = ValidationClient().commands()
    if as_json:
        _emit_json(data)
        return
    for command in data["commands"]:
        click.echo(f"{command['operation']}: {command['cli']}")


@selftest.command("permissions")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def selftest_permissions(as_json: bool) -> None:
    """List capabilities referenced by the public surface."""
    data = ValidationClient().permissions()
    if as_json:
        _emit_json(data)
        return
    for capability in data["capabilities"]:
        click.echo(capability)
