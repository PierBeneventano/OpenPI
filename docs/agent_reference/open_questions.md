# Open Questions For Product Scope

These questions should guide future updates to this directory.

## Preservation Expectations

1. Which files or modules should be considered "do not modify without explicit
   approval" during productization?
2. Answered: prompts are part of the protected research behavior and should stay
   the same while rebuilding the product harness.
3. Answered: layers beyond the data/artifact layer can be rebuilt as product
   shell layers, including UI, control plane, adapter, execution substrate,
   notifications, and derived indexes. They must preserve research-kernel
   behavior and artifact semantics.
4. Which existing result workspaces or campaigns should be treated as canonical
   regression fixtures?
5. What is the minimum acceptable validation before merging harness changes:
   static checks, unit tests, dry-run, replay against saved artifacts, or a small
   budget-tier run?
6. Should max-mode or ultra-mode behavior be frozen entirely until there is
   budget to rerun a reference experiment?

## VS Code And Operator Experience

1. Should the first VS Code extension assume Remote SSH into Engaging, or should
   it also support local clones from day one?
2. Should the extension call existing scripts directly at first, or should we
   introduce a small `msc-control` daemon immediately?
3. What are the top three workflows the GUI must support first: status, logs,
   artifact browsing, OpenClaw chat, campaign launch, stage repair, budget
   monitoring, or plan approval?
4. Should WhatsApp and VS Code share a single message/action history?
5. Should the VS Code GUI be allowed to edit campaign YAML and task files, or
   should it start as a read-mostly operator dashboard?

## OpenClaw And Control Plane

1. Is OpenClaw the long-term orchestrator, or a near-term operator layer around
   campaign scripts?
2. Should OpenClaw actions be represented as auditable structured commands
   before execution?
3. Which control operations require confirmation: launch, repair, abort,
   budget increase, stage status override, archive, or prompt/task rewrite?
4. Should the control plane be cluster-local only for now, or reachable through
   a tunnel from outside Engaging?
5. What is the failure mode if OpenClaw is down but campaigns are still running?

## Future Webapp Scope

1. Is the webapp primarily an operator dashboard, a customer-facing research
   workspace, or both?
2. Will users bring their own OpenRouter/API keys, or will the product own
   provider billing?
3. What multi-user boundaries matter first: per-user campaigns, per-lab
   projects, or single-tenant hosted deployments?
4. Which artifacts should be exposed to non-engineer users?
5. What parts of the Engaging-first workflow must survive when moving to a
   hosted webapp?
