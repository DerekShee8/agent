from __future__ import annotations

import threading


class RunCancelled(Exception):
    """Cooperative cancellation observed at a safe stage boundary."""


class CancellationToken:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelled("run cancelled")
