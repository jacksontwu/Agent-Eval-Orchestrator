# Huawei ECS Worker Bootstrap Design

## Goal

Create a single bootstrap script that prepares a newly created Huawei Cloud ECS instance to become an Agent Eval Orchestrator worker host.

The script prepares the machine up to, but not including, worker daemon startup. It does not prepare evaluation datasets, the evaluated agent binary, or the evaluated agent configuration. Those artifacts will be handled later by changes to Agent Eval Orchestrator that copy them from the controller to workers.

Target platform:

- Ubuntu 22.04.5 LTS
- amd64 / x86_64
- Script is run locally on the new ECS as root

## Non-Goals

The script will not:

- Start `agent_eval_orchestrator.worker.daemon`
- Create a systemd service for the worker daemon
- Download evaluation datasets
- Install or copy `/home/djn/bitfun-cli`
- Install or copy `/home/djn/.config/bitfun`
- Modify the controller node
- Open cloud firewall or security group ports
- Update existing cloned repositories

## Chosen Approach

Use one root-entry shell script with two clear phases:

1. Root phase for system security, OS packages, Docker, SSH hardening, and user creation.
2. `djn` phase for user-owned tools and project repositories.

The script defaults to interactive execution and supports a global non-interactive mode through `--yes`.

Example interactive run:

```bash
bash scripts/bootstrap-huawei-worker.sh
```

Example non-interactive run:

```bash
DJN_PASSWORD='<password-for-djn>' bash scripts/bootstrap-huawei-worker.sh --yes
```

## Root Phase

### Preflight

The script verifies:

- Current user is root.
- OS is Ubuntu 22.04.
- Architecture is amd64 / x86_64.
- `/root/.ssh/authorized_keys` exists and is not empty.
- Required commands can be installed with `apt`.

### Create and Configure `djn`

The script creates a lowercase `djn` user when missing.

Rules:

- Add `djn` to the `sudo` group.
- Do not configure passwordless sudo.
- In interactive mode, use `passwd djn` so the operator sets the password manually.
- In `--yes` mode, set the password from `DJN_PASSWORD`.
- If `--yes` mode needs to set the password and `DJN_PASSWORD` is empty, exit with an error.

### SSH Key Setup

The script copies all current root SSH authorized keys to `djn`:

```text
/root/.ssh/authorized_keys
  -> /home/djn/.ssh/authorized_keys
```

Permissions:

- `/home/djn/.ssh`: `700`
- `/home/djn/.ssh/authorized_keys`: `600`
- Owner: `djn:djn`

If `/home/djn/.ssh/authorized_keys` already exists, the script backs it up before replacing it.

### SSH Hardening

The script disables root SSH login and all password-based SSH login.

It writes an explicit hardening file:

```text
/etc/ssh/sshd_config.d/99-agent-eval-worker-hardening.conf
```

Expected content:

```text
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
```

Huawei Cloud ECS images may include:

```text
/etc/ssh/sshd_config.d/50-cloud-init.conf
```

with:

```text
PasswordAuthentication yes
```

The script must update that file to `PasswordAuthentication no` when it exists. It must back the file up first.

Before restarting SSH, the script runs:

```bash
sshd -t
```

If validation fails, the script exits without restarting SSH.

### Backup Rule for System Files

Before modifying any existing system config file, the script creates a timestamped backup and never overwrites older backups.

Backup name format:

```text
<path>.bak.YYYYMMDD-HHMMSS
```

Files covered:

- `/etc/ssh/sshd_config.d/50-cloud-init.conf`
- `/etc/ssh/sshd_config.d/99-agent-eval-worker-hardening.conf` when replacing an existing file
- `/etc/docker/daemon.json` when it already exists
- `/home/djn/.ssh/authorized_keys` when it already exists

The script does not edit `/etc/ssh/sshd_config`.

### Install Base Packages

The script installs:

- `apt-transport-https`
- `ca-certificates`
- `curl`
- `gnupg2`
- `software-properties-common`
- `git`
- `lsb-release`
- `sudo`

### Install Docker

The script follows the Huawei Cloud Ubuntu Docker CE mirror flow.

It removes old Docker packages before installing Docker CE:

