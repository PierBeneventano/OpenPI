"""``msc hpc`` — SLURM-aware lifecycle management for detached campaign runs.

This is the CLI surface that backs:

  - the new SLURM-default behaviour of ``msc run`` (which delegates to ``hpc submit``)
  - the supervising heartbeat job that polls squeue + auto-resubmits the orchestrator
  - the trap that fires at the end of the orchestrator's sbatch script
  - reconciliation calls made by ``msc campaigns workspace`` and the VS Code extension
  - the file-based steering channel used when the orchestrator is on a compute node

Everything here is plain Click + the public ``msc_sdk`` surface. There are no
new daemons; everything is either a one-shot CLI call or a SLURM job.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import click

from msc_sdk.campaign_store import CampaignStore


# ----- small helpers ------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _resolve_repo_dir() -> str:
    """Find the repo root containing msc_sdk + engaging_config.yaml."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "msc_sdk").is_dir() and (candidate / "consortium").is_dir():
            return str(candidate)
    return os.getcwd()


def _campaign_workspace(store: CampaignStore, campaign_ref: str) -> str:
    """Return absolute workspace_root for a campaign."""
    info = store.inspect_dict(campaign_ref)
    ws = info.get("workspace_root") or info.get("path")
    if not ws:
        raise click.ClickException(f"Campaign {campaign_ref!r} has no workspace_root.")
    return str(ws)


def _run_dir(workspace: str, run_id: str) -> Path:
    return Path(workspace) / "runs" / run_id


def _steering_paths(workspace: str, run_id: str) -> tuple[str, str]:
    base = Path(workspace) / "steering" / run_id
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "inbox.jsonl"), str(base / "outbox.jsonl")


def _job_ids_path(workspace: str, run_id: str) -> Path:
    return _run_dir(workspace, run_id) / "job_ids.json"


