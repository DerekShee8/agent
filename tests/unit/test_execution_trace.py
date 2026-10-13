import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Literal

import pytest


class FakeClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


@pytest.mark.parametrize(
    "status",
    ["started", "succeeded", "refused", "failed", "skipped", "degraded", "cancelled"],
)
def test_trace_event_accepts_only_approved_statuses(
    status: Literal[
        "started",
        "succeeded",
        "refused",
        "failed",
        "skipped",
        "degraded",
        "cancelled",
    ],
) -> None:
    from enterprise_rag.execution_trace import TraceEvent

    event = TraceEvent(
        "run-001", "event-1", None, "retrieval", status, 1.0, 0.0, {}, None
    )

    assert event.status == status
    assert not hasattr(event, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(event, "status", "failed")  # noqa: B010


def test_trace_event_rejects_invalid_status_and_negative_timing() -> None:
    from enterprise_rag.execution_trace import TraceEvent

    with pytest.raises(ValueError, match="invalid trace event status"):
        TraceEvent(
            "run-001",
            "event-1",
            None,
            "retrieval",
            "unknown",  # ty: ignore[invalid-argument-type]
            1.0,
            0.0,
            {},
            None,
        )
    with pytest.raises(ValueError, match="started_at must be non-negative and finite"):
        TraceEvent(
            "run-001", "event-1", None, "retrieval", "started", -1.0, 0.0, {}, None
        )
    with pytest.raises(ValueError, match="duration_ms must be non-negative and finite"):
        TraceEvent(
            "run-001", "event-1", None, "retrieval", "started", 1.0, -1.0, {}, None
        )


@pytest.mark.parametrize("field_name", ["started_at", "duration_ms"])
@pytest.mark.parametrize("invalid_value", [True, Decimal("1.25")])
def test_trace_event_rejects_non_json_native_timing_values(
    field_name: str,
    invalid_value: object,
) -> None:
    from enterprise_rag.execution_trace import TraceEvent

    values: dict[str, object] = {
        "run_id": "run-001",
        "event_id": "event-1",
        "parent_id": None,
        "stage": "retrieval",
        "status": "succeeded",
        "started_at": 1.0,
        "duration_ms": 2.0,
        "attributes": {},
        "error_category": None,
    }
    values[field_name] = invalid_value

    with pytest.raises(TypeError, match=rf"{field_name} must be an int or float"):
        TraceEvent(**values)  # ty: ignore[invalid-argument-type]


def test_trace_event_normalizes_integer_timing_for_json() -> None:
    from enterprise_rag.execution_trace import RunTrace, TraceEvent

    event = TraceEvent(
        "run-001",
        "event-1",
        None,
        "retrieval",
        "succeeded",
        1,
        2,
        {},
        None,
    )

    assert event.started_at == 1.0
    assert isinstance(event.started_at, float)
    assert event.duration_ms == 2.0
    assert isinstance(event.duration_ms, float)
    payload = json.loads(RunTrace("run-001", "succeeded", (event,)).to_json())
    assert payload["events"][0]["started_at"] == 1.0
    assert payload["events"][0]["duration_ms"] == 2.0


def test_run_trace_requires_terminal_status_and_tuple_events() -> None:
    from enterprise_rag.execution_trace import RunTrace, TraceEvent

    event = TraceEvent(
        "run-001", "event-1", None, "retrieval", "succeeded", 1.0, 2.0, {}, None
    )
    trace = RunTrace("run-001", "succeeded", (event,))

    assert trace.events == (event,)
    with pytest.raises(TypeError, match="events must be a tuple"):
        RunTrace(
            "run-001",
            "succeeded",
            [event],  # ty: ignore[invalid-argument-type]
        )
    with pytest.raises(ValueError, match="invalid run status"):
        RunTrace("run-001", "started", ())  # ty: ignore[invalid-argument-type]


def test_run_trace_rejects_non_event_members_before_attribute_access() -> None:
    from enterprise_rag.execution_trace import RunTrace

    with pytest.raises(TypeError, match="events must contain only TraceEvent"):
        RunTrace(
            "run-001",
            "succeeded",
            (object(),),  # ty: ignore[invalid-argument-type]
        )


@pytest.mark.parametrize("tree_shape", ["orphan", "self", "forward"])
def test_run_trace_requires_parents_to_precede_children(tree_shape: str) -> None:
    from enterprise_rag.execution_trace import RunTrace, TraceEvent

    parent_id = {
        "orphan": "missing",
        "self": "event-1",
        "forward": "event-2",
    }[tree_shape]
    events = [
        TraceEvent(
            "run-001",
            "event-1",
            parent_id,
            "child",
            "succeeded",
            1.0,
            1.0,
            {},
            None,
        )
    ]
    if tree_shape == "forward":
        events.append(
            TraceEvent(
                "run-001",
                "event-2",
                None,
                "parent",
                "succeeded",
                0.0,
                3.0,
                {},
                None,
            )
        )

    with pytest.raises(
        ValueError,
        match="parent_id must refer to an earlier event",
    ):
        RunTrace("run-001", "succeeded", tuple(events))


def test_recorder_preserves_start_order_tree_links_and_monotonic_timing() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder(
        run_id="run-001",
        clock=FakeClock(10.0, 10.1, 10.15, 10.4),
    )
    with recorder.span("retrieval") as parent:
        with recorder.span("dense", parent_id=parent.event_id):
            pass
    trace = recorder.finish("succeeded")

    assert [event.stage for event in trace.events] == ["retrieval", "dense"]
    assert trace.events[0].parent_id is None
    assert trace.events[1].parent_id == trace.events[0].event_id
    assert trace.events[0].duration_ms == pytest.approx(400.0)
    assert trace.events[1].duration_ms == pytest.approx(50.0)
    assert all(event.duration_ms >= 0 for event in trace.events)
    assert all(event.status == "succeeded" for event in trace.events)


def test_recorder_rejects_closed_parent_when_creating_child_span() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-closed", clock=FakeClock(0.0, 0.1))
    with recorder.span("parent") as parent:
        pass

    with pytest.raises(ValueError, match="parent_id must reference an active span"):
        recorder.span("child", parent_id=parent.event_id)


def test_recorder_rechecks_parent_when_delayed_child_span_enters() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-delayed", clock=FakeClock(0.0, 0.1, 0.2))
    with recorder.span("parent") as parent:
        delayed_child = recorder.span("child", parent_id=parent.event_id)

    with pytest.raises(ValueError, match="parent_id must reference an active span"):
        delayed_child.__enter__()


def test_trace_attributes_are_recursively_redacted_before_immutable_storage() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    attributes = {
        "api_key": "alpha",
        "nested": {
            "Authorization": "Bearer beta",
            "items": [
                {"secret": "gamma"},
                {"safe": "visible", "TOKEN": "delta"},
            ],
        },
        "tuple_data": ({"password": "epsilon"}, {"system_prompt": "hidden"}),
    }
    recorder = TraceRecorder("run-redaction", clock=FakeClock(1.0, 1.1))
    with recorder.span("generation", attributes=attributes):
        attributes["api_key"] = "mutated"
        attributes["nested"]["items"].append({"secret": "later"})  # ty: ignore[index-not-subscriptable,unresolved-attribute]
    trace = recorder.finish("succeeded")
    event_attributes = trace.events[0].attributes

    assert event_attributes["api_key"] == "<redacted>"
    assert event_attributes["nested"]["Authorization"] == "<redacted>"  # ty: ignore[index-not-subscriptable]
    assert event_attributes["nested"]["items"][1]["safe"] == "visible"  # ty: ignore[index-not-subscriptable]
    with pytest.raises(TypeError):
        event_attributes["new"] = "value"  # ty: ignore[index-assignment]

    serialized = trace.to_json()
    for leaked_secret in (
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "hidden",
        "mutated",
        "later",
    ):
        assert leaked_secret not in serialized
    assert serialized.count("<redacted>") == 6


def test_trace_event_direct_construction_also_snapshots_and_redacts() -> None:
    from enterprise_rag.execution_trace import TraceEvent

    attributes = {"safe": {"value": 1}, "password": "secret"}
    event = TraceEvent(
        "run-001",
        "event-1",
        None,
        "tool",
        "succeeded",
        0.0,
        1.0,
        attributes,
        None,
    )
    attributes["safe"]["value"] = 2  # ty: ignore[index-not-subscriptable]

    assert event.attributes["safe"]["value"] == 1  # ty: ignore[index-not-subscriptable]
    assert event.attributes["password"] == "<redacted>"


def test_span_exception_records_failure_category_and_reraises() -> None:
    from enterprise_rag.errors import ToolExecutionError
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-error", clock=FakeClock(2.0, 2.025))
    with pytest.raises(ToolExecutionError, match="tool exploded"):
        with recorder.span("tool"):
            raise ToolExecutionError("tool exploded")

    trace = recorder.finish("failed")
    assert trace.events[0].status == "failed"
    assert trace.events[0].error_category == "tool_execution"
    assert trace.events[0].duration_ms == pytest.approx(25.0)


def test_span_can_set_explicit_terminal_status_and_category() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-degraded", clock=FakeClock(3.0, 3.01))
    with recorder.span("retrieval") as span:
        span.finish("degraded", error_category="retrieval_unavailable")
    trace = recorder.finish("degraded")

    assert trace.events[0].status == "degraded"
    assert trace.events[0].error_category == "retrieval_unavailable"


def test_recorder_has_clear_single_use_finish_behavior() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-once", clock=FakeClock(0.0, 0.0))
    with recorder.span("work"):
        pass
    trace = recorder.finish("succeeded")

    assert trace.status == "succeeded"
    with pytest.raises(RuntimeError, match="recorder is already finished"):
        recorder.finish("succeeded")
    with pytest.raises(RuntimeError, match="recorder is already finished"):
        recorder.span("late")


def test_recorder_cannot_finish_with_open_spans() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-open", clock=FakeClock(0.0, 0.1))
    span = recorder.span("open")
    span.__enter__()

    with pytest.raises(RuntimeError, match="cannot finish while spans are open"):
        recorder.finish("failed")

    span.__exit__(None, None, None)
    assert recorder.finish("succeeded").status == "succeeded"


def test_trace_json_is_deterministic_and_structured() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-json", clock=FakeClock(4.0, 4.02))
    with recorder.span("stage", attributes={"z": 1, "a": "two"}):
        pass
    trace = recorder.finish("succeeded")

    first = trace.to_json()
    second = trace.to_json()
    payload = json.loads(first)

    assert first == second
    assert payload["run_id"] == "run-json"
    assert payload["status"] == "succeeded"
    assert payload["events"][0]["attributes"] == {"a": "two", "z": 1}


def test_empty_trace_has_stable_identity_values() -> None:
    from enterprise_rag.execution_trace import RunTrace

    first = RunTrace.empty()
    second = RunTrace.empty()

    assert first == second
    assert first.run_id == "untraced"
    assert first.status == "skipped"
    assert first.events == ()


@pytest.mark.parametrize(
    ("case", "field_name"),
    [
        ("event_run_id", "run_id"),
        ("event_id", "event_id"),
        ("parent_id", "parent_id"),
        ("stage", "stage"),
        ("event_status", "status"),
        ("event_error_category", "error_category"),
        ("run_id", "run_id"),
        ("run_status", "status"),
        ("recorder_run_id", "run_id"),
        ("span_stage", "stage"),
        ("span_parent_id", "parent_id"),
        ("span_status", "status"),
        ("span_error_category", "error_category"),
        ("recorder_status", "status"),
    ],
)
def test_every_public_trace_string_path_rejects_non_strings(
    case: str,
    field_name: str,
) -> None:
    from enterprise_rag.execution_trace import RunTrace, TraceEvent, TraceRecorder

    invalid: object = 123
    constructors = {
        "event_run_id": lambda: TraceEvent(
            invalid, "event", None, "stage", "succeeded", 0.0, 0.0, {}, None
        ),
        "event_id": lambda: TraceEvent(
            "run", invalid, None, "stage", "succeeded", 0.0, 0.0, {}, None
        ),
        "parent_id": lambda: TraceEvent(
            "run", "event", invalid, "stage", "succeeded", 0.0, 0.0, {}, None
        ),
        "stage": lambda: TraceEvent(
            "run", "event", None, invalid, "succeeded", 0.0, 0.0, {}, None
        ),
        "event_status": lambda: TraceEvent(
            "run", "event", None, "stage", invalid, 0.0, 0.0, {}, None
        ),
        "event_error_category": lambda: TraceEvent(
            "run", "event", None, "stage", "failed", 0.0, 0.0, {}, invalid
        ),
        "run_id": lambda: RunTrace(invalid, "succeeded", ()),
        "run_status": lambda: RunTrace("run", invalid, ()),
        "recorder_run_id": lambda: TraceRecorder(invalid),
        "span_stage": lambda: TraceRecorder("run").span(invalid),
        "span_parent_id": lambda: TraceRecorder("run").span(
            "stage",
            parent_id=invalid,
        ),
        "span_status": lambda: _finish_span_with_invalid_status(invalid),
        "span_error_category": lambda: _finish_span_with_invalid_error(invalid),
        "recorder_status": lambda: TraceRecorder("run").finish(invalid),
    }

    with pytest.raises(TypeError, match=rf"{field_name} must be a string"):
        constructors[case]()


def _finish_span_with_invalid_error(invalid: object) -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run")
    with recorder.span("stage") as span:
        span.finish(
            "failed",
            error_category=invalid,  # ty: ignore[invalid-argument-type]
        )


def _finish_span_with_invalid_status(invalid: object) -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run")
    with recorder.span("stage") as span:
        span.finish(invalid)  # ty: ignore[invalid-argument-type]


def test_recorder_notifies_observer_once_per_span_lifecycle() -> None:
    from enterprise_rag.execution_trace import TraceEvent, TraceRecorder

    events: list[TraceEvent] = []
    recorder = TraceRecorder(
        "run-1",
        observer=events.append,
        clock=FakeClock(0.0, 0.5),
    )
    with recorder.span("retrieval") as span:
        span.finish("succeeded", attributes={"items": ()})
    trace = recorder.finish("succeeded")

    assert [event.status for event in events] == ["started", "succeeded"]
    assert events[0].event_id == events[1].event_id
    assert events[0].run_id == "run-1"
    assert trace.events == (events[1],)


def test_observer_failure_never_changes_the_business_trace() -> None:
    from enterprise_rag.execution_trace import TraceEvent, TraceRecorder

    observed: list[str] = []

    def exploding_observer(event: TraceEvent) -> None:
        observed.append(event.status)
        raise RuntimeError("observer exploded")

    recorder = TraceRecorder(
        "run-1",
        observer=exploding_observer,
        clock=FakeClock(0.0, 0.5),
    )
    with recorder.span("retrieval") as span:
        span.finish("succeeded")
    trace = recorder.finish("succeeded")

    assert observed == ["started", "succeeded"]
    assert [event.status for event in trace.events] == ["succeeded"]
    assert trace.events[0].duration_ms == pytest.approx(500.0)


def test_terminal_attributes_merge_into_start_attributes_and_stay_redacted() -> None:
    from enterprise_rag.execution_trace import TraceRecorder

    recorder = TraceRecorder("run-1", clock=FakeClock(0.0, 0.5))
    with recorder.span("generation", attributes={"model_id": "qwen"}) as span:
        span.finish("succeeded", attributes={"token": "secret", "usage": {"out": 4}})
    trace = recorder.finish("succeeded")
    event = trace.events[0]

    assert event.attributes["model_id"] == "qwen"
    assert event.attributes["token"] == "<redacted>"
    assert event.attributes["usage"]["out"] == 4  # ty: ignore[index-not-subscriptable]
    assert "secret" not in trace.to_json()


def test_run_trace_rejects_events_carried_over_from_another_run() -> None:
    from enterprise_rag.execution_trace import RunTrace, TraceEvent

    foreign = TraceEvent(
        "other-run",
        "other-run:0001",
        None,
        "retrieval",
        "succeeded",
        1.0,
        2.0,
        {},
        None,
    )

    with pytest.raises(ValueError, match="run id"):
        RunTrace("run-001", "succeeded", (foreign,))


def test_trace_scope_binds_a_recorder_to_one_parent_event() -> None:
    from enterprise_rag.execution_trace import TraceRecorder, TraceScope

    recorder = TraceRecorder("run-1", clock=FakeClock(0.0, 0.1, 0.2, 0.3))
    with recorder.span("retrieval") as parent:
        scope = TraceScope(recorder, parent.event_id)
        with scope.recorder.span("dense", parent_id=scope.parent_id):
            pass
    trace = recorder.finish("succeeded")

    assert scope.recorder is recorder
    assert scope.parent_id == trace.events[0].event_id
    assert trace.events[1].parent_id == trace.events[0].event_id
