# Stage 9 Later Slack And Webapp Planning

This document defines Slack and webapp work as later product stages that should
reuse the SDK/CLI/harness contracts after the local/Remote-SSH product
experience is stable.

Status: planning checkpoint completed on 2026-05-10. No Slack or hosted webapp
runtime code should be built yet. This stage is now an explicit future-product
roadmap and security gate for work after the Remote-SSH dashboard, OpenClaude,
guided setup, and optional OpenClaw safety surfaces have been reviewed.

## Decision

Slack and hosted webapp work are not v1.

They should be planned now so the earlier architecture does not block them, but
implementation should wait until the artifact model, SDK/CLI, harness, VS Code
dashboard, and OpenClaude integration are stable.

Slack should be treated as a professional team notification and lightweight
approval surface. The webapp should be treated as a separate security and
multi-user product, not a quick exposure of local cluster controls.

## Rule

Slack and webapp layers must use the same public SDK/CLI/harness semantics.
They must not import protected research-kernel internals or create a parallel
control plane.

Hosted surfaces require stronger authentication, authorization, secret
management, audit logging, and artifact access control than Remote SSH.

## Slack Concept

Slack is a later professional integration for labs and teams.

Potential v1 Slack features when it is time:

- campaign/stage status notifications
- failure and stalled-stage alerts
- budget threshold alerts
- plan-review notifications
- links back to VS Code/dashboard or webapp
- approval prompts that route through harness confirmation flow

Slack should not initially:

- expose full generated papers to broad channels by default
- dump raw logs by default
- allow unrestricted commands
- bypass the same confirmation/capability model as OpenClaude/OpenClaw

Slack interaction should be channel-safe:

- concise notifications
- redacted secrets
- minimal artifact excerpts
- private approvals where needed
- event ids for audit

## Webapp Concept

The webapp is the eventual hosted or browser-accessible product surface.

Potential webapp responsibilities:

- project dashboard
- campaign/run graph
- artifact browser
- paper/review preview
- log viewer with redaction controls
- budget dashboard
- event/audit timeline
- setup/onboarding surfaces
- team access and permissions
- optional chat/operator integration

The webapp must not become the research kernel. It should talk to an
authenticated control plane that uses the same harness and artifact read models.

## Hosted Security Requirements

Before any hosted/web-facing prototype:

- authentication
- per-user or per-project authorization
- secret storage policy
- artifact access policy
- generated-code isolation plan
- network boundary plan for Engaging or hosted workers
- audit event retention policy
- rate limiting for control endpoints
- confirmation flow for mutations
- redaction policy for logs, prompts, LLM traces, API keys, webhooks, and chat
  ids

No public unauthenticated endpoint should control runs, campaigns, artifacts, or
budgets.

## Multi-User Model

Future design should decide:

- single-user local/Remote SSH
- single-tenant lab deployment
- multi-user hosted SaaS
- per-project membership
- per-campaign permissions
- artifact sharing roles

Recommended first hosted boundary:

- single-tenant lab/project deployment before broad multi-tenant SaaS

This reduces risk while preserving a path to stronger tenancy later.

## Artifact Exposure Policy

Artifacts can contain unpublished research, generated code, logs, prompts,
provider metadata, and review content.

The webapp should classify artifacts:

- public/shareable
- lab-private
- project-private
- sensitive logs/traces
- secrets or secret-adjacent data

Default posture:

- papers/reviews are private to the project
- logs are private and redacted
- prompt/LLM-call traces are opt-in
- raw secret-adjacent artifacts are hidden

## Relationship To Earlier Stages

Slack and webapp should depend on:

- Stage 2 artifact/read models
- Stage 3 SDK/CLI command contracts
- Stage 4 orchestrator harness
- Stage 5 dashboard UX lessons
- Stage 6 OpenClaude skill/capability lessons
- Stage 7 setup/readiness model
- Stage 8 optional automation safety model

If these foundations are not stable, Slack/webapp work should remain design
only.

## Trigger Conditions

Do not start Slack or hosted webapp implementation until these conditions are
met:

- VS Code dashboard has been manually reviewed in the intended Remote SSH flow.
- OpenClaude configuration-first handoff has been reviewed.
- The user accepts the capability/confirmation model for any remote approval
  surface.
- Artifact exposure rules are reviewed against real workspaces.
- There is a concrete deployment target: local lab server, single-tenant lab
  deployment, or hosted product.
- Authentication and authorization design is written before endpoints are
  implemented.

## First Slack Prototype Scope

When Slack becomes active, the first prototype should be notification-only:

- stage complete
- stage failed
- stalled/no heartbeat warning
- budget threshold warning
- human review needed

The first Slack prototype should not accept commands. Approval buttons or slash
commands come only after harness confirmation flows and channel/privacy rules
are reviewed.

## First Webapp Prototype Scope

When webapp work becomes active, the first prototype should be read-only and
single-tenant:

- project readiness
- campaign/run graph
- artifact browser with conservative previews
- logs with redaction defaults
- budget panel
- event timeline

No hosted mutation execution should ship until authentication, authorization,
audit, and confirmation flows are tested.

## Deferred Work

Keep these explicitly out of the current lift:

- multi-tenant SaaS
- public internet control endpoints
- product-managed billing
- hosted cluster job launch
- Slack command execution
- raw prompt/LLM trace browsing by default
- broad artifact sharing links

## Backlog Seeds

Future implementation tickets should be split roughly as:

- Slack notification event mapper.
- Slack redaction and message policy.
- Slack private approval design.
- Webapp auth and tenancy design.
- Webapp artifact exposure matrix.
- Webapp read-only dashboard from Stage 2-4 read models.
- Hosted worker/network boundary design for Engaging.
- Audit retention and export policy.

## Validation Plan

Before implementation:

- security review
- auth/permission design review
- artifact exposure review
- secret handling review
- user workflow review

During implementation:

- permission tests
- redaction tests
- audit event tests
- endpoint auth tests
- mutation confirmation tests
- artifact access tests
- hosted deployment threat model review

## User Evaluation Checkpoint

The user should review:

- whether Slack should remain later or move earlier after VS Code/OpenClaude
- whether webapp should target single-tenant lab deployment first
- which artifacts non-engineer users should see
- whether hosted mode should ever launch runs on user clusters
- what billing/key model is acceptable: bring-your-own OpenRouter key,
  lab-managed key, or product-managed billing

## Exit Criteria

Stage 9 is approved when:

- Slack is confirmed as later or reprioritized deliberately
- webapp is confirmed as post-SDK/CLI/harness/dashboard
- hosted security requirements are accepted
- initial multi-user model direction is accepted
- artifact exposure policy direction is accepted

## Open Risks

- A webapp can accidentally expose sensitive unpublished research if artifact
  access policy is weak.
- Hosted control of cluster jobs introduces trust and network-boundary risks.
- Slack approvals can leak context into channels unless designed carefully.
- Multi-tenant hosting is a much larger product/security lift than Remote SSH.
