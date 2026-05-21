"""msc config — view and manage configuration."""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys

import click
from rich.console import Console
from rich.syntax import Syntax

import yaml

from consortium.cli.core.config_manager import (
    get_config_dir,
    get_value,
    load_config,
    set_value,
)
from consortium.cli.core.env_manager import (
    API_KEYS,
    check_required_keys,
    clear_env_var,
    get_env_file_path,
    upsert_env_var,
)

console = Console()


def _allowed_env_var_names() -> set[str]:
    return {entry["env_var"] for entry in API_KEYS}


def _mask_secret(value: str) -> str:
    """Return a short non-secret preview suitable for UI display."""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"…{text[-4:]}"


@click.group()
def config() -> None:
    """View and manage PoggioAI/MSc configuration.

    \b
    Examples:
      msc config list                  # Show all settings
      msc config get model             # Get a specific value
      msc config set model gpt-5       # Change a setting
      msc config edit                  # Open in $EDITOR
    """


@config.command("list")
@click.pass_context
def config_list(ctx: click.Context) -> None:
    """Show all configuration values."""
    config_dir = ctx.obj.get("config_dir")
    cfg = load_config(config_dir)
    rendered = yaml.dump(cfg, default_flow_style=False, sort_keys=False)
    console.print(Syntax(rendered, "yaml", theme="monokai", line_numbers=False))


@config.command("get")
@click.argument("key")
@click.pass_context
def config_get(ctx: click.Context, key: str) -> None:
    """Get a configuration value by key.

    Use dot notation for nested keys: notifications.telegram.enabled
    """
    config_dir = ctx.obj.get("config_dir")
    value = get_value(key, config_dir)
    if value is None:
        console.print(f"[yellow]Key '{key}' not found.[/]")
        raise SystemExit(1)
    console.print(f"{key} = {value}")


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str) -> None:
    """Set a configuration value.

    \b
    Examples:
      msc config set model claude-opus-4-6
      msc config set budget_usd 50
      msc config set notifications.telegram.enabled true
    """
    config_dir = ctx.obj.get("config_dir")
    set_value(key, value, config_dir)
    console.print(f"[blue]Set[/] {key} = {value}")


@config.command("edit")
@click.pass_context
def config_edit(ctx: click.Context) -> None:
    """Open config file in $EDITOR."""
    config_dir = ctx.obj.get("config_dir")
    cfg_dir = get_config_dir(config_dir)
    cfg_path = cfg_dir / "config.yaml"

    if not cfg_path.exists():
        console.print("[yellow]No config file yet.[/] Run [bold]msc setup[/] first.")
        raise SystemExit(1)

    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(cfg_path)])


@config.command("path")
@click.pass_context
def config_path(ctx: click.Context) -> None:
    """Show the config directory path."""
    config_dir = ctx.obj.get("config_dir")
    cfg_dir = get_config_dir(config_dir)
    console.print(str(cfg_dir))


@config.group("keys")
def config_keys() -> None:
    """List, set, or remove API keys in ~/.msc/.env.

    \b
    Examples:
      msc config keys list                       # Show which keys are set
      msc config keys list --json                # Machine-readable status
      msc config keys set OPENROUTER_API_KEY     # Interactive (getpass)
      echo "sk-or-..." | msc config keys set OPENROUTER_API_KEY --stdin
      msc config keys unset OPENROUTER_API_KEY

    These commands and the VSCode extension's "Configure API Keys" panel
    use the same SDK functions — pick a surface, not a different mechanism.
    """


@config_keys.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def config_keys_list(ctx: click.Context, as_json: bool) -> None:
    """Show which API keys are configured and where they were loaded from."""
    config_dir = ctx.obj.get("config_dir")
    statuses = check_required_keys(config_dir)
    config_path = get_env_file_path(config_dir)

    if as_json:
        payload = {
            "ok": True,
            "config_path": str(config_path),
            "keys": [
                {
                    "env_var": entry["env_var"],
                    "name": entry["name"],
                    "provider": entry.get("provider"),
                    "level": entry["level"],
                    "description": entry.get("description"),
                    "configured": entry["configured"],
                    "source": entry.get("source"),
                }
                for entry in statuses
            ],
        }
        click.echo(json.dumps(payload))
        return

    for entry in statuses:
        status = "[green]set[/green]" if entry["configured"] else "[yellow]missing[/yellow]"
        source = entry.get("source") or "—"
        console.print(
            f"  {entry['name']:<12} ({entry['env_var']:<20}) {status}  source={source}  level={entry['level']}"
        )
    console.print(f"\nConfig file: {config_path}")


