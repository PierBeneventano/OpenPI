"""Public product-shell SDK for PoggioAI/MSc.

The SDK is intentionally layered outside the protected research kernel. It
reads documented artifacts, exposes product read models, and provides stable
surfaces for CLIs and future UIs.
"""

from .artifacts import inspect_campaign, inspect_run_workspace, list_run_workspaces
from .budget import read_budget_workspace
from .capabilities import CapabilityClient
from .campaigns import CampaignClient
from .campaign_store import CampaignStore
from .events import EventStore
from .harness import ActionRequest, OrchestratorHarness
from .manifest import ManifestImportResult, import_manifest, read_manifest, write_manifest
from .openclaude import OpenClaudeReadiness
from .openclaw import OpenClawReadiness
from .project import ProjectClient, inspect_project, project_readiness
from .read_models import (
    ArtifactReadModel,
    BudgetReadModel,
    CampaignReadModel,
    LogReadModel,
    RunReadModel,
    StageReadModel,
    campaign_model_from_kernel_run,
)
from .runs import RunClient
from .setup_state import SetupState
from .stage_contracts import compile_kernel_graph, project_kernel_graph, validate_contract_coverage
from .validation import ValidationClient

__all__ = [
    "ArtifactReadModel",
    "BudgetReadModel",
    "ActionRequest",
    "CapabilityClient",
    "CampaignClient",
    "CampaignStore",
    "CampaignReadModel",
    "EventStore",
    "LogReadModel",
    "ManifestImportResult",
    "OrchestratorHarness",
    "OpenClaudeReadiness",
    "OpenClawReadiness",
    "ProjectClient",
    "RunClient",
    "RunReadModel",
    "SetupState",
    "StageReadModel",
    "ValidationClient",
    "campaign_model_from_kernel_run",
    "compile_kernel_graph",
    "import_manifest",
    "inspect_campaign",
    "inspect_project",
    "inspect_run_workspace",
    "list_run_workspaces",
    "read_manifest",
    "read_budget_workspace",
    "project_readiness",
    "project_kernel_graph",
    "validate_contract_coverage",
    "write_manifest",
]
