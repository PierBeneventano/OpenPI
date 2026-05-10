# Stage 6 Forked OpenClaude Integration

This document defines how MSc should integrate OpenClaude as the primary chat
operator surface while preserving the SDK/CLI/harness boundary and protected
research kernel.

Status: first configuration-first implementation checkpoint completed on
2026-05-10. The repository now includes an MSc OpenClaude skill/playbook,
redacted readiness/env/launch-plan CLI commands, a local launcher script, and VS
Code dashboard readiness visibility. No OpenClaude fork has been created yet.

## External Context

Verified from the upstream OpenClaude repository on 2026-05-10 and revisited at
implementation time on 2026-05-10:

- OpenClaude is an open-source coding-agent CLI with tool-driven workflows.
- It supports OpenAI-compatible providers, including OpenRouter-compatible
  `/v1` provider flows.
- It includes a VS Code extension directory.
- It has provider profile support, with some profile configuration stored in
  plaintext according to its README notes.
- It has a headless gRPC mode for custom integrations.

Reference:

- <https://github.com/Gitlawb/openclaude>

This document should be revisited before implementation because OpenClaude is
an external moving target.

## Decision

OpenClaude should be integrated as a separate fork or tightly scoped
configuration layer, not merged into the MSc research kernel.

OpenClaude should operate MSc through the Stage 3 SDK/CLI and Stage 4
orchestrator harness. It should not directly import or edit protected
`consortium` runtime modules, prompts, graph logic, campaign status semantics,
or generated artifacts.

The first integration should prioritize:

- shared OpenRouter launch environment
- an MSc skill/playbook
- read-only status/artifact/log/budget inspection
- append-only feedback capture
- confirmation-gated mutations
- validation that OpenClaude can exercise the same expressive CLI surface Codex
  can exercise

## Rule

OpenClaude is an operator, not the research engine.

Allowed:

- call public `msc` CLI commands
- use SDK/CLI JSON outputs for context
- inspect read-only graph/status/artifact/log/budget/event data
- create append-only feedback or revision requests through approved commands
- request confirmed launch/repair/resume/approval actions
- display or stream event history into the VS Code chat tab

Not allowed by default:

- raw unrestricted shell control as the primary MSc interface
- direct prompt edits
- direct graph or validator edits
- direct mutation of campaign YAML or task files
- in-place mutation of generated papers or source artifacts
- bypassing SDK/CLI confirmations
- storing duplicate unmanaged API keys when launch-time environment injection
  can avoid it

## Integration Shape

```text
VS Code MSc Dashboard
       |
       +--> Graph / artifacts / logs / budget tabs
       |
       +--> Chat tab hosting or launching OpenClaude
                         |
                         v
             MSc skill/playbook
                         |
                         v
               msc SDK/CLI JSON commands
                         |
                         v
              single orchestrator harness
                         |
                         v
              protected research kernel
```

The dashboard and OpenClaude should both depend on the same SDK/CLI/harness
contracts. Neither should become a separate filesystem parser or control plane.

## Fork Versus Configuration

Begin with the least invasive integration that can deliver the target UX.

Prefer configuration/skill first when possible:

- launch OpenClaude with MSc-provided environment variables
- provide an MSc skill/playbook
- provide command allowlists and instructions
- consume event/read-model outputs from the `msc` CLI

Fork only when needed for:

- embedding cleanly in the MSc VS Code dashboard chat tab
- constraining tools/capabilities in a way upstream cannot support
- adding MSc-specific provider launch behavior
- adding MSc-specific validation or readiness checks
- improving event/permission UX for the product

Any fork should remain shallow and auditable. MSc-specific changes should live
in clearly named files or patches so upstream updates remain possible.

## Provider And OpenRouter Key Handling

MSc and OpenClaude should use the same OpenRouter key without duplicating
secrets into multiple unmanaged plaintext files.

Preferred v1 behavior:

- `msc setup` or Stage 7 guided setup stores the user's OpenRouter key in the
  MSc-supported credential location.
- The VS Code extension or CLI launcher starts OpenClaude with launch-time
  environment variables.
- OpenClaude sees an OpenAI-compatible provider configuration:
  - `CLAUDE_CODE_USE_OPENAI=1`
  - `OPENAI_API_KEY` derived from the configured OpenRouter key
  - `OPENAI_BASE_URL=https://openrouter.ai/api/v1`
  - `OPENAI_MODEL` set by MSc/OpenClaude profile policy
- The raw key is not displayed in dashboard, logs, events, or generated
  manifests.

Avoid by default:

- asking the user to paste the same key into OpenClaude separately
- writing the key into repo-local config
- committing provider profiles
- exposing the key to OpenClaw or Telegram flows unless explicitly needed

If OpenClaude requires a saved provider profile for a specific workflow, the
setup wizard must explain where the secret is stored and prefer user-private
locations.

## MSc Skill/Playbook

The MSc OpenClaude skill/playbook should teach OpenClaude to operate through
public commands, not through freeform internals.

Skill responsibilities:

- discover project readiness
- list campaigns and runs
- inspect graph/status/logs/budget/artifacts
- summarize current state for the user
- identify failed, stalled, incomplete, or missing-artifact states
- prepare feedback/revision requests
- request confirmation for mutations
- run self-validation commands
- cite command outputs and event ids when explaining actions

Skill prohibitions:

- do not edit protected kernel files
- do not mutate artifacts directly
- do not rewrite tasks/campaign YAML unless the user explicitly requests a
  confirmed mutation path
- do not run broad shell commands when a public `msc` command exists
- do not bypass confirmation tokens

Candidate skill commands:

- `msc project readiness --json`
- `msc campaigns list --json`
- `msc campaigns graph <campaign> --json`
- `msc campaigns status <campaign> --json`
- `msc campaigns logs <campaign> <stage-id> --json`
- `msc campaigns artifacts <campaign> <stage-id> --json`
- `msc campaigns budget <campaign> --json`
- `msc runs list --json`
- `msc runs inspect <run> --json`
- `msc artifacts tree <path> --json`
- `msc events list --json`
- `msc selftest commands --json`
- `msc selftest sdk-cli-parity --json`

## Capability Profile

Default OpenClaude v1 capability profile:

- `read.project`
- `read.artifacts`
- `read.runs`
- `read.campaigns`
- `read.logs`
- `read.budget`
- `read.events`
- `write.feedback` through append-only feedback commands

Not granted by default:

- `mutate.launch`
- `mutate.resume`
- `mutate.repair`
- `mutate.plan_approval`
- `mutate.stage_status`
- `mutate.budget`
- `mutate.archive`
- `mutate.config`

Mutating capabilities can be requested through the Stage 3/4 confirmation flow.

## Confirmation From Chat

OpenClaude should never execute sensitive MSc actions directly from ambiguous
chat intent.

Required flow:

1. User asks for an action.
2. OpenClaude calls the relevant dry-run/request command.
3. Harness returns risk summary, capability needed, target, and confirmation
   token.
4. OpenClaude explains the proposed action.
5. User explicitly confirms.
6. OpenClaude passes the confirmation token to the CLI.
7. Harness executes and emits an event.
8. OpenClaude reports the event id/result.

This flow applies to launch, resume, repair, approve/reject plan, abort/cancel,
budget changes, archive/delete, task/campaign rewrites, and artifact mutation.

## VS Code Chat Tab

Stage 5 may reserve a chat tab. Stage 6 defines how it becomes useful.

Acceptable v1 integration options:

- launch OpenClaude in an integrated terminal scoped to the MSc workspace
- embed or bridge to OpenClaude's VS Code extension if the fork supports it
- use OpenClaude headless gRPC mode behind a local-only adapter if that is
  cleaner after review