def _read_job_ids(workspace: str, run_id: str) -> dict[str, Any]:
    path = _job_ids_path(workspace, run_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_job_ids(workspace: str, run_id: str, record: dict[str, Any]) -> None:
    path = _job_ids_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _unknown_runner_flags(argv: list[str]) -> set[str]:
    """Return the subset of ``--flag`` tokens in ``argv`` that the runner
    won't recognize.

    Walks the argv looking for tokens that start with ``--``; for each, asks
    ``consortium.args.known_runner_flags()`` whether the runner's argparse
    spec accepts it. Skips the first three tokens (``python -m consortium.runner``)
    and ignores bare ``--`` separators. Quoted values that happen to start
    with ``--`` are not flagged because by the time argv has been split they
    appear as values to a preceding ``--flag`` token, not as flags themselves.
    """
    try:
        from consortium.args import known_runner_flags
    except Exception:
        return set()  # if we can't load the parser, don't block submission
    known = known_runner_flags()
    skip_runner_prefix = 0
    for i, tok in enumerate(argv[:3]):
        if tok in ("python", "python3") or tok == "-m" or tok.endswith("consortium.runner"):
            skip_runner_prefix = i + 1
    unknown: set[str] = set()
    i = skip_runner_prefix
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            continue
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name not in known:
                unknown.add(name)
            # Skip the value: if `--flag value`, the next token is the value
            # unless it's another flag (argparse positional or store_true case).
            if "=" not in tok and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                i += 2
                continue
        i += 1
    return unknown


def _drop_flags_from_argv(argv: list[str], drop: set[str]) -> list[str]:
    """Return ``argv`` with ``--flag [value]`` pairs removed when the flag is in ``drop``.

    Used by the heartbeat's resubmit path to scrub stale/unknown flags out of
    a stored argv before re-submitting an orchestrator. Mirrors the value-skip
    heuristic used by ``_unknown_runner_flags``.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in drop:
                # If there's an inline value (`--flag=value`) we drop just this token.
                # Otherwise we also drop the next token if it doesn't look like a flag.
                if "=" not in tok and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                    i += 2
                    continue
                i += 1
                continue
        out.append(tok)
        i += 1
    return out


def _squeue_state(job_id: str) -> Optional[str]:
    """Return SLURM state string for a job, or ``None`` if not in the queue.

    Uses ``--noheader --format=%T`` for a deterministic single token. Returns
    ``None`` both when the job legitimately finished (squeue forgets jobs after
    ~5 min by default) and when squeue itself fails — callers must treat
    ``None`` as 'gone, check the trap event' rather than 'definitely dead'.
    """
    try:
        result = subprocess.run(
            ["squeue", "-j", str(job_id), "--noheader", "--format=%T"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    return out or None


def _campaign_events(store: CampaignStore, campaign_ref: str, *, run_id: Optional[str] = None) -> list[dict[str, Any]]:
    """All events for a campaign, optionally filtered to a run_id."""
    campaign_id = store.resolve_ref(campaign_ref)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT id, type, actor, created_at, payload_json FROM campaign_events "
            "WHERE campaign_id=? ORDER BY created_at, id",
            (campaign_id,),
        ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if run_id and str(payload.get("run_id") or "") != str(run_id):
            continue
        events.append({
            "id": row["id"], "type": row["type"], "actor": row["actor"],
            "created_at": row["created_at"], "payload": payload,
        })
    return events


def _last_terminal_event(events: list[dict[str, Any]], run_id: str) -> Optional[dict[str, Any]]:
    """Latest RunExited / RunTruncated / RunCancelled / RunFailed for this run."""
    terminal_types = {"RunExited", "RunTruncated", "RunCancelled", "RunFailed"}
    for event in reversed(events):
        if event["type"] in terminal_types and str(event["payload"].get("run_id") or "") == str(run_id):
            return event
    return None


# ----- click group --------------------------------------------------------------


@click.group()
@click.option("--root", "--campaign-root", "root", type=click.Path(), default=".",
              help="Directory used to resolve campaign refs (contains .msc/campaigns.db).")
@click.pass_context
def hpc(ctx: click.Context, root: str) -> None:
    """Detached campaign execution on SLURM (submit, supervise, steer)."""
    ctx.obj = ctx.obj or {}
    ctx.obj["campaign_root"] = root


# ----- submit -------------------------------------------------------------------


@hpc.command("submit")
@click.argument("campaign")
@click.option("--task", "task", default=None, help="Research task (forwarded to consortium.runner).")
@click.option("--task-file", type=click.Path(exists=True), default=None,
              help="Read task from a file.")
@click.option("--tier", default=None, help="Model tier for this run.")
@click.option("--budget", type=float, default=None, help="USD budget cap for this run.")
@click.option("--output-format", default=None, type=click.Choice(["markdown", "latex"]))
@click.option("--max-run-seconds", type=int, default=None)
@click.option("--partition", default=None, help="SLURM partition override.")
@click.option("--wall", default=None, help="SLURM wall time (e.g. '2-00:00:00').")
@click.option("--cpus", type=int, default=None, help="cpus-per-task for the orchestrator.")
@click.option("--mem", default=None, help="Memory request (e.g. '32G').")
@click.option("--extra-runner-arg", "extra_runner_args", multiple=True,
              help="Extra argv to forward verbatim to consortium.runner (repeatable).")
@click.option("--actor", default="user", help="Actor recorded with the RunSubmitted event.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_submit(
    ctx: click.Context,
    campaign: str,
    task: Optional[str],
    task_file: Optional[str],
    tier: Optional[str],
    budget: Optional[float],
    output_format: Optional[str],
    max_run_seconds: Optional[int],
    partition: Optional[str],
    wall: Optional[str],
    cpus: Optional[int],
    mem: Optional[str],
    extra_runner_args: tuple[str, ...],
    actor: str,
    as_json: bool,
) -> None:
    """Submit a campaign run as a SLURM batch job + supervising heartbeat."""
    from consortium.cli.core.slurm_submit import (
        is_sbatch_available, submit_orchestrator, submit_heartbeat,
    )

    if not is_sbatch_available():
        raise click.ClickException(
            "sbatch is not available on PATH; run `msc run --foreground` to execute locally."
        )

    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    repo_dir = _resolve_repo_dir()

    if task_file and not task:
        task = Path(task_file).read_text()

    # Step 1: record the *logical* run in the campaign store. The orchestrator's
    # later RunAttemptStarted event will refer back to the same run_id.
    # `--tier` is a `msc run`-level Click flag, NOT a `consortium.runner` flag.
    # The tier was baked into `.llm_config.yaml` at campaign creation time;
    # forwarding it to the runner makes argparse die with "unrecognized
    # arguments". We keep the CLI option here for forward-compat but don't
    # propagate it.
    runner_argv: list[str] = ["python", "-m", "consortium.runner"]
    if task:
        runner_argv += ["--task", task]
    if budget is not None:
        runner_argv += ["--budget", str(budget)]
    if output_format:
        runner_argv += ["--output-format", output_format]
    if max_run_seconds is not None:
        runner_argv += ["--max-run-seconds", str(max_run_seconds)]
    runner_argv += list(extra_runner_args)

    # Validate the argv against the runner's argparse spec *before* sbatch
    # so the user gets an immediate error if a stray flag slipped through.
    # This caught the muon-test-v4 incident where --tier (a msc run flag,
    # not a runner flag) silently propagated and killed the orchestrator at
    # startup after 30s of compute.
    _unknown = _unknown_runner_flags(runner_argv)
    if _unknown:
        raise click.ClickException(
            "Refusing to submit: the following arguments are not accepted by "
            "`consortium.runner` and would crash the orchestrator at startup: "
            + ", ".join(sorted(_unknown))
            + ". Drop them or update consortium/args.py if they should be valid."
        )

    record = store.record_run_started(
        campaign_id,
        command={"argv": runner_argv, "submission": "hpc"},
        pid=None,
        metadata={
            "workspace_dir": workspace,
            "submission": {"partition": partition, "wall": wall, "cpus": cpus, "mem": mem},
        },
    )
    run_id = record["run_id"]

    inbox_path, outbox_path = _steering_paths(workspace, run_id)
    Path(inbox_path).touch(exist_ok=True)
    Path(outbox_path).touch(exist_ok=True)

    # Args to forward to consortium.runner. We deliberately leave --campaign-id /
    # --campaign-root / --resume-run-id / steering paths off this list since
    # submit_orchestrator appends them itself.
    # Same constraint as above: don't forward --tier (it's a msc-run-level
    # flag the runner doesn't recognize). Only pass through known runner argv.
    runner_args_for_sbatch: list[str] = []
    if task:
        runner_args_for_sbatch += ["--task", task]
    if budget is not None:
        runner_args_for_sbatch += ["--budget", str(budget)]
    if output_format:
        runner_args_for_sbatch += ["--output-format", output_format]
    if max_run_seconds is not None:
        runner_args_for_sbatch += ["--max-run-seconds", str(max_run_seconds)]
    runner_args_for_sbatch += list(extra_runner_args)

    # Step 2: sbatch the orchestrator. attempt=1 for the initial submission.
    orchestrator_extra_env = {
        "CONSORTIUM_RUN_ATTEMPT": "1",
        # OPENROUTER_API_KEY etc. ride through naturally — they're already in
        # the user's env at the time of sbatch and SLURM propagates them.
    }
    orch_job_id, orch_script = submit_orchestrator(
        campaign_id=campaign_id,
        campaign_root=str(Path(root).resolve()),
        campaign_workspace=workspace,
        run_id=run_id,
        attempt=1,
        repo_dir=repo_dir,
        runner_args=runner_args_for_sbatch,
        inbox_path=inbox_path,
        outbox_path=outbox_path,
        extra_env=orchestrator_extra_env,
        partition=partition,
        time=wall,
        cpus=cpus,
        mem=mem,
    )

    # Step 3: sbatch the heartbeat supervisor.
    hb_job_id, hb_script = submit_heartbeat(
        campaign_id=campaign_id,
        campaign_root=str(Path(root).resolve()),
        campaign_workspace=workspace,
        run_id=run_id,
        repo_dir=repo_dir,
        orchestrator_job_id=orch_job_id,
        attempt=1,
        partition=partition,
    )

    _write_job_ids(workspace, run_id, {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "orchestrator_job_id": orch_job_id,
        "heartbeat_job_id": hb_job_id,
        "attempt": 1,
        "submitted_at": _now_iso(),
        "scripts": {"orchestrator": str(orch_script), "heartbeat": str(hb_script)},
        "steering": {"inbox": inbox_path, "outbox": outbox_path},
    })

    store.append_event(
        campaign_id,
        "RunSubmitted",
        actor=actor,
        payload={
            "run_id": run_id,
            "orchestrator_job_id": orch_job_id,
            "heartbeat_job_id": hb_job_id,
            "partition": partition,
            "wall": wall,
            "attempt": 1,
        },
    )

    result = {
        "ok": True,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "orchestrator_job_id": orch_job_id,
        "heartbeat_job_id": hb_job_id,
        "orchestrator_script": str(orch_script),
        "heartbeat_script": str(hb_script),
        "steering": {"inbox": inbox_path, "outbox": outbox_path},
    }
    if as_json:
        _emit_json(result)
    else:
        click.echo(
            f"Submitted run {run_id}: orchestrator=job:{orch_job_id} heartbeat=job:{hb_job_id}"
        )
        click.echo(f"  workspace : {workspace}")
        click.echo(f"  inbox     : {inbox_path}")


# ----- record-exit (called from the orchestrator's bash trap) -------------------


@hpc.command("record-exit")
@click.option("--campaign", required=True)
@click.option("--run-id", required=True)
@click.option("--exit-code", type=int, required=True)
@click.option("--attempt", type=int, default=1)
@click.option("--slurm-job-id", default="")
@click.option("--reason", default="")
@click.pass_context
def hpc_record_exit(
    ctx: click.Context, campaign: str, run_id: str, exit_code: int,
    attempt: int, slurm_job_id: str, reason: str,
) -> None:
    """Append the terminal event for an orchestrator attempt.

    Called from the EXIT trap inside the sbatch wrapper. Truncation (exit 124,
    which the wrapper writes when SIGUSR1 lands before wall time) records as
    ``RunTruncated`` so the heartbeat will resubmit. Other non-zero exits
    record as ``RunExited`` with the code and rely on the heartbeat / human to
    decide whether to resubmit.
    """
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    event_type = "RunTruncated" if exit_code == 124 else "RunExited"
    store.append_event(
        campaign_id,
        event_type,
        actor="runner_trap",
        payload={
            "run_id": run_id,
            "attempt": attempt,
            "exit_code": exit_code,
            "slurm_job_id": slurm_job_id or None,
            "reason": reason or None,
        },
    )
    # Also flip the runs + campaigns row so the UI's `status` reflects what
    # actually happened, not the stale 'running' the row was created with.
    # Truncated runs stay 'running' since the heartbeat will resubmit; other
    # non-zero exits mark the run failed and the campaign as
    # human_decision_required.
    if event_type == "RunExited":
        try:
            store.record_run_exited(
                campaign_id, run_id, exit_code=exit_code,
                status=None,  # let the helper infer 'completed' vs 'failed' from exit_code
                metadata={"slurm_job_id": slurm_job_id or None, "trap": True},
            )
        except Exception as exc:
            click.echo(f"warn: could not flip runs row: {exc}", err=True)
    click.echo(f"recorded {event_type} for run {run_id} (exit={exit_code})")


# ----- heartbeat (the supervising SLURM job) ------------------------------------


@hpc.command("heartbeat")
@click.option("--campaign", required=True)
@click.option("--run-id", required=True)
@click.option("--orchestrator-job-id", required=True)
@click.option("--attempt", type=int, default=1)
@click.option("--max-attempts", type=int, default=20,
              help="Maximum orchestrator attempts before giving up.")
@click.option("--poll-interval", type=int, default=30,
              help="Seconds between squeue polls.")
@click.option("--self-resubmit-window", type=int, default=900,
              help="Self-resubmit when this many seconds remain on the heartbeat's own wall.")
@click.pass_context
def hpc_heartbeat(
    ctx: click.Context, campaign: str, run_id: str, orchestrator_job_id: str,
    attempt: int, max_attempts: int, poll_interval: int, self_resubmit_window: int,
) -> None:
    """Long-running supervisor for one detached campaign run.

    Behaviour each tick:
      1. Poll squeue for the orchestrator. Write a status file (mtime is the
         cross-node liveness signal for the UI).
      2. If the orchestrator has disappeared:
           - look at the last terminal event for this run;
           - if it says completed/cancelled, we're done;
           - otherwise resubmit (LangGraph resumes from checkpoint).
      3. If our own wall is close to expiring, sbatch a fresh heartbeat for the
         current attempt and exit, so supervision is uninterrupted.
    """
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    repo_dir = _resolve_repo_dir()
    campaign_root_abs = str(Path(root).resolve())
    run_dir = _run_dir(workspace, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "heartbeat_status.json"

    started_at = _time.time()
    wall_deadline = _heartbeat_wall_deadline(started_at)

    current_orch_job_id = orchestrator_job_id
    current_attempt = attempt
    saw_running_at_least_once = False

    click.echo(f"[heartbeat] run={run_id} attempt={attempt} orch_job={current_orch_job_id}")

    while True:
        now_epoch = _time.time()
        state = _squeue_state(current_orch_job_id)
        _write_heartbeat_status(status_path, {
            "run_id": run_id, "attempt": current_attempt,
            "orchestrator_job_id": current_orch_job_id, "state": state or "absent",
            "ts": _now_iso(),
        })

        if state in {"RUNNING", "COMPLETING"}:
            saw_running_at_least_once = True

        # If the orchestrator is gone and we ever saw it run (or it was already
        # gone on tick 1, which can happen if it failed to start), check the
        # terminal event and decide.
        if state is None and (saw_running_at_least_once or now_epoch - started_at > 60):
            events = _campaign_events(store, campaign_id, run_id=run_id)
            terminal = _last_terminal_event(events, run_id)
            terminal_type = terminal["type"] if terminal else None

            if terminal_type == "RunExited" and (terminal["payload"].get("exit_code") in (0,)):
                click.echo(f"[heartbeat] run {run_id} completed cleanly; exiting.")
                store.append_event(campaign_id, "RunFinished", actor="heartbeat",
                                   payload={"run_id": run_id, "attempts": current_attempt})
                return
            if terminal_type == "RunCancelled":
                click.echo(f"[heartbeat] run {run_id} cancelled by user; exiting.")
                return

            # Truncation or undocumented exit. Resubmit if we still have attempts.
            if current_attempt >= max_attempts:
                click.echo(f"[heartbeat] run {run_id} exhausted {max_attempts} attempts; giving up.")
                store.append_event(campaign_id, "RunFailed", actor="heartbeat",
                                   payload={"run_id": run_id, "reason": "exhausted resubmissions",
                                            "attempts": current_attempt})
                return

            new_attempt = current_attempt + 1
            click.echo(f"[heartbeat] resubmitting orchestrator for run {run_id} (attempt {new_attempt})")
            try:
                new_job_id = _resubmit_orchestrator(
                    store=store, campaign_id=campaign_id, campaign_root=campaign_root_abs,
                    workspace=workspace, run_id=run_id, attempt=new_attempt, repo_dir=repo_dir,
                )
            except Exception as exc:
                click.echo(f"[heartbeat] resubmission failed: {exc}", err=True)
                store.append_event(campaign_id, "RunFailed", actor="heartbeat",
                                   payload={"run_id": run_id, "reason": f"resubmission failed: {exc}",
                                            "attempts": current_attempt})
                return
            store.append_event(campaign_id, "RunResumed", actor="heartbeat", payload={
                "run_id": run_id, "prior_attempt": current_attempt, "attempt": new_attempt,
                "new_slurm_job_id": new_job_id,
            })
            current_orch_job_id = new_job_id
            current_attempt = new_attempt
            saw_running_at_least_once = False
            # update job_ids.json so the extension picks up the new orch job
            existing = _read_job_ids(workspace, run_id)
            existing["orchestrator_job_id"] = new_job_id
            existing["attempt"] = new_attempt
            _write_job_ids(workspace, run_id, existing)

        # Self-resubmit when our own wall is close to ending.
        if wall_deadline is not None and (wall_deadline - now_epoch) < self_resubmit_window:
            click.echo(f"[heartbeat] approaching wall; submitting successor heartbeat.")
            try:
                _resubmit_heartbeat(
                    store=store, campaign_id=campaign_id, campaign_root=campaign_root_abs,
                    workspace=workspace, run_id=run_id, attempt=current_attempt,
                    orchestrator_job_id=current_orch_job_id, repo_dir=repo_dir,
                )
            except Exception as exc:
                click.echo(f"[heartbeat] self-resubmit failed: {exc}", err=True)
                store.append_event(campaign_id, "HeartbeatTruncated", actor="heartbeat",
                                   payload={"run_id": run_id, "reason": f"self-resubmit failed: {exc}"})
            return

        _time.sleep(poll_interval)


def _write_heartbeat_status(path: Path, payload: dict[str, Any]) -> None:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        pass


def _heartbeat_wall_deadline(started_at_epoch: float) -> Optional[float]:
    """Approximate when the *current* SLURM job will hit its wall time.

    We read ``SLURM_JOB_END_TIME`` if SLURM exposed it (some versions do);
    otherwise we conservatively estimate from the heartbeat job's own
    --time directive parsed via ``squeue``. If neither works we return
    None and the loop just runs until its job dies, which is safe — the
    caller's resubmit-on-wall behaviour just won't fire in advance.
    """
    end_time = os.getenv("SLURM_JOB_END_TIME")
    if end_time:
        try:
            return float(end_time)
        except ValueError:
            pass
    job_id = os.getenv("SLURM_JOB_ID")
    if not job_id:
        return None
    try:
        out = subprocess.run(
            ["squeue", "-j", job_id, "--noheader", "--format=%L"],
            capture_output=True, text=True, timeout=8,
        )
        remaining = (out.stdout or "").strip()
        if remaining and remaining != "INVALID":
            # %L is `[DD-]HH:MM:SS`.
            secs = _parse_slurm_duration(remaining)
            if secs is not None:
                return started_at_epoch + secs
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


_DUR_RE = re.compile(r"^(?:(\d+)-)?(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _parse_slurm_duration(value: str) -> Optional[int]:
    match = _DUR_RE.match(value)
    if not match:
        return None
    days = int(match.group(1) or 0)
    hours = int(match.group(2))
    minutes = int(match.group(3))
    seconds = int(match.group(4) or 0)
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _resubmit_orchestrator(
    *, store: CampaignStore, campaign_id: str, campaign_root: str, workspace: str,
    run_id: str, attempt: int, repo_dir: str,
) -> str:
    """Submit a fresh orchestrator job for an existing run. Reuses argv from the runs row."""
    from consortium.cli.core.slurm_submit import submit_orchestrator

    # Pull the original argv off the runs table (set by record_run_started).
    with store.connect() as conn:
        row = conn.execute(
            "SELECT command_json, metadata_json FROM runs WHERE id=? AND campaign_id=?",
            (run_id, campaign_id),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"runs row missing for run_id={run_id}")
    try:
        command = json.loads(row["command_json"] or "{}")
    except json.JSONDecodeError:
        command = {}
    metadata: dict[str, Any] = {}
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        pass
    runner_argv = command.get("argv") or []
    # Strip the leading "python -m consortium.runner" so we forward only the
    # downstream flags. The slurm_submit helper prepends them itself.
    if runner_argv[:3] == ["python", "-m", "consortium.runner"]:
        runner_argv = runner_argv[3:]
    # Defensive: if the stored argv contains a flag the runner doesn't
    # recognize (e.g. because args.py was tightened since the original
    # submission), drop it rather than re-submitting a job that's certain
    # to die at argparse.
    _unknown = _unknown_runner_flags(["consortium.runner", *runner_argv])
    if _unknown:
        runner_argv = _drop_flags_from_argv(runner_argv, _unknown)
    submission = metadata.get("submission") or {}
    inbox_path, outbox_path = _steering_paths(workspace, run_id)
    new_job_id, _ = submit_orchestrator(
        campaign_id=campaign_id,
        campaign_root=campaign_root,
        campaign_workspace=workspace,
        run_id=run_id,
        attempt=attempt,
        repo_dir=repo_dir,
        runner_args=runner_argv,
        inbox_path=inbox_path,
        outbox_path=outbox_path,
        extra_env={"CONSORTIUM_RUN_ATTEMPT": str(attempt)},
        partition=submission.get("partition"),
        time=submission.get("wall"),
        cpus=submission.get("cpus"),
        mem=submission.get("mem"),
    )
    return new_job_id


def _resubmit_heartbeat(
    *, store: CampaignStore, campaign_id: str, campaign_root: str, workspace: str,
    run_id: str, attempt: int, orchestrator_job_id: str, repo_dir: str,
) -> str:
    from consortium.cli.core.slurm_submit import submit_heartbeat
    new_job_id, _ = submit_heartbeat(
        campaign_id=campaign_id,
        campaign_root=campaign_root,
        campaign_workspace=workspace,
        run_id=run_id,
        repo_dir=repo_dir,
        orchestrator_job_id=orchestrator_job_id,
        attempt=attempt,
    )
    job_ids = _read_job_ids(workspace, run_id)
    job_ids["heartbeat_job_id"] = new_job_id
    _write_job_ids(workspace, run_id, job_ids)
    return new_job_id


# ----- reconcile ----------------------------------------------------------------


@hpc.command("reconcile")
@click.argument("campaign")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_reconcile(ctx: click.Context, campaign: str, as_json: bool) -> None:
    """Reconcile any detached runs against squeue.

    Called by the VS Code extension's liveness poll and at the top of
    ``msc campaigns workspace``. Writes ``RunFailed`` events for any
    orchestrator job that has vanished from squeue without leaving a clean
    terminal event behind.
    """
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    runs_dir = Path(workspace) / "runs"
    actions: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        out = {"ok": True, "campaign_id": campaign_id, "actions": []}
        if as_json:
            _emit_json(out)
        return

    events = _campaign_events(store, campaign_id)
    open_runs = _open_runs(store, campaign_id)

    for run_id in sorted(open_runs):
        record = _read_job_ids(workspace, run_id)
        orch_job = record.get("orchestrator_job_id")
        if not orch_job:
            continue
        state = _squeue_state(orch_job)
        if state is not None:
            continue
        terminal = _last_terminal_event(events, run_id)
        if terminal is not None:
            continue
        # Orchestrator gone, no terminal event recorded. Heartbeat was meant
        # to catch this; if it didn't (because it was killed too), record a
        # RunFailed ourselves so the UI stops thinking the run is live.
        event = store.append_event(campaign_id, "RunFailed", actor="reconcile",
                                   payload={"run_id": run_id, "reason": "orchestrator job vanished without exit record"})
        # Also flip the runs row to failed so workspace queries see the right status.
        try:
            store.record_run_exited(campaign_id, run_id, exit_code=None,
                                    status="failed", metadata={"reconciled": True})
        except Exception:
            pass
        actions.append({"run_id": run_id, "action": "marked_failed", "event_id": event["id"]})

    out = {"ok": True, "campaign_id": campaign_id, "actions": actions}
    if as_json:
        _emit_json(out)
    else:
        if actions:
            click.echo(f"reconciled {len(actions)} stale run(s) for {campaign_id}")


def _open_runs(store: CampaignStore, campaign_id: str) -> set[str]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM runs WHERE campaign_id=? AND status NOT IN ('completed','failed','cancelled','dry_run_passed')",
            (campaign_id,),
        ).fetchall()
    return {str(row["id"]) for row in rows}


# ----- steer / cancel / tail ----------------------------------------------------


@hpc.command("steer")
@click.argument("campaign")
@click.option("--run-id", default=None, help="Target run; defaults to the latest open run.")
@click.option("--message", "-m", "message", required=True, help="Instruction text to inject.")
@click.option("--type", "kind", default="m", type=click.Choice(["m", "n"]),
              help="m=instruction (default), n=no further input.")
@click.option("--interrupt/--no-interrupt", default=True,
              help="Also enqueue an 'interrupt' so the runner pauses first.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_steer(
    ctx: click.Context, campaign: str, run_id: Optional[str], message: str,
    kind: str, interrupt: bool, as_json: bool,
) -> None:
    """Append a steering instruction to the run's inbox.jsonl."""
    from consortium.interaction.steering_file import write_inbox_record

    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)

    if not run_id:
        run_id = _latest_open_run_id(store, campaign_id, workspace)
        if not run_id:
            raise click.ClickException("No open SLURM run for this campaign.")

    inbox_path, _ = _steering_paths(workspace, run_id)
    if interrupt:
        write_inbox_record(inbox_path, kind="interrupt")
    instruction_id = write_inbox_record(inbox_path, kind="instruction", text=message, type=kind)
    out = {"ok": True, "run_id": run_id, "instruction_id": instruction_id, "inbox": inbox_path}
    if as_json:
        _emit_json(out)
    else:
        click.echo(f"queued instruction {instruction_id} -> {inbox_path}")


