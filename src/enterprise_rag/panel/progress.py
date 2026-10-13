"""A bounded, coalescing progress buffer between the recorder and the browser.

``TraceRecorder`` calls :meth:`BoundedProgressBuffer.publish` synchronously on the
business path, so this buffer never blocks, never raises into business execution,
and never grows without bound. The completed ``Result + RunTrace`` stays
authoritative; a detached buffer simply means the UI waits for it.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from ..execution_trace import TraceEvent
from .public_trace import sanitize_public_trace_event

DEFAULT_CAPACITY = 256


class BoundedProgressBuffer:
    """One deque, one lock, and an explicit give-up rule when it is full."""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        sanitizer: Callable[[TraceEvent], TraceEvent] = sanitize_public_trace_event,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an integer")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if not callable(sanitizer):
            raise TypeError("sanitizer must be callable")
        self._capacity = capacity
        self._sanitizer = sanitizer
        self._events: deque[TraceEvent] = deque()
        self._lock = threading.Lock()
        self._subscribers: list[object] = []
        self._detached = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def detached(self) -> bool:
        with self._lock:
            return self._detached

    @property
    def subscribers(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._subscribers)

    def subscribe(self, consumer: object | None = None) -> None:
        with self._lock:
            if self._detached:
                raise RuntimeError("progress buffer is detached")
            if consumer is not None and any(
                existing is consumer for existing in self._subscribers
            ):
                raise ValueError("consumer is already subscribed")
            self._subscribers.append(consumer)

    def detach(self) -> None:
        with self._lock:
            self._detached = True
            self._subscribers.clear()

    def close(self) -> None:
        self.detach()

    def publish(self, event: TraceEvent) -> None:
        """Retain one sanitized snapshot, or drop it without disturbing business.

        Order: ignore after detach; replace a queued ``started`` snapshot for the
        same event id in place; append below capacity; otherwise evict the oldest
        ``started`` update. A full buffer of only terminal events gives up and
        relies on the authoritative final ``RunTrace``.
        """

        try:
            sanitized = self._sanitizer(event)
        except Exception:
            self.detach()
            return
        with self._lock:
            if self._detached:
                return
            for position, queued in enumerate(self._events):
                if queued.event_id != sanitized.event_id:
                    continue
                if queued.status == "started":
                    self._events[position] = sanitized
                return
            if len(self._events) < self._capacity:
                self._events.append(sanitized)
                return
            if not self._evict_oldest_started():
                self._detached = True
                self._subscribers.clear()
                return
            self._events.append(sanitized)

    def drain(self) -> tuple[TraceEvent, ...]:
        """Return and clear every pending snapshot, oldest first."""

        with self._lock:
            drained = tuple(self._events)
            self._events.clear()
            return drained

    def _evict_oldest_started(self) -> bool:
        for position, queued in enumerate(self._events):
            if queued.status == "started":
                del self._events[position]
                return True
        return False
