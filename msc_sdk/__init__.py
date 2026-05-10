"""Public product-shell SDK for PoggioAI/MSc.

The SDK is intentionally layered outside the protected research kernel. It
reads documented artifacts, exposes product read models, and provides stable
surfaces for CLIs and future UIs.
"""

from .artifacts import inspect_campaign, inspect_run_workspace, list_run_workspaces
from .campaigns import CampaignClient
from .manifest import ManifestImportResult, import_manifest, read_manifest, write_manifest
from .project import ProjectClient, inspect_project, project_readiness
from .read_models import (
    ArtifactReadModel,
    BudgetReadModel,
    CampaignReadModel,
    LogReadModel,
    RunReadModel,
    StageReadModel,
)
from .runs import RunClient
from .validation import ValidationClient

__all__ = [
    "ArtifactReadModel",
    "BudgetReadModel",
    "CampaignClient",
    "CampaignReadModel",
    "LogReadModel",
    "ManifestImportResult",
    "ProjectClient",
    "RunClient",
    "RunReadModel",
    "StageReadModel",
    "ValidationClient",
    "import_manifest",
    "inspect_campaign",
    "inspect_project",
    "inspect_run_workspace",
    "list_run_workspaces",
    "read_manifest",
    "project_readiness",
    "write_manifest",
]
