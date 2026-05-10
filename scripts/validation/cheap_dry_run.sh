#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULT_CONFIG_DIR="$(cd "${REPO_ROOT}/.." && pwd)/.msc"

CONFIG_DIR="${MSC_CONFIG_DIR:-${DEFAULT_CONFIG_DIR}}"
TASK_FILE="${MSC_DRY_RUN_TASK_FILE:-${REPO_ROOT}/examples/quickstart/task.txt}"

if [[ ! -f "${CONFIG_DIR}/.env" ]]; then
  echo "Missing env file: ${CONFIG_DIR}/.env" >&2
  echo "Set MSC_CONFIG_DIR or run setup before invoking this validation." >&2
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

# The top-level CLI receives --config-dir, but the preserved runner path still
# resolves runtime env through HOME/.msc. Point HOME at the config parent so the
# dry run stays inside the same repo-local setup without changing runner logic.
CONFIG_PARENT="$(cd "${CONFIG_DIR}/.." && pwd)"
export HOME="${CONFIG_PARENT}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -x "${REPO_ROOT}/.venv/bin/msc" ]]; then
  MSC_BIN="${REPO_ROOT}/.venv/bin/msc"
elif command -v msc >/dev/null 2>&1; then
  MSC_BIN="msc"
else
  echo "msc command not found. Create the environment first:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  source .venv/bin/activate" >&2
  echo "  python -m pip install -e \".[dev]\"" >&2
  exit 2
fi

LLM_CONFIG="${REPO_ROOT}/.llm_config.yaml"
LLM_CONFIG_BACKUP="$(mktemp "${TMPDIR:-/tmp}/msc-llm-config.XXXXXX")"
LLM_CONFIG_EXISTED=0

if [[ -f "${LLM_CONFIG}" ]]; then
  cp "${LLM_CONFIG}" "${LLM_CONFIG_BACKUP}"
  LLM_CONFIG_EXISTED=1
fi

restore_llm_config() {
  if [[ "${LLM_CONFIG_EXISTED}" == "1" ]]; then
    cp "${LLM_CONFIG_BACKUP}" "${LLM_CONFIG}"
  else
    rm -f "${LLM_CONFIG}"
  fi
  rm -f "${LLM_CONFIG_BACKUP}"
}

trap restore_llm_config EXIT

cd "${REPO_ROOT}"

"${MSC_BIN}" \
  --config-dir "${CONFIG_DIR}" \
  run \
  --tier budget \
  --model gpt-5-mini \
  --budget 1 \
  --output-format markdown \
  --no-counsel \
  --no-math \
  --no-tree-search \
  --dry-run \
  --task-file "${TASK_FILE}"
