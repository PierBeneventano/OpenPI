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
    destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PUBLIC_COMMANDS = [
    CommandSpec("project.inspect", "msc project inspect --json", "ProjectClient.inspect()", "read.project"),
    CommandSpec("project.readiness", "msc project readiness --json", "ProjectClient.readiness()", "read.project"),
    CommandSpec("project.setup_state", "msc project setup-state --json", "build_setup_state(...)", "read.project"),
    CommandSpec("project.tutorial_plan", "msc project tutorial-plan --json", "tutorial_plan()", "read.project"),
    CommandSpec("artifacts.inspect", "msc artifacts inspect <path> --json", "import_manifest(path)", "read.artifacts"),
    CommandSpec("artifacts.index", "msc artifacts index <path> --out <dir> --json", "write_manifest(path, out)", "write.index", True),
    CommandSpec("runs.list", "msc runs list --json", "RunClient.list()", "read.runs"),
    CommandSpec("runs.inspect", "msc runs inspect <run> --json", "RunClient.inspect(ref)", "read.runs"),
    CommandSpec("runs.logs", "msc runs logs <run> --json", "RunClient.logs(ref)", "read.logs"),
    CommandSpec("runs.budget", "msc runs budget <run> --json", "RunClient.budget(ref)", "read.budget"),
    CommandSpec("runs.dry_run", "msc runs dry-run --task-file <path> --json", "RunClient.dry_run()", "read.runs"),
    CommandSpec("campaigns.list", "msc campaigns list --json", "CampaignClient.list()", "read.campaigns"),
    CommandSpec("campaigns.workspace", "msc campaigns workspace <campaign> --json", "CampaignClient.workspace(ref)", "read.campaigns"),
    CommandSpec("campaigns.inspect", "msc campaigns inspect <campaign> --json", "CampaignClient.inspect(ref)", "read.campaigns"),
    CommandSpec("campaigns.graph", "msc campaigns graph <campaign> --json", "CampaignClient.graph(ref)", "read.campaigns"),
    CommandSpec("campaigns.status", "msc campaigns status <campaign> --json", "CampaignClient.status(ref)", "read.campaigns"),
    CommandSpec("campaigns.artifacts", "msc campaigns artifacts <campaign> --json", "CampaignClient.artifacts(ref)", "read.artifacts"),
    CommandSpec("campaigns.create", "msc campaigns create --title <title> --objective <text> --json", "CampaignClient.create(...)", "write.campaigns", True),
    CommandSpec("campaigns.delete", "msc campaigns delete <campaign> --confirm DELETE --json", "CampaignClient.delete(ref)", "delete.campaigns", True, True),
    CommandSpec("campaigns.import", "msc campaigns import <bundle_dir> --json", "CampaignClient.import_bundle(path)", "write.campaigns", True),
    CommandSpec("campaigns.export", "msc campaigns export <campaign> --json", "CampaignClient.export_bundle(ref)", "write.campaigns", True),
    CommandSpec("campaigns.events", "msc campaigns events <campaign> --json", "CampaignClient.events(ref)", "read.events"),
    CommandSpec("campaigns.feedback", "msc campaigns feedback <campaign> --text <feedback> --json", "CampaignClient.feedback(ref, text=...)", "write.feedback", True),
    CommandSpec("campaigns.context_link", "msc campaigns context link <campaign> --note <note> --json", "CampaignClient.link_context(...)", "write.feedback", True),
    CommandSpec("campaigns.context_list", "msc campaigns context list <campaign> --json", "CampaignClient.list_context_links(ref)", "read.campaigns"),
    CommandSpec("campaigns.context_update", "msc campaigns context update <campaign> <link> --status <status> --json", "CampaignClient.update_context_link(...)", "write.feedback", True),
    CommandSpec("campaigns.approve_graph", "msc campaigns approve-graph <campaign> --graph-version <n> --json", "CampaignClient.approve_graph(ref, n)", "write.campaigns", True),
    CommandSpec("campaigns.explain_node", "msc campaigns explain-node <campaign> <node> --json", "CampaignClient.explain_node(ref, node)", "read.campaigns"),
    CommandSpec("campaigns.propose_graph_change", "msc campaigns propose-graph-change <campaign> --json", "CampaignClient.propose_graph_change(...)", "write.approvals", True),
    CommandSpec("campaigns.approve", "msc campaigns approve <approval_id> --json", "CampaignClient.approve(id)", "write.approvals", True),
    CommandSpec("campaigns.reject", "msc campaigns reject <approval_id> --json", "CampaignClient.reject(id)", "write.approvals", True),
    CommandSpec("campaigns.pause", "msc campaigns pause <campaign> --json", "CampaignClient.pause(ref)", "write.campaigns", True),
    CommandSpec("campaigns.resume", "msc campaigns resume <campaign> --json", "CampaignClient.resume(ref)", "write.campaigns", True),
    CommandSpec("campaigns.stop", "msc campaigns stop <campaign> --json", "CampaignClient.stop(ref)", "write.campaigns", True),
    CommandSpec("campaigns.reroute", "msc campaigns reroute <campaign> --from <node> --to <node> --json", "CampaignClient.reroute(...)", "write.approvals", True),
    CommandSpec("campaigns.rewrite_stage", "msc campaigns rewrite-stage <campaign> <node> --instruction <text> --json", "CampaignClient.rewrite_stage(...)", "write.approvals", True),
    CommandSpec("campaigns.rerun_stage", "msc campaigns rerun-stage <campaign> <node> --json", "CampaignClient.rerun_stage(...)", "write.approvals", True),
    CommandSpec("campaigns.rewind", "msc campaigns rewind <campaign> <node> --reason <reason> --json", "CampaignClient.rewind(...)", "write.approvals", True),
    CommandSpec("campaigns.summarize_artifacts", "msc campaigns summarize-artifacts <campaign> --json", "CampaignClient.summarize_artifacts(ref)", "read.artifacts"),
    CommandSpec("campaigns.inspect_budget", "msc campaigns inspect-budget <campaign> --json", "CampaignClient.inspect_budget(ref)", "read.budget"),
    CommandSpec("campaigns.diagnose_execution", "msc campaigns diagnose-execution <campaign> --json", "CampaignClient.diagnose_execution(ref)", "read.diagnostics"),
    CommandSpec("campaigns.approve_milestone", "msc campaigns approve-milestone <campaign> --feedback <text> --json", "CampaignClient.approve_milestone(...)", "write.approvals", True),
    CommandSpec("campaigns.start", "msc campaigns start <campaign> --json", "CampaignClient.start(ref)", "write.campaigns", True),
    CommandSpec("campaigns.continue", "msc campaigns continue <campaign> --json", "CampaignClient.continue_execution(ref)", "write.campaigns", True),
    CommandSpec("campaigns.request_evidence", "msc campaigns request-evidence <campaign> --question <text> --json", "CampaignClient.request_evidence(...)", "write.feedback", True),
    CommandSpec("campaigns.propose_repair", "msc campaigns propose-repair <campaign> --json", "CampaignClient.propose_repair(...)", "write.approvals", True),
    CommandSpec("campaigns.change_tier_model", "msc campaigns change-tier-model <campaign> --tier <tier> --model <model> --json", "CampaignClient.change_tier_model(...)", "write.approvals", True),
    CommandSpec("capabilities.current", "msc capabilities current --json", "CapabilityClient.current()", "read.capabilities"),
    CommandSpec("capabilities.explain", "msc capabilities explain <capability> --json", "CapabilityClient.explain(capability)", "read.capabilities"),
    CommandSpec("events.list", "msc events list --json", "OrchestratorHarness.list_events()", "read.events"),
    CommandSpec("harness.inspect_project", "msc harness inspect-project --json", "OrchestratorHarness.inspect_project()", "read.project"),
    CommandSpec("harness.refresh_manifest", "msc harness refresh-manifest <path> --json", "OrchestratorHarness.refresh_manifest(path)", "write.index", True),
    CommandSpec("harness.request_action", "msc harness request-action <operation> --target <target> --capability <capability> --json", "OrchestratorHarness.request_action(...)", "mutate.*", True),
    CommandSpec("openclaude.readiness", "msc openclaude readiness --json", "openclaude_readiness(...)", "read.project"),
    CommandSpec("openclaude.env", "msc openclaude env --json", "openclaude_env_contract(...)", "read.project"),
    CommandSpec("openclaude.campaign_harness", "msc openclaude campaign-harness <campaign> --json", "openclaude_campaign_harness(...)", "read.campaigns"),
    CommandSpec("openclaude.context_pack", "msc openclaude context-pack <campaign> --json", "openclaude_context_pack(...)", "read.campaigns"),
    CommandSpec("openclaude.models", "msc openclaude models --json", "openclaude_models()", "read.capabilities"),
    CommandSpec("openclaude.launch_plan", "msc openclaude launch --json", "openclaude_launch_plan(...)", "read.project"),
    CommandSpec("openclaw.readiness", "msc openclaw readiness --json", "openclaw_readiness(...)", "read.project"),
    CommandSpec("openclaw.profiles", "msc openclaw profiles --json", "OPENCLAW_PROFILES", "read.capabilities"),
    CommandSpec("openclaw.launch_plan", "msc openclaw launch-plan --json", "openclaw_launch_plan(...)", "read.project"),
]


