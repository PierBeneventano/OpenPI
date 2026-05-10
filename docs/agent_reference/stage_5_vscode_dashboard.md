# Stage 5 Remote-SSH VS Code Read-Only Dashboard

This document defines the first GUI product surface: a Remote-SSH-first VS Code
extension that provides read-only visibility into MSc runs, campaigns, artifacts,
logs, budgets, and eventual OpenClaude handoff.

Status: drafted on 2026-05-10. Pending user review before implementation.

## Decision

VS Code v1 is read-only.

It should run naturally inside the user's Remote SSH session on Engaging, read
local project/workspace state through the Stage 3 SDK/CLI and Stage 4 harness,
and avoid direct imports of protected research-kernel modules.

The first GUI should build trust and observability before it adds control.

## Rule

The extension must not mutate:

- prompts
- graph logic
- campaign YAML
- task files
- generated artifacts
- generated papers
- stage status
- budgets
- launch/repair/resume/abort state

Any future mutation must go through the SDK/CLI and orchestrator harness
confirmation flow.

## Goals

- Give the user a live operational dashboard while SSHed into Engaging.
- Show the campaign/run graph from product read models.
- Make artifacts and logs easy to inspect without teaching the user the old
  filesystem layout.
- Show budget state and liveness clearly.
- Provide a future chat tab location for OpenClaude without coupling the first
  dashboard to the OpenClaude fork.
- Keep the UI useful even when no daemon exists by polling CLI/SDK-backed read
  commands.

## Non-Goals

- Do not launch, repair, abort, approve plans, rewrite tasks, or edit campaign
  files in v1.
- Do not implement the OpenClaude fork in this stage.
- Do not implement OpenClaw or Telegram flows in this stage.
- Do not build a hosted webapp in this stage.
- Do not make the extension parse protected internals directly.

## User Flow

Primary v1 flow:

1. User SSHes into Engaging from VS Code.
2. User opens the MSc checkout or working directory.
3. User opens the MSc dashboard view.
4. Extension detects project readiness through SDK/CLI commands.
5. Extension shows active/recent campaigns and runs.
6. User selects a campaign or run.
7. Graph tab shows stage status, liveness, budget, and artifact completeness.
8. Artifact tab shows generated files and previews.
9. Logs tab shows read-only tails and issue hints.
10. Budget/status panels remain visible as compact operational context.

Future flow:

1. Chat tab hosts or hands off to OpenClaude.
2. OpenClaude uses the same SDK/CLI surface and capability profile.
3. The dashboard reflects actions through events, not hidden UI state.

## Architecture

```text
VS Code Webview / Tree Views / Panels
              |
              v
extension backend on remote VS Code host
              |
              v
SDK/CLI read commands and harness read operations
              |
              v
production manifests, events, raw workspaces
```

The extension backend should execute on the remote VS Code host when the user is
connected through Remote SSH. That lets it read Engaging-local files without
public networking.

The extension frontend should use read models from SDK/CLI JSON output. It
should not know internal `consortium` modules, protected files, or old script
quirks.

## Data Sources

Preferred data sources:

- `msc project inspect --json`
- `msc project readiness --json`
- `msc artifacts inspect <path> --json`
- `msc campaigns list --json`
- `msc campaigns graph <campaign> --json`
- `msc campaigns status <campaign> --json`
- `msc campaigns logs <campaign> <stage-id> --json`
- `msc campaigns artifacts <campaign> <stage-id> --json`
- `msc campaigns budget <campaign> --json`
- `msc runs list --json`
- `msc runs inspect <run> --json`
- `msc runs logs <run> --json`
- `msc runs budget <run> --json`
- `msc events list --json`

Until these commands exist, the extension implementation should wait or use a
thin development adapter that is clearly marked temporary and backed by the same
eventual schemas.

## Views

### Activity Bar Entry

Add a single MSc activity entry with compact navigation:

- Runs
- Campaigns
- Artifacts
- Logs
- Events
- Settings/Readiness

### Graph Tab

The graph tab is the primary view.

Node types:

- campaign
- stage
- run
- artifact group
- feedback/revision request

Edges:

- dependency
- context source
- produced artifact
- revised from
- budget/event association when useful

Node information:

- status
- liveness
- elapsed/staleness
- artifact completeness
- budget summary
- latest event

Interaction:

- click node to inspect details
- click artifact badge to open artifact view
- click log badge to open logs tab scoped to that node
- click budget badge to open budget panel

No graph interaction should mutate runtime state in v1.

### Artifact Browser

The artifact browser should group by:

- project
- campaign
- stage
- run
- artifact kind

Supported preview modes:

- Markdown rendered preview
- TeX text preview
- PDF open in VS Code/pdf viewer when available
- JSON structured viewer
- log text viewer
- image preview for figures

Artifact badges:

- immutable source
- derived
- required
- optional
- missing
- validator declared
- validation warning

### Logs View

