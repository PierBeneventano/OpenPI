"""Test collection quarantine for optional legacy runtime dependencies."""

from __future__ import annotations

from importlib.util import find_spec


def _missing(*module_names: str) -> bool:
    return any(find_spec(module_name) is None for module_name in module_names)


collect_ignore: list[str] = []

if _missing("langchain_core"):
    collect_ignore.extend(
        [
            "test_budget.py",
            "test_config.py",
            "test_deep_research_tool.py",
            "test_literature_rate_limit.py",
        ]
    )

if _missing("litellm"):
    collect_ignore.extend(
        [
            "test_campaign_recovery.py",
            "test_counsel.py",
            "test_extract_verdict.py",
            "test_openrouter_deep_research_tool.py",
            "test_pdf_summary.py",
            "test_prepare_iterate_seed.py",
        ]
    )

if _missing("click"):
    collect_ignore.extend(
        [
            "test_cli_contracts.py",
            "test_guided_setup.py",
            "test_msc_sdk_cli_surface.py",
            "test_msc_sdk_harness.py",
            "test_openclaude_integration.py",
            "test_openclaw_optional.py",
        ]
    )

if _missing("langgraph"):
    collect_ignore.extend(
        [
            "test_brainstorm_gate.py",
            "test_graph.py",
            "test_lit_review_gate.py",
            "test_parallel_graph.py",
            "test_runner.py",
            "test_state.py",
            "test_track_decomposition_gate.py",
        ]
    )