def _latest_open_run_id(store: CampaignStore, campaign_id: str, workspace: str) -> Optional[str]:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE campaign_id=? AND status NOT IN ('completed','failed','cancelled','dry_run_passed') "
            "ORDER BY started_at DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
    return str(row["id"]) if row else None


@hpc.command("cancel")
@click.argument("campaign")
@click.option("--run-id", default=None)
@click.option("--actor", default="user")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_cancel(
    ctx: click.Context, campaign: str, run_id: Optional[str], actor: str, as_json: bool,
) -> None:
    """scancel both jobs for a run; write a RunCancelled event."""
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    if not run_id:
        run_id = _latest_open_run_id(store, campaign_id, workspace)
        if not run_id:
            raise click.ClickException("No open SLURM run for this campaign.")

    record = _read_job_ids(workspace, run_id)
    cancelled: list[str] = []
    for key in ("orchestrator_job_id", "heartbeat_job_id"):
        job_id = record.get(key)
        if not job_id:
            continue
        try:
            subprocess.run(["scancel", str(job_id)], check=False, capture_output=True)
            cancelled.append(str(job_id))
        except FileNotFoundError:
            raise click.ClickException("scancel not on PATH.")

    store.append_event(campaign_id, "RunCancelled", actor=actor,
                       payload={"run_id": run_id, "slurm_job_ids": cancelled})
    try:
        store.record_run_exited(campaign_id, run_id, exit_code=None, status="cancelled",
                                metadata={"cancelled_by": actor})
    except Exception:
        pass

    out = {"ok": True, "run_id": run_id, "cancelled_jobs": cancelled}
    if as_json:
        _emit_json(out)
    else:
        click.echo(f"cancelled jobs {cancelled} for run {run_id}")


