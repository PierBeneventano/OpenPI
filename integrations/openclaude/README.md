# MSc OpenClaude Integration

This directory contains the configuration-first OpenClaude integration for
PoggioAI/MSc.

Stage 6 does not fork OpenClaude yet. It provides:

- `MSC_SKILL.md`: the operator playbook OpenClaude should follow.
- `launch_openclaude_msc.sh`: a local launcher that maps the MSc OpenRouter key
  into OpenAI-compatible OpenClaude environment variables without printing the
  key.

Recommended checks:

```bash
msc openclaude readiness --json
msc openclaude env --json
msc openclaude launch --json
```

Actual launch:

```bash
integrations/openclaude/launch_openclaude_msc.sh
```

OpenClaude must operate through public `msc` SDK/CLI/harness commands. It should
not edit protected prompts, graph logic, campaign semantics, generated papers,
or historical artifacts directly.
