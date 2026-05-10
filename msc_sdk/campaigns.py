"""Campaign-level SDK helpers backed by read-only campaign inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import inspect_campaign
from .read_models import CampaignReadModel


class CampaignClient:
    """Inspect campaign specs and observed state without mutating them."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)

    def list(self) -> list[dict[str, Any]]:
        patterns = ("*_campaign.yaml", "campaign_*.yaml", "campaign_*.yml", "*_campaign.yml")
        files: list[Path] = []
        for pattern in patterns:
            files.extend(self.root.glob(pattern))
        seen: set[Path] = set()
        rows: list[dict[str, Any]] = []
        for path in sorted(files):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rows.append(
                {
                    "path": str(resolved),
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                }
            )
        return rows

    def inspect(self, ref: str | Path) -> CampaignReadModel:
        return inspect_campaign(resolve_campaign_ref(ref, self.root))

    def status(self, ref: str | Path) -> dict[str, Any]:
        campaign = self.inspect(ref)
        return {
            "campaign": campaign.campaign_id,
            "status": campaign.status,
            "budget": campaign.budget.to_dict(),
            "stages": [stage.to_dict() for stage in campaign.stages],
        }

    def graph(self, ref: str | Path) -> dict[str, Any]:
        campaign = self.inspect(ref)
        nodes = [
            {
                "id": stage.stage_id,
                "status": stage.status,
                "workspace": stage.workspace,
                "required_artifacts": [artifact.to_dict() for artifact in stage.required_artifacts],
            }
            for stage in campaign.stages
        ]
        edges = [
            {"source": nodes[index]["id"], "target": nodes[index + 1]["id"], "kind": "stage_order"}
            for index in range(len(nodes) - 1)
        ]
        return {"campaign": campaign.campaign_id, "nodes": nodes, "edges": edges}

    def artifacts(self, ref: str | Path, stage_id: str | None = None) -> dict[str, Any]:
        campaign = self.inspect(ref)
        stages = [stage for stage in campaign.stages if stage_id in (None, stage.stage_id)]
        return {
            "campaign": campaign.campaign_id,
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "required_artifacts": [artifact.to_dict() for artifact in stage.required_artifacts],
                    "optional_artifacts": [artifact.to_dict() for artifact in stage.optional_artifacts],
                }
                for stage in stages
            ],
        }


def resolve_campaign_ref(ref: str | Path, root: str | Path = ".") -> Path:
    """Resolve a campaign file name or path."""
    path = Path(ref)
    if path.exists():
        return path.resolve()
    candidate = Path(root) / str(ref)
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"Campaign spec not found: {ref}")
