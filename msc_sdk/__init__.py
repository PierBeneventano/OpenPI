"""Public product-shell SDK for PoggioAI/MSc.

The SDK is intentionally layered outside the protected research kernel. It
reads documented artifacts, exposes product read models, and provides stable
surfaces for CLIs and future UIs.
"""

from .read_models import (
    ArtifactReadModel,
    BudgetReadModel,
    CampaignReadModel,
    LogReadModel,
    RunReadModel,
    StageReadModel,
)
from .manifest import ManifestImportResult, import_manifest, read_manifest, write_manifest

__all__ = [
    "ArtifactReadModel",
    "BudgetReadModel",
    "CampaignReadModel",
    "LogReadModel",
    "ManifestImportResult",
    "RunReadModel",
    "StageReadModel",
    "import_manifest",
    "read_manifest",
    "write_manifest",
]
