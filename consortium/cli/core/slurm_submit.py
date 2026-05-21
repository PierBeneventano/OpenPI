"""sbatch script generation for detached campaign runs.

Generates and submits two long-running SLURM jobs per campaign run:

* An *orchestrator* job that runs ``python -m consortium.runner`` for the
  campaign. The whole LangGraph executes inside this one process, parallel
  branches included (they're I/O-bound LLM calls so threads work fine).
  When this job exits an EXIT trap records the outcome.

* A *heartbeat* job that supervises the orchestrator: polls ``squeue``,
  writes liveness ticks, resubmits the orchestrator on wall-time truncation,
  and self-resubmits before its own wall expires. Modeled on
  ``scripts/launch_openclaw_gateway.sh``.

Reuses the conda activation + engaging_config.yaml resolution pattern from
``consortium/campaign/runner.py:launch_stage_slurm`` (lines 432-604) so the
behavior matches the rest of the SLURM-aware code in the repo.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


# ----- Cluster config resolution ------------------------------------------------


@dataclass
class ClusterConfig:
    """Resolved cluster settings for sbatch generation."""

    partition: str
    time: str
    cpus: int
    mem: str
    conda_init: str
    conda_env_prefix: str
    conda_module: str
    config_path: str
    repo_dir: str


def load_cluster_config(
    repo_dir: str,
    *,
    role: str = "orchestrator",
    partition: Optional[str] = None,
    time: Optional[str] = None,
    cpus: Optional[int] = None,
    mem: Optional[str] = None,
) -> ClusterConfig:
    """Read engaging_config.yaml and apply caller overrides.

    ``role`` picks which subsection of ``cluster`` to read defaults from
    (orchestrator, repair, experiment_gpu, ...).
    """
    config_path = os.environ.get(
        "ENGAGING_CONFIG", os.path.join(repo_dir, "engaging_config.yaml")
    )
    cluster_config: dict = {}
    if os.path.exists(config_path):
        try:
            from consortium.campaign.workflow_utils import expand_env_vars  # type: ignore
        except Exception:  # pragma: no cover - fall back if module path differs
            def expand_env_vars(text: str) -> str:  # type: ignore
                return text
        with open(config_path) as handle:
            raw_text = handle.read()
        raw = yaml.safe_load(expand_env_vars(raw_text)) or {}
        cluster_config = raw.get("cluster", {})

    section = cluster_config.get(role, {}) or {}
    return ClusterConfig(
        partition=partition or section.get("partition", "sched_mit_hill"),
        time=time or section.get("time", "12:00:00"),
        cpus=cpus or section.get("cpus", 4),
        mem=mem or section.get("mem", "32G"),
        conda_init=cluster_config.get("conda_init_script")
        or os.environ.get("CONDA_INIT_SCRIPT", ""),
        conda_env_prefix=cluster_config.get("conda_env_prefix")
        or os.environ.get("CONDA_PREFIX", ""),
        conda_module=cluster_config.get("modules", {}).get(
            "conda", "miniforge/25.11.0-0"
        ),
        config_path=config_path,
        repo_dir=repo_dir,
    )


# ----- Common script blocks -----------------------------------------------------


def _conda_block(cfg: ClusterConfig) -> str:
    """Return the standard env activation prelude.

    Matches the activation pattern used by ``launch_stage_slurm`` so the
    runtime environment is identical regardless of which path launched the
    job. Tolerates missing conda_init by falling back to ``conda shell.bash
    hook``.
    """
    return f"""set -eo pipefail
export PS1="${{PS1:-}}"

echo "========================================"
echo "Job ID: $SLURM_JOB_ID"
echo "Node:   $(hostname)"
echo "Time:   $(date)"
echo "========================================"

module load {cfg.conda_module} 2>/dev/null || true

_CONDA_INIT={shlex.quote(cfg.conda_init)}
if [ -n "$_CONDA_INIT" ] && [ -f "$_CONDA_INIT" ]; then
    source "$_CONDA_INIT"
elif command -v conda >/dev/null 2>&1; then
    eval "$(conda shell.bash hook)"
fi

conda deactivate 2>/dev/null || true
conda deactivate 2>/dev/null || true
export CONDA_SHLVL=0
unset CONDA_DEFAULT_ENV CONDA_PREFIX CONDA_PROMPT_MODIFIER 2>/dev/null || true

