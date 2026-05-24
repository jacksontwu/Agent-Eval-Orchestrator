from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_eval_orchestrator.controller.ssh_runner import SshRunner


def test_ssh_run_builds_command(sample_ssh_config):
    runner = SshRunner(sample_ssh_config)
    with patch("agent_eval_orchestrator.controller.ssh_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        result = runner.ssh_run("aeo-ecs-0004", "echo ok", check=True)
        assert result.returncode == 0
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "ssh"
        assert "-F" in cmd
        assert str(sample_ssh_config) in cmd
        assert "aeo-ecs-0004" in cmd
        assert cmd[-1] == "echo ok"


def test_rsync_dir_builds_command(sample_ssh_config, tmp_path):
    runner = SshRunner(sample_ssh_config)
    src = tmp_path / "src"
    src.mkdir()
    with patch("agent_eval_orchestrator.controller.ssh_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.rsync_dir(src, "aeo-ecs-0004:/tmp/target/", remote=True)
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "rsync"
        assert "-az" in cmd
        assert "ssh -F" in " ".join(cmd)
