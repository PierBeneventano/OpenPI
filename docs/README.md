# PoggioAI/MSc Documentation

This docs tree now centers the local-first product redesign. The historical
prototype docs are being pruned so future implementation work preserves the
research engine invariants instead of copying accidental proof-of-concept
structure.

## Current Source Of Truth

- [Agent Reference](agent_reference/README.md): V1 requirements, failure points,
  and reengineering storyboard for the product overhaul.
- [Research Engine Invariants](research_engine_invariants.md): what must be
  preserved during the overhaul.
- [Architecture Overview](architecture.md): current runtime architecture and
  near-term product architecture.
- [Local-First Campaign Workspace](local_first_campaign_workspace.md): staged
  migration to campaign bundles, SQLite state, events, and VS Code UX.
- [Data Formats](data_formats.md): existing runtime data shapes and compatibility
  formats.
- [Engaging Setup](engaging_setup.md): cluster setup notes for current testing.

## Preserved Reference Area

`docs/agent_reference/` is the active planning area. The old staged notes were
pruned, but the directory remains the canonical place for requirements,
architecture decisions, failure analysis, and implementation storyboards.