@hpc.command("tail")
@click.argument("campaign")
@click.option("--run-id", default=None)
@click.option("--attempt", type=int, default=None, help="Specific attempt; defaults to latest.")
@click.option("--stream", default="stdout", type=click.Choice(["stdout", "stderr"]))
@click.pass_context
def hpc_tail(
    ctx: click.Context, campaign: str, run_id: Optional[str], attempt: Optional[int], stream: str,
) -> None:
    """Stream the orchestrator's stdout/stderr log via ``tail -F``."""
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    if not run_id:
        run_id = _latest_open_run_id(store, campaign_id, workspace) or _latest_any_run_id(store, campaign_id)
        if not run_id:
            raise click.ClickException("No run found for this campaign.")
    run_dir = _run_dir(workspace, run_id)
    if attempt is None:
        # find the highest attempt with a stdout log
        attempts = sorted(
            int(p.stem.split("_")[-1])
            for p in run_dir.glob(f"{stream}_*.log")
            if p.stem.split("_")[-1].isdigit()
        )
        if not attempts:
            raise click.ClickException(f"No {stream} logs in {run_dir}")
        attempt = attempts[-1]
    log_path = run_dir / f"{stream}_{attempt}.log"
    if not log_path.exists():
        raise click.ClickException(f"log not found: {log_path}")
    os.execvp("tail", ["tail", "-F", str(log_path)])


