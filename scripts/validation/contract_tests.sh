#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEST_HOME="$(mktemp -d "${TMPDIR:-/tmp}/msc-test-home.XXXXXX")"

cleanup() {
  rm -rf "${TEST_HOME}"
}

trap cleanup EXIT

cd "${REPO_ROOT}"

HOME="${TEST_HOME}" "${REPO_ROOT}/.venv/bin/python" -m pytest tests/test_cli_contracts.py -q
