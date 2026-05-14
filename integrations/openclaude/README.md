# MSc OpenClaude Integration

This directory contains the configuration-first OpenClaude integration for
PoggioAI/MSc.

The integration treats OpenClaude as a high-level campaign steering harness. It
provides:

- `MSC_SKILL.md`: the campaign researcher playbook OpenClaude should follow.
- `launch_openclaude_msc.sh`: a local launcher that maps the MSc OpenRouter key
  into OpenAI-compatible OpenClaude environment variables without printing the
  key.
- `msc openclaude campaign-harness <campaign> --json`: the structured campaign
  packet OpenClaude should load before answering or steering a campaign.

Recommended checks:

```bash
msc openclaude readiness --json
msc openclaude env --json
msc openclaude campaign-harness <campaign> --json
msc openclaude launch --json
```

Actual launch:

```bash
integrations/openclaude/launch_openclaude_msc.sh
```

The launcher delegates to `msc openclaude launch --execute`, so it uses the
same shell/config-dir/repo-env resolution as the rest of the SDK. The launch
command injects `MSC_SKILL.md` with `--append-system-prompt-file` and grants the
repo as an allowed directory, so the chat session starts as the MSc campaign
researcher harness rather than a generic OpenClaude session.

OpenClaude must operate through public `msc` SDK/CLI/harness commands. It should
not edit protected prompts, graph logic, campaign semantics, generated papers,
SQLite state, status JSON, or historical artifacts directly.
