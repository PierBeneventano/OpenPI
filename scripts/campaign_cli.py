#!/usr/bin/env python3
"""
Campaign CLI bridge — structured JSON interface for OpenClaw overseer agent.

Every subcommand prints a JSON object to stdout and exits with code 0 on
success, 1 on error.  The OpenClaw agent calls these via its exec tool.

Usage:
    python scripts/campaign_cli.py --campaign campaign_v2.yaml status
    python scripts/campaign_cli.py --campaign campaign_v2.yaml stage-logs theory1 --tail 100
    python scripts/campaign_cli.py --campaign campaign_v2.yaml launch theory1
    python scripts/campaign_cli.py --campaign campaign_v2.yaml budget
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# Ensure consortium is importable from repo root
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from consortium.campaign.spec import load_spec
from consortium.campaign.status import (
    COMPLETED, FAILED, IN_PROGRESS, PENDING, REPAIRING,
    check_stage_artifacts, init_status, is_pid_alive, is_stage_alive,
    read_status, write_status,
)


REPO_ROOT = os.path.dirname(_HERE)


def _json_out(obj: dict, exit_code: int = 0) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return exit_code


def _sync_budget_from_private_ledger(campaign_dir: str, pricing: dict) -> None:
    """Recompute budget_state.json for each stage from the private token ledger.

    The private ledger (api_token_calls.jsonl) records every API call with a
    ``run_token_file`` that indicates which stage workspace the call belongs to.
    This function sums costs per-stage and writes/updates budget_state.json files
    so that CampaignBudgetManager picks up accurate numbers.

    This is necessary because the running process may use old code that lacks the
    BudgetTrackingCallback, so budget_state.json may be stale or absent.
    """
    import yaml

    ledger_path = os.path.join(REPO_ROOT, ".local", "private_token_usage", "api_token_calls.jsonl")
    if not os.path.exists(ledger_path):
        return

    if not pricing:
        config_path = os.path.join(REPO_ROOT, ".llm_config.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                pricing = yaml.safe_load(f).get("budget", {}).get("pricing", {})

    campaign_abs = os.path.abspath(campaign_dir)
    campaign_real = os.path.realpath(campaign_dir)
    # Match against both canonical and symlink-resolved paths
    campaign_match_paths = {campaign_abs, campaign_real}

    # Accumulate costs per stage directory
    stage_costs: dict[str, dict] = {}  # stage_dir -> {"total_usd": float, "by_model": {}}
    # Dedup: with the litellm_callback, the same call may appear multiple times
    # (once from vlm/budgeted_model source, once from litellm_callback source)
    seen_calls: set[str] = set()

    with open(ledger_path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtf = row.get("run_token_file", "")
            if not rtf:
                continue
            # Check both original and resolved paths
            rtf_dir = os.path.dirname(rtf)
            rtf_real = os.path.realpath(rtf_dir)
            matched_base = None
            for cp in campaign_match_paths:
                if cp in rtf or cp in rtf_real:
                    matched_base = cp
                    break
            if matched_base is None:
                continue

            # Dedup by timestamp + model + token counts (litellm_callback + other sources)
            dedup_key = f"{row.get('timestamp', '')}|{row.get('model_id', '')}|{row.get('input_tokens', '')}|{row.get('output_tokens', '')}"
            if dedup_key in seen_calls:
                continue
            seen_calls.add(dedup_key)

            # Identify stage directory from run_token_file path
            # Use realpath for reliable relpath computation
            rel = os.path.relpath(rtf_real, campaign_real)
            stage_dir_name = rel.split(os.sep)[0] if rel != "." else ""
            if not stage_dir_name:
                continue

            model = row.get("model_id", "")
            inp = int(row.get("input_tokens", 0) or 0)
            out = int(row.get("output_tokens", 0) or 0)

            p = pricing.get(model, {})
            cost = (inp / 1000.0) * p.get("input_per_1k", 0) + (out / 1000.0) * p.get("output_per_1k", 0)

            entry = stage_costs.setdefault(stage_dir_name, {"total_usd": 0.0, "by_model": {}})
            entry["total_usd"] += cost
            entry["by_model"][model] = round(entry["by_model"].get(model, 0.0) + cost, 6)

    # Also include any budget_state.json entries written by BudgetManager (new callback path)
    # Take the max of ledger-computed vs existing budget_state to avoid undercounting
    for stage_name, ledger_data in stage_costs.items():
        bs_path = os.path.join(campaign_dir, stage_name, "budget_state.json")
        existing_total = 0.0
        if os.path.exists(bs_path):
            try:
                with open(bs_path) as f:
                    existing = json.load(f)
                existing_total = float(existing.get("total_usd", 0.0))
            except Exception:
                pass

        # Merge: ledger is authoritative for pre-fix calls, budget_state for post-fix calls
        # Use the larger value to avoid undercounting
        merged_total = max(ledger_data["total_usd"], existing_total)
        merged_by_model = dict(ledger_data["by_model"])

        # If budget_state had higher values for some models, keep those
        if os.path.exists(bs_path):
            try:
                with open(bs_path) as f:
                    existing = json.load(f)
                for model_id, model_cost in (existing.get("by_model") or {}).items():
                    if float(model_cost) > merged_by_model.get(model_id, 0.0):
                        merged_by_model[model_id] = round(float(model_cost), 6)
            except Exception:
                pass

        budget_state = {
            "usd_limit": 2000,
            "total_usd": round(merged_total, 6),
            "by_model": merged_by_model,
            "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        stage_dir = os.path.join(campaign_dir, stage_name)
        if os.path.isdir(stage_dir):
            with open(bs_path, "w") as f:
                json.dump(budget_state, f, indent=2)


def _find_stage(spec, stage_id: str):
    for s in spec.stages:
        if s.id == stage_id:
            return s
    return None


def _check_stage_liveness(status, stage_id: str, campaign_dir: str) -> dict:
    """Cross-node liveness check using multiple signals.

    PID checks only work on the same node. When the overseer runs on a
    different node than the stage, we fall back to:
    1. SLURM job ID check (works cross-node)
    2. Log file recency (if log was modified in last 10 min, likely alive)
    3. Workspace activity (recent file modifications)
    """
    import time

    pid = status.stage_pid(stage_id)
    slurm_jid = status.stage_slurm_job_id(stage_id)
    workspace = status.stage_workspace(stage_id) or ""

    result = {
        "pid_alive": None,
        "slurm_alive": None,
        "log_active": None,
        "workspace_active": None,
        "overall": "unknown",
    }

    # 1. SLURM job check (cross-node)
    if slurm_jid:
        from consortium.campaign.status import is_slurm_job_alive
        result["slurm_alive"] = is_slurm_job_alive(slurm_jid)

    # 2. PID check (local node only — may fail cross-node)
    if pid:
        result["pid_alive"] = is_pid_alive(pid)

    # 3. Log file recency
    logs_dir = os.path.join(campaign_dir, "logs")
    if os.path.isdir(logs_dir):
        for fname in os.listdir(logs_dir):
            if stage_id in fname:
                log_path = os.path.join(logs_dir, fname)
                try:
                    mtime = os.path.getmtime(log_path)
                    age_seconds = time.time() - mtime
                    if age_seconds < 600:  # modified in last 10 minutes
                        result["log_active"] = True
                        break
                except OSError:
                    pass
        if result["log_active"] is None:
            result["log_active"] = False

    # 4. Workspace activity (any file modified in last 10 min)
    if workspace and os.path.isdir(workspace):
        try:
            newest = 0
            for root, dirs, files in os.walk(workspace):
                for f in files[:50]:  # sample, don't walk everything
                    try:
                        mt = os.path.getmtime(os.path.join(root, f))
                        newest = max(newest, mt)
                    except OSError:
                        pass
                break  # only top-level + first subdirectory
            if newest > 0:
                result["workspace_active"] = (time.time() - newest) < 600
        except OSError:
            pass

    # Overall assessment
    if result["slurm_alive"] is True:
        result["overall"] = "alive"
    elif result["pid_alive"] is True:
        result["overall"] = "alive"
    elif result["pid_alive"] is False and result["slurm_alive"] is None:
        # PID check failed but no SLURM job — could be cross-node false negative
        if result["log_active"] or result["workspace_active"]:
            result["overall"] = "likely_alive"
        else:
            result["overall"] = "likely_dead"
    elif result["slurm_alive"] is False:
        result["overall"] = "dead"

    return result


# ------------------------------------------------------------------
# Subcommands
# ------------------------------------------------------------------

def cmd_status(args, spec, status, campaign_dir: str) -> int:
    """Full campaign status + budget snapshot."""
    from consortium.campaign.budget_manager import CampaignBudgetManager

    # Sync budget from private ledger before reading
    _sync_budget_from_private_ledger(campaign_dir, pricing={})

    stages = {}
    for stage in spec.stages:
        sid = stage.id
        s_data = status.raw()["stages"].get(sid, {})
        workspace = status.stage_workspace(sid) or ""
        pid = status.stage_pid(sid)
        # Cross-node liveness check
        liveness = _check_stage_liveness(status, sid, campaign_dir) if status.is_in_progress(sid) else None

        # Artifact check
        if workspace and os.path.isdir(workspace):
            all_present, missing = check_stage_artifacts(workspace, stage)
        else:
            all_present, missing = False, stage.success_artifacts.get("required", [])

        stages[sid] = {
            "status": status.stage_status(sid),
            "workspace": workspace,
            "pid": pid,
            "slurm_job_id": status.stage_slurm_job_id(sid),
            "alive": liveness["overall"] if liveness else None,
            "liveness_detail": liveness,
            "artifacts_complete": all_present,
            "missing_artifacts": missing,
            "started_at": s_data.get("started_at"),
            "completed_at": s_data.get("completed_at"),
            "fail_reason": s_data.get("fail_reason"),
            "repair_attempts": status.repair_attempt_count(sid),
        }

    # Budget
    budget_mgr = CampaignBudgetManager(campaign_dir, usd_limit=getattr(spec, 'budget_usd', 2000.0))
    budget = budget_mgr.to_dict()

    return _json_out({
        "campaign_name": spec.name,
        "campaign_dir": campaign_dir,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "is_complete": status.campaign_finished(spec),
        "has_failure": status.campaign_failed(spec),
        "stages": stages,
        "budget": budget,
    })


def cmd_stage_logs(args, spec, status, campaign_dir: str) -> int:
    """Read last N lines of stage stdout/stderr logs."""
    stage_id = args.stage_id
    tail_n = args.tail
    logs_dir = os.path.join(campaign_dir, "logs")

    result = {"stage_id": stage_id, "stdout": None, "stderr": None}

    for suffix, key in [("_stdout.log", "stdout"), ("_stderr.log", "stderr"), (".log", "stdout")]:
        path = os.path.join(logs_dir, f"{stage_id}{suffix}")
        if os.path.exists(path):
            with open(path) as f:
                lines = f.readlines()
            result[key] = "".join(lines[-tail_n:])

    # Also check SLURM logs
    for fname in sorted(os.listdir(logs_dir)) if os.path.isdir(logs_dir) else []:
        if stage_id in fname and fname.endswith(".log") and result["stdout"] is None:
            path = os.path.join(logs_dir, fname)
            with open(path) as f:
                lines = f.readlines()
            result["stdout"] = "".join(lines[-tail_n:])

    return _json_out(result)


def cmd_stage_artifacts(args, spec, status, campaign_dir: str) -> int:
    """Check artifact presence for a stage."""
    stage_id = args.stage_id
    stage = _find_stage(spec, stage_id)
    if not stage:
        return _json_out({"error": f"Unknown stage: {stage_id}"}, 1)

    workspace = status.stage_workspace(stage_id) or ""
    if workspace and os.path.isdir(workspace):
        all_present, missing = check_stage_artifacts(workspace, stage)
    else:
        all_present = False
        missing = stage.success_artifacts.get("required", [])

    return _json_out({
        "stage_id": stage_id,
        "workspace": workspace,
        "all_present": all_present,
        "missing": missing,
        "required": stage.success_artifacts.get("required", []),
        "optional": stage.success_artifacts.get("optional", []),
    })


def cmd_launch(args, spec, status, campaign_dir: str) -> int:
    """Launch a specific stage."""
    stage_id = args.stage_id
    stage = _find_stage(spec, stage_id)
    if not stage:
        return _json_out({"error": f"Unknown stage: {stage_id}"}, 1)

    current = status.stage_status(stage_id)
    if current == IN_PROGRESS:
        return _json_out({"error": f"Stage '{stage_id}' is already in progress"}, 1)
    if current == COMPLETED:
        return _json_out({"error": f"Stage '{stage_id}' already completed. Use set-stage-status to reset."}, 1)

    # Check dependencies
    if not status.all_complete(stage.depends_on):
        incomplete = [d for d in stage.depends_on if not status.is_complete(d)]
        return _json_out({"error": f"Dependencies not met: {incomplete}"}, 1)

    from consortium.campaign.runner import launch_stage, build_stage_workspace

    proc = launch_stage(stage, spec, status, campaign_dir)
    workspace = build_stage_workspace(stage, spec, status)
    status.mark_in_progress(stage_id, workspace, proc.pid)
    write_status(campaign_dir, status)

    return _json_out({
        "stage_id": stage_id,
        "action": "launched",
        "pid": proc.pid,
        "workspace": workspace,
    })


def cmd_repair(args, spec, status, campaign_dir: str) -> int:
    """Trigger repair for a failed stage."""
    stage_id = args.stage_id
    stage = _find_stage(spec, stage_id)
    if not stage:
        return _json_out({"error": f"Unknown stage: {stage_id}"}, 1)

    current = status.stage_status(stage_id)
    if current not in (FAILED, REPAIRING):
        return _json_out({"error": f"Stage '{stage_id}' is {current}, not failed"}, 1)

    from consortium.campaign.repair_agent import attempt_repair

    result = attempt_repair(stage, spec, status, campaign_dir)
    status.add_repair_attempt(
        stage_id,
        success=result.success,
        diagnosis=result.diagnosis,
        actions=result.actions_taken,
        duration=result.duration_seconds,
        error=result.error,
    )
    write_status(campaign_dir, status)

    return _json_out({
        "stage_id": stage_id,
        "success": result.success,
        "diagnosis": result.diagnosis[:500],
        "actions_taken": result.actions_taken[:20],
        "duration_seconds": result.duration_seconds,
        "error": result.error,
    })


def cmd_budget(args, spec, status, campaign_dir: str) -> int:
    """Budget summary with rigor recommendation."""
    from consortium.campaign.budget_manager import CampaignBudgetManager, DEGRADATION_PROFILES

    # Sync budget from private ledger before reading
    _sync_budget_from_private_ledger(campaign_dir, pricing={})
    mgr = CampaignBudgetManager(campaign_dir, usd_limit=getattr(spec, 'budget_usd', 2000.0))
    rigor = mgr.recommended_rigor_level()
    profile = DEGRADATION_PROFILES.get(rigor, {})

    # Count remaining stages
    remaining_stages = sum(1 for s in spec.stages if status.stage_status(s.id) not in (COMPLETED,))

    return _json_out({
        **mgr.to_dict(),
        "rigor_profile": profile,
        "remaining_stages": remaining_stages,
        "per_stage_allocation_usd": round(mgr.allocate_stage("next", remaining_stages), 2),
    })


def cmd_analyze_logs(args, spec, status, campaign_dir: str) -> int:
    """Structured log analysis — extract errors, warnings, patterns."""
    stage_id = args.stage_id
    logs_dir = os.path.join(campaign_dir, "logs")
    tail_n = args.tail

    issues = []
    log_content = ""

    # Find log files
    for suffix in ["_stderr.log", "_stdout.log", ".log"]:
        path = os.path.join(logs_dir, f"{stage_id}{suffix}")
        if os.path.exists(path):
            with open(path) as f:
                lines = f.readlines()
            chunk = lines[-tail_n:]
            log_content += "".join(chunk)

    # Pattern matching for common issues
    patterns = {
        "oom": (r"(OutOfMemoryError|CUDA out of memory|OOM|MemoryError)", "critical"),
        "rate_limit": (r"(rate.?limit|429|Too Many Requests|RateLimitError)", "warning"),
        "timeout": (r"(TimeoutError|timed? ?out|deadline exceeded)", "warning"),
        "cuda_error": (r"(CUDA error|RuntimeError.*CUDA|cudnn|NCCL)", "critical"),
        "import_error": (r"(ImportError|ModuleNotFoundError|No module named)", "error"),
        "file_not_found": (r"(FileNotFoundError|No such file|ENOENT)", "error"),
        "permission": (r"(PermissionError|Permission denied|EACCES)", "error"),
        "api_error": (r"(APIError|APIConnectionError|InternalServerError|ServiceUnavailable)", "warning"),
        "traceback": (r"(Traceback \(most recent call last\))", "error"),
    }

    for name, (pattern, severity) in patterns.items():
        matches = re.findall(pattern, log_content, re.IGNORECASE)
        if matches:
            issues.append({
                "type": name,
                "severity": severity,
                "count": len(matches),
                "sample": matches[0] if matches else "",
            })

    # Count ERROR/WARNING lines
    error_lines = [l for l in log_content.split("\n") if re.search(r'\bERROR\b', l, re.IGNORECASE)]
    warning_lines = [l for l in log_content.split("\n") if re.search(r'\bWARNING\b', l, re.IGNORECASE)]

    return _json_out({
        "stage_id": stage_id,
        "log_lines_analyzed": tail_n,
        "issues": issues,
        "error_count": len(error_lines),
        "warning_count": len(warning_lines),
        "last_errors": error_lines[-5:] if error_lines else [],
        "last_warnings": warning_lines[-3:] if warning_lines else [],
        "summary": "critical" if any(i["severity"] == "critical" for i in issues) else
                   "errors" if any(i["severity"] == "error" for i in issues) else
                   "warnings" if issues else "clean",
    })


def cmd_set_stage_status(args, spec, status, campaign_dir: str) -> int:
    """Manually override a stage's status."""
    stage_id = args.stage_id
    new_status = args.new_status

    if _find_stage(spec, stage_id) is None:
        return _json_out({"error": f"Unknown stage: {stage_id}"}, 1)

    valid = {PENDING, IN_PROGRESS, COMPLETED, FAILED}
    if new_status not in valid:
        return _json_out({"error": f"Invalid status: {new_status}. Valid: {valid}"}, 1)

    old_status = status.stage_status(stage_id)

    if new_status == PENDING:
        status.mark_pending_retry(stage_id)
    elif new_status == COMPLETED:
        status.mark_completed(stage_id)
    elif new_status == FAILED:
        status.mark_failed(stage_id, "Manually set to failed", [])

    write_status(campaign_dir, status)

    return _json_out({
        "stage_id": stage_id,
        "old_status": old_status,
        "new_status": new_status,
    })