def _latest_any_run_id(store: CampaignStore, campaign_id: str) -> Optional[str]:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE campaign_id=? ORDER BY started_at DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
    return str(row["id"]) if row else None


# ----- status (machine-readable snapshot of a run) ------------------------------


@hpc.command("status")
@click.argument("campaign")
@click.option("--run-id", default=None)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_status(ctx: click.Context, campaign: str, run_id: Optional[str], as_json: bool) -> None:
    """Print a quick health snapshot: job states, last events, steering counts."""
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    if not run_id:
        run_id = _latest_open_run_id(store, campaign_id, workspace) or _latest_any_run_id(store, campaign_id)
        if not run_id:
            raise click.ClickException("No runs for this campaign.")
    record = _read_job_ids(workspace, run_id)
    orch_job = record.get("orchestrator_job_id")
    hb_job = record.get("heartbeat_job_id")
    events = _campaign_events(store, campaign_id, run_id=run_id)
    out = {
        "campaign_id": campaign_id,
        "run_id": run_id,
        "attempt": record.get("attempt"),
        "orchestrator_job_id": orch_job,
        "orchestrator_state": _squeue_state(orch_job) if orch_job else None,
        "heartbeat_job_id": hb_job,
        "heartbeat_state": _squeue_state(hb_job) if hb_job else None,
        "steering_inbox": record.get("steering", {}).get("inbox"),
        "last_event": events[-1] if events else None,
    }
    if as_json:
        _emit_json(out)
    else:
        click.echo(json.dumps(out, indent=2, sort_keys=True, default=str))


