# OpenClaude Coworking Contract

OpenClaude is the primary AI coworker over the local SDK. It must use campaign
read models and public SDK/CLI operations; it must not mutate SQLite, status
JSON, raw artifacts, run workspaces, or graph internals directly.

## Truth Sources

OpenClaude should read:

- `msc campaigns workspace <campaign> --json`
- `msc campaigns graph <campaign> --json`
- `msc campaigns explain-node <campaign> <stage> --json`
- `msc campaigns summarize-artifacts <campaign> --json`
- `msc campaigns inspect-budget <campaign> --json`
- `msc campaigns diagnose-execution <campaign> --json`

The workspace aisles are the preferred orientation surface:

- `aim`
- `map`
- `evidence`
- `decisions`
- `diagnostics`

Raw logs, status files, prompts, process IDs, and run workspaces are not
campaign state. If `workspace.execution.status` is `not_started` and the
workspace lists no SDK-native attempts, the next action should remain
SDK-native campaign start unless the current campaign read model says
otherwise.

## Allowed Mutations

OpenClaude may call typed operations after researcher intent is clear:

- record feedback,
- link context,
- approve or reject pending decisions,
- pause, resume, or stop a campaign,
- rerun, rewind, reroute, or rewrite a stage,
- request more evidence,
- propose repair,
- propose tier/model policy changes.

Unsafe or expensive actions should become explicit approvals or graph-change
proposals. OpenClaude should not silently increase budget, bypass a failed
duality gate, or edit product truth by hand.

## OpenClaw Boundary

OpenClaw may wrap these same operations for convenience. It should remain
read-only or wrapper-only and should never become another state authority.
