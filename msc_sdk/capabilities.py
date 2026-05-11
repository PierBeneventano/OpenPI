"""Capability profiles for the product-shell orchestrator harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


READ_CAPABILITIES = {
    "read.project",
    "read.artifacts",
    "read.runs",
    "read.campaigns",
    "read.logs",
    "read.budget",
    "read.events",
    "read.capabilities",
}

WRITE_CAPABILITIES = {
    "write.index",
    "write.feedback",
    "write.campaigns",
}

MUTATE_CAPABILITIES = {
    "mutate.launch",
    "mutate.resume",
    "mutate.repair",
    "mutate.plan_approval",
    "mutate.stage_status",
    "mutate.budget",
    "mutate.archive",
    "mutate.config",
}

PROFILE_CAPABILITIES = {
    "read_only": READ_CAPABILITIES,
    "default": READ_CAPABILITIES | {"write.index", "write.campaigns"},
    "openclaude_v1": READ_CAPABILITIES | {"write.index", "write.feedback", "write.campaigns"},
    "openclaw_read_only": READ_CAPABILITIES,
}

CAPABILITY_DESCRIPTIONS = {
    "read.project": "Inspect project setup and readiness.",
    "read.artifacts": "Inspect raw artifacts and derived manifests.",
    "read.runs": "List and inspect run workspaces.",
    "read.campaigns": "List and inspect campaign specs and status.",
    "read.logs": "Read log file metadata and tails.",
    "read.budget": "Read budget state and ledger summaries.",
    "read.events": "Read product-shell event logs.",
    "read.capabilities": "Read capability profiles and explanations.",
    "write.index": "Write derived indexes under approved product-shell locations.",
    "write.feedback": "Append human feedback without mutating historical artifacts.",
    "write.campaigns": "Create, import, export, and approve local campaign workspace state.",
    "mutate.launch": "Launch a run, campaign, or stage through preserved entry points.",
    "mutate.resume": "Resume an existing run through preserved entry points.",
    "mutate.repair": "Repair a failed stage through preserved campaign behavior.",
    "mutate.plan_approval": "Approve or reject generated campaign plans.",
    "mutate.stage_status": "Override product-visible stage status where supported.",
    "mutate.budget": "Increase or override budget configuration.",
    "mutate.archive": "Archive or delete workspaces through approved behavior.",
    "mutate.config": "Mutate product or runtime configuration.",
}


@dataclass(frozen=True)
class CapabilityDecision:
    """Result of checking a profile against a required capability."""

    ok: bool
    capability: str
    profile: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityClient:
    """Expose and evaluate product-shell capability profiles."""

    def __init__(self, profile: str = "default"):
        self.profile = profile

    def current(self) -> dict[str, Any]:
        capabilities = sorted(PROFILE_CAPABILITIES.get(self.profile, set()))
        return {
            "ok": True,
            "profile": self.profile,
            "capabilities": capabilities,
            "mutations_allowed": bool(set(capabilities) & MUTATE_CAPABILITIES),
        }

    def explain(self, capability: str) -> dict[str, Any]:
        return {
            "ok": capability in CAPABILITY_DESCRIPTIONS,
            "capability": capability,
            "description": CAPABILITY_DESCRIPTIONS.get(capability),
            "profiles": sorted(
                profile
                for profile, capabilities in PROFILE_CAPABILITIES.items()
                if capability in capabilities
            ),
            "mutating": capability in MUTATE_CAPABILITIES,
        }

    def check(self, capability: str) -> CapabilityDecision:
        capabilities = PROFILE_CAPABILITIES.get(self.profile, set())
        if capability in capabilities:
            return CapabilityDecision(True, capability, self.profile, "capability_granted")
        return CapabilityDecision(False, capability, self.profile, "capability_denied")
