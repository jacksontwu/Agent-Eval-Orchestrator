import threading
import time

from app.service.orchestration.manager import OrchestrationManager


def test_manager_starts_and_stops():
    counter = {"n": 0}

    def loop(stop: threading.Event) -> None:
        while not stop.is_set():
            counter["n"] += 1
            stop.wait(0.01)

    mgr = OrchestrationManager([loop])
    mgr.start()
    time.sleep(0.1)
    mgr.stop()
    assert counter["n"] > 0
    assert all(not t.is_alive() for t in mgr._threads) if mgr._threads else True
