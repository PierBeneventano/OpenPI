"""Machine-readable artifact manifest commands."""

from __future__ import annotations

import json

import click

from msc_sdk.manifest import import_manifest, write_manifest


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
def artifacts() -> None:
    """Inspect and index artifact manifests."""


@artifacts.command("inspect")
@click.argument("path", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def artifacts_inspect(path: str, as_json: bool) -> None:
    """Inspect a run, results directory, or campaign spec."""
    data = import_manifest(path).to_dict()
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['kind']} manifest: {data['source']}")


@artifacts.command("index")
@click.argument("path", type=click.Path(exists=True))
@click.option("--out", "out_dir", type=click.Path(), default=".msc_index", help="Derived index directory.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def artifacts_index(path: str, out_dir: str, as_json: bool) -> None:
    """Write a derived manifest without changing raw artifacts."""
    manifest_path = write_manifest(path, out_dir)
    data = {"ok": True, "manifest_path": str(manifest_path)}
    if as_json:
        _emit_json(data)
        return
    click.echo(str(manifest_path))
