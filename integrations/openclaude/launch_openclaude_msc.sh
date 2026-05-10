#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULT_CONFIG_DIR="$(cd "${REPO_ROOT}/.." && pwd)/.msc"
CONFIG_DIR="${MSC_CONFIG_DIR:-${DEFAULT_CONFIG_DIR}}"
MODEL="${MSC_OPENCLAUDE_MODEL:-openai/gpt-5-mini}"

if [[ ! -f "${CONFIG_DIR}/.env" ]]; then
  echo "Missing MSc env file: ${CONFIG_DIR}/.env" >&2
  echo "Set MSC_CONFIG_DIR or run msc setup before launching OpenClaude." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${CONFIG_DIR}/.env"
set +a

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is not configured in ${CONFIG_DIR}/.env" >&2
  exit 2
fi

if ! command -v openclaude >/dev/null 2>&1; then
  echo "openclaude command not found. Install it with: npm install -g @gitlawb/openclaude" >&2
  exit 127
fi

export CLAUDE_CODE_USE_OPENAI=1
export OPENAI_API_KEY="${OPENROUTER_API_KEY}"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_MODEL="${MODEL}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${REPO_ROOT}"
exec openclaude "$@"
