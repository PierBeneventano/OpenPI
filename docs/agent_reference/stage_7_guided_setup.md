# Stage 7 Guided Setup And Tutorial

This document defines the guided setup and tutorial flow that should hide
Engaging, OpenRouter, OpenClaude, and optional OpenClaw/Telegram nuance behind a
clear product experience.

Status: drafted on 2026-05-10. Pending user review before implementation.

## Decision

Setup should be guided, staged, and honest about what is required versus
optional.

Required setup:

- project/environment readiness
- Python/CLI readiness
- OpenRouter API key configuration
- basic `msc doctor` or product readiness checks
- SDK/CLI JSON command availability once implemented

Optional setup:

- OpenClaude launch/profile integration
- VS Code dashboard readiness
- OpenClaw automation
- Telegram notifications/control
- later Slack integration

OpenClaw must not be enabled by default.

## Rule

The setup flow may configure product-shell infrastructure. It must not change
the research kernel, prompts, graph behavior, model policy, budget semantics, or
artifact completion semantics.

Setup must not overwrite user credentials or existing configuration without
explicit confirmation.

## Goals

- Make first-run onboarding understandable for a user SSHed into Engaging.
- Collect or verify the OpenRouter key once and share it safely with MSc and
  OpenClaude launch flows.
- Explain optional automation without making it feel mandatory.
- Provide clear readiness states that the VS Code dashboard can display.
- Support both CLI-only and VS Code-guided onboarding.
- Produce an auditable setup state without logging raw secrets.

## Non-Goals

- Do not build the Slack setup in v1.
- Do not require OpenClaw or Telegram.
- Do not require OpenClaude to run the base research system.
- Do not expose a public web control plane.
- Do not run a real paid research job as part of setup.

## Setup Phases

### Phase 1: Environment Detection

Detect:

- project root
- installed `msc` command
- editable install versus package install
- Python version and virtual environment
- results directory
- writable config directory
- Engaging/SLURM availability
- LaTeX/PDF tool availability
- `rg` and other useful local tools where relevant

Output:

- readiness status
- warnings
- suggested next action
- no mutations

### Phase 2: OpenRouter Key

Required for real runs.

Flow:

1. Check whether `OPENROUTER_API_KEY` is available through supported precedence.
2. If missing, ask user for key.
3. Store in the supported user-private credential location.
4. Confirm without printing the key.
5. Run a lightweight readiness check only if the user approves any network/API
   call that might touch provider infrastructure.

Credential precedence should remain understandable:

1. shell environment
2. `--config-dir` or `~/.msc/.env`
3. repo-root `.env` only when intentionally participating

The setup UI should explain which source is active.

### Phase 3: MSc Readiness

Run or expose:

- `msc doctor`
- `msc project readiness --json` once Stage 3 exists
- dry-run launch validation where supported
- campaign YAML validation where a campaign is selected

Setup should not start a paid run by default.

### Phase 4: OpenClaude Readiness

Optional.

Flow:

1. Detect OpenClaude availability.
2. Verify whether it can be launched with MSc-provided OpenRouter environment.
3. Do not duplicate the key into OpenClaude profile files unless necessary and
   explicitly approved.
4. Install or enable the MSc skill/playbook when available.
5. Run OpenClaude-specific validation when available.

Expected launch environment:

- `CLAUDE_CODE_USE_OPENAI=1`
- `OPENAI_API_KEY` derived from the configured OpenRouter key
- `OPENAI_BASE_URL=https://openrouter.ai/api/v1`
- `OPENAI_MODEL` selected by MSc/OpenClaude profile policy

### Phase 5: VS Code Dashboard Readiness

Optional but recommended for product v1.

Check:

- extension installed
- workspace trusted if VS Code requires it
- Remote SSH mode detected
- SDK/CLI JSON commands available
- read-only dashboard can load project state
- graph/artifact/log/budget panes can render empty or fixture-backed states

### Phase 6: Optional OpenClaw/Telegram

Optional and off by default.

Flow:

1. Explain what OpenClaw does and why some users may not want it.
2. Show capability profile before enabling.
3. Collect Telegram credentials only if the user chooses Telegram.
4. Validate credentials only after explicit user action.
5. Install or configure OpenClaw script as a cluster-local add-on.
6. Record that OpenClaw is optional and can be disabled without affecting core
   MSc use.

### Phase 7: Tutorial Run

The tutorial should prefer:

- dry-run checks
- quickstart campaign inspection
- fixture-backed dashboard exploration
- optional budget-tier smoke run only with explicit user approval

No tutorial step should spend money without a clear confirmation.

## UX Surfaces

Setup should be available through:

- CLI: `msc setup` and future `msc project readiness --json`
- VS Code dashboard readiness panel
- Stage 7 guided wizard once the extension exists

The CLI remains canonical. The GUI can call the same commands and display the
same readiness model.

## Setup State Model

Recommended setup state fields:

- `project_detected`
- `python_ready`
- `cli_ready`
- `config_dir`
- `credential_source`
- `openrouter_configured`
- `openrouter_verified`
- `results_dir_ready`
- `slurm_available`
- `latex_available`
- `sdk_json_ready`
- `vscode_extension_ready`
- `openclaude_available`
- `openclaude_launch_ready`
- `openclaw_enabled`
- `telegram_enabled`
- `warnings`
- `next_actions`

Secrets must never be included in setup state.

## Security And Privacy

- Do not print API keys.
- Do not commit credential files.
- Prefer user-private config over repo-local `.env`.
- Explain when repo-root `.env` participates.
- Redact Telegram bot token, chat id, Slack webhook, and future provider keys.
- Avoid sending setup diagnostics outside the user's machine/cluster.
- Require confirmation before any network credential validation.

## Validation Plan

Before implementation:

- user reviews required versus optional setup boundaries
- user confirms OpenRouter key handling
- user confirms OpenClaw/Telegram remains opt-in

During implementation:

- tests for missing key, env key, user config key, and repo-local key precedence
- tests for redaction in CLI and JSON output
- tests for setup idempotency
- tests that existing credentials are not overwritten without confirmation
- tests for no-cost dry-run tutorial path
- optional integration tests for OpenClaude/OpenClaw when installed

## User Evaluation Checkpoint

The user should review:

- whether the setup phases match the intended onboarding story
- whether `~/.msc/.env` remains the right first credential store
- whether OpenClaude should be offered during required setup or optional setup
- whether OpenClaw/Telegram copy makes the risk and optionality clear
- whether tutorial smoke runs should ever spend budget automatically

## Exit Criteria

Stage 7 is approved when:

- required setup scope is accepted
- optional integration boundaries are accepted
- OpenRouter key flow is accepted
- readiness state model is accepted
- tutorial no-cost default is accepted
- OpenClaw/Telegram opt-in posture is accepted

## Open Risks

- Setup can become a hidden mutation surface if it silently edits user files.
- Provider key validation may incur network calls or small costs if not
  designed carefully.
- Too much optional setup can overwhelm first-time users.
- OpenClaude and OpenClaw setup may change as their upstream tools evolve.
