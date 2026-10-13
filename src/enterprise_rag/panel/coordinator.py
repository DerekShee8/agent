"""The one global panel run: lifecycle, cancellation, and last-five history.

The coordinator owns exactly one active run. It binds a fresh cancellation token
and progress buffer to each accepted ``RunSpec``, maps only structured result
statuses to terminal states, and keeps the completed ``Result + RunTrace``
authoritative. Late updates from an older run can never touch a newer record.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

from ..application import AgentRunRequest, ApplicationPort, RagAskRequest
from ..errors import ErrorCategory
from ..execution_control import CancellationToken, RunCancelled
from ..execution_trace import TraceEvent
from ..models import AgentResult, RagResult
from .contracts import (
    InitializationStage,
    PanelLifecycleState,
    PanelRunRecord,
    PanelTerminalState,
    RunRequest,
    RunSpec,
)
from .progress import BoundedProgressBuffer
from .public_trace import reconcile_terminal_events, sanitize_public_trace_event

_HISTORY_LIMIT = 5
_CANCEL_BOUNDARY_STAGE = "cancel_boundary"
_REQUEST_SPEC_FIELDS = (
    "run_id",
    "created_at",
    "mode",
    "scenario_id",
    "question_or_task",
    "generator",
    "provider_name",
    "provider_fingerprint",
    "fallback_local",
)
_RESULT_STATES: dict[str, PanelTerminalState] = {
    "answered": "answered",
    "refused": "refused",
    "failed": "failed",
}


class PanelRuntimePort(Protocol):
    """The slice of ``PanelRuntime`` the coordinator depends on."""

    @property
    def state(self) -> str: ...

    @property
    def runtime_ready_seconds(self) -> float | None: ...

    async def initialize(
        self,
        observer: Callable[[InitializationStage], None] | None = None,
    ) -> tuple[InitializationStage, ...]: ...

    async def application_for(
        self,
        spec: RunSpec,
        token: CancellationToken,
        observer: Callable[[TraceEvent], None] | None = None,
    ) -> ApplicationPort: ...

    async def aclose(self) -> None: ...


class RunCoordinator:
    """Single-run lifecycle, cooperative cancellation, and bounded history."""

    def __init__(
        self,
        runtime: PanelRuntimePort,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._runtime = runtime
        self._clock = clock
        self._lock = threading.Lock()
        self._state: PanelLifecycleState = "initializing"
        self._stages: tuple[InitializationStage, ...] = ()
        self._initialization_duration_ms = 0.0
        self._active_run_id: str | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._token: CancellationToken | None = None
        self._pending_request: RunRequest | None = None
        self._buffers: dict[str, BoundedProgressBuffer] = {}
        self._prefixes: dict[str, list[TraceEvent]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._records: deque[PanelRunRecord] = deque(maxlen=_HISTORY_LIMIT)
        self._closed = False

    @property
    def state(self) -> PanelLifecycleState:
        with self._lock:
            return self._state

    @property
    def active_run_id(self) -> str | None:
        with self._lock:
            return self._active_run_id

    @property
    def history(self) -> tuple[PanelRunRecord, ...]:
        with self._lock:
            return tuple(reversed(self._records))

    @property
    def stages(self) -> tuple[InitializationStage, ...]:
        with self._lock:
            return self._stages

    async def initialize(
        self,
        observer: Callable[[InitializationStage], None] | None = None,
    ) -> tuple[InitializationStage, ...]:
        """Join the runtime's single shared initialization task."""

        stages = await self._runtime.initialize(observer)
        with self._lock:
            self._stages = tuple(stages)
            self._initialization_duration_ms = self._ready_duration_ms()
            if self._state == "initializing":
                self._state = (
                    "ready"
                    if self._runtime.state == "ready"
                    else "initialization_failed"
                )
        return self._stages

    def begin_authorization(self, request: RunRequest) -> None:
        """Reserve a run id for an online confirmation without creating a client."""

        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        with self._lock:
            if self._state != "ready":
                raise RuntimeError("panel is not ready for a new run")
            self._pending_request = request
            self._active_run_id = request.run_id
            self._state = "authorizing"

    def record_blocked(
        self,
        request: RunRequest,
        error_category: str,
        public_error: str,
    ) -> PanelRunRecord:
        """Store the one legal BLOCKED record for the matching pending request."""

        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        with self._lock:
            pending = self._pending_request
            if self._state != "authorizing" or pending is None or pending != request:
                raise ValueError("no matching pending authorization request")
            record = PanelRunRecord(
                run_id=request.run_id,
                request=request,
                spec=None,
                state="blocked",
                result=None,
                trace=None,
                progress_events=(),
                initialization_duration_ms=self._initialization_duration_ms,
                run_duration_ms=0.0,
                error_category=error_category,
                public_error=public_error,
            )
            self._store_terminal_locked(record)
            return record

    async def start(self, spec: RunSpec) -> str:
        """Accept one run, bind its resources, and launch its single task."""

        if not isinstance(spec, RunSpec):
            raise TypeError("spec must be a RunSpec")
        with self._lock:
            if self._closed:
                raise RuntimeError("coordinator is closed")
            if spec.generator == "online":
                if self._state != "authorizing":
                    raise RuntimeError("online runs require a pending authorization")
            elif self._state != "ready":
                raise RuntimeError(
                    "panel is not ready for a new run"
                    if self._state in {"initializing", "initialization_failed"}
                    else "a run is already active"
                )
            request = self._request_for_locked(spec)
            token = CancellationToken()
            buffer = BoundedProgressBuffer()
            self._active_run_id = spec.run_id
            self._token = token
            self._state = "running"
            self._buffers[spec.run_id] = buffer
            self._prefixes[spec.run_id] = []
            observer = self._observer_for(spec.run_id)
        started = self._clock()
        try:
            application = await self._runtime.application_for(spec, token, observer)
        except Exception as error:
            self._store_terminal(
                self._pre_service_failure(spec, request, error, started)
            )
            return spec.run_id
        task = asyncio.create_task(self._execute(spec, request, application, started))
        with self._lock:
            self._active_task = task
            self._tasks[spec.run_id] = task
        return spec.run_id

    def request_cancel(self, run_id: str) -> bool:
        """Mark the matching active run cancelled; idempotent and run-scoped."""

        with self._lock:
            if run_id != self._active_run_id or self._state != "running":
                return False
            self._state = "cancelling"
            token = self._token
        if token is not None:
            token.cancel()
        return True

    def drain_progress(self, run_id: str) -> tuple[TraceEvent, ...]:
        with self._lock:
            buffer = self._buffers.get(run_id)
        if buffer is None:
            return ()
        return buffer.drain()

    async def wait(self, run_id: str) -> PanelRunRecord:
        """Await one run's terminal record."""

        with self._lock:
            task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        record = self._find_record(run_id)
        if record is None:
            raise ValueError(f"unknown panel run: {run_id}")
        return record

    async def aclose(self) -> None:
        """Detach progress, settle any active run, close the runtime once."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            buffers = tuple(self._buffers.values())
            task = self._active_task
            token = self._token
        for buffer in buffers:
            buffer.detach()
        if token is not None:
            token.cancel()
        if task is not None:
            try:
                await task
            except Exception:
                pass
        await self._runtime.aclose()
        with self._lock:
            self._records.clear()
            self._buffers.clear()
            self._prefixes.clear()
            self._tasks.clear()
            self._active_run_id = None
            self._active_task = None
            self._token = None
            self._pending_request = None

    async def _execute(
        self,
        spec: RunSpec,
        request: RunRequest,
        application: ApplicationPort,
        started: float,
    ) -> None:
        try:
            result = await self._invoke(spec, application)
        except RunCancelled:
            self._store_terminal(self._cancelled_record(spec, request, started))
            return
        except Exception as error:
            self._store_terminal(self._failed_record(spec, request, error, started))
            return
        self._store_terminal(self._completed_record(spec, request, result, started))

    async def _invoke(
        self,
        spec: RunSpec,
        application: ApplicationPort,
    ) -> RagResult | AgentResult:
        fallback = True if spec.fallback_local else None
        if spec.mode == "rag":
            return await application.ask_rag(
                RagAskRequest(
                    question=spec.question_or_task,
                    generator=spec.generator,
                    provider=spec.provider_name,
                    fallback_local=fallback,
                )
            )
        return await application.run_agent(
            AgentRunRequest(
                task=spec.question_or_task,
                generator=spec.generator,
                provider=spec.provider_name,
                fallback_local=fallback,
            )
        )

    def _completed_record(
        self,
        spec: RunSpec,
        request: RunRequest,
        result: RagResult | AgentResult,
        started: float,
    ) -> PanelRunRecord:
        state = _RESULT_STATES[result.status]
        return PanelRunRecord(
            run_id=spec.run_id,
            request=request,
            spec=spec,
            state=state,
            result=result,
            trace=result.trace,
            progress_events=self._reconciled_prefix(spec.run_id, result.trace),
            initialization_duration_ms=self._initialization_duration_ms,
            run_duration_ms=self._elapsed_ms(started),
            # A business failure must carry the result's own category verbatim.
            error_category=result.error_category if state == "failed" else None,
            public_error=None,
        )

    def _cancelled_record(
        self,
        spec: RunSpec,
        request: RunRequest,
        started: float,
    ) -> PanelRunRecord:
        return PanelRunRecord(
            run_id=spec.run_id,
            request=request,
            spec=spec,
            state="cancelled",
            result=None,
            trace=None,
            progress_events=(
                *self._prefix_snapshot(spec.run_id),
                self._cancel_boundary(spec.run_id),
            ),
            initialization_duration_ms=self._initialization_duration_ms,
            run_duration_ms=self._elapsed_ms(started),
            error_category=None,
            public_error=None,
        )

    def _failed_record(
        self,
        spec: RunSpec,
        request: RunRequest,
        error: Exception,
        started: float,
    ) -> PanelRunRecord:
        category = _exception_category(error)
        return PanelRunRecord(
            run_id=spec.run_id,
            request=request,
            spec=spec,
            state="failed",
            result=None,
            trace=None,
            progress_events=self._prefix_snapshot(spec.run_id),
            initialization_duration_ms=self._initialization_duration_ms,
            run_duration_ms=self._elapsed_ms(started),
            error_category=category,
            public_error=f"panel run failed before returning a result: {category}",
        )

    def _pre_service_failure(
        self,
        spec: RunSpec,
        request: RunRequest,
        error: Exception,
        started: float,
    ) -> PanelRunRecord:
        category = _exception_category(error)
        return PanelRunRecord(
            run_id=spec.run_id,
            request=request,
            spec=spec,
            state="failed",
            result=None,
            trace=None,
            progress_events=(),
            initialization_duration_ms=self._initialization_duration_ms,
            run_duration_ms=self._elapsed_ms(started),
            error_category=category,
            public_error=f"panel run could not start: {category}",
        )

    def _cancel_boundary(self, run_id: str) -> TraceEvent:
        return TraceEvent(
            run_id,
            f"{run_id}:cancel",
            None,
            _CANCEL_BOUNDARY_STAGE,
            "cancelled",
            0.0,
            0.0,
            {},
            None,
        )

    def _store_terminal(self, record: PanelRunRecord) -> None:
        with self._lock:
            self._store_terminal_locked(record)

    def _store_terminal_locked(self, record: PanelRunRecord) -> None:
        if record.run_id != self._active_run_id:
            # A late update from an older run must never touch a newer record.
            return
        self._records.append(record)
        self._active_run_id = None
        self._active_task = None
        self._token = None
        self._pending_request = None
        self._state = "ready"
        self._evict_orphans()

    def _evict_orphans(self) -> None:
        live = {record.run_id for record in self._records}
        live.update(self._tasks)
        for run_id in tuple(self._buffers):
            if run_id in live:
                continue
            self._buffers.pop(run_id, None)
            self._prefixes.pop(run_id, None)
            self._tasks.pop(run_id, None)

    def _observer_for(self, run_id: str) -> Callable[[TraceEvent], None]:
        buffer = self._buffers[run_id]
        prefix = self._prefixes[run_id]

        def observe(event: TraceEvent) -> None:
            with self._lock:
                if self._active_run_id != run_id:
                    return
            try:
                sanitized = sanitize_public_trace_event(event)
            except Exception:
                return
            prefix.append(sanitized)
            buffer.publish(sanitized)

        return observe

    def _prefix_snapshot(self, run_id: str) -> tuple[TraceEvent, ...]:
        with self._lock:
            return tuple(self._prefixes.get(run_id, ()))

    def _reconciled_prefix(self, run_id: str, trace: object) -> tuple[TraceEvent, ...]:
        from ..execution_trace import RunTrace

        assert isinstance(trace, RunTrace)
        return reconcile_terminal_events(self._prefix_snapshot(run_id), trace)

    def _request_for_locked(self, spec: RunSpec) -> RunRequest:
        if spec.generator == "online":
            pending = self._pending_request
            if pending is None or pending.run_id != spec.run_id:
                raise ValueError("online run has no matching pending authorization")
            for field_name in _REQUEST_SPEC_FIELDS:
                if getattr(pending, field_name) != getattr(spec, field_name):
                    raise ValueError(
                        f"spec must copy the pending request: {field_name}"
                    )
            return pending
        return RunRequest(
            run_id=spec.run_id,
            created_at=spec.created_at,
            mode=spec.mode,
            scenario_id=spec.scenario_id,
            question_or_task=spec.question_or_task,
            generator=spec.generator,
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
        )

    def _find_record(self, run_id: str) -> PanelRunRecord | None:
        with self._lock:
            for record in reversed(self._records):
                if record.run_id == run_id:
                    return record
        return None

    def _ready_duration_ms(self) -> float:
        ready_seconds = self._runtime.runtime_ready_seconds
        if ready_seconds is not None:
            return max(0.0, ready_seconds * 1000.0)
        return sum(stage.duration_ms for stage in self._stages)

    def _elapsed_ms(self, started: float) -> float:
        return max(0.0, (self._clock() - started) * 1000.0)


def _exception_category(error: Exception) -> str:
    category = getattr(error, "category", ErrorCategory.PROTOCOL)
    if isinstance(category, ErrorCategory):
        category = category.value
    if isinstance(category, str) and category:
        return category
    return ErrorCategory.PROTOCOL.value
