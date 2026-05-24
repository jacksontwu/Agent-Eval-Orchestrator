# Huawei ECS Worker Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `scripts/bootstrap-huawei-worker.sh`, a root-run bootstrap script that prepares a fresh Ubuntu 22.04 amd64 Huawei ECS instance as an Agent Eval Orchestrator worker host up to (but not including) worker daemon startup.

**Architecture:** One entry script orchestrates two phases. Shared bash libraries under `scripts/lib/` hold testable functions (backup, confirm, config generation, idempotent clone/skip logic). Root phase handles system security, apt packages, Docker CE from Huawei mirror, and SSH hardening. `djn` phase installs uv, clones two repos under `/home/djn/worker`, and runs `uv sync` / harbor verification. Bats tests exercise pure logic without root; ECS manual checklist covers integration acceptance.

**Tech Stack:** Bash, apt, Docker CE (Huawei mirror), uv, git, Bats, ShellCheck

---

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/bootstrap-huawei-worker.sh` | CLI parsing, phase orchestration, exit codes |
| `scripts/lib/common.sh` | Logging, `die`, `confirm`, `backup_file`, `run_as_djn`, `require_root` |
| `scripts/lib/preflight.sh` | OS 22.04, amd64, authorized_keys, apt availability checks |
| `scripts/lib/user.sh` | Create/reuse `djn`, password (interactive / `DJN_PASSWORD`), SSH key copy |
| `scripts/lib/ssh.sh` | Hardening drop-in, `50-cloud-init.conf` patch, `sshd -t`, restart |
| `scripts/lib/packages.sh` | Base apt package list and install function |
| `scripts/lib/docker.sh` | Remove legacy packages, Huawei apt repo, CE install, `daemon.json`, verify |
| `scripts/lib/djn-phase.sh` | `/home/djn/worker` layout, uv install/skip, git clone/skip, `uv sync`, harbor help |
| `scripts/lib/output.sh` | Success summary and later daemon startup hint |
| `tests/bootstrap/test_helper.bash` | Bats setup, temp dirs, helper to source libs |
| `tests/bootstrap/common.bats` | Tests for `backup_file`, `confirm`, timestamp format |
| `tests/bootstrap/cli.bats` | Tests for `--yes` and `DJN_PASSWORD` validation |
| `tests/bootstrap/preflight.bats` | Tests for OS/arch checks with mocked `/etc/os-release` |
| `tests/bootstrap/user.bats` | Tests for SSH authorized_keys copy + permissions |
| `tests/bootstrap/ssh.bats` | Tests for hardening file content and cloud-init patch |
| `tests/bootstrap/docker.bats` | Tests for `daemon.json` content |
| `tests/bootstrap/djn-phase.bats` | Tests for clone skip-if-exists and worker layout paths |
| `Makefile` | `make test-bootstrap`, `make shellcheck-bootstrap` targets |

---

### Task 1: Bats test harness and Makefile targets

**Files:**
- Create: `tests/bootstrap/test_helper.bash`
- Create: `Makefile`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/common.bats`:

```bash
#!/usr/bin/env bats

load test_helper

@test "test_helper loads common library" {
  type backup_file >& /dev/null
  [[ $? -eq 0 ]]
}
```

Create `tests/bootstrap/test_helper.bash`:

```bash
#!/usr/bin/env bash

setup() {
  TEST_TEMP="${BATS_TEST_TMPDIR}/bootstrap-$$"
  mkdir -p "${TEST_TEMP}"
  export REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  # shellcheck source=scripts/lib/common.sh
  source "${REPO_ROOT}/scripts/lib/common.sh"
}

teardown() {
  rm -rf "${TEST_TEMP}"
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/djn/code/Agent-Eval-Orchestrator && bats tests/bootstrap/common.bats -v`
Expected: FAIL — `scripts/lib/common.sh: No such file or directory` or `backup_file: not found`

- [ ] **Step 3: Write minimal implementation**

Create stub `scripts/lib/common.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

backup_file() {
  :
}
```

Add `Makefile`:

```makefile
.PHONY: test-bootstrap shellcheck-bootstrap

test-bootstrap:
	bats tests/bootstrap/

shellcheck-bootstrap:
	shellcheck scripts/bootstrap-huawei-worker.sh scripts/lib/*.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test-bootstrap`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add tests/bootstrap/test_helper.bash tests/bootstrap/common.bats scripts/lib/common.sh Makefile
git commit -m "test: add bats harness for huawei worker bootstrap"
```

---

### Task 2: `backup_file` with timestamped backups

**Files:**
- Modify: `scripts/lib/common.sh`
- Modify: `tests/bootstrap/common.bats`

- [ ] **Step 1: Write the failing test**

Append to `tests/bootstrap/common.bats`:

```bash
@test "backup_file creates timestamped backup without overwriting prior backup" {
  local src="${TEST_TEMP}/config.conf"
  echo "original" > "${src}"

  backup_file "${src}"
  local first_backup
  first_backup="$(ls "${TEST_TEMP}"/config.conf.bak.* | head -1)"
  [[ -f "${first_backup}" ]]
  grep -q "original" "${first_backup}"

  echo "updated" > "${src}"
  sleep 1
  backup_file "${src}"

  local backup_count
  backup_count="$(ls "${TEST_TEMP}"/config.conf.bak.* | wc -l)"
  [[ "${backup_count}" -eq 2 ]]
}