def cmd_distill(args, spec, status, campaign_dir: str) -> int:
    """Run memory distillation for a completed stage."""
    stage_id = args.stage_id
    stage = _find_stage(spec, stage_id)
    if not stage:
        return _json_out({"error": f"Unknown stage: {stage_id}"}, 1)

    workspace = status.stage_workspace(stage_id) or ""
    if not workspace or not os.path.isdir(workspace):
        return _json_out({"error": f"No workspace for stage '{stage_id}'"}, 1)

    from consortium.campaign.memory import distill_stage_memory

    summary_path = distill_stage_memory(stage, workspace, campaign_dir)

    return _json_out({
        "stage_id": stage_id,
        "summary_path": str(summary_path),
        "action": "distilled",
    })


def cmd_rewrite_task(args, spec, status, campaign_dir: str) -> int:
    """Append text to a stage's task file."""
    stage_id = args.stage_id
    stage = _find_stage(spec, stage_id)
    if not stage:
        return _json_out({"error": f"Unknown stage: {stage_id}"}, 1)

    if not stage.task_file or not os.path.exists(stage.task_file):
        return _json_out({"error": f"No task file for stage '{stage_id}'"}, 1)

    append_text = args.append

    with open(stage.task_file, "a") as f:
        f.write(f"\n\n--- OVERSEER AMENDMENT ({datetime.now(timezone.utc).isoformat()}) ---\n")
        f.write(append_text + "\n")

    return _json_out({
        "stage_id": stage_id,
        "task_file": stage.task_file,
        "action": "appended",
        "appended_length": len(append_text),
    })


