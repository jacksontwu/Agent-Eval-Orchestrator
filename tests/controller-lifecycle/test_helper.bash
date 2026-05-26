#!/usr/bin/env bash

setup() {
  TEST_REPO="${BATS_TEST_TMPDIR}/aeo-repo-$$"
  MOCK_BIN="${TEST_REPO}/mock-bin"
  mkdir -p "${TEST_REPO}/scripts" "${TEST_REPO}/runtime/logs" "${MOCK_BIN}"

  export REPO_ROOT="${TEST_REPO}"
  export AEO_SCRIPT="${REPO_ROOT}/scripts/aeo-controller.sh"

  cat > "${AEO_SCRIPT}" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || -z "${1:-}" ]]; then
  echo "Usage: aeo-controller.sh {start|stop|restart|status}"
  exit 0
fi
echo "not implemented: $1" >&2
exit 1
STUB
  chmod +x "${AEO_SCRIPT}"

  REAL_SCRIPT="${BATS_TEST_DIRNAME}/../../scripts/aeo-controller.sh"
  if [[ -f "${REAL_SCRIPT}" ]]; then
    cp "${REAL_SCRIPT}" "${AEO_SCRIPT}"
    chmod +x "${AEO_SCRIPT}"
  fi

  cp "${BATS_TEST_DIRNAME}/mocks/uv" "${MOCK_BIN}/uv" 2>/dev/null || true
  cp "${BATS_TEST_DIRNAME}/mocks/curl" "${MOCK_BIN}/curl" 2>/dev/null || true
  chmod +x "${MOCK_BIN}/uv" "${MOCK_BIN}/curl" 2>/dev/null || true
  export MOCK_UV_LOG="${TEST_REPO}/mock-uv.log"
  export PATH="${MOCK_BIN}:${PATH}"
}

teardown() {
  if [[ -f "${TEST_REPO}/runtime/controller.pid" ]]; then
    pid="$(cat "${TEST_REPO}/runtime/controller.pid" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  fi
  rm -rf "${TEST_REPO}"
}

write_env() {
  cat > "${TEST_REPO}/.env" <<EOF
AEO_HOST=127.0.0.1
AEO_PORT=7380
AEO_SHARED_ROOT=runtime
AEO_AUTH_TOKEN=test-token
AEO_SSH_CONFIG=~/.ssh/config
EOF
}

run_aeo() {
  (cd "${TEST_REPO}" && AEO_CONTROLLER_TEST_MODE=1 bash "${AEO_SCRIPT}" "$@")
}