Log view should support:

- run/campaign/stage scoped logs
- tail count selection
- refresh
- issue highlighting for common error patterns
- newest log detection
- clear source path display

It should not follow logs through a long-running shell process in v1 unless the
implementation can cancel safely and avoid orphan processes.

### Budget And Status Panels

Panels should show:

- campaign/run status
- active/stalled/failed count
- budget limit
- total spend
- by-model spend
- last budget update
- token usage pointers when available
- liveness summary

### Events View

Events view should show:

- observations
- indexing events
- confirmation requests
- denied actions
- confirmed mutations
- OpenClaude/OpenClaw actions when later available
- feedback/revision requests

In v1, event display is read-only.

### Chat Tab Placeholder

The dashboard may reserve a chat tab for OpenClaude integration, but v1 should
not fake a chat experience.

Acceptable v1 behavior:

- show readiness for future OpenClaude integration
- show configured provider status without exposing secrets
- show last known OpenClaude/OpenClaw event history if events exist

OpenClaude itself belongs to Stage 6.

## UX Principles

- Operational, dense, and calm rather than marketing-like.
- No large hero screens.
- Prioritize scanning, comparison, and repeated checks.
- Treat graph/status/log/budget views as the first-screen experience.
- Prefer predictable navigation over decorative panels.
- Do not use UI copy to explain the whole system inside the app.
- Avoid presenting mutation affordances in v1, including disabled launch/repair
  buttons that imply the dashboard is almost a controller.

## Readiness And Setup Hooks

The extension should surface readiness without owning setup in v1:

- project detected
- virtual environment or installed CLI detected
- `msc` command available
- OpenRouter key configured without revealing value
- results directory detected
- campaign specs detected
- SDK/CLI JSON support detected
- optional OpenClaude availability later
- optional OpenClaw availability later

Guided setup belongs to Stage 7, but Stage 5 should leave space for that flow.

## Refresh Model

Initial refresh model:

- manual refresh button
- automatic polling every 15-30 seconds when a dashboard view is open
- pause polling when VS Code window is inactive if possible
- no long-lived daemon required

Later refresh model:

- event stream from daemon or harness if Stage 4 adds one
- file watcher for manifests/events where safe
- explicit reconnect behavior

## Security And Privacy

- Do not expose raw API keys.
- Redact notification tokens, webhook URLs, and chat ids.
- Treat logs, prompts, LLM-call logs, generated code, and unpublished papers as
  sensitive.
- Avoid sending workspace data outside the Remote SSH environment.
- No public network listener is required for v1.
- Do not embed broad shell access in the webview.

## Implementation Notes

Recommended first implementation path:

1. Wait for Stage 2/3 read-model commands or implement a temporary adapter with
   the same schemas.
2. Scaffold VS Code extension in a product-shell directory, not inside
   protected kernel modules.
3. Implement project readiness view.
4. Implement campaigns/runs list from JSON commands.
5. Implement graph tab from graph read model.
6. Implement artifact browser from artifact tree read model.
7. Implement logs and budget views.
8. Add events view.
9. Add visual regression/manual screenshot checks for desktop and Remote SSH
   scenarios.

Potential directory names:

- `extensions/vscode-msc/`
- `apps/vscode-extension/`

The final location should be chosen when implementation begins.

## Validation Plan

Before implementation:

- user reviews this UX and integration boundary
- SDK/CLI JSON command availability is confirmed or temporary adapter is
  explicitly approved

During implementation:

- unit tests for parsing CLI JSON responses
- fixture tests using checked-in campaign specs/manifests
- UI tests for empty, active, completed, failed, and missing-fixture states
- manual Remote SSH smoke test
- screenshot checks for desktop layout
- confirm no command path mutates files in v1
- confirm no protected kernel imports are used

## User Evaluation Checkpoint

The user should review:

- whether Remote SSH is the correct v1 assumption
- whether the graph tab captures the live work they want to see
- whether the artifact/log/budget panes cover the workflows they need first
- whether chat should be a placeholder or hidden until Stage 6
- whether polling is acceptable before a daemon exists
- whether the UI should use temporary adapters before SDK/CLI implementation or
  wait for Stage 2/3 commands

## Exit Criteria

Stage 5 is approved when:

- read-only boundary is accepted
- Remote-SSH-first assumption is accepted
- view list and graph semantics are accepted
- data sources are accepted
- refresh model is accepted
- OpenClaude handoff placeholder behavior is accepted
- implementation dependency on SDK/CLI read models is understood

## Open Risks

- Building the extension before SDK/CLI JSON commands exist could create a
  second ad hoc parser layer.
- Users may expect controls in the dashboard unless the read-only posture is
  visually clear.
- Polling may be enough for v1 but feel less live than a daemon/event stream.
- Logs and artifacts may contain sensitive unpublished research; previews need
  conservative defaults.
- Without real result fixtures, graph and artifact states may be under-tested.
