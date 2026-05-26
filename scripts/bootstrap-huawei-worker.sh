#!/usr/bin/env bash
set -euo pipefail

# --- configuration ---
INTERACTIVE="${INTERACTIVE:-yes}"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
ROOT_AUTHORIZED_KEYS="${ROOT_AUTHORIZED_KEYS:-/root/.ssh/authorized_keys}"
UNAME_M="${UNAME_M:-$(uname -m)}"
DJN_USER="${DJN_USER:-djn}"
DJN_HOME="${DJN_HOME:-/home/djn}"
SSH_HARDENING_DIR="${SSH_HARDENING_DIR:-/etc/ssh/sshd_config.d}"
SSH_HARDENING_FILE="${SSH_HARDENING_FILE:-${SSH_HARDENING_DIR}/99-agent-eval-worker-hardening.conf}"
CLOUD_INIT_SSH_CONF="${CLOUD_INIT_SSH_CONF:-/etc/ssh/sshd_config.d/50-cloud-init.conf}"
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
DOCKER_APT_REPO_URL="https://mirrors.huaweicloud.com/docker-ce/linux/ubuntu"
DOCKER_DAEMON_JSON="${DOCKER_DAEMON_JSON:-/etc/docker/daemon.json}"
LEGACY_DOCKER_PACKAGES=(
  docker docker-engine docker.io containerd runc
  docker-compose docker-compose-v2
)
DOCKER_CE_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
SWR_MIRROR="https://6bc9e025405d418487910921d203eb49.mirror.swr.myhuaweicloud.com"
UV_BIN="${UV_BIN:-/home/djn/.local/bin/uv}"
WORKER_ROOT="${WORKER_ROOT:-/home/djn/worker}"
AEO_REPO_DIR="${AEO_REPO_DIR:-${WORKER_ROOT}/agent-eval-orchestrator}"
HARBOR_REPO_DIR="${HARBOR_REPO_DIR:-${WORKER_ROOT}/harbor}"
AEO_REPO_URL="https://github.com/jacksontwu/Agent-Eval-Orchestrator.git"
HARBOR_REPO_URL="https://github.com/JinnanDuan/bitfun-harbor.git"
GIT="${GIT:-git}"

# --- common ---
log() {
  printf '[bootstrap] %s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "must run as root"
}

timestamp_suffix() {
  date +%Y%m%d-%H%M%S
}

backup_file() {
  local path="$1"
  [[ -e "${path}" ]] || return 0
  local backup
  backup="${path}.bak.$(timestamp_suffix)"
  cp -a "${path}" "${backup}"
  log "Backed up ${path} -> ${backup}"
}

confirm() {
  local prompt="$1"
  if [[ "${INTERACTIVE}" == "no" ]]; then
    return 0
  fi
  read -r -p "${prompt} [y/N] " reply
  case "${reply}" in
    y|Y|yes|YES) return 0 ;;
    *) die "Aborted by operator" ;;
  esac
}

# --- cli ---
BOOTSTRAP_MODE="${BOOTSTRAP_MODE:-full}"

parse_bootstrap_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes)
        INTERACTIVE="no"
        shift
        ;;
      --configure-docker)
        BOOTSTRAP_MODE="configure-docker"
        shift
        ;;
      -h|--help)
        cat <<'EOF'
Usage: bash scripts/bootstrap-huawei-worker.sh [--yes] [--configure-docker]

  --yes               Non-interactive mode. Requires DJN_PASSWORD when setting djn password.
  --configure-docker  Update Docker daemon.json address pools on an existing worker.
                      Run as djn (docker group); does not require root SSH.
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

# --- preflight ---
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

# --- user ---
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
  if [[ "${INTERACTIVE}" == "no" ]]; then
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

# --- ssh ---
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

# --- packages ---
install_base_packages() {
  confirm "Install base apt packages?"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y "${BASE_PACKAGES[@]}"
  log "Installed base packages"
}

