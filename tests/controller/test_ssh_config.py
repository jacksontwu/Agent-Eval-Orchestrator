from agent_eval_orchestrator.controller.ssh_config import (
    list_ssh_hosts,
    resolve_ssh_alias,
)


def test_list_ssh_hosts_skips_wildcards(sample_ssh_config):
    items = list_ssh_hosts(sample_ssh_config)
    aliases = {item["hostAlias"] for item in items}
    assert aliases == {"aeo-ecs-0004-root", "aeo-ecs-0004"}
    djn = next(item for item in items if item["hostAlias"] == "aeo-ecs-0004")
    assert djn["hostname"] == "192.168.0.244"
    assert djn["user"] == "djn"
    assert djn["port"] == 22


def test_resolve_ssh_alias_unknown(sample_ssh_config):
    try:
        resolve_ssh_alias(sample_ssh_config, "missing-host")
    except ValueError as exc:
        assert "missing-host" in str(exc)
    else:
        raise AssertionError("expected ValueError")
