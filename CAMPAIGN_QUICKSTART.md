# Campaign Quick Start

This guide shows the supported way to go from a research idea to an autonomous MSc campaign.

## Prerequisites

Run the dashboard's Setup tab (or `msc setup` + `msc openclaude install`) first — see the [README "One-place setup" section](README.md#2-one-place-setup-under-5-minutes). When that page reports all required rows green, you have:

- `python -m pip install -e ".[dev]"` completed in this checkout
- OpenRouter key in `~/.msc/.env` (or shell / `--config-dir` / repo `.env`)
- OpenClaude binary on PATH (for the chat steering UI)
- `msc doctor` passing
- Optional: LaTeX, SLURM, OpenClaw, and notification credentials

## Canonical Workflow: `msc campaign`

`msc` is the public interface. Installed campaign commands work from outside the repo root once the package has been installed from this checkout.

### 1. Generate a campaign

```bash
msc campaign init \
  --name "my_project" \
  --task "Investigate the role of normalization layers in transformer training dynamics" \
  --budget 150
```

This creates:

- `my_project_campaign.yaml`
- `automation_tasks/generated/my_project_discovery_task.txt`
- a campaign workspace rooted under `results/` in the directory where you launch stages

### 2. Review the generated files

Important fields:

```yaml
name: "My Project"
workspace_root: "results/my_project"
budget_usd: 150

planning:
  enabled: true
  base_task_file: automation_tasks/generated/my_project_discovery_task.txt
  max_stages: 6
  human_review: false
```

Edit the task file or YAML before launch if needed.

### 3. Start the campaign

```bash
msc campaign start my_project_campaign.yaml
```

Each stage run writes normal MSc artifacts such as:

- `effective_models.json`
- `experiment_metadata.json`
- `run_status.json`
- `budget_state.json`
- `logs/`

### 4. Monitor it

```bash
msc campaign status my_project_campaign.yaml
msc status
msc logs -f
```

## Credential Resolution

For normal installed CLI usage, credential precedence is:

1. shell environment variables
2. `--config-dir` or `~/.msc/.env`
3. repo-root `.env` only when launched from the checkout root or when explicitly enabled

If you suspect a repo-local `.env` is affecting behavior:

```bash
CONSORTIUM_USE_REPO_ENV=0 msc doctor
```

## Supported Working Directories

- Generated campaign YAML files can be started from whatever directory you created them in.
- Installed `msc campaign ...` commands do not require the current working directory to be the repo root.
- Campaign results are written under the current working directory unless the campaign YAML points elsewhere with `workspace_root`.
- Curated example files under `examples/quickstart/` still require either repo-root cwd or absolute paths.

Maintained example files:

- [campaign.yaml](/home/mabdel03/orcd/scratch/AI_Researcher/MSc_Internal_audit_20260409_134959/MSc_Internal/examples/quickstart/campaign.yaml)
- [task.txt](/home/mabdel03/orcd/scratch/AI_Researcher/MSc_Internal_audit_20260409_134959/MSc_Internal/examples/quickstart/task.txt)

## Budgeting

- Campaign-wide budget lives in the top-level `budget_usd` field of the campaign YAML.
- Per-run effective model policy comes from the selected run tier.
- Each run records `effective_models.json` and `experiment_metadata.json`, so you can inspect what model surfaces were actually enabled.
- `budget` campaigns stay on the budget-tier policy unless you explicitly override a model surface.

## Advanced / Direct Scripts

Use direct scripts only for advanced automation tied to this checkout:

```bash
python scripts/campaign_heartbeat.py --campaign my_project_campaign.yaml --validate
python scripts/campaign_cli.py --campaign my_project_campaign.yaml status
```

Direct scripts are useful for cron, heartbeat loops, and scheduler wrappers. They are not the recommended first-time-user path.

## Troubleshooting

| Problem | Fix |
|---|---|
| `msc campaign start` cannot authenticate | Run `msc doctor` and verify OpenRouter is coming from the expected source |
| Campaign spec will not load | Regenerate with `msc campaign init` or fix `planning.base_task_file` |
| Campaign spend is too high | Lower `budget_usd`, choose a cheaper tier for constituent runs, or reduce planning/repair scope |
| Expected paper artifacts are missing | Check `msc status`, then inspect `logs/`, `run_status.json`, and any partial files under `paper_workspace/` |
| HPC stages never launch | Verify SLURM, `engaging_config.yaml`, and any scheduler wrappers |

For strict paper campaigns, inspect failures in this order:

1. stage stderr logs
2. canonical artifacts under `paper_workspace/`
3. `effective_models.json` and `experiment_metadata.json`
4. campaign status output
