from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from math import isfinite
from time import monotonic
from types import MappingProxyType
from typing import Literal, cast

TraceStatus = Literal[
    "started",
    "succeeded",
    "refused",
    "failed",
    "skipped",
    "degraded",
    "cancelled",
]
RunStatus = Literal["succeeded", "refused", "failed", "skipped", "degraded"]

_TRACE_STATUSES = frozenset(
    {
        "started",
        "succeeded",
        "refused",
        "failed",
        "skipped",
        "degraded",
        "cancelled",
    }
)
# "cancelled" is a trace-only status owned by the coordinator's cancel boundary. A
# completed business RunTrace never carries it, so it stays out of RunStatus and out
# of every service span's accepted terminal values.
_RUN_STATUSES = frozenset({"succeeded", "refused", "failed", "skipped", "degraded"})
_SENSITIVE_KEYS = frozenset(
    {"api_key", "authorization", "secret", "token", "password", "system_prompt"}
)
_REDACTION_MARKER = "<redacted>"

# A live consumer of span lifecycle events. It is strictly observational: it receives
# immutable snapshots and can never change business execution or the final RunTrace.
TraceObserver = Callable[["TraceEvent"], None]


@dataclass(frozen=True, slots=True)
class TraceScope:
    """Binds a recorder to the parent event a nested component must hang under."""

    recorder: TraceRecorder
    parent_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.recorder, TraceRecorder):
            raise TypeError("recorder must be a TraceRecorder")
        _require_nonempty(self.parent_id, "parent_id")


def _freeze_value(value: object, *, redact_sensitive: bool) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            if redact_sensitive and key.casefold() in _SENSITIVE_KEYS:
                frozen[key] = _REDACTION_MARKER
            else:
                frozen[key] = _freeze_value(
                    nested_value,
                    redact_sensitive=redact_sensitive,
                )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_value(item, redact_sensitive=redact_sensitive) for item in value
        )
    if isinstance(value, Enum):
        return _freeze_value(value.value, redact_sensitive=redact_sensitive)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("structured values must contain only finite numbers")
        return value
    raise TypeError(f"unsupported structured value: {type(value).__name__}")


