#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODEL="${MSC_OPENCLAUDE_MODEL:-openai/gpt-5-mini}"
MSC_BIN="${MSC_BIN:-${REPO_ROOT}/.venv/bin/msc}"

if [[ ! -x "${MSC_BIN}" ]]; then
  MSC_BIN="$(command -v msc || true)"
fi

if [[ -z "${MSC_BIN}" ]]; then
  echo "msc command not found. Install the project CLI or set MSC_BIN." >&2
  exit 2
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${REPO_ROOT}"
exec "${MSC_BIN}" --no-banner openclaude --model "${MODEL}" launch --execute -- "$@"
