#!/usr/bin/env bats

load test_helper

@test "help prints usage" {
  run bash "${AEO_SCRIPT}" --help
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"start"* ]]
  [[ "$output" == *"status"* ]]
}

@test ".env.example exists in repo root" {
  [[ -f "${BATS_TEST_DIRNAME}/../../.env.example" ]]
}

@test "unknown subcommand exits non-zero" {
  write_env
  run run_aeo explode
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"unknown subcommand"* ]]
}

@test "start fails when .env is missing" {
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *".env.example"* ]]
}

@test "start fails when AEO_AUTH_TOKEN is empty" {
  cat > "${TEST_REPO}/.env" <<'EOF'
AEO_SHARED_ROOT=runtime
AEO_AUTH_TOKEN=
EOF
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"AEO_AUTH_TOKEN"* ]]
}

@test "start fails when AEO_SHARED_ROOT is empty" {
  cat > "${TEST_REPO}/.env" <<'EOF'
AEO_AUTH_TOKEN=tok
AEO_SHARED_ROOT=
EOF
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"AEO_SHARED_ROOT"* ]]
}