def _freeze_mapping(
    value: Mapping[str, object],
    *,
    redact_sensitive: bool = True,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("value must be a mapping")
    return cast(
        Mapping[str, object],
        _freeze_value(value, redact_sensitive=redact_sensitive),
    )


def _plain_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    return value


def _require_nonempty(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _normalize_timing(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be an int or float")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be non-negative and finite") from exc
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be non-negative and finite")
    return normalized


@dataclass(frozen=True, slots=True)
class TraceEvent:
    run_id: str
    event_id: str
    parent_id: str | None
    stage: str
    status: TraceStatus
    started_at: float
    duration_ms: float
    attributes: Mapping[str, object]
    error_category: str | None

    def __post_init__(self) -> None:
        _require_nonempty(self.run_id, "run_id")
        _require_nonempty(self.event_id, "event_id")
        if self.parent_id is not None:
            _require_nonempty(self.parent_id, "parent_id")
        _require_nonempty(self.stage, "stage")
        _require_nonempty(self.status, "status")
        if self.status not in _TRACE_STATUSES:
            raise ValueError(f"invalid trace event status: {self.status}")
        object.__setattr__(
            self,
            "started_at",
            _normalize_timing(self.started_at, "started_at"),
        )
        object.__setattr__(
            self,
            "duration_ms",
            _normalize_timing(self.duration_ms, "duration_ms"),
        )
        if self.error_category is not None:
            _require_nonempty(self.error_category, "error_category")
        object.__setattr__(self, "attributes", _freeze_mapping(self.attributes))


@dataclass(frozen=True, slots=True)
class RunTrace:
    run_id: str
    status: RunStatus
    events: tuple[TraceEvent, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.run_id, "run_id")
        _require_nonempty(self.status, "status")
        if self.status not in _RUN_STATUSES:
            raise ValueError(f"invalid run status: {self.status}")
        if not isinstance(self.events, tuple):
            raise TypeError("events must be a tuple")
        if any(not isinstance(event, TraceEvent) for event in self.events):
            raise TypeError("events must contain only TraceEvent")
        if any(event.status == "started" for event in self.events):
            raise ValueError("finished run cannot contain started events")
        if any(event.run_id != self.run_id for event in self.events):
            raise ValueError("event run id must match the trace run id")
        event_ids = tuple(event.event_id for event in self.events)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_ids must be unique")
        earlier_event_ids: set[str] = set()
        for event in self.events:
            if (
                event.parent_id is not None
                and event.parent_id not in earlier_event_ids
            ):
                raise ValueError("parent_id must refer to an earlier event")
            earlier_event_ids.add(event.event_id)

    @classmethod
    def empty(cls) -> RunTrace:
        return cls(run_id="untraced", status="skipped", events=())

    def to_json(self) -> str:
        payload = {
            "run_id": self.run_id,
            "status": self.status,
            "events": [
                {
                    "run_id": event.run_id,
                    "event_id": event.event_id,
                    "parent_id": event.parent_id,
                    "stage": event.stage,
                    "status": event.status,
                    "started_at": event.started_at,
                    "duration_ms": event.duration_ms,
                    "attributes": _plain_value(event.attributes),
                    "error_category": event.error_category,
                }
                for event in self.events
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class _TraceSpan:
    __slots__ = (
        "_attributes",
        "_entered",
        "_error_category",
        "_event_id",
        "_parent_id",
        "_recorder",
        "_stage",
        "_terminal_attributes",
        "_terminal_status",
    )

    def __init__(
        self,
        recorder: TraceRecorder,
        stage: str,
        parent_id: str | None,
        attributes: Mapping[str, object],
    ) -> None:
        self._recorder = recorder
        self._stage = stage
        self._parent_id = parent_id
        self._attributes = attributes
        self._event_id: str | None = None
        self._terminal_status: RunStatus | None = None
        self._terminal_attributes: Mapping[str, object] = {}
        self._error_category: str | None = None
        self._entered = False

    @property
    def event_id(self) -> str:
        if self._event_id is None:
            raise RuntimeError("span has not started")
        return self._event_id

    def __enter__(self) -> _TraceSpan:
        if self._entered:
            raise RuntimeError("span cannot be entered more than once")
        self._entered = True
        self._event_id = self._recorder._start_span(
            stage=self._stage,
            parent_id=self._parent_id,
            attributes=self._attributes,
        )
        return self

    def finish(
        self,
        status: RunStatus,
        *,
        error_category: str | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        if not self._entered or self._event_id is None:
            raise RuntimeError("span has not started")
        _require_nonempty(status, "status")
        if status not in _RUN_STATUSES:
            raise ValueError(f"invalid terminal span status: {status}")
        if self._terminal_status is not None:
            raise RuntimeError("span terminal status is already set")
        if error_category is not None:
            _require_nonempty(error_category, "error_category")
        self._terminal_status = status
        self._error_category = error_category
        self._terminal_attributes = attributes or {}

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> Literal[False]:
        del exc_type, traceback
        if self._event_id is None:
            raise RuntimeError("span has not started")

        if exc is not None:
            category = getattr(exc, "category", None)
            if isinstance(category, Enum):
                category = category.value
            error_category = category if isinstance(category, str) else None
            self._recorder._close_span(
                self._event_id,
                status="failed",
                error_category=error_category,
                attributes=self._terminal_attributes,
            )
        else:
            self._recorder._close_span(
                self._event_id,
                status=self._terminal_status or "succeeded",
                error_category=self._error_category,
                attributes=self._terminal_attributes,
            )
        return False


@contextmanager
def trace_span(
    recorder: TraceRecorder | None,
    stage: str,
    *,
    parent_id: str | None = None,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[_TraceSpan | None]:
    """Open a span when a recorder exists, or yield nothing when untraced.

    Services use this so one pipeline serves both a traced run and an untraced
    nested call without branching; ``None`` means "run without observing".
    """

    if recorder is None:
        yield None
        return
    with recorder.span(
        stage,
        parent_id=parent_id,
        attributes=attributes,
    ) as span:
        yield span


@contextmanager
def trace_stage(
    scope: TraceScope | None,
    stage: str,
) -> Iterator[_TraceSpan | None]:
    """Open a child span under the scope's parent, or yield nothing when untraced.

    Nested components use this so one code path serves both traced and untraced
    callers without a second implementation.
    """

    with trace_span(
        None if scope is None else scope.recorder,
        stage,
        parent_id=None if scope is None else scope.parent_id,
    ) as span:
        yield span


class TraceRecorder:
    __slots__ = (
        "_active_event_ids",
        "_clock",
        "_event_indexes",
        "_events",
        "_finished",
        "_observer",
        "run_id",
    )

    def __init__(
        self,
        run_id: str,
        clock: Callable[[], float] = monotonic,
        *,
        observer: TraceObserver | None = None,
    ) -> None:
        _require_nonempty(run_id, "run_id")
        if observer is not None and not callable(observer):
            raise TypeError("observer must be callable")
        self.run_id = run_id
        self._clock = clock
        self._observer = observer
        self._events: list[TraceEvent] = []
        self._event_indexes: dict[str, int] = {}
        self._active_event_ids: set[str] = set()
        self._finished = False

    def _notify(self, event: TraceEvent) -> None:
        """Hand one immutable snapshot to the observer.

        A live consumer must never change business execution or the final trace, so
        every observer failure is swallowed here. ``BaseException`` is deliberately
        not caught: cancellation and interpreter shutdown still propagate.
        """

        observer = self._observer
        if observer is None:
            return
        try:
            observer(event)
        except Exception:
            return

    def _ensure_open(self) -> None:
        if self._finished:
            raise RuntimeError("recorder is already finished")

    def _require_active_parent(self, parent_id: str | None) -> None:
        if parent_id is None:
            return
        if parent_id not in self._event_indexes:
            raise ValueError(f"unknown parent_id: {parent_id}")
        if parent_id not in self._active_event_ids:
            raise ValueError("parent_id must reference an active span")

    def span(
        self,
        stage: str,
        *,
        parent_id: str | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> _TraceSpan:
        self._ensure_open()
        _require_nonempty(stage, "stage")
        if parent_id is not None:
            _require_nonempty(parent_id, "parent_id")
        self._require_active_parent(parent_id)
        return _TraceSpan(self, stage, parent_id, attributes or {})

    def _start_span(
        self,
        *,
        stage: str,
        parent_id: str | None,
        attributes: Mapping[str, object],
    ) -> str:
        self._ensure_open()
        self._require_active_parent(parent_id)
        event_id = f"{self.run_id}:{len(self._events) + 1:04d}"
        event = TraceEvent(
            run_id=self.run_id,
            event_id=event_id,
            parent_id=parent_id,
            stage=stage,
            status="started",
            started_at=float(self._clock()),
            duration_ms=0.0,
            attributes=attributes,
            error_category=None,
        )
        self._event_indexes[event_id] = len(self._events)
        self._events.append(event)
        self._active_event_ids.add(event_id)
        self._notify(event)
        return event_id

    def _close_span(
        self,
        event_id: str,
        *,
        status: RunStatus,
        error_category: str | None,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        if event_id not in self._active_event_ids:
            raise RuntimeError("span is not active")
        index = self._event_indexes[event_id]
        event = self._events[index]
        ended_at = float(self._clock())
        duration_ms = max(0.0, (ended_at - event.started_at) * 1000.0)
        merged = dict(event.attributes)
        if attributes:
            merged.update(attributes)
        updated = replace(
            event,
            status=status,
            duration_ms=duration_ms,
            attributes=_freeze_mapping(merged),
            error_category=error_category,
        )
        self._events[index] = updated
        self._active_event_ids.remove(event_id)
        self._notify(updated)

    def finish(self, status: RunStatus) -> RunTrace:
        self._ensure_open()
        _require_nonempty(status, "status")
        if status not in _RUN_STATUSES:
            raise ValueError(f"invalid run status: {status}")
        if self._active_event_ids:
            raise RuntimeError("cannot finish while spans are open")
        self._finished = True
        return RunTrace(run_id=self.run_id, status=status, events=tuple(self._events))
