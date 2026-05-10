# Stage 8 Optional OpenClaw Automation

This document defines OpenClaw as an optional, capability-limited cluster
automation add-on for users who want away-from-keyboard oversight through
Telegram or similar channels.

Status: first safety/checkpoint implementation completed on 2026-05-10. The
initial product surface is read-only/dry-run oriented: OpenClaw readiness,
capability profiles, launch-plan JSON, redacted status JSON, and VS Code setup
visibility. Existing start/stop commands remain for compatibility but are not
the preferred product path.

## Decision

OpenClaw is not the primary product interface.

The primary operator interface is OpenClaude plus the VS Code dashboard over the
public SDK/CLI/harness. OpenClaw remains an optional convenience layer for users
who explicitly choose cluster-local automation.

OpenClaw should operate only through public SDK/CLI commands and the Stage 4
orchestrator harness. It should not require broad, unbounded access to all code
and artifacts by default.

## Rule

OpenClaw may observe, notify, and request confirmed actions. It must not mutate
research logic, prompts, graph behavior, validators, model policy, checkpoint
semantics, budget semantics, or generated artifacts.

OpenClaw must be safe to disable. Campaigns and runs continue independently.

## Goals

- Let users monitor long Engaging campaigns while away.
- Send useful notifications for stage completion, failure, stalling, budget
  alerts, and required human review.
- Allow optional confirmation-gated actions from Telegram.
- Keep OpenClaw cluster-local and capability-scoped.
- Make all OpenClaw actions auditable through harness events.

## Non-Goals

- Do not make OpenClaw mandatory.
- Do not make Telegram mandatory.
- Do not expose a public unauthenticated control endpoint.
- Do not give OpenClaw raw arbitrary shell authority as the product contract.
- Do not replace OpenClaude or the VS Code dashboard.

## Capability Profiles

### Read-Only Profile

Default.

Allowed:

- inspect project readiness
- list campaigns/runs
- inspect campaign status
- inspect stage logs
- inspect artifacts list
- inspect budget summary
- send notifications

Not allowed:

- launch
- repair
- approve/reject plans
- modify budgets
- archive/delete
- edit tasks/config/artifacts

### Operator Profile

Optional and confirmation-gated.

Allowed after confirmation:

- launch a launchable pending stage
- tick heartbeat where supported
- approve or reject a generated plan
- resume where current preserved behavior supports it

### Repair Profile

Optional and confirmation-gated.

Allowed after confirmation:

- invoke current repair flow for a failed/repairing stage
- report repair diagnosis and event id

Not allowed:

- invent repair behavior outside current harness entry points
- edit protected kernel files directly

### Admin Profile

Not v1 default.

Requires explicit opt-in and likely should remain local/CLI-first:

- budget changes
- archive/delete
- stage status override
- task/campaign rewrites
- config mutation

## Telegram Flow

Telegram is optional.

Recommended flow:

1. User opts into Telegram during Stage 7 setup.
2. Setup stores `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in supported
   user-private config.
3. OpenClaw sends read-only notifications by default.
4. Sensitive actions are shown as confirmation requests.
5. User confirms with an explicit token or structured approval.
6. OpenClaw passes confirmation to the public CLI/harness.
7. Harness emits an event.
8. OpenClaw reports result and event id.

Telegram messages should avoid leaking:

- raw API keys
- full prompt/LLM traces
- unpublished paper contents by default
- large logs unless explicitly requested

## Current Entry Points To Wrap

Current OpenClaw-related surfaces:

- `scripts/campaign_heartbeat.py`
- `scripts/campaign_cli.py`
- `scripts/launch_openclaw_gateway.sh`
- `msc openclaw ...`
- `msc campaign ...`

Stage 8 should wrap these through the SDK/CLI/harness rather than expanding
direct-script authority.

## OpenClaw Down Behavior

If OpenClaw is down:

- running campaigns continue
- SLURM jobs continue
- heartbeat continues only if separately scheduled
- users can inspect state through VS Code, OpenClaude, CLI, and raw current
  entry points
- no generated artifacts are lost because OpenClaw is not the artifact source
  of truth

OpenClaw recovery should reload state from current artifacts and harness events.

## Security Boundaries

- Cluster-local by default.
- No public listener by default.
- No tunnel without authentication and explicit user approval.
- Read-only default capability profile.
- Mutations require confirmation tokens.
- Secrets redacted from logs/events/messages.
- OpenClaw should never be the only holder of state.

## Validation Plan

Before implementation:

- user reviews OpenClaw optionality and capability profiles
- user confirms Telegram interaction model
- user confirms OpenClaw-down behavior

During implementation:

- test read-only OpenClaw mode against fixtures
- test notifications without mutation authority
- test mutation refusal without confirmation
- test confirmation-gated launch/repair/plan approval dry-runs
- test secret redaction in messages/events/logs
- test OpenClaw restart/recovery from current state
- test behavior when OpenClaw is disabled

## User Evaluation Checkpoint

The user should review:

- whether OpenClaw should stay read-only by default
- whether Telegram is the right first notification/control channel
- whether operator/repair/admin profiles are scoped correctly
- whether any OpenClaw actions should be removed from v1
- whether users should see OpenClaw as an advanced setup section only

## Exit Criteria

Stage 8 is approved when:

- OpenClaw optionality is accepted
- default read-only capability profile is accepted
- Telegram confirmation flow is accepted
- OpenClaw-down behavior is accepted
- security boundaries are accepted
- current entry points to wrap are accepted

## Open Risks

- Users may distrust persistent cluster agents unless capability boundaries are
  extremely clear.
- Telegram can leak sensitive information if message defaults are too verbose.
- OpenClaw can drift into a second control plane if it bypasses the SDK/CLI.
- Repair/admin profiles may be too powerful for early product users.
