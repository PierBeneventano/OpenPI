"""Self-validation metadata for SDK/CLI parity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CommandSpec:
    """Machine-readable public command descriptor."""

    operation: str
    cli: str
    sdk: str
    capability: str
    mutates: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PUBLIC_COMMANDS = [
    CommandSpec("project.inspect", "msc project inspect --json", "ProjectClient.inspect()", "read.project"),
    CommandSpec("project.readiness", "msc project readiness --json", "ProjectClient.readiness()", "read.project"),
    CommandSpec("artifacts.inspect", "msc artifacts inspect <path> --json", "import_manifest(path)", "read.artifacts"),
    CommandSpec("artifacts.index", "msc artifacts index <path> --out <dir> --json", "write_manifest(path, out)", "write.index", True),
    CommandSpec("runs.list", "msc runs list --json", "RunClient.list()", "read.runs"),
    CommandSpec("runs.inspect", "msc runs inspect <run> --json", "RunClient.inspect(ref)", "read.runs"),
    CommandSpec("runs.logs", "msc runs logs <run> --json", "RunClient.logs(ref)", "read.logs"),
    CommandSpec("runs.budget", "msc runs budget <run> --json", "RunClient.budget(ref)", "read.budget"),
    CommandSpec("runs.dry_run", "msc runs dry-run --task-file <path> --json", "RunClient.dry_run()", "read.runs"),
    CommandSpec("campaigns.list", "msc campaigns list --json", "CampaignClient.list()", "read.campaigns"),
    CommandSpec("campaigns.inspect", "msc campaigns inspect <campaign> --json", "CampaignClient.inspect(ref)", "read.campaigns"),
    CommandSpec("campaigns.graph", "msc campaigns graph <campaign> --json", "CampaignClient.graph(ref)", "read.campaigns"),
    CommandSpec("campaigns.status", "msc campaigns status <campaign> --json", "CampaignClient.status(ref)", "read.campaigns"),
    CommandSpec("campaigns.artifacts", "msc campaigns artifacts <campaign> --json", "CampaignClient.artifacts(ref)", "read.artifacts"),
    CommandSpec("capabilities.current", "msc capabilities current --json", "CapabilityClient.current()", "read.capabilities"),
    CommandSpec("capabilities.explain", "msc capabilities explain <capability> --json", "CapabilityClient.explain(capability)", "read.capabilities"),
    CommandSpec("events.list", "msc events list --json", "OrchestratorHarness.list_events()", "read.events"),
    CommandSpec("harness.inspect_project", "msc harness inspect-project --json", "OrchestratorHarness.inspect_project()", "read.project"),
    CommandSpec("harness.refresh_manifest", "msc harness refresh-manifest <path> --json", "OrchestratorHarness.refresh_manifest(path)", "write.index", True),
    CommandSpec("harness.request_action", "msc harness request-action <operation> --target <target> --capability <capability> --json", "OrchestratorHarness.request_action(...)", "mutate.*", True),
    CommandSpec("openclaude.readiness", "msc openclaude readiness --json", "openclaude_readiness(...)", "read.project"),
    CommandSpec("openclaude.env", "msc openclaude env --json", "openclaude_env_contract(...)", "read.project"),
    CommandSpec("openclaude.launch_plan", "msc openclaude launch --json", "openclaude_launch_plan(...)", "read.project"),
]


class ValidationClient:
    """Expose self-test metadata used by agent harnesses."""

    def commands(self) -> dict[str, Any]:
        return {
            "ok": True,
            "commands": [command.to_dict() for command in PUBLIC_COMMANDS],
        }

    def permissions(self) -> dict[str, Any]:
        capabilities = sorted({command.capability for command in PUBLIC_COMMANDS})
        return {
            "ok": True,
            "default_profile": "read_only_with_index_write",
            "capabilities": capabilities,
            "mutating_operations": [
                command.to_dict() for command in PUBLIC_COMMANDS if command.mutates
            ],
        }