@config_keys.command("set")
@click.argument("name")
@click.option("--value", "value_inline", help="Pass the value inline (less secure — appears in shell history).")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read the value from stdin (recommended for scripts).")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable result.")
@click.pass_context
def config_keys_set(
    ctx: click.Context,
    name: str,
    value_inline: str | None,
    from_stdin: bool,
    as_json: bool,
) -> None:
    """Set an API key in ~/.msc/.env (chmod 600 enforced)."""
    name = name.strip().upper()
    if name not in _allowed_env_var_names():
        msg = f"Unknown API key '{name}'. Known: {sorted(_allowed_env_var_names())}"
        if as_json:
            click.echo(json.dumps({"ok": False, "env_var": name, "error": msg}))
        else:
            console.print(f"[red]{msg}[/red]")
        raise SystemExit(2)

    if value_inline is not None:
        value = value_inline
    elif from_stdin:
        value = sys.stdin.read().strip()
    else:
        value = getpass.getpass(f"Enter value for {name}: ").strip()

    if not value:
        msg = "Empty value — refusing to write."
        if as_json:
            click.echo(json.dumps({"ok": False, "env_var": name, "error": msg}))
        else:
            console.print(f"[red]{msg}[/red]")
        raise SystemExit(2)

    config_dir = ctx.obj.get("config_dir")
    path = upsert_env_var(name, value, config_dir_override=config_dir)
    if as_json:
        click.echo(json.dumps({
            "ok": True,
            "env_var": name,
            "config_path": str(path),
            "preview": _mask_secret(value),
        }))
    else:
        console.print(f"[green]Saved[/green] {name}={_mask_secret(value)} → {path}")


@config_keys.command("unset")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable result.")
@click.pass_context
def config_keys_unset(ctx: click.Context, name: str, as_json: bool) -> None:
    """Remove an API key from ~/.msc/.env."""
    name = name.strip().upper()
    if name not in _allowed_env_var_names():
        msg = f"Unknown API key '{name}'. Known: {sorted(_allowed_env_var_names())}"
        if as_json:
            click.echo(json.dumps({"ok": False, "env_var": name, "error": msg}))
        else:
            console.print(f"[red]{msg}[/red]")
        raise SystemExit(2)

    config_dir = ctx.obj.get("config_dir")
    path = clear_env_var(name, config_dir_override=config_dir)
    if as_json:
        click.echo(json.dumps({"ok": True, "env_var": name, "config_path": str(path)}))
    else:
        console.print(f"[green]Removed[/green] {name} → {path}")


# ----- pricing CRUD over .llm_config.yaml --------------------------------------


def _llm_config_path() -> str:
    """Resolve the .llm_config.yaml path from CWD upward; falls back to project root."""
    cwd = os.path.abspath(os.getcwd())
    while True:
        candidate = os.path.join(cwd, ".llm_config.yaml")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    # Default to project root from env, or current dir
    root = os.getenv("CONSORTIUM_PROJECT_ROOT") or os.getcwd()
    return os.path.join(root, ".llm_config.yaml")


def _load_llm_config() -> dict:
    path = _llm_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _save_llm_config(cfg: dict) -> str:
    path = _llm_config_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)
    return path


@config.group("pricing")
def config_pricing() -> None:
    """List, set, or remove per-model pricing in `.llm_config.yaml`."""


@config_pricing.command("list")
@click.option("--json", "as_json", is_flag=True)
def config_pricing_list(as_json: bool) -> None:
    """Print all model pricing entries from `.llm_config.yaml`."""
    cfg = _load_llm_config()
    pricing = (cfg.get("budget") or {}).get("pricing") or {}
    if as_json:
        click.echo(json.dumps({"ok": True, "pricing": pricing, "path": _llm_config_path()}, indent=2))
        return
    if not pricing:
        click.echo("(no pricing entries)")
        return
    for model, rates in sorted(pricing.items()):
        click.echo(
            f"{model:<55s}  in=${rates.get('input_per_1k', 0):>7.5f}/1k "
            f"out=${rates.get('output_per_1k', 0):>7.5f}/1k"
        )


@config_pricing.command("set")
@click.argument("model")
@click.option("--input-per-1k", type=float, required=True)
@click.option("--output-per-1k", type=float, required=True)
@click.option("--json", "as_json", is_flag=True)
def config_pricing_set(
    model: str, input_per_1k: float, output_per_1k: float, as_json: bool,
) -> None:
    """Add or update a model's pricing in `.llm_config.yaml`."""
    cfg = _load_llm_config()
    budget = cfg.setdefault("budget", {})
    pricing = budget.setdefault("pricing", {})
    pricing[model] = {
        "input_per_1k": float(input_per_1k),
        "output_per_1k": float(output_per_1k),
    }
    path = _save_llm_config(cfg)
    out = {"ok": True, "model": model, "input_per_1k": float(input_per_1k),
           "output_per_1k": float(output_per_1k), "path": path}
    if as_json:
        click.echo(json.dumps(out, indent=2))
    else:
        click.echo(f"set {model}: in=${input_per_1k}/1k out=${output_per_1k}/1k -> {path}")


@config_pricing.command("unset")
@click.argument("model")
@click.option("--json", "as_json", is_flag=True)
def config_pricing_unset(model: str, as_json: bool) -> None:
    """Remove a model's pricing entry."""
    cfg = _load_llm_config()
    pricing = (cfg.get("budget") or {}).get("pricing") or {}
    existed = pricing.pop(model, None)
    path = _save_llm_config(cfg) if existed is not None else _llm_config_path()
    out = {"ok": True, "model": model, "removed": bool(existed), "path": path}
    if as_json:
        click.echo(json.dumps(out, indent=2))
    else:
        click.echo(f"{'removed' if existed else 'not found'}: {model} -> {path}")
