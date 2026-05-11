# Local-First Campaign Workspace Storyboard

This document tracks the staged migration from file-spec-first campaigns to a
local campaign workspace model. The product goal is to keep research artifacts
visible as normal repo files while using `.msc/campaigns.db` as the local
control and index layer.

The redesign constraints are defined in
[Research Engine Invariants](research_engine_invariants.md). This storyboard is
an implementation path for those invariants, not a replacement for them.

## Pass 0: Stabilized Extension MVP

State after this pass:

- The VS Code extension is a React webview with campaign home, workspace graph,
  steering placeholder, and artifact preview views.
- `node_modules/` is ignored; extension dependencies are pinned by
  `extensions/vscode-msc/package-lock.json`.
- `consortium scaffold` remains the zero-spend UX path for graph and artifact
  integration testing.

Verification:

```bash
npm test --prefix extensions/vscode-msc
```

## Passes 1-4: Local Campaign Store And API-Backed Extension

State after this pass:

- `msc_sdk.campaign_store.CampaignStore` owns local campaign state in
  `.msc/campaigns.db`.
- Mutations append SQLite events and mirror readable JSONL events at
  `.msc/events/campaigns.jsonl`.
- Graph snapshots are written to `.msc/snapshots/<campaign_id>.graph.json`.
- Campaign export/import now uses explicit JSON bundle directories under
  `campaigns/<campaign_id>/`: `campaign.json`, `graph.json`,
  `artifacts.json`, `events.jsonl`, and `README.md`.
- YAML campaign specs are not an operational API for the local-first product.
  Older YAML-oriented artifact readers remain separate compatibility utilities.
- `msc campaigns create/import/export/events/approve-graph` provide the public
  local-first campaign API.
- The VS Code extension creates campaigns through `msc campaigns create` rather
  than writing campaign state files itself.

Verification:

```bash
npm test --prefix extensions/vscode-msc
./.venv/bin/msc --no-banner campaigns --root "$(mktemp -d)" create \
  --title "CLI Smoke" \
  --objective "Smoke local campaign store." \
  --template consortium_scaffold \
  --budget 1 \
  --json
```

## Remaining Passes

Next implementation targets are now detailed in
[agent_reference/reengineering_storyboard.md](agent_reference/reengineering_storyboard.md).
Short version:

- Pass 5: explicit stage contract model. Extract the historical pipeline into
  typed stage definitions with purpose, inputs, outputs, validators, tools,
  budget policy, failure policy, and next routes.
- Pass 6: runner event bridge. Emit `RunStarted`, stage status transitions,
  `ArtifactIndexed`, budget updates, steering events, and `RunExited` into the
  campaign store.
- Pass 7: approval and steering APIs for pause/resume/stop/reroute/rewrite and
  failure recovery.
- Pass 8: OpenClaude harness over the SDK/CLI so natural-language steering is
  part of V1 without bypassing approvals or budgets.
- Pass 9: runtime graph and VS Code cockpit refinement over real campaign
  events.
