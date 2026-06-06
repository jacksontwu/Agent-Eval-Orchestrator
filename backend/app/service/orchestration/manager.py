from __future__ import annotations

import threading
from collections.abc import Callable


class OrchestrationManager:
    def __init__(self, loops: list[Callable[[threading.Event], None]]) -> None:
        self._loops = loops
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop.clear()
        for loop in self._loops:
            t = threading.Thread(target=loop, args=(self._stop,), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
