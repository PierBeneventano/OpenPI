# MSc Dashboard

Read-only VS Code dashboard for PoggioAI/MSc workspaces.

Open with `MSc: Open Dashboard` from the command palette while connected to the
target workspace, including Remote SSH sessions on Engaging.

For local development from the repo root, run the VS Code launch configuration
`Run MSc Dashboard Extension` and use `MSc: Open Dashboard` in the new Extension
Development Host window. The command is not available in a normal VS Code window
unless the extension has been installed or launched in extension-development
mode.

The extension shells out to the public `msc` JSON CLI and does not mutate runs,
campaigns, artifacts, prompts, graph logic, budgets, or generated papers.
