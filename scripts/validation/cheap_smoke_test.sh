#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULT_CONFIG_DIR="$(cd "${REPO_ROOT}/.." && pwd)/.msc"

CONFIG_DIR="${MSC_CONFIG_DIR:-${DEFAULT_CONFIG_DIR}}"
TASK_FILE="${MSC_SMOKE_TASK_FILE:-${REPO_ROOT}/examples/quickstart/task.txt}"
SMOKE_BUDGET_USD="${MSC_SMOKE_BUDGET_USD:-5}"
SMOKE_MODEL="${MSC_SMOKE_MODEL:-gpt-5-mini}"
SMOKE_DEEP_RESEARCH_MODEL="${MSC_SMOKE_DEEP_RESEARCH_MODEL:-openrouter/perplexity/sonar-pro}"
SMOKE_TIMEOUT_SECONDS="${MSC_SMOKE_TIMEOUT_SECONDS:-900}"
SMOKE_LOG_DIR="${MSC_SMOKE_LOG_DIR:-${REPO_ROOT}/logs/validation}"
SMOKE_REPORT_DIR="${MSC_SMOKE_REPORT_DIR:-${REPO_ROOT}/logs/validation}"

if [[ "${MSC_PAID_SMOKE_APPROVED:-}" != "1" ]]; then
  cat >&2 <<MSG
Refusing to launch a paid smoke run without explicit approval.

This command runs the real pipeline with cheap model surfaces only.
Set MSC_PAID_SMOKE_APPROVED=1 when you intentionally want to spend provider budget.

Example:
  MSC_PAID_SMOKE_APPROVED=1 scripts/validation/cheap_smoke_test.sh

Defaults:
  task: ${TASK_FILE}
  model: ${SMOKE_MODEL}
  deep research model: ${SMOKE_DEEP_RESEARCH_MODEL}
  budget: \$${SMOKE_BUDGET_USD}
  timeout: ${SMOKE_TIMEOUT_SECONDS}s
MSG
  exit 2
fi

if [[ ! -f "${CONFIG_DIR}/.env" ]]; then
  echo "Missing env file: ${CONFIG_DIR}/.env" >&2
  echo "Set MSC_CONFIG_DIR or run setup before invoking this validation." >&2
  exit 2
fi

if [[ ! -f "${TASK_FILE}" ]]; then
  echo "Missing smoke task file: ${TASK_FILE}" >&2
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

# The preserved runner path resolves runtime env through HOME/.msc. Point HOME
# at the config parent so this smoke run uses the same repo-local setup.
CONFIG_PARENT="$(cd "${CONFIG_DIR}/.." && pwd)"
export HOME="${CONFIG_PARENT}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export DEEP_RESEARCH_MODEL="${SMOKE_DEEP_RESEARCH_MODEL}"
export MSC_SMOKE_BUDGET_USD="${SMOKE_BUDGET_USD}"

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
mkdir -p "${SMOKE_LOG_DIR}" "${SMOKE_REPORT_DIR}"

RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${SMOKE_LOG_DIR}/cheap_smoke_${RUN_ID}.log"
REPORT_FILE="${SMOKE_REPORT_DIR}/cheap_smoke_${RUN_ID}.json"
BEFORE_FILE="$(mktemp "${TMPDIR:-/tmp}/msc-smoke-before.XXXXXX")"
AFTER_FILE="$(mktemp "${TMPDIR:-/tmp}/msc-smoke-after.XXXXXX")"

find results -maxdepth 1 -mindepth 1 -type d -name 'consortium_*' -print | sort > "${BEFORE_FILE}"

set +e
"${MSC_BIN}" \
  --config-dir "${CONFIG_DIR}" \
  run \
  --tier budget \
  --model "${SMOKE_MODEL}" \
  --budget "${SMOKE_BUDGET_USD}" \
  --output-format markdown \
  --no-counsel \
  --no-math \
  --no-tree-search \
  --max-run-seconds "${SMOKE_TIMEOUT_SECONDS}" \
  --no-stream \
  --task-file "${TASK_FILE}" 2>&1 | tee "${LOG_FILE}"
RUN_RC="${PIPESTATUS[0]}"
set -e

find results -maxdepth 1 -mindepth 1 -type d -name 'consortium_*' -print | sort > "${AFTER_FILE}"
WORKSPACE="$(comm -13 "${BEFORE_FILE}" "${AFTER_FILE}" | tail -n 1)"
rm -f "${BEFORE_FILE}" "${AFTER_FILE}"

if [[ -z "${WORKSPACE}" ]]; then
  WORKSPACE="$(find results -maxdepth 1 -mindepth 1 -type d -name 'consortium_*' -print | sort | tail -n 1)"
fi

if [[ -z "${WORKSPACE}" ]]; then
  echo "Smoke run did not produce a workspace. Log: ${LOG_FILE}" >&2
  exit 1
fi

echo "Smoke workspace: ${WORKSPACE}"
echo "Smoke log: ${LOG_FILE}"

ANALYZE_RC=0
"${REPO_ROOT}/.venv/bin/python" \
  "${SCRIPT_DIR}/analyze_smoke_workspace.py" \
  "${WORKSPACE}" \
  --json-out "${REPORT_FILE}" || ANALYZE_RC="$?"

echo "Smoke report: ${REPORT_FILE}"

if [[ "${RUN_RC}" != "0" ]]; then
  echo "Smoke pipeline exited with code ${RUN_RC}." >&2
  exit "${RUN_RC}"
fi

exit "${ANALYZE_RC}"