- `docker`
- `docker-engine`
- `docker.io`
- `containerd`
- `runc`

It adds the Huawei Cloud Docker CE apt source:

```text
https://mirrors.huaweicloud.com/docker-ce/linux/ubuntu
```

It installs:

- `docker-ce`
- `docker-ce-cli`
- `containerd.io`
- `docker-buildx-plugin`
- `docker-compose-plugin`

Then it adds `djn` to the `docker` group.

### Configure Huawei SWR Registry Mirror

The script writes Docker daemon registry mirror config:

```json
{
  "registry-mirrors": [
    "https://6bc9e025405d418487910921d203eb49.mirror.swr.myhuaweicloud.com"
  ]
}
```

Target file:

```text
/etc/docker/daemon.json
```

If the file exists, back it up first.

Then:

```bash
systemctl enable --now docker
systemctl restart docker
```

Verification:

```bash
docker version
docker compose version
docker info
```

`docker info` must show the Huawei SWR mirror under `Registry Mirrors`.

## `djn` Phase

All project files created by the script live under one root:

```text
/home/djn/worker
```

Expected structure:

```text
/home/djn/worker/
├── agent-eval-orchestrator/
└── harbor/
```

This structure matches the current controller path inference:

- Worker `shared-root`: `/home/djn/worker/agent-eval-orchestrator/runtime`
- Inferred Harbor repo: `/home/djn/worker/harbor`

### Install uv

Run as `djn`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify:

```bash
/home/djn/.local/bin/uv --version
```

If uv already exists, skip installation and verify the existing binary.

### Clone Agent Eval Orchestrator

Repository:

```text
https://github.com/jacksontwu/Agent-Eval-Orchestrator.git
```

Target:

```text
/home/djn/worker/agent-eval-orchestrator
```

If the target directory already exists, skip it. Do not pull, overwrite, or delete existing content.

Then run:

```bash
cd /home/djn/worker/agent-eval-orchestrator
/home/djn/.local/bin/uv sync
```

### Clone bitfun-harbor

Repository:

```text
https://github.com/JinnanDuan/bitfun-harbor.git
```

Target:

```text
/home/djn/worker/harbor
```

If the target directory already exists, skip it. Do not pull, overwrite, or delete existing content.

Verify Harbor:

```bash
cd /home/djn/worker/harbor
/home/djn/.local/bin/uv run harbor --help
```

## Interaction Model

By default, the script prompts before high-impact operations:

- Creating or changing `djn`
- Setting the `djn` password
- Replacing `/home/djn/.ssh/authorized_keys`
- Modifying SSH config and restarting SSH
- Removing old Docker packages
- Writing Docker daemon config and restarting Docker
- Cloning repositories
- Running `uv sync`
- Running `uv run harbor --help`

With `--yes`, the script skips confirmations and fails fast when required non-interactive inputs are missing.

## Idempotency

The script is safe to run more than once:

- Existing `djn` user is reused.
- Existing cloned repositories are skipped.
- Existing uv binary is reused.
- Existing modified system config files are backed up before modification.
- Backups use timestamped filenames to avoid overwriting earlier backups.
- Existing Docker installation is normalized to the expected source, service state, and mirror config.

## Final Output

On success, print:

```text
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
```

Also print the later worker startup shape without filling secrets:

```bash
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
```

## Acceptance Criteria

- Running the script on a fresh Ubuntu 22.04.5 amd64 ECS creates `djn` and allows SSH key login as `djn`.
- Root SSH login is disabled.
- SSH password login is disabled for all users.
- `djn` is in `sudo` and `docker` groups.
- Docker CE is installed from the Huawei Cloud Docker CE mirror.
- Docker SWR registry mirror is configured.
- `uv` is installed and available at `/home/djn/.local/bin/uv`.
- Agent Eval Orchestrator is present at `/home/djn/worker/agent-eval-orchestrator`.
- `uv sync` succeeds in Agent Eval Orchestrator.
- bitfun-harbor is present at `/home/djn/worker/harbor`.
- `uv run harbor --help` succeeds in bitfun-harbor.
- The script does not download datasets, prepare `bitfun-cli`, prepare `.config/bitfun`, or start a worker daemon.
