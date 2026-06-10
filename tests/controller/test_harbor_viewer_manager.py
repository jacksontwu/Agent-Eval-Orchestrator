import socket

from agent_eval_orchestrator.controller.harbor_viewer import HarborViewerManager


def test_pick_port_skips_ports_used_outside_manager(tmp_path):
    occupied = socket.socket()
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    manager = HarborViewerManager(
        harbor_repo=tmp_path / "harbor",
        logs_dir=tmp_path / "logs",
        port_start=port,
        port_end=port + 1,
    )

    try:
        assert manager._pick_port() == port + 1
    finally:
        occupied.close()