_CONDA_ENV={shlex.quote(cfg.conda_env_prefix)}
if [ -n "$_CONDA_ENV" ]; then
    conda activate "$_CONDA_ENV"
else
    conda activate consortium 2>/dev/null || conda activate base
fi

EXPECTED_PYTHON="{cfg.conda_env_prefix}/bin/python"
ACTUAL_PYTHON="$(which python)"
if [ -n "$_CONDA_ENV" ] && [ "$ACTUAL_PYTHON" != "$EXPECTED_PYTHON" ]; then
    echo "WARNING: python resolves to $ACTUAL_PYTHON (expected $EXPECTED_PYTHON)"
    export PATH="{cfg.conda_env_prefix}/bin:$PATH"
fi

export CONSORTIUM_SLURM_ENABLED=1
export ENGAGING_CONFIG={shlex.quote(cfg.config_path)}
"""


def _submit(script_path: Path) -> str:
    """Run ``sbatch <script>`` and return the parsed job id as a string."""
    result = subprocess.run(
        ["sbatch", str(script_path)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sbatch failed for {script_path.name}: {result.stderr.strip() or result.stdout.strip()}"
        )
    match = re.search(r"Submitted batch job (\d+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not parse SLURM job id from sbatch stdout: {result.stdout!r}")
    return match.group(1)


# ----- Public submitters --------------------------------------------------------


def run_dir_for(campaign_workspace: str, run_id: str) -> Path:
    """Standard location of per-run artifacts (sbatch script, logs)."""
    return Path(campaign_workspace) / "runs" / run_id


def submit_orchestrator(
    *,
    campaign_id: str,
    campaign_root: str,
    campaign_workspace: str,
    run_id: str,
    attempt: int,
    repo_dir: str,
    runner_args: list[str],
    inbox_path: str,
    outbox_path: str,
    extra_env: Optional[dict[str, str]] = None,
    partition: Optional[str] = None,
    time: Optional[str] = None,
    cpus: Optional[int] = None,
    mem: Optional[str] = None,
) -> tuple[str, Path]:
    """Generate + submit the orchestrator sbatch.

    Returns ``(job_id, script_path)``. Writes the script to
    ``<workspace>/runs/<run_id>/orchestrator_<attempt>.slurm`` and the
    SLURM stdout/stderr to ``stdout_<attempt>.log`` / ``stderr_<attempt>.log``
    in the same directory.

    Args:
        runner_args: Extra args forwarded to ``python -m consortium.runner``.
            Typically the same args the foreground ``msc run`` would build,
            minus anything we set explicitly here.
        inbox_path/outbox_path: Where the file-based steering poller reads /
            acks. Passed through to the runner.
        attempt: 1-indexed attempt number for this run; used for resume.
    """
    cfg = load_cluster_config(repo_dir, role="orchestrator", partition=partition, time=time, cpus=cpus, mem=mem)
    run_dir = run_dir_for(campaign_workspace, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    script_path = run_dir / f"orchestrator_{attempt}.slurm"
    stdout_log = run_dir / f"stdout_{attempt}.log"
    stderr_log = run_dir / f"stderr_{attempt}.log"

    extra_env = dict(extra_env or {})
    # Resume key: stable across attempts so the LangGraph SqliteSaver
    # checkpointer re-enters the same thread.
    extra_env.setdefault("CONSORTIUM_RUN_ID", run_id)
    extra_env.setdefault("CONSORTIUM_CAMPAIGN_ID", campaign_id)
    extra_env.setdefault("CONSORTIUM_CAMPAIGN_ROOT", campaign_root)
    env_exports = "".join(
        f"export {key}={shlex.quote(value)}\n" for key, value in extra_env.items()
    )

    # Append our resume + steering flags to whatever the caller wants to pass.
    full_args = list(runner_args) + [
        "--campaign-id", campaign_id,
        "--campaign-root", campaign_root,
        "--resume-run-id", run_id,
        "--steering-inbox", inbox_path,
        "--steering-outbox", outbox_path,
    ]
    args_quoted = " ".join(shlex.quote(a) for a in full_args)
    msc_module = "consortium.cli.main"

    body = _conda_block(cfg)
    script = f"""#!/bin/bash
