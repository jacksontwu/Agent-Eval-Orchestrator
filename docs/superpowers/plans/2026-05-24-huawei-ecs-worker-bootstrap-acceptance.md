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
scp scripts/bootstrap-huawei-worker.sh root@<ECS_IP>:/tmp/
ssh root@<ECS_IP> 'DJN_PASSWORD=<set-strong-password> bash /tmp/bootstrap-huawei-worker.sh --yes'
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
