"""The global single-run lifecycle, cancellation, and last-five history.

Every wait here is driven by ``asyncio.Event``; no test sleeps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal

import pytest

from enterprise_rag.errors import ConfigurationError
from enterprise_rag.execution_control import CancellationToken
from enterprise_rag.execution_trace import TraceEvent, TraceRecorder
from enterprise_rag.models import RagResult, Source
from enterprise_rag.panel.contracts import (
    InitializationStage,
    RunRequest,
    RunSpec,
)
from enterprise_rag.panel.coordinator import RunCoordinator

STAGES = tuple(
    InitializationStage(name, "succeeded", 10.0)
    for name in (
        "CHECKING_CONFIG",
        "LOADING_INDEX",
        "LOADING_RETRIEVER",
        "LOADING_RERANKER",
        "PREPARING_DEFAULT_LOCAL_GENERATOR",
        "READY",
    )
)


class FakeApplication:
    """Mimics the real Application, including its stage-boundary token check."""

    def __init__(
        self,
        token: CancellationToken,
        behaviour: Callable[[str, str], object],
        run_id: str,
    ) -> None:
        self._token = token
        self._behaviour = behaviour
        self._run_id = run_id

    async def ask_rag(self, request: object) -> object:
        result = await self._behaviour("rag", self._run_id)
        self._token.raise_if_cancelled()
        return result

    async def run_agent(self, request: object) -> object:
        result = await self._behaviour("agent", self._run_id)
        self._token.raise_if_cancelled()
        return result


class FakeRuntime:
    def __init__(
        self,
        *,
        ready: bool = True,
        behaviour: Callable[[str, str], object] | None = None,
        bind_error: Exception | None = None,
    ) -> None:
        self._state = "ready" if ready else "initialization_failed"
        self.runtime_ready_seconds = 1.5
        self.observers: list[Callable[[TraceEvent], None]] = []
        self.tokens: list[CancellationToken] = []
        self.aclose_calls = 0
        self._behaviour = behaviour or _answered
        self._bind_error = bind_error

    @property
    def state(self) -> str:
        return self._state

    async def initialize(
        self,
        observer: Callable[[InitializationStage], None] | None = None,
    ) -> tuple[InitializationStage, ...]:
        for stage in STAGES:
            if observer is not None:
                observer(stage)
        return STAGES

    async def application_for(
        self,
        spec: RunSpec,
        token: CancellationToken,
        observer: Callable[[TraceEvent], None] | None = None,
    ) -> object:
        if self._bind_error is not None:
            raise self._bind_error
        self.tokens.append(token)
        if observer is not None:
            self.observers.append(observer)
        return FakeApplication(token, self._behaviour, spec.run_id)

    async def aclose(self) -> None:
        self.aclose_calls += 1


async def _answered(_mode: str, run_id: str) -> RagResult:
    recorder = TraceRecorder(run_id)
    with recorder.span("retrieval"):
        pass
    return RagResult.answered(
        "answer",
        (Source(filename="policy.md", chunk_id="policy.md::0000", score=0.9),),
        trace=recorder.finish("succeeded"),
    )


def local_spec(run_id: str, *, mode: str = "rag") -> RunSpec:
    return RunSpec(
        run_id=run_id,
        created_at=0.0,
        mode=mode,  # type: ignore[arg-type]
        scenario_id="scenario",
        question_or_task="question",
        generator="local",
        provider_name=None,
        provider_fingerprint=None,
        fallback_local=False,
        online_authorization=None,
    )


def online_request(run_id: str) -> RunRequest:
    return RunRequest(
        run_id=run_id,
        created_at=0.0,
        mode="rag",
        scenario_id="scenario",
        question_or_task="question",
        generator="online",
        provider_name="relay",
        provider_fingerprint="fp",
        fallback_local=False,
    )


class BlockingBehaviour:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self, mode: str, run_id: str) -> RagResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await _answered(mode, run_id)


async def ready_coordinator(
    runtime: FakeRuntime,
) -> RunCoordinator:
    coordinator = RunCoordinator(runtime, clock=lambda: 0.0)
    await coordinator.initialize()
    return coordinator


@pytest.mark.asyncio
async def test_initialization_transitions_to_ready() -> None:
    coordinator = await ready_coordinator(FakeRuntime())

    assert coordinator.state == "ready"
    assert [stage.name for stage in coordinator.stages] == [
        stage.name for stage in STAGES
    ]


@pytest.mark.asyncio
async def test_initialization_failure_disables_runs() -> None:
    coordinator = await ready_coordinator(FakeRuntime(ready=False))

    assert coordinator.state == "initialization_failed"
    with pytest.raises(RuntimeError, match="not ready"):
        await coordinator.start(local_spec("run-1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["answered", "refused", "failed"])
async def test_ready_to_running_to_terminal_and_back(status: str) -> None:
    async def behaviour(_mode: str, run_id: str) -> RagResult:
        recorder = TraceRecorder(run_id)
        with recorder.span("retrieval"):
            pass
        if status == "answered":
            return RagResult.answered(
                "answer",
                (
                    Source(
                        filename="policy.md",
                        chunk_id="policy.md::0000",
                        score=0.9,
                    ),
                ),
                trace=recorder.finish("succeeded"),
            )
        if status == "refused":
            return RagResult.refused(
                "insufficient_evidence",
                trace=recorder.finish("refused"),
            )
        return RagResult.failed(
            "retrieval_unavailable",
            trace=recorder.finish("failed"),
        )

    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    run_id = await coordinator.start(local_spec("run-1"))
    record = await coordinator.wait(run_id)

    assert record.state == status
    assert record.result is not None and record.result.status == status
    assert record.trace == record.result.trace
    assert coordinator.state == "ready"
    assert coordinator.active_run_id is None


@pytest.mark.asyncio
async def test_ready_to_authorizing_to_blocked_and_back() -> None:
    coordinator = await ready_coordinator(FakeRuntime())
    request = online_request("run-b")

    coordinator.begin_authorization(request)

    assert coordinator.state == "authorizing"
    assert coordinator.active_run_id == "run-b"

    record = coordinator.record_blocked(
        request,
        "authorization_cancelled",
        "the operator declined the online confirmation",
    )

    assert record.state == "blocked"
    assert record.spec is None
    assert record.result is None
    assert record.trace is None
    assert record.progress_events == ()
    assert record.error_category == "authorization_cancelled"
    assert coordinator.state == "ready"
    assert coordinator.history[0].run_id == "run-b"


@pytest.mark.asyncio
async def test_blocked_record_requires_the_matching_pending_request() -> None:
    coordinator = await ready_coordinator(FakeRuntime())

    with pytest.raises(ValueError, match="pending"):
        coordinator.record_blocked(
            online_request("run-b"),
            "authorization_cancelled",
            "declined",
        )

    coordinator.begin_authorization(online_request("run-b"))
    with pytest.raises(ValueError, match="pending"):
        coordinator.record_blocked(
            online_request("run-other"),
            "authorization_cancelled",
            "declined",
        )


@pytest.mark.asyncio
async def test_running_to_cancelling_to_cancelled_and_back() -> None:
    behaviour = BlockingBehaviour()
    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    run_id = await coordinator.start(local_spec("run-c"))
    await behaviour.started.wait()

    assert coordinator.state == "running"
    assert coordinator.request_cancel(run_id) is True
    assert coordinator.state == "cancelling"
    behaviour.release.set()

    record = await coordinator.wait(run_id)

    assert (record.state, record.error_category, record.public_error) == (
        "cancelled",
        None,
        None,
    )
    assert record.spec is not None
    assert record.result is None
    assert record.trace is None
    assert [event.stage for event in record.progress_events] == ["cancel_boundary"]
    assert record.progress_events[-1].status == "cancelled"
    assert coordinator.state == "ready"


@pytest.mark.asyncio
async def test_second_run_is_rejected_immediately_without_a_queue() -> None:
    behaviour = BlockingBehaviour()
    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    await coordinator.start(local_spec("run-1"))
    await behaviour.started.wait()

    with pytest.raises(RuntimeError, match="already active"):
        await coordinator.start(local_spec("run-2"))

    behaviour.release.set()
    await coordinator.wait("run-1")

    assert [record.run_id for record in coordinator.history] == ["run-1"]
    assert behaviour.calls == 1


@pytest.mark.asyncio
async def test_repeated_stop_is_idempotent_and_scoped_to_the_active_run() -> None:
    behaviour = BlockingBehaviour()
    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    run_id = await coordinator.start(local_spec("run-1"))
    await behaviour.started.wait()

    assert coordinator.request_cancel("run-other") is False
    assert coordinator.request_cancel(run_id) is True
    assert coordinator.request_cancel(run_id) is False
    behaviour.release.set()
    assert coordinator.request_cancel(run_id) is False

    record = await coordinator.wait(run_id)
    assert record.state == "cancelled"
    assert len(coordinator.history) == 1


@pytest.mark.asyncio
async def test_history_keeps_the_newest_five_in_stable_order() -> None:
    coordinator = await ready_coordinator(FakeRuntime())

    for index in range(1, 7):
        run_id = await coordinator.start(local_spec(f"run-{index}"))
        record = await coordinator.wait(run_id)
        assert (record.state, record.error_category, record.public_error) == (
            "answered",
            None,
            None,
        )

    history = coordinator.history
    assert [record.run_id for record in history] == [
        "run-6",
        "run-5",
        "run-4",
        "run-3",
        "run-2",
    ]
    with pytest.raises(ValueError, match="unknown panel run"):
        await coordinator.wait("run-1")


@pytest.mark.asyncio
async def test_identical_input_produces_a_distinct_record_per_run_id() -> None:
    coordinator = await ready_coordinator(FakeRuntime())

    first = await coordinator.start(local_spec("run-a"))
    await coordinator.wait(first)
    second = await coordinator.start(local_spec("run-b"))
    await coordinator.wait(second)

    assert first != second
    assert [record.run_id for record in coordinator.history] == ["run-b", "run-a"]
    assert all(record.state == "answered" for record in coordinator.history)


@pytest.mark.asyncio
async def test_a_delayed_event_from_an_older_run_cannot_touch_the_active_view() -> None:
    behaviour = BlockingBehaviour()
    runtime = FakeRuntime(behaviour=behaviour)
    coordinator = await ready_coordinator(runtime)

    await coordinator.start(local_spec("run-6"))
    await behaviour.started.wait()
    stale_observer = runtime.observers[-1]
    behaviour.release.set()
    await coordinator.wait("run-6")

    blocking = BlockingBehaviour()
    runtime._behaviour = blocking
    await coordinator.start(local_spec("run-7"))
    await blocking.started.wait()

    stale_observer(
        TraceEvent(
            "run-6",
            "run-6:9999",
            None,
            "generation",
            "succeeded",
            0.0,
            0.0,
            {},
            None,
        )
    )

    assert coordinator.drain_progress("run-7") == ()
    assert coordinator.active_run_id == "run-7"

    blocking.release.set()
    seventh = await coordinator.wait("run-7")
    assert all(event.run_id == "run-7" for event in seventh.progress_events)
    assert next(record.run_id for record in coordinator.history) == "run-7"


@pytest.mark.asyncio
async def test_binding_failure_is_failed_not_blocked() -> None:
    runtime = FakeRuntime(bind_error=ConfigurationError("runtime is not ready"))
    coordinator = await ready_coordinator(runtime)

    run_id = await coordinator.start(local_spec("run-1"))
    record = await coordinator.wait(run_id)

    assert record.state == "failed"
    assert record.spec is not None
    assert record.result is None
    assert record.trace is None
    assert record.error_category == "configuration"
    assert record.public_error is not None
    assert coordinator.state == "ready"


@pytest.mark.asyncio
async def test_an_unexpected_service_exception_becomes_a_public_failure() -> None:
    async def behaviour(_mode: str, _run_id: str) -> RagResult:
        raise ConfigurationError("secret internal detail")

    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    run_id = await coordinator.start(local_spec("run-1"))
    record = await coordinator.wait(run_id)

    assert record.state == "failed"
    assert record.error_category == "configuration"
    assert record.public_error is not None
    assert "secret internal detail" not in record.public_error


@pytest.mark.asyncio
async def test_online_run_requires_a_pending_authorization() -> None:
    coordinator = await ready_coordinator(FakeRuntime())
    spec = RunSpec(
        run_id="run-online",
        created_at=0.0,
        mode="rag",
        scenario_id="scenario",
        question_or_task="question",
        generator="online",
        provider_name="relay",
        provider_fingerprint="fp",
        fallback_local=False,
        online_authorization=_authorization("run-online", "fp", False),
    )

    with pytest.raises(RuntimeError, match="pending authorization"):
        await coordinator.start(spec)


def _authorization(run_id: str, fingerprint: str, fallback: bool) -> object:
    from enterprise_rag.panel.contracts import OnlineRunAuthorization

    return OnlineRunAuthorization(
        authorization_id="auth-1",
        run_id=run_id,
        profile_fingerprint=fingerprint,
        max_requests=2,
        input_token_cap=1_000,
        output_token_cap=100,
        max_cost_cny=Decimal("1"),
        synthetic_data_only=True,
        fallback_local=fallback,
    )


@pytest.mark.asyncio
async def test_five_terminal_state_invariants_survive_the_return_to_ready() -> None:
    async def behaviour(_mode: str, run_id: str) -> RagResult:
        if run_id == "run-failed":
            raise ConfigurationError("the service failed after starting")
        return await _answered(_mode, run_id)

    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    answered_id = await coordinator.start(local_spec("run-answered"))
    answered = await coordinator.wait(answered_id)
    assert answered.state == "answered"
    assert answered.spec is not None and answered.spec.run_id == answered_id
    assert answered.result is not None and answered.trace == answered.result.trace
    assert answered.trace.run_id == answered_id
    assert answered.error_category is None and answered.public_error is None
    assert answered.progress_events
    assert coordinator.state == "ready"

    request = online_request("run-blocked")
    coordinator.begin_authorization(request)
    blocked = coordinator.record_blocked(
        request,
        "authorization_cancelled",
        "the operator declined the online confirmation",
    )
    assert blocked.spec is None
    assert blocked.result is None and blocked.trace is None
    assert blocked.progress_events == ()
    assert blocked.error_category and blocked.public_error
    assert coordinator.state == "ready"

    failed_id = await coordinator.start(local_spec("run-failed"))
    failed = await coordinator.wait(failed_id)
    assert failed.state == "failed"
    assert failed.spec is not None
    assert failed.result is None and failed.trace is None
    assert failed.error_category and failed.public_error
    assert coordinator.state == "ready"

    # Returning the lifecycle to READY must not have mutated any terminal record.
    history = {record.run_id: record for record in coordinator.history}
    assert history["run-answered"] == answered
    assert history["run-blocked"] == blocked
    assert history["run-failed"] == failed


@pytest.mark.asyncio
async def test_a_cancelled_record_keeps_its_prefix_and_one_boundary() -> None:
    behaviour = BlockingBehaviour()
    coordinator = await ready_coordinator(FakeRuntime(behaviour=behaviour))

    run_id = await coordinator.start(local_spec("run-cancelled"))
    await behaviour.started.wait()
    assert coordinator.request_cancel(run_id) is True
    behaviour.release.set()
    record = await coordinator.wait(run_id)

    assert record.state == "cancelled"
    assert record.spec is not None and record.spec.run_id == run_id
    assert record.result is None and record.trace is None
    assert record.error_category is None and record.public_error is None
    boundaries = [
        event for event in record.progress_events if event.stage == "cancel_boundary"
    ]
    assert len(boundaries) == 1
    assert boundaries[0].status == "cancelled"
    assert boundaries[0].run_id == run_id
    assert all(event.run_id == run_id for event in record.progress_events)
    assert coordinator.state == "ready"


@pytest.mark.asyncio
async def test_aclose_settles_the_active_run_and_closes_the_runtime_once() -> None:
    behaviour = BlockingBehaviour()
    runtime = FakeRuntime(behaviour=behaviour)
    coordinator = await ready_coordinator(runtime)
    await coordinator.start(local_spec("run-1"))
    await behaviour.started.wait()

    closing = asyncio.create_task(coordinator.aclose())
    behaviour.release.set()
    await closing

    assert runtime.aclose_calls == 1
    assert coordinator.history == ()

    await coordinator.aclose()
    assert runtime.aclose_calls == 1