@test "backup_file is no-op when source missing" {
  run backup_file "${TEST_TEMP}/missing.conf"
  [[ "${status}" -eq 0 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/common.bats -v`
Expected: FAIL — backup files not created

- [ ] **Step 3: Write minimal implementation**

Replace `backup_file` in `scripts/lib/common.sh`:

```bash
log() {
  printf '[bootstrap] %s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

timestamp_suffix() {
  date +%Y%m%d-%H%M%S
}

backup_file() {
  local path="$1"
  [[ -e "${path}" ]] || return 0
  local backup="${path}.bak.$(timestamp_suffix)"
  cp -a "${path}" "${backup}"
  log "Backed up ${path} -> ${backup}"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/common.bats -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/common.sh tests/bootstrap/common.bats
git commit -m "feat(bootstrap): add timestamped backup_file helper"
```

---

### Task 3: CLI parsing (`--yes`) and `DJN_PASSWORD` guard

**Files:**
- Create: `scripts/lib/cli.sh`
- Create: `tests/bootstrap/cli.bats`
- Modify: `tests/bootstrap/test_helper.bash`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/cli.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/cli.sh
  source "${REPO_ROOT}/scripts/lib/cli.sh"
}

@test "parse_bootstrap_args sets INTERACTIVE=yes by default" {
  INTERACTIVE=yes
  parse_bootstrap_args
  [[ "${INTERACTIVE}" == "yes" ]]
}

@test "parse_bootstrap_args --yes disables prompts" {
  INTERACTIVE=yes
  parse_bootstrap_args --yes
  [[ "${INTERACTIVE}" == "no" ]]
}

@test "require_djn_password_for_noninteractive fails when empty" {
  INTERACTIVE=no
  DJN_PASSWORD=""
  run require_djn_password_for_noninteractive
  [[ "${status}" -eq 1 ]]
  [[ "${output}" == *"DJN_PASSWORD"* ]]
}

@test "require_djn_password_for_noninteractive passes when set" {
  INTERACTIVE=no
  DJN_PASSWORD="secret"
  run require_djn_password_for_noninteractive
  [[ "${status}" -eq 0 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/cli.bats -v`
Expected: FAIL — `parse_bootstrap_args: command not found`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/cli.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

INTERACTIVE="${INTERACTIVE:-yes}"

parse_bootstrap_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes)
        INTERACTIVE="no"
        shift
        ;;
      -h|--help)
        cat <<'EOF'
Usage: bash scripts/bootstrap-huawei-worker.sh [--yes]

  --yes   Non-interactive mode. Requires DJN_PASSWORD when setting djn password.
EOF
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done
}

require_djn_password_for_noninteractive() {
  if [[ "${INTERACTIVE}" == "no" && -z "${DJN_PASSWORD:-}" ]]; then
    die "DJN_PASSWORD must be set in --yes mode when djn password is required"
  fi
}
```

Update `tests/bootstrap/test_helper.bash` to also source `cli.sh` when needed (cli.bats uses `setup_file`).

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/cli.bats -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/cli.sh tests/bootstrap/cli.bats tests/bootstrap/test_helper.bash
git commit -m "feat(bootstrap): add --yes CLI parsing and DJN_PASSWORD guard"
```

---

### Task 4: Interactive `confirm` helper

**Files:**
- Modify: `scripts/lib/common.sh`
- Modify: `tests/bootstrap/common.bats`

- [ ] **Step 1: Write the failing test**

Append to `tests/bootstrap/common.bats`:

```bash
@test "confirm skips prompt when INTERACTIVE=no" {
  INTERACTIVE=no
  run confirm "Proceed?"
  [[ "${status}" -eq 0 ]]
  [[ -z "${output}" ]]
}

@test "confirm accepts yes in interactive mode" {
  INTERACTIVE=yes
  run confirm "Proceed?" <<< "y"
  [[ "${status}" -eq 0 ]]
}

@test "confirm rejects no in interactive mode" {
  INTERACTIVE=yes
  run confirm "Proceed?" <<< "n"
  [[ "${status}" -eq 1 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/common.bats -v -f confirm`
Expected: FAIL — `confirm: command not found`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/lib/common.sh`:

```bash
confirm() {
  local prompt="$1"
  if [[ "${INTERACTIVE:-yes}" == "no" ]]; then
    return 0
  fi
  read -r -p "${prompt} [y/N] " reply
  case "${reply}" in
    y|Y|yes|YES) return 0 ;;
    *) die "Aborted by operator" ;;
  esac
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/common.bats -v -f confirm`
Expected: PASS (3 confirm tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/common.sh tests/bootstrap/common.bats
git commit -m "feat(bootstrap): add interactive confirm helper"
```

---

### Task 5: Preflight checks (Ubuntu 22.04, amd64, authorized_keys)

**Files:**
- Create: `scripts/lib/preflight.sh`
- Create: `tests/bootstrap/preflight.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/preflight.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/preflight.sh
  source "${REPO_ROOT}/scripts/lib/preflight.sh"
}

@test "assert_ubuntu_2204 accepts mocked 22.04 os-release" {
  local fake_os="${TEST_TEMP}/os-release"
  cat > "${fake_os}" <<'EOF'
ID=ubuntu
VERSION_ID="22.04"
EOF
  OS_RELEASE_FILE="${fake_os}"
  run assert_ubuntu_2204
  [[ "${status}" -eq 0 ]]
}

@test "assert_ubuntu_2204 rejects non-ubuntu" {
  local fake_os="${TEST_TEMP}/os-release-bad"
  echo 'ID=debian' > "${fake_os}"
  OS_RELEASE_FILE="${fake_os}"
  run assert_ubuntu_2204
  [[ "${status}" -eq 1 ]]
}

@test "assert_amd64 accepts x86_64" {
  UNAME_M="x86_64"
  run assert_amd64
  [[ "${status}" -eq 0 ]]
}

@test "assert_amd64 rejects arm64" {
  UNAME_M="aarch64"
  run assert_amd64
  [[ "${status}" -eq 1 ]]
}

@test "assert_root_authorized_keys requires non-empty file" {
  local keys="${TEST_TEMP}/authorized_keys"
  touch "${keys}"
  ROOT_AUTHORIZED_KEYS="${keys}"
  run assert_root_authorized_keys
  [[ "${status}" -eq 1 ]]

  echo "ssh-ed25519 AAAA test" > "${keys}"
  run assert_root_authorized_keys
  [[ "${status}" -eq 0 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/preflight.bats -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/preflight.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
ROOT_AUTHORIZED_KEYS="${ROOT_AUTHORIZED_KEYS:-/root/.ssh/authorized_keys}"
UNAME_M="${UNAME_M:-$(uname -m)}"

assert_ubuntu_2204() {
  [[ -f "${OS_RELEASE_FILE}" ]] || die "missing ${OS_RELEASE_FILE}"
  # shellcheck disable=SC1090
  source "${OS_RELEASE_FILE}"
  [[ "${ID:-}" == "ubuntu" ]] || die "expected Ubuntu, got ${ID:-unknown}"
  [[ "${VERSION_ID:-}" == "22.04" ]] || die "expected Ubuntu 22.04, got ${VERSION_ID:-unknown}"
}

assert_amd64() {
  [[ "${UNAME_M}" == "x86_64" ]] || die "expected amd64/x86_64, got ${UNAME_M}"
}

assert_root_authorized_keys() {
  [[ -s "${ROOT_AUTHORIZED_KEYS}" ]] || die "missing or empty ${ROOT_AUTHORIZED_KEYS}"
}

assert_apt_available() {
  command -v apt-get >/dev/null 2>&1 || die "apt-get not available"
}

run_preflight_checks() {
  require_root
  assert_ubuntu_2204
  assert_amd64
  assert_root_authorized_keys
  assert_apt_available
  log "Preflight checks passed"
}
```

Add to `scripts/lib/common.sh`:

```bash
require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "must run as root"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/preflight.bats -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/preflight.sh scripts/lib/common.sh tests/bootstrap/preflight.bats
git commit -m "feat(bootstrap): add preflight checks for ubuntu 22.04 and amd64"
```

---

### Task 6: `djn` user creation and password handling

**Files:**
- Create: `scripts/lib/user.sh`
- Create: `tests/bootstrap/user.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/user.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/user.sh
  source "${REPO_ROOT}/scripts/lib/user.sh"
}

@test "ensure_djn_user adds user when missing" {
  id djn >/dev/null 2>&1 && skip "djn already exists on host"
  run ensure_djn_user
  [[ "${status}" -eq 0 ]]
  id djn
  id -nG djn | grep -qw sudo
}

@test "copy_root_keys_to_djn sets permissions and backs up existing" {
  local djn_home="${TEST_TEMP}/djn-home"
  local root_keys="${TEST_TEMP}/root-keys"
  mkdir -p "${djn_home}/.ssh"
  echo "old-key" > "${djn_home}/.ssh/authorized_keys"
  echo "ssh-ed25519 AAAA root" > "${root_keys}"

  DJN_HOME="${djn_home}"
  ROOT_AUTHORIZED_KEYS="${root_keys}"
  DJN_USER="djn"

  run copy_root_keys_to_djn
  [[ "${status}" -eq 0 ]]
  [[ -f "${djn_home}/.ssh/authorized_keys.bak."* ]]
  grep -q "ssh-ed25519 AAAA root" "${djn_home}/.ssh/authorized_keys"
  [[ "$(stat -c '%a' "${djn_home}/.ssh")" == "700" ]]
  [[ "$(stat -c '%a' "${djn_home}/.ssh/authorized_keys")" == "600" ]]
}
```

Note: first test skips on hosts that already have `djn`; second test uses temp dirs and does not need root.

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/user.bats -v -f "copy_root_keys"`
Expected: FAIL — `copy_root_keys_to_djn: command not found`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/user.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

DJN_USER="${DJN_USER:-djn}"
DJN_HOME="${DJN_HOME:-/home/djn}"
ROOT_AUTHORIZED_KEYS="${ROOT_AUTHORIZED_KEYS:-/root/.ssh/authorized_keys}"

ensure_djn_user() {
  if id "${DJN_USER}" >/dev/null 2>&1; then
    log "User ${DJN_USER} already exists"
    return 0
  fi
  confirm "Create user ${DJN_USER} with sudo group membership?"
  useradd -m -s /bin/bash "${DJN_USER}"
  usermod -aG sudo "${DJN_USER}"
  log "Created user ${DJN_USER}"
}

set_djn_password() {
  if ! id "${DJN_USER}" >/dev/null 2>&1; then
    die "cannot set password: ${DJN_USER} missing"
  fi
  confirm "Set password for ${DJN_USER}?"
  if [[ "${INTERACTIVE:-yes}" == "no" ]]; then
    require_djn_password_for_noninteractive
    echo "${DJN_USER}:${DJN_PASSWORD}" | chpasswd
    log "Set ${DJN_USER} password from DJN_PASSWORD"
  else
    passwd "${DJN_USER}"
  fi
}

copy_root_keys_to_djn() {
  confirm "Copy root authorized_keys to ${DJN_USER}?"
  install -d -m 700 -o "${DJN_USER}" -g "${DJN_USER}" "${DJN_HOME}/.ssh"
  local dest="${DJN_HOME}/.ssh/authorized_keys"
  if [[ -e "${dest}" ]]; then
    backup_file "${dest}"
  fi
  install -m 600 -o "${DJN_USER}" -g "${DJN_USER}" "${ROOT_AUTHORIZED_KEYS}" "${dest}"
  log "Installed ${DJN_USER} authorized_keys"
}

setup_djn_account() {
  ensure_djn_user
  set_djn_password
  copy_root_keys_to_djn
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/user.bats -v -f "copy_root_keys"`
Expected: PASS (1 test; user-creation test may skip locally)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/user.sh tests/bootstrap/user.bats
git commit -m "feat(bootstrap): add djn user and ssh key setup"
```

---

### Task 7: SSH hardening drop-in and cloud-init patch

**Files:**
- Create: `scripts/lib/ssh.sh`
- Create: `tests/bootstrap/ssh.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/ssh.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/ssh.sh
  source "${REPO_ROOT}/scripts/lib/ssh.sh"
}

