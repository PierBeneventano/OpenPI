# PoggioAI/MSc

> End-to-end agentic research system, from question to paper.

Built by the [Poggio Lab](https://poggio-lab.mit.edu/) at MIT.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

[Website](https://PoggioAI.github.io) | [Discord](https://discord.gg/Pz7spPPY) | [GitHub](https://github.com/PoggioAI/PoggioAI_MSc)

---

## What Is MSc?

**MSc** (Multi-agent Scientific Collaboration) is a research automation system that turns a research question into a literature-grounded manuscript draft. It orchestrates specialist agents for planning, literature review, theory, experimentation, synthesis, and writing on top of a LangGraph pipeline.

The supported product surface is the installed `msc` CLI. Repo-local helpers and direct scripts still exist for advanced automation, but new users should treat `msc` as the canonical interface.

---

## New User Setup

### Prerequisites

- Python 3.10+
- Git
- An OpenRouter API key
- Optional: LaTeX for PDF-heavy tiers
- Optional: SLURM for HPC workflows

### 1. Clone and install

```bash
git clone https://github.com/PoggioAI/PoggioAI_MSc.git
cd PoggioAI_MSc
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If you do not need dev extras, `python -m pip install -e .` is also supported.

### 2. One-place setup (under 5 minutes)

The fastest path is the dashboard's **Setup** tab. It checks everything (API key, OpenClaude binary, Python, CLI, optional tooling) in a single checklist and lets you fix any red item from the same panel.

1. Open the dashboard: `MSc: Open Dashboard` (or `MSc: Open Setup` to land directly).
2. Paste your OpenRouter key into the **OpenRouter API key** row. It's written to `~/.msc/.env` (mode 600).
3. Click **Install OpenClaude**. The dashboard runs `msc openclaude install`, which `npm install -g @gitlawb/openclaude` into the same environment that ships `msc` itself.
4. When all required rows turn green, close the panel and start a campaign.

Both the terminal and the dashboard share `~/.msc/.env` — there is no separate "VSCode key store" to keep in sync.

**Terminal equivalents** (one source of truth):

```bash
msc setup                       # tier-first interactive wizard (also writes ~/.msc/config.yaml)
msc openclaude install          # idempotent OpenClaude install
msc config keys list            # who's set, and from where
echo "sk-or-..." | msc config keys set OPENROUTER_API_KEY --stdin
msc config keys unset OPENROUTER_API_KEY
```

The setup flow is tier-first. Your default tier defines the default model, budget, output mode, and rigor settings unless you explicitly override them.

### 3. Verify the environment

```bash
msc doctor                      # full environment check
msc project setup-state --json  # same checks the dashboard Setup tab uses
```

`msc doctor` validates Python, package installation, API keys, optional tooling, and campaign/runtime availability. It also shows where each credential came from, such as `shell`, `config-dir`, or `repo-env`.

### 4. Validate a run before spending money

```bash
msc run --tier budget --dry-run "Test task"
```

### 5. Launch a real run

```bash
# Cheapest supported first run
msc run --tier budget "What are the key differences between transformer and state-space models?"

# Standard research run
msc run --tier medium "Survey mechanistic interpretability methods"

# Max-rigor run
msc run --tier ultra "Best possible literature-grounded analysis of attention mechanisms"
```

### 6. Monitor outputs

```bash
msc status
msc logs -f
msc runs
msc budget
```

Each run writes a workspace under `results/` in your current working directory. Important artifacts include:

- `experiment_metadata.json`
- `effective_models.json`
- `run_status.json`
- `budget_state.json`
- `logs/`
- final paper artifacts when the run completes

---

## Supported Working Directories

Once installed, `msc` supports real runs from outside the repo root.

```bash
cd /some/other/workdir
msc run --tier budget "My task"
```

Notes:

- Results are written to the current working directory's `results/`.
- The generated `.llm_config.yaml` is written in the current working directory for that run context.
- Repo-local example files such as `examples/quickstart/task.txt` still need either the repo root as the current directory or an absolute path.

---

## Credential Precedence

For normal installed CLI usage, MSc resolves credentials in this order:

1. Existing shell environment variables
2. `--config-dir` / `~/.msc/.env`
3. Repo-root `.env` only when you launch from the source checkout root or explicitly opt in

Examples:

```bash
# Force repo-root .env to participate even outside the checkout root
CONSORTIUM_USE_REPO_ENV=1 msc doctor

# Force installed CLI flows to ignore repo-root .env
CONSORTIUM_USE_REPO_ENV=0 msc doctor
```

For new-user setups, the recommended path is to keep credentials in `~/.msc/.env` via `msc setup`.

---

## Tier Contract

Tiers are the primary control surface. They now define the effective runtime model policy, not just the top-level model string.

| Tier | Budget Cap | Runtime Model Contract | Key Features |
|---|---:|---|---|
| `budget` | $35 | `gpt-5-mini` across main, persona council, summaries, and experiment-tool helpers | Markdown, no counsel |
| `light` | $75 | `gpt-5-mini` family, planning enabled | Cheap exploratory runs |
| `medium` | $200 | `claude-sonnet-4-6` primary with sonnet/economy per-agent routing | LaTeX, math agents |
| `pro` | $400 | Opus primary, controlled mixed-model counsel | Adversarial verification |
| `max` | $750 | Frontier mixed-model stack from tier policy | Tree search, deeper counsel |
| `ultra` | $2000 | Best-research-first frontier stack from tier policy | Deep tree search, persona council, ensemble review |

Important details:

- `effective_models.json` records the actual resolved model surfaces for every run.
- `experiment_metadata.json` records model provenance so you can tell whether a setting came from the tier, config, or an explicit CLI override.
- Tier presets are generated conservatively so the emitted runtime config stays within provider-safe limits by default.

You can still override pieces of a tier explicitly:

```bash
msc run --tier medium --model claude-opus-4-6 "My task"
msc run --tier budget --counsel "My task"
msc run --tier medium --no-math "My task"
```

---

## Configuration

All user CLI defaults live in `~/.msc/config.yaml`.

```bash
msc config get tier
msc config set tier ultra
msc config set budget_usd 100
msc config set output_format latex
msc config set enable_counsel true
```

Tier selection remains the recommended default. Only set individual knobs when you intentionally want to diverge from the tier contract.

---

## Quickstart Example

The maintained first-run example lives in [examples/quickstart/README.md](/home/mabdel03/orcd/scratch/AI_Researcher/MSc_Internal_audit_20260409_134959/MSc_Internal/examples/quickstart/README.md).

From the repo root:

```bash
msc run --tier budget --task-file examples/quickstart/task.txt
```

From outside the repo root:

```bash
msc run --tier budget --task-file /absolute/path/to/examples/quickstart/task.txt
```

---

## Campaigns

Campaigns orchestrate multi-stage research projects.

```bash
msc campaign init --name "my_project" --task "Investigate normalization layers in transformer training" --budget 150
msc campaign start my_project_campaign.yaml
msc campaign status my_project_campaign.yaml
```

Installed `msc campaign ...` commands are supported from outside the repo root once the package is installed from the checkout.

See [CAMPAIGN_QUICKSTART.md](/home/mabdel03/orcd/scratch/AI_Researcher/MSc_Internal_audit_20260409_134959/MSc_Internal/CAMPAIGN_QUICKSTART.md) for the full campaign workflow.

---

## HPC / SLURM

For cluster-backed usage, start with [docs/engaging_setup.md](/home/mabdel03/orcd/scratch/AI_Researcher/MSc_Internal_audit_20260409_134959/MSc_Internal/docs/engaging_setup.md).

Recommended cluster flow:

```bash
msc setup
msc doctor
msc run --mode hpc --tier budget --dry-run "Test task"
```

Use direct scripts only when you intentionally need scheduler-native automation around this checkout.

---

## OpenClaw

OpenClaw provides optional autonomous campaign oversight.

```bash
msc openclaw setup
msc openclaw start
msc openclaw status
```

It is most useful for long-running SLURM or campaign workflows.
