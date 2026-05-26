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
