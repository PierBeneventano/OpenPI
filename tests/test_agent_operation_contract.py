from __future__ import annotations

from pathlib import Path

from msc_sdk.openclaude import openclaude_launch_plan
from msc_sdk.openclaw import openclaw_launch_plan
from msc_sdk.validation import ValidationClient, public_operation_contract


def test_public_operation_contract_read_only_excludes_mutations():
    contract = public_operation_contract("read_only")

    assert contract["surface"] == "msc_cli_sdk_v1"
    assert contract["mutations_allowed"] is False
    assert "campaign_events" in contract["read_model_sources"]
    assert all(not operation["mutates"] for operation in contract["operations"])


def test_public_operation_contract_openclaude_allows_autonomous_campaign_mutations():
    contract = ValidationClient().operation_contract("openclaude_v1")

    assert contract["ok"] is True
    assert contract["mutations_allowed"] is True
    assert contract["confirmation_required_for_mutations"] is False
    assert any(operation["operation"] == "campaigns.approve" for operation in contract["operations"])
    assert any(operation["operation"] == "campaigns.context_link" for operation in contract["operations"])
    assert any(operation["operation"] == "campaigns.rewind" for operation in contract["operations"])
    assert "SQLite" in contract["storage_boundary"]


def test_openclaude_and_openclaw_launch_plans_include_operation_contracts(tmp_path: Path):
    openclaude = openclaude_launch_plan(project_root=tmp_path, openrouter_configured=True)
    script = tmp_path / "launch.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    openclaw = openclaw_launch_plan(script)

    assert openclaude["operation_contract"]["profile"] == "openclaude_v1"
    assert openclaw["operation_contract"]["profile"] == "openclaw_read_only"
    assert all(not operation["mutates"] for operation in openclaw["operation_contract"]["operations"])
