"""msc openclaw — manage OpenClaw autonomous oversight."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click
from rich.console import Console

from consortium.cli.core.paths import find_script_path
from msc_sdk.openclaw import (
    OPENCLAW_PROFILES,
    openclaw_config_path,
    openclaw_launch_plan,
    openclaw_readiness,
    read_openclaw_config,
)

console = Console()


@click.group()
def openclaw() -> None:
    """Manage OpenClaw autonomous campaign oversight.

    \b
    OpenClaw provides a gateway for autonomous campaign management
    with heartbeat monitoring, log analysis, and auto-repair.

    \b
    Examples:
      msc openclaw setup    # Configure OpenClaw
      msc openclaw start    # Start the gateway
      msc openclaw status   # Check gateway health
    """


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@openclaw.command("readiness")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def openclaw_readiness_cmd(as_json: bool) -> None:
    """Show OpenClaw readiness without starting automation."""
    launch_script = _find_launch_script()
    data = openclaw_readiness(launch_script=launch_script).to_dict()
    if as_json:
        _emit_json(data)
        return
    console.print("[blue]configured[/]" if data["configured"] else "[dim]not configured[/]")


@openclaw.command("profiles")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def openclaw_profiles(as_json: bool) -> None:
    """Show OpenClaw capability profiles."""
    data = {"ok": True, "profiles": OPENCLAW_PROFILES}
    if as_json:
        _emit_json(data)
        return
    for name in OPENCLAW_PROFILES:
        click.echo(name)


@openclaw.command("launch-plan")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def openclaw_launch_plan_cmd(as_json: bool) -> None:
    """Show the OpenClaw launch command without executing it."""
    data = openclaw_launch_plan(_find_launch_script())
    if as_json:
        _emit_json(data)
        return
    click.echo(" ".join(data["command"]) if data.get("command") else "launch script not found")


@openclaw.command("setup")
@click.pass_context
def openclaw_setup(ctx: click.Context) -> None:
    """Interactive OpenClaw configuration wizard."""
    import questionary

    console.print("[bold]OpenClaw Setup[/]\n")

    config_dir = Path.home() / ".openclaw"

    if config_dir.exists():
        console.print(f"  OpenClaw directory found at [bold]{config_dir}[/]")
        reconfigure = questionary.confirm("  Reconfigure?", default=False).ask()
        if not reconfigure:
            return
    else:
        config_dir.mkdir(parents=True, exist_ok=True)

    # Gateway port
    port = questionary.text("Gateway port:", default="18789").ask()

    # Auth mode
    auth_mode = questionary.select(
        "Authentication mode:",
        choices=["token", "none"],
        default="token",
    ).ask()

    # Generate config
    import secrets

    config = {
        "gateway": {
            "port": int(port),
            "mode": "local",
            "bind": "loopback",
            "auth": {
                "mode": auth_mode,
                "token": secrets.token_hex(24) if auth_mode == "token" else None,
            },
        },
        "agents": {
            "list": [
                {
                    "id": "campaign-overseer",
                    "model": "anthropic/claude-opus-4-6",
                    "workspace": str(config_dir / "workspace-campaign-overseer"),
                }
            ]
        },
        "channels": {
            "telegram": {"enabled": False},
        },
    }

    config_file = config_dir / "openclaw.json"
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)

    console.print(f"\n[blue]OpenClaw config saved to {config_file}[/]")
    console.print("  Start with: [bold]msc openclaw start[/]")


@openclaw.command("start")
@click.option("--background/--foreground", default=True, help="Run in background.")
def openclaw_start(background: bool) -> None:
    """Start the OpenClaw gateway."""
    # Try SLURM first, fall back to local
    launch_script = _find_launch_script()
    if launch_script:
        console.print(f"Launching via [bold]{launch_script}[/]...")
        if background:
            subprocess.Popen(
                ["bash", str(launch_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            console.print("[blue]Gateway starting in background.[/]")
        else:
            subprocess.run(["bash", str(launch_script)])
    else:
        console.print("[yellow]Launch script not found.[/] Looking for scripts/launch_openclaw_gateway.sh")


@openclaw.command("stop")
def openclaw_stop() -> None:
    """Stop the OpenClaw gateway."""
    console.print("Stopping OpenClaw gateway...")
    # Try to find and kill the process
    try:
        result = subprocess.run(
            ["pkill", "-f", "openclaw"],
            capture_output=True,
        )
        if result.returncode == 0:
            console.print("[blue]Gateway stopped.[/]")
        else:
            console.print("[dim]No running gateway found.[/]")
    except FileNotFoundError:
        console.print("[red]pkill not available.[/]")


@openclaw.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def openclaw_status(as_json: bool) -> None:
    """Check OpenClaw gateway health."""
    config_file = openclaw_config_path()
    if not config_file.exists():
        if as_json:
            _emit_json({
                "ok": True,
                "configured": False,
                "running": False,
                "config_path": str(config_file),
            })
            return
        console.print("[yellow]OpenClaw not configured.[/] Run [bold]msc openclaw setup[/]")
        return

    try:
        config = read_openclaw_config(config_file)
        port = config.get("gateway", {}).get("port", 18789)
        running = False
        if not as_json:
            console.print(f"  Config: {config_file}")
            console.print(f"  Port: {port}")

        # Try to connect
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        running = result == 0
        if as_json:
            _emit_json({
                "ok": True,
                "configured": True,
                "running": running,
                "port": port,
                "config_path": str(config_file),
                "config": config,
            })
            return
        if result == 0:
            console.print(f"  Status: [blue]running[/] (port {port} open)")
        else:
            console.print(f"  Status: [red]not running[/] (port {port} closed)")
    except (json.JSONDecodeError, OSError) as e:
        console.print(f"[red]Error reading config:[/] {e}")


def _find_launch_script() -> Path | None:
    """Find the OpenClaw gateway launch script."""
    return find_script_path("launch_openclaw_gateway.sh")