# ----- pricing dispositions (pause/resume on uncovered models) -----------------


def _pricing_inbox_path(workspace: str, run_id: str) -> Path:
    return Path(workspace) / "steering" / run_id / "pricing_inbox.jsonl"


@hpc.command("set-pricing-disposition")
@click.argument("campaign")
@click.option("--model", "model_id", required=True, help="Model id the disposition applies to.")
@click.option("--action", required=True,
              type=click.Choice(["use_suggested", "set_rate", "treat_as_zero", "skip_model"]),
              help="Disposition action — see `msc hpc set-pricing-disposition --help`.")
@click.option("--input-per-1k", type=float, default=None,
              help="Required when --action=set_rate: input price per 1k tokens.")
@click.option("--output-per-1k", type=float, default=None,
              help="Required when --action=set_rate: output price per 1k tokens.")
@click.option("--run-id", default=None,
              help="Target run; defaults to the most recently open run.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_set_pricing_disposition(
    ctx: click.Context, campaign: str, model_id: str, action: str,
    input_per_1k: Optional[float], output_per_1k: Optional[float],
    run_id: Optional[str], as_json: bool,
) -> None:
    """Resolve a `PricingUnknown` decision on a paused campaign.

    Appends a JSON record the budget enforcer is polling for; the runner
    resumes within ~2s of the write. The chosen disposition is persisted to
    campaign metadata so the same model won't re-prompt later in the run.
    """
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)
    if not run_id:
        run_id = _latest_open_run_id(store, campaign_id, workspace)
        if not run_id:
            raise click.ClickException("No open run for this campaign.")
    inbox = _pricing_inbox_path(workspace, run_id)
    inbox.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"model_id": model_id, "action": action, "ts": _now_iso()}
    if action == "set_rate":
        if input_per_1k is None or output_per_1k is None:
            raise click.ClickException(
                "--action=set_rate requires --input-per-1k and --output-per-1k."
            )
        record["input_per_1k"] = float(input_per_1k)
        record["output_per_1k"] = float(output_per_1k)
    with inbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    out = {"ok": True, "run_id": run_id, "model_id": model_id, "action": action, "inbox": str(inbox)}
    if as_json:
        _emit_json(out)
    else:
        click.echo(f"queued pricing disposition for {model_id}: {action}")