#SBATCH --job-name=msc_run_{run_id[:24]}_a{attempt}
#SBATCH --partition={cfg.partition}
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task={cfg.cpus}
#SBATCH --time={cfg.time}
#SBATCH --mem={cfg.mem}
#SBATCH --output={stdout_log}
#SBATCH --error={stderr_log}
#SBATCH --signal=B:USR1@300

{body}
{env_exports}
cd {shlex.quote(repo_dir)}

RUN_ID={shlex.quote(run_id)}
CAMPAIGN_ID={shlex.quote(campaign_id)}
CAMPAIGN_ROOT={shlex.quote(campaign_root)}
ATTEMPT={attempt}

# The trap writes a terminal event for any exit path (clean, scancel, OOM,
# wall-time signal handler). SLURM sends SIGTERM ~`KillWait` before SIGKILL,
# so this fires in nearly all failure modes. --root is the group-level option;
# --campaign / --run-id / etc. are the record-exit subcommand options.
record_exit() {{
    local code="$1"
    python -m {msc_module} hpc \
        --root "$CAMPAIGN_ROOT" \
        record-exit \
        --campaign "$CAMPAIGN_ID" \
        --run-id "$RUN_ID" \
        --attempt "$ATTEMPT" \
        --exit-code "$code" \
        --slurm-job-id "${{SLURM_JOB_ID:-0}}" || true
}}
trap 'record_exit $?' EXIT

# USR1 lands ~5 min before wall time when --signal=B:USR1@300 is set; treat
# as a hint to checkpoint and bail cleanly so the heartbeat can resubmit.
on_usr1() {{
    echo "[wrapper] Received USR1: wall time approaching, exiting gracefully"
    record_exit 124
    exit 124
}}
trap on_usr1 USR1

python -m consortium.runner {args_quoted}
"""
    script_path.write_text(script)
    os.chmod(script_path, 0o755)
    job_id = _submit(script_path)
    return job_id, script_path


def submit_heartbeat(
    *,
    campaign_id: str,
    campaign_root: str,
    campaign_workspace: str,
    run_id: str,
    repo_dir: str,
    orchestrator_job_id: str,
    attempt: int,
    partition: Optional[str] = None,
    time: Optional[str] = None,
) -> tuple[str, Path]:
    """Generate + submit the heartbeat supervisor sbatch.

    Lightweight job (1 CPU, 2G mem, 12h wall by default). Self-resubmits
    before its wall expires; only the most recent heartbeat instance is
    active at any time.
    """
    cfg = load_cluster_config(
        repo_dir,
        role="orchestrator",  # uses the same partition; just less mem/cpu
        partition=partition,
        time=time or "12:00:00",
        cpus=1,
        mem="2G",
    )
    run_dir = run_dir_for(campaign_workspace, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    script_path = run_dir / f"heartbeat_{attempt}.slurm"
    stdout_log = run_dir / f"heartbeat_stdout_{attempt}.log"
    stderr_log = run_dir / f"heartbeat_stderr_{attempt}.log"

    msc_module = "consortium.cli.main"

    body = _conda_block(cfg)
    script = f"""#!/bin/bash
#SBATCH --job-name=msc_hb_{run_id[:24]}_a{attempt}
#SBATCH --partition={cfg.partition}
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task={cfg.cpus}
#SBATCH --time={cfg.time}
#SBATCH --mem={cfg.mem}
#SBATCH --output={stdout_log}
#SBATCH --error={stderr_log}

{body}
cd {shlex.quote(repo_dir)}

python -m {msc_module} hpc heartbeat \
    --campaign-root {shlex.quote(campaign_root)} \
    --campaign {shlex.quote(campaign_id)} \
    --run-id {shlex.quote(run_id)} \
    --orchestrator-job-id {shlex.quote(orchestrator_job_id)} \
    --attempt {attempt}
"""
    script_path.write_text(script)
    os.chmod(script_path, 0o755)
    job_id = _submit(script_path)
    return job_id, script_path


def is_sbatch_available() -> bool:
    """True if ``sbatch`` is on PATH; used for the auto-detect default."""
    import shutil

    return shutil.which("sbatch") is not None