AGENT_OPERATION_PROFILES = {
    "read_only": {
        "mutations_allowed": False,
        "confirmation_required_for_mutations": True,
    },
    "operator": {
        "mutations_allowed": True,
        "confirmation_required_for_mutations": True,
    },
    "openclaude_v1": {
        "mutations_allowed": True,
        "confirmation_required_for_mutations": False,
    },
    "openclaw_read_only": {
        "mutations_allowed": False,
        "confirmation_required_for_mutations": True,
    },
}


def public_operation_contract(profile: str = "read_only") -> dict[str, Any]:
    """Return the public operation surface available to agent integrations."""

    profile_data = AGENT_OPERATION_PROFILES.get(profile, AGENT_OPERATION_PROFILES["read_only"])
    mutations_allowed = bool(profile_data["mutations_allowed"])
    destructive_allowed = bool(profile_data.get("destructive_allowed", False))
    commands = [
        command for command in PUBLIC_COMMANDS
        if (mutations_allowed or not command.mutates) and (destructive_allowed or not command.destructive)
    ]
    return {
        "surface": "msc_cli_sdk_v1",
        "profile": profile if profile in AGENT_OPERATION_PROFILES else "read_only",
        "read_model_sources": ["kernel_events", "campaign_events"],
        "storage_boundary": "agents must use public msc commands or SDK clients, not SQLite, status files, or legacy graph internals",
        "mutations_allowed": mutations_allowed,
        "confirmation_required_for_mutations": bool(profile_data["confirmation_required_for_mutations"]),
        "operations": [command.to_dict() for command in commands],
    }


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

    def operation_contract(self, profile: str = "read_only") -> dict[str, Any]:
        return {"ok": True, **public_operation_contract(profile)}
