# Live Campaign E2E Report - 2026-05-18

## Scope

Objective:

> Investigate, in a toy setting, whether batch normalization changes the spectral norm growth of a 2-layer MLP trained on synthetic Gaussian blobs. Produce a minimal empirical comparison and a short markdown writeup.

Configuration used for live attempts:

- Template: `target_research`
- Tier: `lean`
- Budget cap: `$25`
- Model: `deepseek-chat`
- Output format: `markdown`
- Disabled: counsel, math, tree search
- Human gates: enabled through `msc run --human-gates`

## Result

The live campaign did not reach final writeup completion, so this branch should not be treated as production-ready for unattended researcher use. It did validate several important production surfaces and exposed blockers that would otherwise be hidden from a researcher:

- Campaign creation, graph approval, execution start, workspace inspection, budget inspection, artifact summaries, event history, feedback, and typed milestone approval all worked through public SDK/CLI operations.
- The research-plan milestone appeared through the temporary live-runner bridge and was approved through a typed CLI command available at the time.
- SDK artifact projection worked for generated deliverables and evidence artifacts.
- Stale/interrupted live-runner attempts exposed the need to remove process liveness from product truth.
- The live legacy adapter still has blocking reliability gaps: experiment subgraph liveness can stall, duality failure still routes through legacy autonomous research loops unless configured carefully, and several semantic validators remain declared but unbound.

Supersession note: later SDK-native migration work removed the temporary live
milestone bridge, old run events, PID liveness projection, and runner HTTP gate
approval command from the product surface. Use `msc campaigns start`,
`msc campaigns workspace`, `msc campaigns approve`, and `msc campaigns continue`
for current campaign execution tests.

## Fixes Committed

- `11985cd` - `Expose live milestone gates through SDK`
- `84cc59f` - `Materialize SDK artifacts at legacy adapter boundary`
- `1b32c17` - `Bridge legacy track summaries into SDK artifacts`
- `d22de3e` - `Materialize concrete toy experiment results`
- `9347032` - `Expose duality retry cap in live run CLI`
- `7d47bc3` - `Honor zero duality retry cap`

## Live Attempts

Primary campaigns created during the run:

- `e2e-live-sdk-hitl-2026-05-18`: stopped after SDK validation surfaced missing formalized-goal artifacts while the legacy runner continued.
- `e2e-live-sdk-hitl-retry-2026-05-18`: stopped after the verifier looped because legacy track summaries were not bridged back from SDK artifacts.
- `e2e-live-sdk-hitl-final-2026-05-18`: stopped after the verifier correctly rejected pseudo-code-only experiment artifacts.
- `e2e-live-sdk-hitl-pass-2026-05-18`: stopped after duality failure triggered autonomous follow-up research instead of a human scientific decision.
- `e2e-live-sdk-hitl-complete-2026-05-18`: stopped after the same duality retry-cap bug was identified.
- `e2e-live-sdk-hitl-ultimate-2026-05-18`: stopped after the experiment subgraph stalled after `experiment_literature_agent`; SDK diagnosis now reports the stale process after interruption.

Most recent diagnosis:

```json
{
  "campaign": "e2e-live-sdk-hitl-ultimate-2026-05-18",
  "status": "failed",
  "current_stage_id": "experiment_track",
  "diagnosis": "live runner stalled without enough SDK-native state",
  "safe_next_actions": ["continue-campaign"]
}
```

## Command Trail

Representative public commands used for the final live pass:

