"""
Args for system initialization
"""
import argparse
from .utils import AVAILABLE_MODELS

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="PoggioAI/MSc — Multi-Agent Research Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create new workspace (model from .llm_config.yaml)
  python -m consortium.runner --task "Research transformer attention mechanisms"

  # Resume from existing workspace
  python -m consortium.runner --resume results/consortium_20250929_143022/ --model claude-sonnet-4-6
  python -m consortium.runner --resume results/consortium_20250929_143022/ --task "Continue writing the conclusion section"
        """
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["local", "tinker", "hpc"],
        default=None,
        help="Deployment mode: 'local' (laptop-only, CPU experiments), "
             "'tinker' (local orchestrator + Tinker API for GPU training), "
             "'hpc' (SLURM-based HPC cluster). "
             "Default: auto-detect from environment.",
    )

    parser.add_argument(
        "--model",
        type=str,
        choices=AVAILABLE_MODELS,
        default=None,
        help="LLM model to use for all agents (default: from .llm_config.yaml)"
    )

    parser.add_argument(
        "--interpreter",
        type=str,
        default="python",
        help="Python interpreter path for experiment execution"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    parser.add_argument(
        "--log-to-files",
        dest="log_to_files",
        action="store_true",
        default=None,
        help="Redirect stdout/stderr to logs/consortium_<timestamp>.{out,err} (default: on)"
    )

    parser.add_argument(
        "--no-log-to-files",
        dest="log_to_files",
        action="store_false",
        help="Do not redirect stdout/stderr to log files"
    )

    parser.add_argument(
        "--log-training-data",
        action="store_true",
        default=False,
        help="Log all LLM input/output pairs to training_data.jsonl in the "
             "workspace directory (OpenAI fine-tuning JSONL format). "
             "Also respects CONSORTIUM_LOG_TRAINING_DATA=1 env var."
    )

    parser.add_argument(
        "--no-tool-calls-in-training-data",
        action="store_true",
        default=False,
        help="Exclude tool call/result messages from training data logs."
    )

    parser.add_argument(
        "--reasoning-effort",
        type=str,
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        default=None,
        help="GPT-5 reasoning effort level (default: high, overrides .llm_config.yaml)"
    )

    parser.add_argument(
        "--verbosity",
        type=str,
        choices=["low", "medium", "high"],
        default=None,
        help="GPT-5 verbosity level (default: medium, overrides .llm_config.yaml)"
    )

    parser.add_argument(
        "--callback_host",
        type=str,
        default="127.0.0.1",
        help="Host for the callback server"
    )

    parser.add_argument(
        "--callback_port",
        type=int,
        default=5001,
        help="Port for the callback server"
    )

    parser.add_argument(
        "--enable-planning",
        action="store_true",
        help="Enable planning feature for research agents (creates systematic step-by-step plans)"
    )

    parser.add_argument(
        "--planning-interval",
        type=int,
        default=3,
        help="Interval for planning steps (e.g., 3 = replan every 3 steps). Only used if --enable-planning is set."
    )

    parser.add_argument(
        "--resume",
        type=str,
        help="Resume from an existing workspace directory (e.g., results/20250128_143022_task_name/). "
             "If specified, will continue from the existing workspace instead of creating a new one."
    )

    parser.add_argument(
        "--start-from-stage",
        type=str,
        default=None,
        help=(
            "Start the fixed-stage pipeline from a specific stage. "
            "Requires --resume. Accepts canonical stage names (e.g., "
            "'experimentation_agent') and short aliases (e.g., 'experimentation')."
        ),
    )

    parser.add_argument(
        "--campaign-id",
        type=str,
        default=None,
        help="Attach this run to a local-first campaign id in .msc/campaigns.db.",
    )

    parser.add_argument(
        "--campaign-root",
        type=str,
        default=None,
        help="Workspace root containing .msc/campaigns.db for --campaign-id.",
    )

    parser.add_argument(
        "--campaign-graph-version",
        type=int,
        default=None,
        help="Graph snapshot version this run is executing.",
    )

    # -----------------------------------------------------------------
    # Iterate mode (revision from prior paper + feedback)
    # -----------------------------------------------------------------
    parser.add_argument(
        "--iterate",
        type=str,
        default=None,
        metavar="DIR",
        help="Path to a directory containing a prior paper (.pdf or .tex) and "
             "feedback files (.tex/.md). The pipeline re-runs in revision mode, "
             "skipping discovery phases and producing an improved paper.",
    )

    parser.add_argument(
        "--iterate-start-stage",
        type=str,
        default=None,
        help="Hard override for iterate-mode entry stage. When set, iterate routing "
             "is bypassed and the pipeline enters the requested canonical stage directly. "
             "Use 'writeup_agent' to skip resource preparation if paper_workspace is ready.",
    )

    parser.add_argument(
        "--task",
        type=str,
        help="The research task description. Can be used with --resume to continue a previous task."
    )

    parser.add_argument(
        "--require-pdf",
        action="store_true",
        help="Require final_paper.pdf before the manager can terminate successfully."
    )

    parser.add_argument(
        "--enforce-paper-artifacts",
        action="store_true",
        help="Enforce paper artifact checks (final_paper.tex; optionally final_paper.pdf and experiments_to_run_later.md)."
    )

    parser.add_argument(
        "--manager-max-steps",
        type=int,
        default=None,
        help="Override manager agent max steps for this run."
    )

    parser.add_argument(
        "--enable-math-agents",
        action="store_true",
        help="Enable theorem-oriented math agents (proposer, prover, rigorous verifier, empirical verifier)."
    )

    parser.add_argument(
        "--require-experiment-plan",
        action="store_true",
        help="When paper artifact enforcement is enabled, also require experiments_to_run_later.md."
    )

    parser.add_argument(
        "--enforce-editorial-artifacts",
        action="store_true",
        help="Require editorial workflow artifacts (style guide, intro skeleton, review verdict, etc.).",
    )

    parser.add_argument(
        "--min-review-score",
        type=int,
        default=8,
        help="Minimum reviewer overall_score required by the strict review gate.",
    )

    parser.add_argument(
        "--pipeline-mode",
        type=str,
        default=None,
        help=(
            "DEPRECATED: ignored. The pipeline now always runs a fixed-stage workflow. "
            "Use --start-from-stage with --resume to begin from a specific stage."
        ),
    )

    parser.add_argument(
        "--followup-max-iterations",
        type=int,
        default=3,
        help="Maximum Step 6 <-> 6.2 follow-up loops in full_research mode.",
    )

    parser.add_argument(
        "--max-rebuttal-iterations",
        type=int,
        default=2,
        help="Maximum rebuttal loops when reviewer identifies issues requiring "
             "new experiments or theory work (default: 2).",
    )

    parser.add_argument(
        "--enable-counsel",
        dest="enable_counsel",
        action="store_true",
        default=False,
        help="Enable multi-model counsel mode: each pipeline stage runs 4 independent models "
             "that debate and synthesize a consensus output (overrides counsel.enabled in config).",
    )

    parser.add_argument(
        "--no-counsel",
        dest="no_counsel",
        action="store_true",
        default=False,
        help="Disable counsel mode even if counsel.enabled=true in .llm_config.yaml.",
    )

    parser.add_argument(
        "--counsel-max-debate-rounds",
        dest="counsel_max_debate_rounds",
        type=int,
        default=None,
        help="Number of debate rounds per pipeline stage in counsel mode (default: 3, "
             "overrides counsel.max_debate_rounds in config).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate API keys, config, and workspace setup then exit without running the pipeline. "
             "Useful for checking your environment before a real (paid) run.",
    )

    parser.add_argument(
        "--no-steering",
        action="store_true",
        help="Disable the TCP/HTTP live-steering sockets (ports 5001/5002). "
             "Use this in Docker containers, restricted environments, or shared servers "
             "where binding ports is not possible.",
    )

    parser.add_argument(
        "--output-format",
        type=str,
        choices=["latex", "markdown"],
        default="latex",
        help="Output format for the final paper. 'latex' (default) produces final_paper.tex + PDF "
             "(requires pdflatex). 'markdown' produces final_paper.md with no LaTeX dependency.",
    )

    parser.add_argument(
        "--list-runs",
        action="store_true",
        help="List past runs in the results/ directory with cost and status, then exit.",
    )

    # -----------------------------------------------------------------
    # Agentic tree search
    # -----------------------------------------------------------------
    parser.add_argument(
        "--enable-tree-search",
        action="store_true",
        default=False,
        help="Enable agentic tree search: explore multiple proof strategies, "
             "ideas, and experiment designs in parallel via DAG-layered best-first search.",
    )

    parser.add_argument(
        "--tree-max-breadth",
        type=int,
        default=3,
        help="Maximum parallel branches per decision point in tree search (default: 3).",
    )

    parser.add_argument(
        "--tree-max-depth",
        type=int,
        default=4,
        help="Maximum debugging/refinement recursion depth in tree search (default: 4).",
    )

    parser.add_argument(
        "--tree-max-parallel",
        type=int,
        default=6,
        help="Maximum concurrent tree branches executing at once (default: 6).",
    )

    parser.add_argument(
        "--tree-pruning-threshold",
        type=float,
        default=0.2,
        help="Score threshold below which tree branches are pruned (default: 0.2).",
    )

    parser.add_argument(
        "--tree-counsel-mode",
        type=str,
        choices=["all_nodes", "final_only", "by_depth", "by_node_type"],
        default="all_nodes",
        help="When to run multi-model counsel within tree branches: "
             "'all_nodes' (default, maximum quality), 'final_only', 'by_depth', 'by_node_type'.",
    )

    # -----------------------------------------------------------------
    # Adversarial verification
    # -----------------------------------------------------------------
    parser.add_argument(
        "--adversarial-verification",
        action="store_true",
        default=False,
        help="Enable adversarial verification: after cooperative verifiers pass, "
             "run a hostile red-team verifier that tries to break proofs and experiments. "
             "Branches only succeed if the adversarial verifier finds no CRITICAL issues.",
    )

    # -----------------------------------------------------------------
    # Milestone gates (human-in-the-loop)
    # -----------------------------------------------------------------
    parser.add_argument(
        "--enable-milestone-gates",
        action="store_true",
        default=False,
        help="Pause the pipeline at strategic milestone points (after research plan, "
             "track merge, results analysis, reviewer) and wait for human input via HTTP.",
    )

    parser.add_argument(
        "--milestone-timeout",
        type=int,
        default=3600,
        help="Seconds to wait for human response at milestone gates before auto-proceeding (default: 3600).",
    )

    # -----------------------------------------------------------------
    # Persona council
    # -----------------------------------------------------------------
    parser.add_argument(
        "--persona-debate-rounds",
        type=int,
        default=None,
        help="Number of debate rounds in persona council (default: 3, "
             "overrides persona_council.max_debate_rounds in config).",
    )

    parser.add_argument(
        "--persona-post-vote-retries",
        dest="persona_post_vote_retries",
        type=int,
        default=None,
        help="Max post-vote retry rounds in persona council (default: 1).",
    )

    parser.add_argument(
        "--no-duality-check",
        action="store_true",
        default=False,
        help="Disable duality check gate (formalize results goes directly to paper production).",
    )

    parser.add_argument(
        "--theory-repair-max-attempts",
        dest="theory_repair_max_attempts",
        type=int,
        default=None,
        help="Max theory track repair retries (default: 2).",
    )
    parser.add_argument(
        "--duality-max-attempts",
        dest="duality_max_attempts",
        type=int,
        default=None,
        help="Max duality gate rework attempts (default: 2).",
    )
    parser.add_argument(
        "--max-validation-retries",
        dest="max_validation_retries",
        type=int,
        default=None,
        help="Max validation gate retries before forced completion (default: 3).",
    )
    parser.add_argument(
        "--enable-ensemble-review",
        action="store_true",
        default=False,
        help="Enable 5-reviewer ensemble (parallel reviews with different biases).",
    )

    # -----------------------------------------------------------------
    # Autonomous operation
    # -----------------------------------------------------------------
    parser.add_argument(
        "--autonomous-mode",
        action="store_true",
        default=True,
        help="Run fully autonomously with no human-in-the-loop gates "
             "(default: True). Disables milestone gates and auto-proceeds "
             "on timeouts.",
    )
    parser.add_argument(
        "--no-autonomous-mode",
        action="store_false",
        dest="autonomous_mode",
        help="Disable autonomous mode — enable milestone gates and "
             "human approval checkpoints.",
    )

    # -----------------------------------------------------------------
    # Reliability — watchdog & timeout
    # -----------------------------------------------------------------
    parser.add_argument(
        "--max-run-seconds",
        type=int,
        default=None,
        help="Hard timeout for the entire pipeline run in seconds. "
             "If the graph hasn't finished after this many seconds, the "
             "process is killed with SIGALRM. Also enables a progress "
             "heartbeat file (.progress_heartbeat) in the workspace.",
    )
    parser.add_argument(
        "--recursion-limit",
        type=int,
        default=None,
        help="Maximum LangGraph transition budget for the pipeline. "
             "By default this is derived from the number of stages and "
             "configured research loops, avoiding LangGraph's small default.",
    )

    return parser.parse_args()