@hpc.command("set-pricing")
@click.argument("campaign")
@click.option("--model", "model_id", required=True)
@click.option("--input-per-1k", type=float, required=True)
@click.option("--output-per-1k", type=float, required=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_set_pricing(
    ctx: click.Context, campaign: str, model_id: str,
    input_per_1k: float, output_per_1k: float, as_json: bool,
) -> None:
    """Shortcut: convenience wrapper that calls `set-pricing-disposition` with --action=set_rate."""
    ctx.invoke(
        hpc_set_pricing_disposition,
        campaign=campaign, model_id=model_id, action="set_rate",
        input_per_1k=input_per_1k, output_per_1k=output_per_1k,
        run_id=None, as_json=as_json,
    )


# ----- restart-node / restart-campaign ------------------------------------------


def _prior_submit_args(store: CampaignStore, campaign_id: str, run_id: Optional[str]) -> dict[str, Any]:
    """Pull the original runner argv + submission knobs off the latest runs row.

    Used by both restart-node and restart-campaign to reconstruct a fresh
    `hpc submit` invocation that matches whatever the user originally
    configured (task, budget, partition, wall, persona overrides, etc.)
    rather than asking them to re-type everything in the confirmation modal.
    """
    if not run_id:
        run_id = _latest_any_run_id(store, campaign_id)
    if not run_id:
        return {}
    with store.connect() as conn:
        row = conn.execute(
            "SELECT command_json, metadata_json FROM runs WHERE id=? AND campaign_id=?",
            (run_id, campaign_id),
        ).fetchone()
    if row is None:
        return {}
    try:
        command = json.loads(row["command_json"] or "{}")
    except json.JSONDecodeError:
        command = {}
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    submission = metadata.get("submission") or {}
    argv = command.get("argv") or []
    # Strip the leading `python -m consortium.runner` prefix; we only want
    # the downstream flags. submit() will re-prepend the prefix.
    if argv[:3] == ["python", "-m", "consortium.runner"]:
        argv = argv[3:]
    return {
        "argv": argv,
        "partition": submission.get("partition"),
        "wall": submission.get("wall"),
        "cpus": submission.get("cpus"),
        "mem": submission.get("mem"),
    }


def _argv_get(argv: list[str], flag: str) -> Optional[str]:
    """Extract a value following `flag` in argv. Handles `--flag=value` too."""
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(flag + "="):
            return tok.split("=", 1)[1]
    return None


def _argv_extra_runner_args(argv: list[str]) -> list[str]:
    """Return only the runner argv tokens that aren't recognized by `hpc submit`'s
    explicit options — these become `--extra-runner-arg` values on the resubmit."""
    explicit = {"--task", "--tier", "--budget", "--output-format", "--max-run-seconds"}
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in explicit and i + 1 < len(argv):
            i += 2
            continue
        if any(tok.startswith(name + "=") for name in explicit):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _shell_submit(
    *, root: str, campaign_id: str, prior: dict[str, Any],
    additional_extra: list[str] | None = None,
) -> dict[str, Any]:
    """Shell out to `msc hpc submit` with the prior run's args + any additions.

    Returns the parsed JSON the submit prints, or raises ClickException on
    failure. Subprocessing matches the dispatch pattern `msc run` already
    uses to call `hpc submit`.
    """
    argv = prior.get("argv") or []
    task = _argv_get(argv, "--task")
    budget = _argv_get(argv, "--budget")
    output_format = _argv_get(argv, "--output-format")
    max_run_seconds = _argv_get(argv, "--max-run-seconds")
    extra = _argv_extra_runner_args(argv)
    if additional_extra:
        extra = [*extra, *additional_extra]

    cmd = [sys.executable, "-m", "consortium.cli.main", "--no-banner",
           "hpc", "--root", root, "submit", campaign_id, "--json"]
    if task:
        cmd += ["--task", task]
    if budget:
        cmd += ["--budget", str(budget)]
    if output_format:
        cmd += ["--output-format", output_format]
    if max_run_seconds:
        cmd += ["--max-run-seconds", str(max_run_seconds)]
    if prior.get("partition"):
        cmd += ["--partition", str(prior["partition"])]
    if prior.get("wall"):
        cmd += ["--wall", str(prior["wall"])]
    if prior.get("cpus") is not None:
        cmd += ["--cpus", str(prior["cpus"])]
    if prior.get("mem"):
        cmd += ["--mem", str(prior["mem"])]
    for tok in extra:
        cmd += ["--extra-runner-arg", tok]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise click.ClickException(
            f"hpc submit failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"hpc submit did not return JSON: {exc}; stdout={result.stdout!r}")


@hpc.command("restart-node")
@click.argument("campaign")
@click.argument("node_id")
@click.option("--reason", default="user-initiated restart")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_restart_node(
    ctx: click.Context, campaign: str, node_id: str, reason: str, as_json: bool,
) -> None:
    """Restart a single node: cancel any live run, then sbatch with --start-from-stage.

    Reuses the prior run's argv (task, budget, partition, persona overrides,
    etc.) so the user doesn't have to re-type anything. The new orchestrator
    enters via `--start-from-stage <node_id>` which makes LangGraph build a
    fresh thread_id, sidestepping the saved checkpoint at that node.
    """
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)

    # 1. Cancel any live SLURM run for this campaign.
    prior_run_id = _latest_open_run_id(store, campaign_id, workspace)
    prior_cancelled = False
    if prior_run_id:
        ctx.invoke(hpc_cancel, campaign=campaign, run_id=prior_run_id,
                   actor="restart-node", as_json=False)
        prior_cancelled = True
    else:
        prior_run_id = _latest_any_run_id(store, campaign_id)

    # 2. Audit trail: rerun_stage proposal + auto-approve so the event log
    #    shows the user's intent in the canonical form. The proposal is
    #    operationally inert (it doesn't reset state), but it keeps the
    #    decision history consistent with what the Overseer would write.
    from msc_sdk.campaigns import CampaignClient

    client = CampaignClient(root)
    try:
        proposal = client.rerun_stage(campaign_id, node_id, reason=reason)
        approval_id = (proposal.get("approval") or {}).get("id")
        if approval_id:
            client.approve(approval_id, actor="restart-node")
    except Exception as exc:
        # Audit-only; non-fatal.
        click.echo(f"warn: rerun_stage proposal failed (continuing anyway): {exc}", err=True)

    # 3. Resubmit with --start-from-stage appended to the prior argv.
    prior = _prior_submit_args(store, campaign_id, prior_run_id)
    submit_result = _shell_submit(
        root=root, campaign_id=campaign_id, prior=prior,
        additional_extra=["--start-from-stage", node_id],
    )

    # 4. NodeRestartRequested event for the run-history drawer.
    event = store.append_event(
        campaign_id, "NodeRestartRequested", actor="restart-node",
        payload={
            "node_id": node_id, "prior_run_id": prior_run_id,
            "new_run_id": submit_result.get("run_id"), "reason": reason,
        },
    )
    out = {
        "ok": True, "campaign_id": campaign_id, "node_id": node_id,
        "prior_run_id": prior_run_id, "prior_run_cancelled": prior_cancelled,
        "new_run_id": submit_result.get("run_id"),
        "new_orchestrator_job_id": submit_result.get("orchestrator_job_id"),
        "new_heartbeat_job_id": submit_result.get("heartbeat_job_id"),
        "event_id": event["id"],
    }
    if as_json:
        _emit_json(out)
    else:
        click.echo(
            f"restarted node {node_id}: "
            f"prior_run={prior_run_id} cancelled={prior_cancelled} "
            f"-> new_run={out['new_run_id']} orch={out['new_orchestrator_job_id']}"
        )


@hpc.command("restart-campaign")
@click.argument("campaign")
@click.option("--reason", default="user-initiated restart")
@click.option("--archive/--no-archive", default=True,
              help="Archive prior runs/<run_id>/ dirs to runs/archive/ (default: yes).")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_restart_campaign(
    ctx: click.Context, campaign: str, reason: str, archive: bool, as_json: bool,
) -> None:
    """Reset *this* campaign in place: cancel, archive, reset, submit fresh.

    Same campaign id. Same title/objective/budget/persona overrides. The
    prior run directory is archived (so its artifacts remain inspectable
    via the UI's history drawer) and the graph_nodes table is reset to
    'pending' so the next orchestrator runs from the entry node.
    """
    root = ctx.obj["campaign_root"]
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)

    # 1. Cancel any live run.
    prior_run_id = _latest_open_run_id(store, campaign_id, workspace)
    prior_run_ids: list[str] = []
    if prior_run_id:
        ctx.invoke(hpc_cancel, campaign=campaign, run_id=prior_run_id,
                   actor="restart-campaign", as_json=False)
        prior_run_ids.append(prior_run_id)

    # 2. Archive every existing run dir into runs/archive/.
    archived_paths: list[str] = []
    runs_dir = Path(workspace) / "runs"
    archive_dir = runs_dir / "archive"
    if archive and runs_dir.is_dir():
        archive_dir.mkdir(parents=True, exist_ok=True)
        for entry in sorted(runs_dir.iterdir()):
            if not entry.is_dir() or entry.name == "archive":
                continue
            target = archive_dir / entry.name
            if target.exists():
                # already archived previously; skip
                continue
            try:
                entry.rename(target)
                archived_paths.append(str(target))
                if entry.name not in prior_run_ids:
                    prior_run_ids.append(entry.name)
            except OSError as exc:
                click.echo(f"warn: could not archive {entry}: {exc}", err=True)

    # 3. Reset graph_nodes statuses + bump campaign status row.
    reset_info = store.reset_graph_node_statuses(campaign_id)

    # 4. Audit event.
    event = store.append_event(
        campaign_id, "CampaignRestarted", actor="restart-campaign",
        payload={
            "prior_run_ids": prior_run_ids,
            "archived_paths": archived_paths,
            "nodes_reset": reset_info.get("nodes_reset", 0),
            "reason": reason,
        },
    )

    # 5. Submit a fresh run, reusing the prior argv but WITHOUT
    #    --start-from-stage (we want the entry node).
    prior = _prior_submit_args(store, campaign_id, prior_run_ids[0] if prior_run_ids else None)
    submit_result = _shell_submit(root=root, campaign_id=campaign_id, prior=prior)

    out = {
        "ok": True, "campaign_id": campaign_id,
        "prior_run_ids": prior_run_ids, "archived_paths": archived_paths,
        "nodes_reset": reset_info.get("nodes_reset", 0),
        "new_run_id": submit_result.get("run_id"),
        "new_orchestrator_job_id": submit_result.get("orchestrator_job_id"),
        "new_heartbeat_job_id": submit_result.get("heartbeat_job_id"),
        "event_id": event["id"],
    }
    if as_json:
        _emit_json(out)
    else:
        click.echo(
            f"restarted campaign {campaign_id}: "
            f"archived={len(archived_paths)} runs, reset {reset_info.get('nodes_reset', 0)} nodes "
            f"-> new_run={out['new_run_id']}"
        )