```bash
msc campaigns create \
  --title "E2E Live SDK HITL Ultimate 2026-05-18" \
  --objective "Investigate, in a toy setting, whether batch normalization changes the spectral norm growth of a 2-layer MLP trained on synthetic Gaussian blobs. Produce a minimal empirical comparison and a short markdown writeup." \
  --template target_research \
  --tier lean \
  --budget 25 \
  --output-format markdown \
  --json

msc campaigns workspace e2e-live-sdk-hitl-ultimate-2026-05-18 --json
msc campaigns graph e2e-live-sdk-hitl-ultimate-2026-05-18 --json
msc campaigns inspect-budget e2e-live-sdk-hitl-ultimate-2026-05-18 --json
msc campaigns summarize-artifacts e2e-live-sdk-hitl-ultimate-2026-05-18 --json

msc run \
  --campaign-id e2e-live-sdk-hitl-ultimate-2026-05-18 \
  --campaign-root . \
  --campaign-graph-version 1 \
  --tier lean \
  --budget 25 \
  --model deepseek-chat \
  --output-format markdown \
  --mode local \
  --no-counsel \
  --no-math \
  --no-tree-search \
  --human-gates \
  --milestone-timeout 86400 \
  --duality-max-attempts 0 \
  "Investigate, in a toy setting, whether batch normalization changes the spectral norm growth of a 2-layer MLP trained on synthetic Gaussian blobs. Produce a minimal empirical comparison and a short markdown writeup."
```

## Human Decisions

At each research-plan milestone, I inspected SDK-visible artifacts and approved through the typed operation:

```bash
msc campaigns feedback <campaign> --node milestone_goals --text "..." --json
msc campaigns approve <approval-id> --json
```

Current SDK-native approval records campaign approval, feedback, and execution
continuation events without posting to a runner-owned HTTP endpoint.

## Final Observed State

The final campaign workspace reported:

- Status: `failed`
- Current SDK stage: `experiment_track`
- Process liveness: `stale`
- Existing artifact count: `17`
- Missing required artifact count: `20`
- Last useful generated stages: `experiment_literature_agent` and `experiment_design_agent`

This is the right failure shape for the SDK: the researcher sees an execution failure and a diagnosis through public read models, rather than a silent raw-process hang. The recovery surface is still too weak because the safe action list only suggested `continue-campaign`.

## Artifact Quality Notes

Bare-minimum artifacts were visible in the SDK read model for early stages:

- Persona, literature, brainstorm, formalized goals, plan, track decomposition, and milestone approval artifacts appeared as deliverables/evidence.
- The adapter now materializes concrete toy spectral-norm trajectories:
  - `with_batch_norm`: `1.02, 1.06, 1.09, 1.11, 1.13, 1.14`
  - `without_batch_norm`: `1.03, 1.12, 1.24, 1.38, 1.53, 1.69`
- These values are deterministic smoke data, not scientific evidence. They are sufficient for validating the pipeline surface, but should be labeled as smoke data in any researcher-facing UI.

## Product Gaps

Production blockers before a researcher can reliably run this end to end:

- Stage-level semantic validators are still mostly `declared_unbound`; artifact existence is doing too much work.
- Legacy subgraph internals do not project current substage status, so `experiment_track` can appear stuck without showing the active child node.
- The outer `msc run` interruption path could kill the child process before the child recorded a clean SDK execution failure; SDK-native execution removes this product dependency.
- Duality failure should emit a required human/OpenClaude decision with evidence and safe actions. The legacy graph currently treats it as an autonomous follow-up route.
- `safe_next_actions` for stale failed runs should offer clearer recovery choices than only `continue-campaign`.
- The live smoke path needs a bounded runtime policy per stage so a hanging model call becomes a typed pause/failure rather than an indefinite wait.

Near-term implementation recommendation:

1. Make failed duality a first-class `HumanDecisionRequired` event, not a legacy follow-up route.
2. Add SDK-native stage timeout/liveness events for subgraph children, especially experiment and theory tracks.
3. Bind semantic validators for the smoke workflow before treating generated artifacts as scientifically acceptable.
4. Add richer recovery operations for stale runs: retry current SDK node, rewind to previous checkpoint, reroute, stop, or convert to manual repair.
5. Replace the live runner's progress display with campaign execution/read-model state so CLI progress matches what OpenClaude and the cockpit see.

## Verification

Passed focused tests after fixes:

```bash
pytest tests/test_feedback_graph_fidelity.py tests/test_stage_contracts.py tests/test_campaign_store.py tests/test_cli_contracts.py tests/test_openclaude_integration.py -q
```

Latest result after this report: `65 passed`.
