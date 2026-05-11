"""Translate presets and CLI options into consortium argv flags."""

from __future__ import annotations

from consortium.cli.core.presets import Preset, PRESETS


def preset_to_argv(preset: Preset, task: str, **overrides: object) -> list[str]:
    """Convert a preset + task + overrides into a consortium CLI argv list.

    Returns a list like ["consortium", "--task", "...", "--model", "...", ...].
    """
    argv = ["consortium"]

    # Task
    argv.extend(["--task", task])

    # Model (overridable)
    model = str(overrides.pop("model", preset.model))
    argv.extend(["--model", model])

    # Budget is set via .llm_config.yaml (written by run.py), not CLI flag.
    overrides.pop("budget_usd", None)  # consume but don't emit

    # Output format (overridable)
    output_format = str(overrides.pop("output_format", preset.output_format))
    argv.extend(["--output-format", output_format])

    # Boolean flags
    if overrides.pop("enable_counsel", preset.enable_counsel):
        argv.append("--enable-counsel")
    if overrides.pop("no_counsel", preset.no_counsel):
        argv.append("--no-counsel")
    if overrides.pop("enable_math_agents", preset.enable_math_agents):
        argv.append("--enable-math-agents")
    if overrides.pop("enable_tree_search", preset.enable_tree_search):
        argv.append("--enable-tree-search")
    if overrides.pop("adversarial_verification", preset.adversarial_verification):
        argv.append("--adversarial-verification")
    if overrides.pop("enable_planning", preset.enable_planning):
        argv.append("--enable-planning")
    if overrides.pop("enforce_paper_artifacts", preset.enforce_paper_artifacts):
        argv.append("--enforce-paper-artifacts")
    if overrides.pop("enforce_editorial_artifacts", preset.enforce_editorial_artifacts):
        argv.append("--enforce-editorial-artifacts")
    if overrides.pop("autonomous_mode", preset.autonomous_mode):
        argv.append("--autonomous-mode")

    # Ensemble review
    if overrides.pop("enable_ensemble_review", preset.enable_ensemble_review):
        argv.append("--enable-ensemble-review")

    # Quality knobs from tier (only emit if set on the preset or overridden)
    _quality_int_flags = [
        ("followup_max_iterations", "--followup-max-iterations"),
        ("max_rebuttal_iterations", "--max-rebuttal-iterations"),
        ("min_review_score", "--min-review-score"),
        ("manager_max_steps", "--manager-max-steps"),
        ("theory_repair_max_attempts", "--theory-repair-max-attempts"),
        ("duality_max_attempts", "--duality-max-attempts"),
        ("persona_post_vote_retries", "--persona-post-vote-retries"),
        ("max_validation_retries", "--max-validation-retries"),
        ("tree_max_breadth", "--tree-max-breadth"),
        ("tree_max_depth", "--tree-max-depth"),
        ("tree_max_parallel", "--tree-max-parallel"),
    ]
    for attr, flag in _quality_int_flags:
        val = overrides.pop(attr, getattr(preset, attr, None))
        if val is not None:
            argv.extend([flag, str(int(val))])

    _quality_float_flags = [
        ("tree_pruning_threshold", "--tree-pruning-threshold"),
    ]
    for attr, flag in _quality_float_flags:
        val = overrides.pop(attr, getattr(preset, attr, None))
        if val is not None:
            argv.extend([flag, str(val)])

    # Counsel debate rounds (from preset or override)
    counsel_rounds = overrides.pop("counsel_debate_rounds", None)
    if counsel_rounds is None and preset.counsel_debate_rounds:
        counsel_rounds = preset.counsel_debate_rounds
    if counsel_rounds:
        argv.extend(["--counsel-max-debate-rounds", str(counsel_rounds)])

    # Persona debate rounds (from preset or override)
    persona_rounds = overrides.pop("persona_debate_rounds", None)
    if persona_rounds is None and preset.counsel_debate_rounds:
        # Use counsel debate rounds as persona default too for ultra tier
        pass  # persona debate rounds set separately via --persona-debate-rounds
    if persona_rounds:
        argv.extend(["--persona-debate-rounds", str(persona_rounds)])

    # Iterate mode
    iterate_dir = overrides.pop("iterate", None)
    if iterate_dir:
        argv.extend(["--iterate", str(iterate_dir)])
    iterate_start = overrides.pop("iterate_start_stage", None)
    if iterate_start:
        argv.extend(["--iterate-start-stage", str(iterate_start)])

    # Dry run
    if overrides.pop("dry_run", False):
        argv.append("--dry-run")

    # Resume
    resume = overrides.pop("resume", None)
    if resume:
        argv.extend(["--resume", str(resume)])

    # Start from stage
    start_from = overrides.pop("start_from_stage", None)
    if start_from:
        argv.extend(["--start-from-stage", str(start_from)])

    # Campaign attachment
    campaign_id = overrides.pop("campaign_id", None)
    if campaign_id:
        argv.extend(["--campaign-id", str(campaign_id)])
    campaign_root = overrides.pop("campaign_root", None)
    if campaign_root:
        argv.extend(["--campaign-root", str(campaign_root)])
    campaign_graph_version = overrides.pop("campaign_graph_version", None)
    if campaign_graph_version:
        argv.extend(["--campaign-graph-version", str(campaign_graph_version)])

    # Mode override
    mode = overrides.pop("mode", None)
    if mode:
        argv.extend(["--mode", str(mode)])

    # Task file (alternative to inline task)
    task_file = overrides.pop("task_file", None)
    if task_file:
        # Replace the inline task with file contents
        try:
            with open(str(task_file), "r") as f:
                file_task = f.read().strip()
            # Find and replace the task in argv
            idx = argv.index("--task")
            argv[idx + 1] = file_task
        except (OSError, ValueError):
            pass

    # Max run seconds
    max_run = overrides.pop("max_run_seconds", None)
    if max_run:
        argv.extend(["--max-run-seconds", str(max_run)])

    return argv


def build_argv(
    task: str,
    preset_name: str = "standard",
    **overrides: object,
) -> list[str]:
    """Build consortium argv from a preset name + overrides.

    This is the main entry point for the run command.
    """
    preset = PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}. Choose from: {', '.join(PRESETS)}")
    return preset_to_argv(preset, task, **overrides)
