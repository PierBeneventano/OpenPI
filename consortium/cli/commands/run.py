"""msc run — execute a research pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from consortium.cli.core.config_manager import load_explicit_config
from consortium.cli.core.env_manager import (
    check_required_keys,
    has_required_llm_key,
    inject_runtime_env,
)
from consortium.cli.core.flag_translator import build_argv
from consortium.cli.core.llm_config_generator import write_llm_config
from consortium.cli.core.paths import build_runner_argv, find_project_root
from consortium.cli.core.presets import PRESETS, TIERS, TIER_CHOICES, resolve_tier_name
from consortium.models import AVAILABLE_MODELS

console = Console()


def _should_use_repo_env(project_root: Path | None) -> bool:
    if project_root is None:
        return False
    cwd = Path.cwd().resolve()
    project_root = project_root.resolve()
    return cwd == project_root or project_root in cwd.parents


@click.command()
@click.argument("task", required=False)
@click.option(
    "--tier", "-t",
    type=click.Choice(list(TIER_CHOICES)),
    default=None,
    help="Product tier: scaffold, lean, standard, serious, ultra. Legacy tier names remain accepted.",
)
@click.option(
    "--preset", "-p",
    type=click.Choice(list(PRESETS.keys())),
    default=None,
    hidden=True,
    help="Deprecated — use --tier instead.",
)
@click.option("--task-file", "-f", type=click.Path(exists=True), help="Read task from a file.")
@click.option("--model", "-m", type=str, default=None, help="Override the LLM model.")
@click.option("--budget", "-b", type=int, default=None, help="Override the budget in USD.")
@click.option("--output-format", "-o", type=click.Choice(["markdown", "latex"]), default=None)
@click.option("--mode", type=click.Choice(["local", "tinker", "hpc"]), default=None)
@click.option("--dry-run", is_flag=True, help="Validate setup without running (no cost).")
@click.option("--counsel/--no-counsel", default=None, help="Enable/disable multi-model counsel.")
@click.option("--math/--no-math", default=None, help="Enable/disable math agents.")
@click.option("--tree-search/--no-tree-search", default=None, help="Enable/disable tree search.")
@click.option("--max-run-seconds", type=int, default=None, help="Hard timeout in seconds.")
@click.option("--stream/--no-stream", default=True, help="Enable/disable streaming display.")
@click.option("--campaign-id", type=str, default=None, help="Attach this run to a local-first campaign id.")
@click.option("--campaign-root", type=click.Path(), default=None, help="Workspace root containing .msc/campaigns.db.")
@click.option("--campaign-graph-version", type=int, default=None, help="Graph snapshot version being executed.")
@click.option(
    "--iterate", "-i", type=click.Path(exists=True), default=None,
    help="Path to directory with prior paper (.tex/.pdf) + feedback (.md/.tex) for revision mode.",
)
@click.option("--iterate-start-stage", type=str, default=None, help="Override entry stage for iterate mode.")
@click.option("--detach/--foreground", "detach", default=None,
              help="Force/forbid sbatch submission. Defaults to auto-detect: sbatch on HPC, foreground otherwise.")
@click.option("--wall", type=str, default=None, help="SLURM wall time when detached (e.g. '2-00:00:00').")
@click.option("--partition", type=str, default=None, help="SLURM partition override when detached.")
@click.option("--cpus", type=int, default=None, help="cpus-per-task for the detached orchestrator job.")
@click.option("--mem", type=str, default=None, help="Memory request when detached (e.g. '32G').")
@click.pass_context
def run(
    ctx: click.Context,
    task: str | None,
    tier: str | None,
    preset: str | None,
    task_file: str | None,
    model: str | None,
    budget: int | None,
    output_format: str | None,
    mode: str | None,
    dry_run: bool,
    counsel: bool | None,
    math: bool | None,
    tree_search: bool | None,
    max_run_seconds: int | None,
    stream: bool,
    campaign_id: str | None,
    campaign_root: str | None,
    campaign_graph_version: int | None,
    iterate: str | None,
    iterate_start_stage: str | None,
    detach: bool | None,
    wall: str | None,
    partition: str | None,
    cpus: int | None,
    mem: str | None,
) -> None:
    """Run a research pipeline on a question or topic.

    \b
    Examples:
      msc run "How do transformers handle long-range dependencies?"
      msc run --tier pro "Prove the Goldbach conjecture for small primes"
      msc run --task-file my_task.txt --tier max
      msc run "Quick survey of GAN architectures" --tier budget --dry-run
    """
    config_dir = ctx.obj.get("config_dir")
    user_cfg = load_explicit_config(config_dir)
    project_root = find_project_root()

    # Resolve task
    if task_file and not task:
        with open(task_file, "r") as f:
            task = f.read().strip()
    if not task:
        if iterate:
            task = "Revise and improve the paper based on reviewer feedback."
        else:
            console.print("[bold white on red] Error [/] Provide a research task as an argument or via --task-file.")
            raise SystemExit(1)

    quiet = ctx.obj.get("quiet", False)

    # Check for API keys
    if not has_required_llm_key(config_dir, repo_root=project_root):
        console.print(
            "[bold white on red] Error [/] OPENROUTER_API_KEY is required. "
            "Run [bold white]msc setup[/] first or configure it in your environment."
        )
        raise SystemExit(1)

    # ── Resolve tier ────────────────────────────────────────────────
    # Priority: --tier > --preset (backward compat) > config.yaml > default
    tier_name: str | None = tier
    if tier_name is None and preset is not None:
        tier_name = resolve_tier_name(preset)
    if tier_name is None:
        tier_name = user_cfg.get("tier") or user_cfg.get("preset")
    if tier_name is None:
        tier_name = "medium"
    tier_name = resolve_tier_name(tier_name)
    selected_tier = TIERS[tier_name]

    persisted_model = user_cfg.get("model")
    persisted_budget = user_cfg.get("budget_usd")
    persisted_output = user_cfg.get("output_format")
    persisted_mode = user_cfg.get("mode")
    persisted_counsel = user_cfg.get("enable_counsel")
    persisted_math = user_cfg.get("enable_math_agents")
    persisted_tree = user_cfg.get("enable_tree_search")

    effective_model = model or (
        persisted_model if persisted_model and persisted_model != selected_tier.model else selected_tier.model
    )
    if effective_model not in AVAILABLE_MODELS:
        console.print(
            "[bold white on red] Error [/] "
            f"Unknown model '{effective_model}'. Choose one of: {', '.join(AVAILABLE_MODELS)}."
        )
        raise SystemExit(2)
    effective_budget = budget if budget is not None else (
        int(persisted_budget)
        if persisted_budget is not None and int(persisted_budget) != selected_tier.budget_usd
        else selected_tier.budget_usd
    )
    effective_output = output_format or (
        persisted_output
        if persisted_output and persisted_output != selected_tier.output_format
        else selected_tier.output_format
    )
    effective_mode = mode or (
        persisted_mode if persisted_mode and persisted_mode != "auto" else None
    )
    effective_counsel = counsel
    if effective_counsel is None and persisted_counsel is not None and bool(persisted_counsel) != selected_tier.enable_counsel:
        effective_counsel = bool(persisted_counsel)
    if effective_counsel is None:
        effective_counsel = selected_tier.enable_counsel

    effective_math = math
    if effective_math is None and persisted_math is not None and bool(persisted_math) != selected_tier.enable_math_agents:
        effective_math = bool(persisted_math)
    if effective_math is None:
        effective_math = selected_tier.enable_math_agents

    effective_tree = tree_search
    if effective_tree is None and persisted_tree is not None and bool(persisted_tree) != selected_tier.enable_tree_search:
        effective_tree = bool(persisted_tree)
    if effective_tree is None:
        effective_tree = selected_tier.enable_tree_search

    # ── Generate .llm_config.yaml from tier ─────────────────────────
    if not user_cfg.get("custom_llm_config", False):
        _cfg_path = Path(".llm_config.yaml")
        if _cfg_path.exists():
            try:
                _head = _cfg_path.read_text()[:300]
                if "Auto-generated by msc" not in _head:
                    backup_path = _cfg_path.with_suffix(_cfg_path.suffix + ".bak")
                    counter = 1
                    while backup_path.exists():
                        backup_path = _cfg_path.with_suffix(_cfg_path.suffix + f".bak.{counter}")
                        counter += 1
                    _cfg_path.replace(backup_path)
                    console.print(
                        "[dim]Backed up existing .llm_config.yaml to "
                        f"{backup_path.name} and regenerated it from the selected tier.[/]\n"
                        "[dim]Set custom_llm_config: true in ~/.msc/config.yaml if you want "
                        "to manage the project file yourself.[/]"
                    )
            except OSError:
                pass
        llm_overrides: dict[str, object] = {
            "model": effective_model,
            "budget_usd": effective_budget,
            "counsel": effective_counsel,
        }
        write_llm_config(selected_tier, ".llm_config.yaml", overrides=llm_overrides)

    # Build overrides from CLI flags
    overrides: dict[str, object] = {"dry_run": dry_run}
    if effective_model != selected_tier.model:
        overrides["model"] = effective_model
    if effective_budget != selected_tier.budget_usd:
        overrides["budget_usd"] = effective_budget
    if effective_output != selected_tier.output_format:
        overrides["output_format"] = effective_output
    if effective_mode:
        overrides["mode"] = effective_mode
    if max_run_seconds:
        overrides["max_run_seconds"] = max_run_seconds
    if campaign_id:
        overrides["campaign_id"] = campaign_id
    if campaign_root:
        overrides["campaign_root"] = campaign_root
    if campaign_graph_version:
        overrides["campaign_graph_version"] = campaign_graph_version
    if effective_counsel is True:
        overrides["enable_counsel"] = True
        overrides["no_counsel"] = False
    else:
        overrides["enable_counsel"] = False
        overrides["no_counsel"] = True
    if effective_math is True:
        overrides["enable_math_agents"] = True
    else:
        overrides["enable_math_agents"] = False
    if effective_tree is True:
        overrides["enable_tree_search"] = True
    else:
        overrides["enable_tree_search"] = False
    if iterate:
        overrides["iterate"] = iterate
    if iterate_start_stage:
        overrides["iterate_start_stage"] = iterate_start_stage

    # Build argv
    argv = build_runner_argv(build_argv(task, tier_name, **overrides))

    # Show run summary
    if not quiet:
        counsel_status = "off"
        if effective_counsel:
            specs = selected_tier.counsel_model_specs
            counsel_status = f"on ({len(specs)} models)" if specs else "on"
        key_results = check_required_keys(config_dir, repo_root=project_root)
        openrouter_source = next(
            (kr.get("source") for kr in key_results if kr["env_var"] == "OPENROUTER_API_KEY" and kr["configured"]),
            None,
        ) or "shell"

        table = Table(
            title="[bold white]Run Configuration[/]",
            show_header=False,
            padding=(0, 2),
            border_style="bright_black",
        )
        table.add_column("Key", style="bold blue")
        table.add_column("Value", style="white")
        table.add_row("Task", task[:80] + ("..." if len(task) > 80 else ""))
        table.add_row("Tier", f"{selected_tier.tier_label} ({selected_tier.budget_range})")
        table.add_row("Model", effective_model)
        table.add_row("Budget", f"${effective_budget}")
        table.add_row("Output", effective_output)
        table.add_row("Counsel", counsel_status)
        table.add_row("Credentials", f"OpenRouter via {openrouter_source}")
        table.add_row("Dry run", "yes" if dry_run else "no")
        console.print(table)
        console.print()

    # Inject API keys into environment
    allow_repo_env = _should_use_repo_env(project_root)
    inject_runtime_env(
        config_dir_override=config_dir,
        repo_root=project_root,
        allow_repo_env=allow_repo_env,
    )
    key_results = check_required_keys(config_dir, repo_root=project_root, allow_repo_env=allow_repo_env)

    # Run consortium
    if not quiet:
        console.print("[bold blue]\u25b6 Starting pipeline...[/]")
        console.print(f"[dim]$ {' '.join(argv)}[/dim]\n")

    env = dict(**os.environ)
    env["CONSORTIUM_SELECTED_TIER"] = selected_tier.name
    env["CONSORTIUM_MODEL_POLICY_SOURCE"] = (
        "config" if user_cfg.get("custom_llm_config", False) else "tier"
    )
    env["CONSORTIUM_USE_REPO_ENV"] = "1" if allow_repo_env else "0"
    env["CONSORTIUM_CREDENTIAL_SOURCES_JSON"] = json.dumps(
        {
            kr["env_var"]: kr.get("source")
            for kr in key_results
            if kr.get("configured") and kr.get("source")
        }
    )
    if project_root is not None:
        env["CONSORTIUM_PROJECT_ROOT"] = str(project_root)
    if selected_tier.name == "live-smoke":
        from consortium.models import get_openrouter_name

        cheap_model = f"openrouter/{get_openrouter_name(effective_model)}"
        env.setdefault("DEEP_RESEARCH_MODEL", cheap_model)
        env.setdefault("OPENDEEPSEARCH_MODEL", cheap_model)
        env.setdefault("OPENDEEPSEARCH_FALLBACK_MODEL", cheap_model)
        env.setdefault("DEEP_RESEARCH_MAX_PAPERS", "3")
        env.setdefault("DEEP_RESEARCH_MAX_TOKENS", "2048")
        env.setdefault("CONSORTIUM_VLM_MODEL", cheap_model)
        env.setdefault("CONSORTIUM_LIVE_SMOKE", "1")

    # ── Decide foreground vs detached (sbatch) ──────────────────────
    # Auto-detect: if sbatch is on PATH and we have a campaign to attach to,
    # submit to SLURM by default. The orchestrator process survives the IDE
    # closing because it runs inside the batch job, not as our subprocess.
    from consortium.cli.core.slurm_submit import is_sbatch_available

    should_detach = False
    if detach is True:
        should_detach = True
    elif detach is False:
        should_detach = False
    elif detach is None and not dry_run and is_sbatch_available() and campaign_id:
        should_detach = True

    if should_detach and not campaign_id:
        console.print(
            "[bold white on red] Error [/] --detach requires --campaign-id "
            "(detached runs are tracked against a campaign in .msc/campaigns.db)."
        )
        raise SystemExit(2)

    if should_detach:
        try:
            return _dispatch_to_hpc_submit(
                ctx=ctx,
                env=env,
                campaign_id=campaign_id,
                campaign_root=campaign_root or str(project_root) if project_root else campaign_root,
                argv=argv,
                task=task,
                tier=tier_name,
                budget=effective_budget,
                output_format=effective_output,
                max_run_seconds=max_run_seconds,
                wall=wall,
                partition=partition,
                cpus=cpus,
                mem=mem,
                quiet=quiet,
            )
        except Exception as exc:
            console.print(f"[bold white on red] Error [/] SLURM submission failed: {exc}")
            console.print("[dim]Falling back to foreground execution. Use --foreground to suppress this fallback.[/dim]")
            # fall through to foreground

    # Use streaming display if available and requested
    use_streaming = stream and not dry_run and not quiet and sys.stdout.isatty()

    if use_streaming:
        try:
            from consortium.cli.display import StreamingDisplay
            display = StreamingDisplay(budget=effective_budget)
            rc = display.run(argv, env)
            raise SystemExit(rc)
        except ImportError:
            pass  # Fall back to simple run

    try:
        proc = subprocess.run(argv, env=env)
        raise SystemExit(proc.returncode)
    except FileNotFoundError:
        console.print(
            "[bold white on red] Error [/] Python could not launch the consortium runner module. "
            "Reinstall with: [bold white]python -m pip install -e .[/]"
        )
        raise SystemExit(1)


def _dispatch_to_hpc_submit(
    *, ctx, env: dict[str, str], campaign_id: str, campaign_root: str | None,
    argv: list[str], task: str | None, tier: str | None, budget: int | None,
    output_format: str | None, max_run_seconds: int | None,
    wall: str | None, partition: str | None, cpus: int | None, mem: str | None,
    quiet: bool,
) -> None:
    """Shell out to ``msc hpc submit`` for a detached run.

    Subprocessing is intentional: ``hpc submit`` calls ``sbatch`` which is a
    short, well-defined operation, and the foreground ``msc run`` process
    should exit as soon as the job is queued (the IDE doesn't want to wait).
    """
    # Re-extract any "free" runner args from argv beyond the explicit kwargs
    # so that --counsel / --math / --tree-search / etc. ride through too.
    forwarded: list[str] = []
    skip_next = False
    consumed = {"--task", "--tier", "--budget", "--output-format", "--max-run-seconds",
                "--campaign-id", "--campaign-root", "--campaign-graph-version"}
    for index, value in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if index < 3:  # python, -m, consortium.runner
            continue
        if value in consumed:
            skip_next = True
            continue
        if any(value.startswith(name + "=") for name in consumed):
            continue
        forwarded.append(value)

    submit_cmd = [sys.executable, "-m", "consortium.cli.main", "hpc",
                  "--root", campaign_root or ".", "submit", campaign_id]
    if task:
        submit_cmd += ["--task", task]
    if tier:
        submit_cmd += ["--tier", tier]
    if budget is not None:
        submit_cmd += ["--budget", str(budget)]
    if output_format:
        submit_cmd += ["--output-format", output_format]
    if max_run_seconds is not None:
        submit_cmd += ["--max-run-seconds", str(max_run_seconds)]
    if partition:
        submit_cmd += ["--partition", partition]
    if wall:
        submit_cmd += ["--wall", wall]
    if cpus is not None:
        submit_cmd += ["--cpus", str(cpus)]
    if mem:
        submit_cmd += ["--mem", mem]
    for extra in forwarded:
        submit_cmd += ["--extra-runner-arg", extra]
    if not quiet:
        submit_cmd += ["--json"]

    result = subprocess.run(submit_cmd, env=env, capture_output=quiet, text=True)
    if quiet and result.stdout:
        click.echo(result.stdout, nl=False)
    raise SystemExit(result.returncode)