@test "render_hardening_conf contains required directives" {
  run render_hardening_conf
  [[ "${status}" -eq 0 ]]
  [[ "${output}" == *"PermitRootLogin no"* ]]
  [[ "${output}" == *"PasswordAuthentication no"* ]]
  [[ "${output}" == *"PubkeyAuthentication yes"* ]]
}

@test "patch_cloud_init_password_auth disables password auth with backup" {
  local conf="${TEST_TEMP}/50-cloud-init.conf"
  echo "PasswordAuthentication yes" > "${conf}"
  CLOUD_INIT_SSH_CONF="${conf}"

  patch_cloud_init_password_auth
  [[ -f "${conf}.bak."* ]]
  grep -q "PasswordAuthentication no" "${conf}"
}

@test "write_hardening_files writes drop-in under temp dir" {
  SSH_HARDENING_DIR="${TEST_TEMP}/sshd_config.d"
  mkdir -p "${SSH_HARDENING_DIR}"
  SSH_HARDENING_FILE="${SSH_HARDENING_DIR}/99-agent-eval-worker-hardening.conf"

  write_hardening_dropin
  [[ -f "${SSH_HARDENING_FILE}" ]]
  grep -q "PermitRootLogin no" "${SSH_HARDENING_FILE}"
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/ssh.bats -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/ssh.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SSH_HARDENING_DIR="${SSH_HARDENING_DIR:-/etc/ssh/sshd_config.d}"
SSH_HARDENING_FILE="${SSH_HARDENING_FILE:-${SSH_HARDENING_DIR}/99-agent-eval-worker-hardening.conf}"
CLOUD_INIT_SSH_CONF="${CLOUD_INIT_SSH_CONF:-/etc/ssh/sshd_config.d/50-cloud-init.conf}"

render_hardening_conf() {
  cat <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
EOF
}

patch_cloud_init_password_auth() {
  [[ -f "${CLOUD_INIT_SSH_CONF}" ]] || return 0
  backup_file "${CLOUD_INIT_SSH_CONF}"
  sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' "${CLOUD_INIT_SSH_CONF}"
  if ! grep -q '^PasswordAuthentication no' "${CLOUD_INIT_SSH_CONF}"; then
    echo 'PasswordAuthentication no' >> "${CLOUD_INIT_SSH_CONF}"
  fi
  log "Patched ${CLOUD_INIT_SSH_CONF}"
}

write_hardening_dropin() {
  if [[ -e "${SSH_HARDENING_FILE}" ]]; then
    backup_file "${SSH_HARDENING_FILE}"
  fi
  render_hardening_conf > "${SSH_HARDENING_FILE}"
  chmod 644 "${SSH_HARDENING_FILE}"
  log "Wrote ${SSH_HARDENING_FILE}"
}

apply_ssh_hardening() {
  confirm "Apply SSH hardening and restart sshd?"
  patch_cloud_init_password_auth
  write_hardening_dropin
  sshd -t
  systemctl restart ssh || systemctl restart sshd
  log "SSH hardening applied"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/ssh.bats -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/ssh.sh tests/bootstrap/ssh.bats
git commit -m "feat(bootstrap): add SSH hardening drop-in and cloud-init patch"
```

---

### Task 8: Base apt packages

**Files:**
- Create: `scripts/lib/packages.sh`
- Create: `tests/bootstrap/packages.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/packages.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/packages.sh
  source "${REPO_ROOT}/scripts/lib/packages.sh"
}

@test "BASE_PACKAGES includes required packages" {
  for pkg in apt-transport-https ca-certificates curl gnupg2 \
             software-properties-common git lsb-release sudo; do
    [[ " ${BASE_PACKAGES[*]} " == *" ${pkg} "* ]]
  done
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/packages.bats -v`
Expected: FAIL — `BASE_PACKAGES: unbound variable`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/packages.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE_PACKAGES=(
  apt-transport-https
  ca-certificates
  curl
  gnupg2
  software-properties-common
  git
  lsb-release
  sudo
)

install_base_packages() {
  confirm "Install base apt packages?"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y "${BASE_PACKAGES[@]}"
  log "Installed base packages"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/packages.bats -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/packages.sh tests/bootstrap/packages.bats
git commit -m "feat(bootstrap): add base apt package list and installer"
```

---

### Task 9: Docker CE install and Huawei SWR mirror config

**Files:**
- Create: `scripts/lib/docker.sh`
- Create: `tests/bootstrap/docker.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/docker.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/docker.sh
  source "${REPO_ROOT}/scripts/lib/docker.sh"
}

@test "render_daemon_json includes Huawei SWR mirror" {
  run render_daemon_json
  [[ "${status}" -eq 0 ]]
  [[ "${output}" == *"6bc9e025405d418487910921d203eb49.mirror.swr.myhuaweicloud.com"* ]]
}

@test "write_daemon_json backs up existing file" {
  local file="${TEST_TEMP}/daemon.json"
  echo '{"old":true}' > "${file}"
  DOCKER_DAEMON_JSON="${file}"

  write_daemon_json
  [[ -f "${file}.bak."* ]]
  grep -q "registry-mirrors" "${file}"
}

@test "docker_apt_repo_url points at Huawei mirror" {
  [[ "${DOCKER_APT_REPO_URL}" == "https://mirrors.huaweicloud.com/docker-ce/linux/ubuntu" ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/docker.bats -v`
Expected: FAIL — functions/constants not defined

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/docker.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

DOCKER_APT_REPO_URL="https://mirrors.huaweicloud.com/docker-ce/linux/ubuntu"
DOCKER_DAEMON_JSON="${DOCKER_DAEMON_JSON:-/etc/docker/daemon.json}"
LEGACY_DOCKER_PACKAGES=(docker docker-engine docker.io containerd runc)
DOCKER_CE_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
SWR_MIRROR="https://6bc9e025405d418487910921d203eb49.mirror.swr.myhuaweicloud.com"

render_daemon_json() {
  cat <<EOF
{
  "registry-mirrors": [
    "${SWR_MIRROR}"
  ]
}
EOF
}

write_daemon_json() {
  if [[ -e "${DOCKER_DAEMON_JSON}" ]]; then
    backup_file "${DOCKER_DAEMON_JSON}"
  fi
  install -d -m 755 "$(dirname "${DOCKER_DAEMON_JSON}")"
  render_daemon_json > "${DOCKER_DAEMON_JSON}"
  chmod 644 "${DOCKER_DAEMON_JSON}"
  log "Wrote ${DOCKER_DAEMON_JSON}"
}

remove_legacy_docker_packages() {
  confirm "Remove legacy Docker packages if present?"
  apt-get remove -y "${LEGACY_DOCKER_PACKAGES[@]}" 2>/dev/null || true
}

install_docker_ce_from_huawei() {
  confirm "Install Docker CE from Huawei mirror?"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "${DOCKER_APT_REPO_URL}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] ${DOCKER_APT_REPO_URL} \
    $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y "${DOCKER_CE_PACKAGES[@]}"
  log "Installed Docker CE"
}

configure_docker_service() {
  confirm "Write Docker daemon.json and restart Docker?"
  write_daemon_json
  usermod -aG docker "${DJN_USER:-djn}"
  systemctl enable --now docker
  systemctl restart docker
}

verify_docker_installation() {
  docker version >/dev/null
  docker compose version >/dev/null
  docker info | grep -F "${SWR_MIRROR}" >/dev/null
  log "Docker verification passed"
}

setup_docker() {
  remove_legacy_docker_packages
  install_docker_ce_from_huawei
  configure_docker_service
  verify_docker_installation
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/docker.bats -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/docker.sh tests/bootstrap/docker.bats
git commit -m "feat(bootstrap): add Docker CE install and SWR mirror config"
```

---

### Task 10: `djn` phase — worker layout, uv, git clones, harbor verify

**Files:**
- Create: `scripts/lib/djn-phase.sh`
- Create: `tests/bootstrap/djn-phase.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/djn-phase.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/djn-phase.sh
  source "${REPO_ROOT}/scripts/lib/djn-phase.sh"
}

@test "worker paths match controller inference layout" {
  [[ "${WORKER_ROOT}" == "/home/djn/worker" ]]
  [[ "${AEO_REPO_DIR}" == "/home/djn/worker/agent-eval-orchestrator" ]]
  [[ "${HARBOR_REPO_DIR}" == "/home/djn/worker/harbor" ]]
}

@test "clone_repo_if_missing skips existing directory" {
  local repo="${TEST_TEMP}/existing-repo"
  mkdir -p "${repo}"
  echo "keep" > "${repo}/marker.txt"

  run clone_repo_if_missing "https://example.com/repo.git" "${repo}"
  [[ "${status}" -eq 0 ]]
  [[ "$(cat "${repo}/marker.txt")" == "keep" ]]
}

@test "clone_repo_if_missing clones when absent" {
  local repo="${TEST_TEMP}/new-repo"
  GIT="${REPO_ROOT}/tests/bootstrap/fake-git.sh"
  run clone_repo_if_missing "https://example.com/repo.git" "${repo}"
  [[ "${status}" -eq 0 ]]
  [[ -d "${repo}" ]]
}
```

Create `tests/bootstrap/fake-git.sh` for the clone test:

```bash
#!/usr/bin/env bash
set -euo pipefail
dest="$2"
mkdir -p "${dest}"
echo "cloned" > "${dest}/.cloned"
```

Make executable: `chmod +x tests/bootstrap/fake-git.sh`

In `djn-phase.sh`, allow override: `GIT="${GIT:-git}"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/djn-phase.bats -v`
Expected: FAIL — constants/functions not defined

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/djn-phase.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

DJN_USER="${DJN_USER:-djn}"
UV_BIN="${UV_BIN:-/home/djn/.local/bin/uv}"
WORKER_ROOT="${WORKER_ROOT:-/home/djn/worker}"
AEO_REPO_DIR="${AEO_REPO_DIR:-${WORKER_ROOT}/agent-eval-orchestrator}"
HARBOR_REPO_DIR="${HARBOR_REPO_DIR:-${WORKER_ROOT}/harbor}"
AEO_REPO_URL="https://github.com/jacksontwu/Agent-Eval-Orchestrator.git"
HARBOR_REPO_URL="https://github.com/JinnanDuan/bitfun-harbor.git"
GIT="${GIT:-git}"

run_as_djn() {
  sudo -u "${DJN_USER}" -H bash -lc "$*"
}

ensure_worker_root() {
  confirm "Create worker project root at ${WORKER_ROOT}?"
  install -d -m 755 -o "${DJN_USER}" -g "${DJN_USER}" "${WORKER_ROOT}"
}

clone_repo_if_missing() {
  local url="$1"
  local dest="$2"
  if [[ -d "${dest}" ]]; then
    log "Skip clone; directory exists: ${dest}"
    return 0
  fi
  confirm "Clone ${url} -> ${dest}?"
  run_as_djn "${GIT} clone ${url@Q} ${dest@Q}"
}

install_uv_for_djn() {
  if run_as_djn "test -x ${UV_BIN@Q}"; then
    log "uv already installed at ${UV_BIN}"
  else
    confirm "Install uv for ${DJN_USER}?"
    run_as_djn 'curl -LsSf https://astral.sh/uv/install.sh | sh'
  fi
  run_as_djn "${UV_BIN@Q} --version"
}

sync_agent_eval_orchestrator() {
  confirm "Run uv sync in ${AEO_REPO_DIR}?"
  run_as_djn "cd ${AEO_REPO_DIR@Q} && ${UV_BIN@Q} sync"
}

verify_harbor_cli() {
  confirm "Verify harbor CLI in ${HARBOR_REPO_DIR}?"
  run_as_djn "cd ${HARBOR_REPO_DIR@Q} && ${UV_BIN@Q} run harbor --help"
}

run_djn_phase() {
  ensure_worker_root
  install_uv_for_djn
  clone_repo_if_missing "${AEO_REPO_URL}" "${AEO_REPO_DIR}"
  sync_agent_eval_orchestrator
  clone_repo_if_missing "${HARBOR_REPO_URL}" "${HARBOR_REPO_DIR}"
  verify_harbor_cli
  log "djn phase completed"
}
```

Add `run_as_djn` to tests by exporting `DJN_USER` as current user in clone test:

In `djn-phase.bats` third test, prepend:

```bash
export DJN_USER="$(id -un)"
export WORKER_ROOT="${TEST_TEMP}/worker"
export AEO_REPO_DIR="${WORKER_ROOT}/agent-eval-orchestrator"
export HARBOR_REPO_DIR="${WORKER_ROOT}/harbor"
export UV_BIN="$(command -v uv || echo /home/djn/.local/bin/uv)"
export GIT="${REPO_ROOT}/tests/bootstrap/fake-git.sh"
INTERACTIVE=no
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/djn-phase.bats -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/djn-phase.sh tests/bootstrap/djn-phase.bats tests/bootstrap/fake-git.sh
git commit -m "feat(bootstrap): add djn phase for uv, clones, and harbor verify"
```

---

### Task 11: Success output and daemon startup hint

**Files:**
- Create: `scripts/lib/output.sh`
- Create: `tests/bootstrap/output.bats`

- [ ] **Step 1: Write the failing test**

Create `tests/bootstrap/output.bats`:

```bash
#!/usr/bin/env bats

load test_helper

setup_file() {
  # shellcheck source=scripts/lib/output.sh
  source "${REPO_ROOT}/scripts/lib/output.sh"
}

@test "print_success_summary mentions paths and non-goals" {
  run print_success_summary
  [[ "${status}" -eq 0 ]]
  [[ "${output}" == *"Worker preflight completed."* ]]
  [[ "${output}" == *"/home/djn/worker/agent-eval-orchestrator"* ]]
  [[ "${output}" == *"/home/djn/worker/harbor"* ]]
  [[ "${output}" == *"worker daemon startup"* ]]
  [[ "${output}" == *"datasets"* ]]
}

@test "print_daemon_startup_hint uses placeholders not secrets" {
  run print_daemon_startup_hint
  [[ "${status}" -eq 0 ]]
  [[ "${output}" == *"<CONTROL_HOST>"* ]]
  [[ "${output}" == *"<WORKER_ID>"* ]]
  [[ "${output}" == *"<AEO_TOKEN>"* ]]
  [[ "${output}" != *"secret"* ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/output.bats -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Write minimal implementation**

Create `scripts/lib/output.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

print_success_summary() {
  cat <<'EOF'
Worker preflight completed.

Login user:
  djn

Project root:
  /home/djn/worker

Agent Eval Orchestrator:
  /home/djn/worker/agent-eval-orchestrator

Harbor:
  /home/djn/worker/harbor

Not prepared by this script:
  datasets
  /home/djn/bitfun-cli
  /home/djn/.config/bitfun
  worker daemon startup
EOF
}

print_daemon_startup_hint() {
  cat <<'EOF'
Later worker startup example:

cd /home/djn/worker/agent-eval-orchestrator
/home/djn/.local/bin/uv run python -u -m agent_eval_orchestrator.worker.daemon \
  --controller-url http://<CONTROL_HOST>:7380 \
  --worker-id <WORKER_ID> \
  --display-name <WORKER_ID> \
  --host <WORKER_HOST> \
  --shared-root /home/djn/worker/agent-eval-orchestrator/runtime \
  --local-root /home/djn/worker/agent-eval-orchestrator/runtime/workers/<WORKER_ID>/local \
  --slots 1 \
  --poll-interval 3 \
  --auth-token '<AEO_TOKEN>'
EOF
}

print_bootstrap_success() {
  print_success_summary
  echo
  print_daemon_startup_hint
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/output.bats -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/output.sh tests/bootstrap/output.bats
git commit -m "feat(bootstrap): add success summary and daemon startup hint"
```

---

### Task 12: Main entry script wiring

**Files:**
- Create: `scripts/bootstrap-huawei-worker.sh`
- Modify: `scripts/lib/common.sh` (source chain helper)

- [ ] **Step 1: Write the failing test**

Append to `tests/bootstrap/common.bats`:

```bash
@test "bootstrap entry script exists and is executable" {
  [[ -x "${REPO_ROOT}/scripts/bootstrap-huawei-worker.sh" ]]
}

@test "bootstrap --help exits zero" {
  run bash "${REPO_ROOT}/scripts/bootstrap-huawei-worker.sh" --help
  [[ "${status}" -eq 0 ]]
  [[ "${output}" == *"--yes"* ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/common.bats -v -f bootstrap`
Expected: FAIL — script missing or not executable

- [ ] **Step 3: Write minimal implementation**

Create `scripts/bootstrap-huawei-worker.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=scripts/lib/cli.sh
source "${SCRIPT_DIR}/lib/cli.sh"
# shellcheck source=scripts/lib/preflight.sh
source "${SCRIPT_DIR}/lib/preflight.sh"
# shellcheck source=scripts/lib/user.sh
source "${SCRIPT_DIR}/lib/user.sh"
# shellcheck source=scripts/lib/ssh.sh
source "${SCRIPT_DIR}/lib/ssh.sh"
# shellcheck source=scripts/lib/packages.sh
source "${SCRIPT_DIR}/lib/packages.sh"
# shellcheck source=scripts/lib/docker.sh
source "${SCRIPT_DIR}/lib/docker.sh"
# shellcheck source=scripts/lib/djn-phase.sh
source "${SCRIPT_DIR}/lib/djn-phase.sh"
# shellcheck source=scripts/lib/output.sh
source "${SCRIPT_DIR}/lib/output.sh"

main() {
  parse_bootstrap_args "$@"
  run_preflight_checks
  setup_djn_account
  apply_ssh_hardening
  install_base_packages
  setup_docker
  run_djn_phase
  print_bootstrap_success
}

main "$@"
```

Make executable:

```bash
chmod +x scripts/bootstrap-huawei-worker.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats tests/bootstrap/common.bats -v -f bootstrap`
Expected: PASS (2 tests)

Run full suite: `make test-bootstrap`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/bootstrap-huawei-worker.sh
git commit -m "feat(bootstrap): wire huawei ECS worker bootstrap entry script"
```

---

### Task 13: ShellCheck and README documentation

**Files:**
- Modify: `README.md`
- Modify: `Makefile` (ensure shellcheck target works)

- [ ] **Step 1: Write the failing test**

Add `tests/bootstrap/lint.bats`:

```bash
#!/usr/bin/env bats

load test_helper

@test "shellcheck passes on bootstrap scripts" {
  if ! command -v shellcheck >/dev/null 2>&1; then
    skip "shellcheck not installed"
  fi
  run shellcheck scripts/bootstrap-huawei-worker.sh scripts/lib/*.sh
  [[ "${status}" -eq 0 ]]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats tests/bootstrap/lint.bats -v`
Expected: FAIL or skip if shellcheck missing; if installed, may FAIL on SC issues — fix them in Step 3.

- [ ] **Step 3: Write minimal implementation**

Fix any ShellCheck findings (typical: declare `INTERACTIVE` in `cli.sh`, quote variables, add `# shellcheck disable` only when necessary).

Append to `README.md` after **环境准备**:

```markdown
## Huawei ECS Worker Bootstrap

在新创建的 Ubuntu 22.04.5 LTS amd64 华为云 ECS 上以 root 运行：

```bash
bash scripts/bootstrap-huawei-worker.sh
```

非交互模式（需设置 `djn` 密码）：

```bash
DJN_PASSWORD='<password-for-djn>' bash scripts/bootstrap-huawei-worker.sh --yes
```

脚本会创建 `djn` 用户、加固 SSH、安装 Docker CE（华为镜像源 + SWR registry mirror）、安装 uv，并克隆：

- `/home/djn/worker/agent-eval-orchestrator`
- `/home/djn/worker/harbor`

脚本**不会**下载 datasets、安装 bitfun-cli、准备 `.config/bitfun`，也**不会**启动 worker daemon。

本地验证 bootstrap 单元测试：

```bash
make test-bootstrap
make shellcheck-bootstrap
```
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make shellcheck-bootstrap && make test-bootstrap`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md Makefile tests/bootstrap/lint.bats scripts/
git commit -m "docs: document huawei ECS worker bootstrap script and lint tests"
```

---

### Task 14: Manual ECS acceptance checklist

**Files:**
- Create: `docs/superpowers/plans/2026-05-24-huawei-ecs-worker-bootstrap-acceptance.md`

- [ ] **Step 1: Write acceptance checklist document**

Create `docs/superpowers/plans/2026-05-24-huawei-ecs-worker-bootstrap-acceptance.md`:

```markdown
# Huawei ECS Worker Bootstrap — Manual Acceptance

Run on a **fresh** Ubuntu 22.04.5 amd64 Huawei ECS as root.

## Pre-run

- [ ] `/root/.ssh/authorized_keys` contains at least one key
- [ ] Keep an open SSH session while testing SSH hardening

## Execute

```bash
DJN_PASSWORD='<set-strong-password>' bash scripts/bootstrap-huawei-worker.sh --yes
```

Or copy script to ECS from dev machine:

```bash
scp -r scripts/ root@<ECS_IP>:/tmp/bootstrap-scripts/
ssh root@<ECS_IP> 'bash /tmp/bootstrap-scripts/bootstrap-huawei-worker.sh --yes'
```

## Verify

- [ ] `ssh djn@<ECS_IP>` works with key (no password prompt for SSH)
- [ ] `ssh root@<ECS_IP>` is rejected
- [ ] `grep -r PasswordAuthentication /etc/ssh/sshd_config.d/` shows `no`
- [ ] `groups djn` includes `sudo` and `docker`
- [ ] `docker info | grep -F mirror.swr.myhuaweicloud.com` matches
- [ ] `/home/djn/.local/bin/uv --version` succeeds
- [ ] `test -d /home/djn/worker/agent-eval-orchestrator`
- [ ] `test -d /home/djn/worker/harbor`
- [ ] `cd /home/djn/worker/harbor && /home/djn/.local/bin/uv run harbor --help` succeeds
- [ ] Script output lists datasets / bitfun-cli / daemon as **not prepared**
- [ ] Re-run script: exits 0, skips existing clones, creates new `.bak.*` only when configs change

## Non-goals confirmed

- [ ] No `agent_eval_orchestrator.worker.daemon` process running
- [ ] No `/home/djn/bitfun-cli`
- [ ] No `/home/djn/.config/bitfun`
- [ ] No datasets under `agent-eval-orchestrator/datasets`
```

- [ ] **Step 2: Review against spec acceptance criteria**

Cross-check each bullet in spec **Acceptance Criteria** section — all mapped above.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-05-24-huawei-ecs-worker-bootstrap-acceptance.md
git commit -m "docs: add manual ECS acceptance checklist for worker bootstrap"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|------------------|------|
| Root-only entry, Ubuntu 22.04 amd64 preflight | Task 5, 12 |
| Create/configure `djn`, sudo, password modes | Task 6 |
| Copy root authorized_keys with backup | Task 6 |
| SSH hardening drop-in + 50-cloud-init patch + `sshd -t` | Task 7 |
| Timestamped backups | Task 2 |
| Base apt packages | Task 8 |
| Docker CE from Huawei mirror, legacy removal | Task 9 |
| SWR registry mirror in daemon.json | Task 9 |
| `/home/djn/worker` layout | Task 10 |
| uv install/skip | Task 10 |
| Clone AEO + `uv sync` | Task 10 |
| Clone harbor + `uv run harbor --help` | Task 10 |
| Interactive prompts / `--yes` | Tasks 3, 4 |
| Idempotent re-runs | Tasks 2, 6, 9, 10 |
| Success output + daemon hint | Task 11 |
| Non-goals (no daemon, datasets, bitfun-cli) | Task 11, 14 |

No gaps found.

### Placeholder scan

No TBD/TODO/implement-later placeholders in tasks. All code blocks are complete.

### Type consistency

- `INTERACTIVE` values: `yes` / `no` throughout
- Path constants: `/home/djn/worker/...` consistent across `djn-phase.sh` and `output.sh`
- `DJN_USER` default `djn` used in user, docker, djn-phase modules
- Controller harbor inference: `worker_root.parent / "harbor"` matches `/home/djn/worker/harbor` when repo is `/home/djn/worker/agent-eval-orchestrator`

---

## Prerequisites for implementers

Install on dev machine before Task 1:

```bash
# Ubuntu/Debian
sudo apt-get install -y bats shellcheck

# macOS
brew install bats-core shellcheck
```

On ECS (script installs these itself): Ubuntu 22.04.5 amd64, root SSH key access.

Recommended: create an isolated git worktree before starting (see superpowers:using-git-worktrees).
