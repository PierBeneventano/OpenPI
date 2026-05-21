"""Top-level click group for the msc CLI."""

import click

from consortium.cli import __version__


class MSCGroup(click.Group):
    """Custom group that shows banner and help if consortium is not installed."""

    def invoke(self, ctx: click.Context) -> None:
        super().invoke(ctx)

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # If no args provided, show banner + help instead of erroring
        if not args:
            ctx.invoke(cli)
            click.echo(ctx.get_help())
            ctx.exit(0)
        return super().parse_args(ctx, args)


@click.group(cls=MSCGroup)
@click.version_option(__version__, prog_name="msc")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-essential output.")
@click.option("--no-banner", is_flag=True, help="Suppress the startup banner.")
@click.option(
    "--config-dir",
    type=click.Path(),
    default=None,
    help="Override config directory (default: ~/.msc).",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, no_banner: bool, config_dir: str | None) -> None:
    """PoggioAI/MSc — End-to-end agentic research system.

    Run autonomous research from question to paper with one command.
    Built by the Poggio Lab at MIT.

    \b
    Quick start:
      msc setup                        # First-time setup
      msc run "your research question" # Run a research pipeline
      msc doctor                       # Check your environment
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["no_banner"] = no_banner
    ctx.obj["config_dir"] = config_dir

    # Show banner on bare `msc` invocation (no subcommand)
    if not no_banner and not quiet and ctx.invoked_subcommand is None:
        from consortium.cli.banner import print_banner
        print_banner()


# Register subcommands (lazy imports to keep startup fast)
def _register_commands() -> None:
    from consortium.cli.commands.run import run
    from consortium.cli.commands.setup import setup
    from consortium.cli.commands.doctor import doctor
    from consortium.cli.commands.config_cmd import config
    from consortium.cli.commands.runs import runs
    from consortium.cli.commands.resume import resume
    from consortium.cli.commands.campaign import campaign
    from consortium.cli.commands.budget import budget
    from consortium.cli.commands.notify import notify
    from consortium.cli.commands.openclaw import openclaw
    from consortium.cli.commands.openclaude import openclaude
    from consortium.cli.commands.extras import install
    from consortium.cli.commands.status import status
    from consortium.cli.commands.logs import logs
    from consortium.cli.commands.project import project
    from consortium.cli.commands.artifacts import artifacts
    from consortium.cli.commands.campaigns import campaigns
    from consortium.cli.commands.selftest import selftest
    from consortium.cli.commands.capabilities import capabilities
    from consortium.cli.commands.events import events
    from consortium.cli.commands.harness import harness
    from consortium.cli.commands.hpc import hpc

    cli.add_command(run)
    cli.add_command(setup)
    cli.add_command(doctor)
    cli.add_command(config)
    cli.add_command(runs)
    cli.add_command(resume)
    cli.add_command(campaign)
    cli.add_command(budget)
    cli.add_command(notify)
    cli.add_command(openclaw)
    cli.add_command(openclaude)
    cli.add_command(install)
    cli.add_command(status)
    cli.add_command(logs)
    cli.add_command(project)
    cli.add_command(artifacts)
    cli.add_command(campaigns)
    cli.add_command(selftest)
    cli.add_command(capabilities)
    cli.add_command(events)
    cli.add_command(harness)
    cli.add_command(hpc)


_register_commands()


if __name__ == "__main__":
    cli()