Default recommendation:

- start with launch/handoff into an integrated terminal or OpenClaude's own VS
  Code extension behavior
- defer a custom gRPC bridge until SDK/CLI/harness read models and permission
  events are stable

The chat tab should display:

- provider readiness without secrets
- active project/campaign context
- links to graph/artifact/log views
- recent OpenClaude action events
- confirmation request status when applicable

## Validation And Handoff From Codex

Codex should validate the SDK/CLI surface before OpenClaude is trusted to drive
it.

Required validation before OpenClaude integration:

- `msc selftest commands --json`
- `msc selftest sdk-cli-parity --json`
- `msc selftest fixtures --json`
- `msc selftest permissions --json`
- read-only command smoke tests against checked-in fixtures
- dry-run mutation refusal without confirmation
- redaction checks for OpenRouter key and provider config

OpenClaude-specific validation:

- OpenClaude launches with MSc-provided OpenRouter environment.
- OpenClaude can run the MSc skill/playbook.
- OpenClaude can inspect campaigns/runs through public commands.
- OpenClaude refuses protected-kernel edits by instruction and capability.
- OpenClaude mutation requests produce confirmation requests first.
- OpenClaude reports event ids after confirmed actions.

## Security And Privacy

- Do not expose the OpenRouter key in UI, logs, event files, manifests, or
  command output.
- Keep OpenClaude local to the user environment by default.
- Do not expose OpenClaude gRPC beyond loopback without authentication and
  explicit approval.
- Treat generated papers, logs, prompts, and LLM-call traces as sensitive.
- Prefer capability-scoped command access over broad shell access.
- Keep OpenClaw optional and separate from OpenClaude authority.

## Implementation Notes

Suggested sequence:

1. Review OpenClaude upstream structure and extension behavior at implementation
   time. Completed for the configuration-first checkpoint.
2. Decide configuration-first versus fork-first. Configuration-first was chosen.
3. Create MSc skill/playbook using only public SDK/CLI commands. Completed at
   `integrations/openclaude/MSC_SKILL.md`.
4. Add launcher that injects OpenRouter-compatible environment variables.
   Completed at `integrations/openclaude/launch_openclaude_msc.sh`.
5. Add readiness checks in VS Code dashboard. Completed with the OpenClaude
   dashboard tab and `msc openclaude readiness --json`.
6. Add chat tab handoff to OpenClaude.
7. Add OpenClaude validation commands or workflow.
8. Add fork patches only where upstream configuration cannot satisfy the UX or
   safety boundary.

Potential repository locations:

- `integrations/openclaude/`
- `extensions/openclaude-msc/`
- `docs/agent_reference/openclaude_skill.md`

The final layout should be chosen when implementation begins.

## User Evaluation Checkpoint

The user should review:

- whether OpenClaude should start as configuration/skill or immediate fork
- whether shared OpenRouter launch environment is the right credential model
- whether the default OpenClaude capability profile is safe
- whether append-only feedback should be enabled in v1
- whether chat tab should launch a terminal, bridge to OpenClaude's VS Code
  extension, or wait for headless gRPC integration
- whether the self-validation handoff from Codex to OpenClaude is sufficient

## Exit Criteria

Stage 6 is approved when:

- integration shape is accepted
- fork-versus-configuration default is accepted
- provider/key handling is accepted
- MSc skill/playbook scope is accepted
- OpenClaude capability profile is accepted
- chat confirmation flow is accepted
- validation and handoff plan is accepted

## Open Risks

- OpenClaude upstream may change quickly, so implementation-time verification
  is required.
- A deep fork can become expensive to maintain.
- Saved provider profiles may duplicate secrets unless launch-time environment
  injection works reliably.
- Headless gRPC could be powerful but may add premature daemon complexity.
- Broad OpenClaude shell/tool access can undermine the carefully scoped
  SDK/CLI/harness boundary unless the skill and permissions are enforced.
