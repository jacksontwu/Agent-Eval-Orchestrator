#!/usr/bin/env bats

load test_helper

@test "start writes pid file in test mode" {
  write_env
  run run_aeo start
  [[ "$status" -eq 0 ]]
  [[ -f "${TEST_REPO}/runtime/controller.pid" ]]
  pid="$(cat "${TEST_REPO}/runtime/controller.pid")"
  kill -0 "${pid}"
}

@test "second start fails while running" {
  write_env
  run_aeo start
  run run_aeo start
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"already running"* ]]
}

@test "stop removes pid file and exits cleanly" {
  write_env
  run_aeo start
  run run_aeo stop
  [[ "$status" -eq 0 ]]
  [[ ! -f "${TEST_REPO}/runtime/controller.pid" ]]
}

@test "stop when already stopped exits zero" {
  write_env
  run run_aeo stop
  [[ "$status" -eq 0 ]]
}

@test "status reports running when healthy" {
  write_env
  run_aeo start
  run run_aeo status
  [[ "$status" -eq 0 ]]
  [[ "$output" == *"running"* ]]
  [[ "$output" == *"7380"* ]]
}

@test "status exits non-zero when stopped" {
  write_env
  run run_aeo status
  [[ "$status" -ne 0 ]]
  [[ "$output" == *"stopped"* ]]
}

@test "restart stops then starts" {
  write_env
  run_aeo start
  run run_aeo restart
  [[ "$status" -eq 0 ]]
  run run_aeo status
  [[ "$status" -eq 0 ]]
}