# ----- resume-from-deadlock (persona_council human-resolved) --------------------


@hpc.command("resume-from-deadlock")
@click.argument("campaign")
@click.option("--mode", required=True, type=click.Choice(["accept_as_is", "edited"]),
              help="'accept_as_is' uses the latest synthesized draft verbatim; "
                   "'edited' assumes the human has already edited research_proposal.md.")
@click.option("--reason", default="", help="Optional human-supplied reason for the audit log.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def hpc_resume_from_deadlock(
    ctx: click.Context, campaign: str, mode: str, reason: str, as_json: bool,
) -> None:
    """Resolve a persona_council deadlock and resubmit the runner.

    Marks the open ``persona_council_deadlock`` blob with the chosen mode,
    auto-approves the underlying decision, then sbatches a fresh orchestrator
    with ``--start-from-stage persona_council``. The runner's pre-check sees
    the resolution and uses the existing ``research_proposal.md`` (edited or
    not) as the council's output, advancing the graph immediately.
    """
    from msc_sdk.campaigns import CampaignClient

    root = ctx.obj["campaign_root"]
    client = CampaignClient(root)
    store = CampaignStore(root)
    campaign_id = store.resolve_ref(campaign)
    workspace = _campaign_workspace(store, campaign_id)

    # 1. Resolve the blob. Raises if no open deadlock — idempotency guard.
    try:
        resolution = client.resolve_council_deadlock(
            campaign_id, mode=mode, actor="resume-from-deadlock", reason=reason,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))

    # 2. Cancel any orphan run for this campaign, then resubmit with the
    #    same argv + --start-from-stage persona_council. Mirrors restart-node.
    prior_run_id = _latest_open_run_id(store, campaign_id, workspace)
    prior_cancelled = False
    if prior_run_id:
        ctx.invoke(hpc_cancel, campaign=campaign, run_id=prior_run_id,
                   actor="resume-from-deadlock", as_json=False)
        prior_cancelled = True
    else:
        prior_run_id = _latest_any_run_id(store, campaign_id)

    prior = _prior_submit_args(store, campaign_id, prior_run_id)
    submit_result = _shell_submit(
        root=root, campaign_id=campaign_id, prior=prior,
        additional_extra=["--start-from-stage", "persona_council"],
    )

    out = {
        "ok": True, "campaign_id": campaign_id,
        "decision_id": resolution.get("decision_id"),
        "mode": mode,
        "proposal_path": resolution.get("proposal_path"),
        "prior_run_id": prior_run_id,
        "prior_run_cancelled": prior_cancelled,
        "new_run_id": submit_result.get("run_id"),
        "new_orchestrator_job_id": submit_result.get("orchestrator_job_id"),
        "new_heartbeat_job_id": submit_result.get("heartbeat_job_id"),
        "event_id": resolution.get("event_id"),
    }
    if as_json:
        _emit_json(out)
    else:
        click.echo(
            f"resumed deadlocked {campaign_id} (mode={mode}): "
            f"new_run={out['new_run_id']} orch={out['new_orchestrator_job_id']}"
        )