# --- docker ---
render_daemon_json() {
  cat <<EOF
{
  "registry-mirrors": [
    "${SWR_MIRROR}"
  ],
  "default-address-pools": [
    {"base": "10.201.0.0/16", "size": 24},
    {"base": "10.202.0.0/16", "size": 24},
    {"base": "10.203.0.0/16", "size": 24}
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

require_docker_cli() {
  command -v docker >/dev/null 2>&1 || die "docker not available"
  docker info >/dev/null 2>&1 || die "docker not reachable; ensure the current user is in the docker group"
}

backup_daemon_json_via_docker() {
  local backup="/etc/docker/daemon.json.bak.$(timestamp_suffix)"
  docker run --rm -v /etc/docker:/etc/docker alpine \
    sh -c "test -f /etc/docker/daemon.json && cp /etc/docker/daemon.json '${backup}' || true"
  log "Backed up /etc/docker/daemon.json -> ${backup}"
}

write_daemon_json_via_docker() {
  local tmp
  tmp="$(mktemp)"
  render_daemon_json > "${tmp}"
  docker run --rm -v /etc/docker:/etc/docker -v "${tmp}:/src/daemon.json:ro" alpine \
    sh -c 'cp /src/daemon.json /etc/docker/daemon.json'
  rm -f "${tmp}"
  log "Wrote ${DOCKER_DAEMON_JSON} via docker mount"
}

restart_docker_via_nsenter() {
  # Restarting dockerd drops the client connection; that is expected.
  docker run --rm --privileged --pid=host -v /:/host alpine \
    nsenter -t 1 -m -u -i -n -p -- systemctl restart docker >/dev/null 2>&1 || true

  for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "Docker did not become ready after restart"
}

verify_docker_address_pools() {
  docker version >/dev/null
  docker compose version >/dev/null
  docker info | grep -F "${SWR_MIRROR}" >/dev/null
  docker system info --format 'DefaultAddressPools={{json .DefaultAddressPools}}' | grep -F '"Base":"10.201.0.0/16"' >/dev/null
  log "Docker address pool verification passed"
}

configure_docker_address_pools() {
  confirm "Prune unused Docker networks, rewrite daemon.json, and restart Docker?"
  require_docker_cli
  log "Pruning unused Docker networks"
  docker network prune -f
  backup_daemon_json_via_docker
  write_daemon_json_via_docker
  log "Restarting Docker"
  restart_docker_via_nsenter
  verify_docker_address_pools
  log "Network count: $(docker network ls | wc -l)"
}

remove_legacy_docker_packages() {
  confirm "Remove legacy Docker packages if present?"
  export DEBIAN_FRONTEND=noninteractive
  apt-get remove -y --purge "${LEGACY_DOCKER_PACKAGES[@]}" 2>/dev/null || true
  apt-get autoremove -y 2>/dev/null || true
}

install_docker_ce_from_huawei() {
  confirm "Install Docker CE from Huawei mirror?"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "${DOCKER_APT_REPO_URL}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  # shellcheck disable=SC1091
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
  usermod -aG docker "${DJN_USER}"
  systemctl enable --now docker
  systemctl restart docker
}

verify_docker_installation() {
  verify_docker_address_pools
}

setup_docker() {
  remove_legacy_docker_packages
  install_docker_ce_from_huawei
  configure_docker_service
  verify_docker_installation
}

# --- djn phase ---
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

# --- output ---
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

# --- main ---
main() {
  parse_bootstrap_args "$@"
  case "${BOOTSTRAP_MODE}" in
    configure-docker)
      configure_docker_address_pools
      ;;
    full)
      run_preflight_checks
      setup_djn_account
      apply_ssh_hardening
      install_base_packages
      setup_docker
      run_djn_phase
      print_bootstrap_success
      ;;
    *)
      die "Unknown bootstrap mode: ${BOOTSTRAP_MODE}"
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