def cmd_launchable(args, spec, status, campaign_dir: str) -> int:
    """List stages that are ready to launch (dependencies met, status pending)."""
    ready = []
    for stage in spec.stages:
        if status.stage_status(stage.id) != PENDING:
            continue
        if status.all_complete(stage.depends_on):
            ready.append({
                "stage_id": stage.id,
                "depends_on": stage.depends_on,
                "args": stage.args,
            })

    return _json_out({
        "launchable_stages": ready,
        "count": len(ready),
    })


def cmd_check_credits(args, spec, status, campaign_dir: str) -> int:
    """Check if the OpenRouter API is accessible (validates credits/auth)."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return _json_out({
            "api_accessible": False,
            "error": "OPENROUTER_API_KEY not set in environment",
        }, 1)

    try:
        import litellm
        # Minimal API call to verify access via OpenRouter
        resp = litellm.completion(
            model="openrouter/anthropic/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            api_key=api_key,
        )
        return _json_out({
            "api_accessible": True,
            "model_used": "openrouter/anthropic/claude-haiku-4-5-20251001",
            "message": "OpenRouter API access verified.",
        })
    except Exception as e:
        err_str = str(e)
        is_credit_issue = "credit" in err_str.lower() or "balance" in err_str.lower()
        return _json_out({
            "api_accessible": False,
            "error": err_str,
            "is_credit_issue": is_credit_issue,
        }, 1)


def cmd_validate_pipeline(args, spec, status, campaign_dir: str) -> int:
    """Validate that all stages use pipeline v2."""
    issues = []
    for stage in spec.stages:
        has_v2 = False
        has_v1 = False
        for i, arg in enumerate(stage.args):
            if arg == "--pipeline-version" and i + 1 < len(stage.args):
                if stage.args[i + 1] == "v2":
                    has_v2 = True
                elif stage.args[i + 1] == "v1":
                    has_v1 = True
        if has_v1:
            issues.append(f"Stage '{stage.id}' explicitly uses deprecated v1 pipeline")
        # Note: omitting --pipeline-version is now fine since default is v2
    return _json_out({
        "valid": len(issues) == 0,
        "issues": issues,
        "note": "Default pipeline is now v2; stages without --pipeline-version will use v2.",
    })


def cmd_init_campaign(args) -> int:
    """Create a new campaign YAML + initialize status from scratch.

    This is the single entrypoint for starting a new campaign with full
    isolation guarantees.
    """
    import shutil
    import yaml

    name = args.name
    task_file = args.task
    budget = args.budget
    venue = args.venue

    # Derive a slug from name for workspace + yaml filename
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    yaml_filename = f"campaign_{slug}.yaml"
    yaml_path = os.path.join(REPO_ROOT, yaml_filename)
    workspace_root = f"results/{slug}"
    workspace_abs = os.path.join(REPO_ROOT, workspace_root)

    # --- Campaign isolation check ---
    if os.path.exists(yaml_path) and not args.force:
        return _json_out({
            "error": f"Campaign YAML already exists: {yaml_filename}. "
                     f"Use --force to overwrite or choose a different --name.",
        }, 1)

    if os.path.exists(workspace_abs) and not args.force:
        return _json_out({
            "error": f"Workspace directory already exists: {workspace_root}. "
                     f"Use --force to overwrite or choose a different --name.",
        }, 1)

    # Check for collision with other campaigns' workspace_root
    existing_yamls = [
        f for f in os.listdir(REPO_ROOT)
        if f.startswith("campaign_") and f.endswith(".yaml")
        and not f.endswith("_DEPRECATED.yaml")
    ]
    for yf in existing_yamls:
        if yf == yaml_filename:
            continue
        try:
            with open(os.path.join(REPO_ROOT, yf)) as fh:
                existing_raw = yaml.safe_load(fh)
            existing_ws = existing_raw.get("workspace_root", "")
            if existing_ws == workspace_root:
                return _json_out({
                    "error": f"workspace_root '{workspace_root}' collides with "
                             f"existing campaign '{yf}'. Choose a different --name.",
                }, 1)
        except Exception:
            pass

    # Validate task file exists
    task_path = task_file
    if not os.path.isabs(task_path):
        task_path = os.path.join(REPO_ROOT, task_path)
    if not os.path.exists(task_path):
        return _json_out({"error": f"Task file not found: {task_file}"}, 1)

    # --- Build the campaign YAML ---
    campaign_config = {
        "name": name,
        "workspace_root": workspace_root,
        "heartbeat_interval_minutes": 15,
        "max_idle_ticks": 6,
        "max_campaign_hours": 96,
        "counsel_model_timeout_seconds": 3600,
        "planning": {
            "enabled": True,
            "base_task_file": task_file,
            "max_stages": 6,
            "max_parallel": 2,
            "human_review": not args.auto_approve,
            "planning_budget_usd": 5.0,
            "planning_timeout_seconds": 600,
        },
        "stages": [],
        "repair": {
            "enabled": True,
            "max_attempts": 2,
            "launcher": "slurm",
            "claude_binary": "auto",
            "model": "claude-opus-4-6",
            "effort": "max",
            "budget_usd": 10.0,
            "timeout_seconds": 600,
            "retry_delay_seconds": 10,
            "allowed_actions": [
                "edit_code",
                "fix_config",
                "generate_missing_artifacts",
                "install_dependencies",
            ],
            "two_phase": True,
            "plan_model": "claude-opus-4-6",
            "plan_effort": "max",
            "plan_budget_usd": 5.0,
            "plan_timeout_seconds": 300,
            "review_model": "claude-opus-4-6",
            "review_temperature": 0.2,
            "min_review_score": 7,
            "backoff_base_seconds": 60,
            "backoff_max_seconds": 900,
            "repairing_timeout_seconds": 3600,
            "max_review_failures": 3,
            "escalation_timeout_minutes": 60,
            "auto_retry_on_timeout": True,
        },
        "notification": {
            "ntfy_topic": "OpenClaw-Engaging",
            "telegram_bot_token": "${TELEGRAM_BOT_TOKEN}",
            "telegram_chat_id": "${TELEGRAM_CHAT_ID}",
            "on_stage_complete": True,
            "on_failure": True,
            "on_heartbeat": True,
        },
    }

    # Add venue as a comment/metadata field if specified
    if venue:
        campaign_config["target_venue"] = venue

    # Write YAML
    with open(yaml_path, "w") as fh:
        fh.write(f"# {name}\n")
        fh.write(f"# Created: {datetime.now(timezone.utc).isoformat()}\n")
        fh.write(f"# Task: {task_file}\n")
        if venue:
            fh.write(f"# Target venue: {venue}\n")
        fh.write(f"# Init: python scripts/campaign_heartbeat.py --campaign {yaml_filename} --init\n")
        fh.write(f"# Run:  python scripts/campaign_heartbeat.py --campaign {yaml_filename}\n\n")
        yaml.dump(campaign_config, fh, default_flow_style=False, sort_keys=False)

    # --- Initialize status ---
    spec = load_spec(yaml_path)
    campaign_dir = workspace_abs
    os.makedirs(campaign_dir, exist_ok=True)
    os.makedirs(os.path.join(campaign_dir, "logs"), exist_ok=True)
    status = init_status(campaign_dir, spec, yaml_path)

    # --- Provider health check (lightweight) ---
    health_results = {}
    try:
        import litellm
        for model_name in ["claude-opus-4-6", "gpt-5.4", "gemini/gemini-3-pro-preview"]:
            try:
                resp = litellm.completion(
                    model=model_name,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                    timeout=15,
                )
                health_results[model_name] = "ok"
            except Exception as e:
                health_results[model_name] = f"error: {type(e).__name__}: {str(e)[:100]}"
    except ImportError:
        health_results["litellm"] = "not installed"

    return _json_out({
        "action": "init-campaign",
        "yaml_file": yaml_filename,
        "workspace_root": workspace_root,
        "campaign_name": name,
        "task_file": task_file,
        "target_venue": venue or "not set",
        "auto_approve_plan": args.auto_approve,
        "budget_usd": budget,
        "status_initialized": True,
        "provider_health": health_results,
        "next_steps": [
            f"Review the generated YAML: {yaml_filename}",
            f"Launch: python scripts/campaign_cli.py --campaign {yaml_filename} launch discovery_plan",
            f"Or let heartbeat auto-launch: python scripts/campaign_heartbeat.py --campaign {yaml_filename}",
        ],
    })


def cmd_approve_plan(args, spec, status, campaign_dir: str) -> int:
    """Approve the dynamic campaign plan for execution."""
    approval_path = os.path.join(campaign_dir, "plan_approval.json")
    if os.path.exists(approval_path):
        with open(approval_path) as f:
            existing = json.load(f)
        return _json_out({
            "error": f"Plan already has action: {existing.get('action')}",
            "approval_path": approval_path,
        }, 1)

    # Check that a plan exists
    plan_review_path = os.path.join(campaign_dir, "campaign_plan_review.md")
    planning_counsel_ws = status.stage_workspace("planning_counsel")
    plan_json_path = os.path.join(planning_counsel_ws, "campaign_plan.json") if planning_counsel_ws else ""

    if not plan_json_path or not os.path.exists(plan_json_path):
        return _json_out({"error": "No campaign plan found. Has planning_counsel completed?"}, 1)

    approval = {
        "action": "approve",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(approval_path, "w") as f:
        json.dump(approval, f, indent=2)

    return _json_out({
        "action": "approved",
        "approval_path": approval_path,
        "message": "Campaign plan approved. Next heartbeat tick will inject dynamic stages.",
    })


def cmd_reject_plan(args, spec, status, campaign_dir: str) -> int:
    """Reject the dynamic campaign plan."""
    approval_path = os.path.join(campaign_dir, "plan_approval.json")
    rejection = {
        "action": "reject",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(approval_path, "w") as f:
        json.dump(rejection, f, indent=2)

    return _json_out({
        "action": "rejected",
        "approval_path": approval_path,
        "message": "Campaign plan rejected. Campaign will halt at planning stage.",
    })


def cmd_show_plan(args, spec, status, campaign_dir: str) -> int:
    """Show the proposed campaign plan."""
    # Try campaign_plan_review.md first
    review_path = os.path.join(campaign_dir, "campaign_plan_review.md")
    plan_json = None

    planning_counsel_ws = status.stage_workspace("planning_counsel")
    if planning_counsel_ws:
        plan_json_path = os.path.join(planning_counsel_ws, "campaign_plan.json")
        if os.path.exists(plan_json_path):
            with open(plan_json_path) as f:
                plan_json = json.load(f)

    review_md = None
    if os.path.exists(review_path):
        with open(review_path) as f:
            review_md = f.read()

    if not plan_json and not review_md:
        return _json_out({"error": "No campaign plan found. Has planning_counsel completed?"}, 1)

    # Check approval status
    approval_path = os.path.join(campaign_dir, "plan_approval.json")
    approval_status = None
    if os.path.exists(approval_path):
        with open(approval_path) as f:
            approval_status = json.load(f).get("action")

    return _json_out({
        "plan": plan_json,
        "review_markdown": review_md,
        "approval_status": approval_status,
    })


# ------------------------------------------------------------------
# CLI parser
# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Campaign CLI bridge for OpenClaw overseer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--campaign", required=False, help="Path to campaign.yaml")
    parser.add_argument("--campaign-dir", help="Override campaign directory")

    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("status", help="Full campaign status + budget")

    p_logs = subs.add_parser("stage-logs", help="Read stage log tails")
    p_logs.add_argument("stage_id")
    p_logs.add_argument("--tail", type=int, default=100)

    p_art = subs.add_parser("stage-artifacts", help="Check stage artifacts")
    p_art.add_argument("stage_id")

    p_launch = subs.add_parser("launch", help="Launch a stage")
    p_launch.add_argument("stage_id")

    p_repair = subs.add_parser("repair", help="Trigger repair")
    p_repair.add_argument("stage_id")

    subs.add_parser("budget", help="Budget summary")

    p_analyze = subs.add_parser("analyze-logs", help="Structured log analysis")
    p_analyze.add_argument("stage_id")
    p_analyze.add_argument("--tail", type=int, default=200)

    p_set = subs.add_parser("set-stage-status", help="Override stage status")
    p_set.add_argument("stage_id")
    p_set.add_argument("new_status", choices=["pending", "in_progress", "completed", "failed"])

    p_distill = subs.add_parser("distill", help="Run memory distillation")
    p_distill.add_argument("stage_id")

    p_rewrite = subs.add_parser("rewrite-task", help="Append to task file")
    p_rewrite.add_argument("stage_id")
    p_rewrite.add_argument("--append", required=True)

    subs.add_parser("launchable", help="List stages ready to launch")
    subs.add_parser("check-credits", help="Verify API access and credit balance")
    subs.add_parser("validate-pipeline", help="Validate all stages use v2 pipeline")

    subs.add_parser("approve-plan", help="Approve the dynamic campaign plan")
    subs.add_parser("reject-plan", help="Reject the dynamic campaign plan")
    subs.add_parser("show-plan", help="Show the proposed campaign plan")

    p_init = subs.add_parser(
        "init-campaign",
        help="Create a new campaign YAML + initialize workspace (does NOT require --campaign)",
    )
    p_init.add_argument("--name", required=True, help="Campaign name (e.g. 'Muon Regularization v6')")
    p_init.add_argument("--task", required=True, help="Path to research proposal task file")
    p_init.add_argument("--budget", type=float, default=2000.0, help="Total budget in USD")
    p_init.add_argument("--venue", default=None, help="Target venue (neurips, icml, iclr)")
    p_init.add_argument("--auto-approve", action="store_true", help="Skip human plan approval")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing YAML/workspace")

    args = parser.parse_args()

    # init-campaign is special: it creates the YAML, so --campaign is not required.
    if args.command == "init-campaign":
        try:
            return cmd_init_campaign(args)
        except Exception as e:
            return _json_out({"error": str(e), "type": type(e).__name__}, 1)

    if not args.campaign:
        parser.error("--campaign is required for all commands except init-campaign")

    try:
        spec = load_spec(args.campaign)
        campaign_dir = args.campaign_dir or spec.workspace_root
        status = init_status(campaign_dir, spec, args.campaign)

        cmd_map = {
            "status": cmd_status,
            "stage-logs": cmd_stage_logs,
            "stage-artifacts": cmd_stage_artifacts,
            "launch": cmd_launch,
            "repair": cmd_repair,
            "budget": cmd_budget,
            "analyze-logs": cmd_analyze_logs,
            "set-stage-status": cmd_set_stage_status,
            "distill": cmd_distill,
            "rewrite-task": cmd_rewrite_task,
            "launchable": cmd_launchable,
            "check-credits": cmd_check_credits,
            "validate-pipeline": cmd_validate_pipeline,
            "approve-plan": cmd_approve_plan,
            "reject-plan": cmd_reject_plan,
            "show-plan": cmd_show_plan,
        }

        return cmd_map[args.command](args, spec, status, campaign_dir)

    except Exception as e:
        return _json_out({"error": str(e), "type": type(e).__name__}, 1)


if __name__ == "__main__":
    sys.exit(main())
