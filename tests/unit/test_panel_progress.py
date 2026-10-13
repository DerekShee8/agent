"""Bounded, coalescing progress delivery between the recorder and the browser."""

from __future__ import annotations

import pytest

from enterprise_rag.execution_trace import TraceEvent
from enterprise_rag.panel.progress import BoundedProgressBuffer

SECRET = "sk-live-should-never-surface"
CRITICAL_STAGES = (
    "generation",
    "repair_generation",
    "backend_switch",
    "cancel_boundary",
    "agent_finish",
)


def event(
    event_id: str,
    *,
    stage: str = "retrieval",
    status: str = "started",
    attributes: dict[str, object] | None = None,
    error_category: str | None = None,
) -> TraceEvent:
    return TraceEvent(
        "run-1",
        event_id,
        None,
        stage,
        status,  # type: ignore[arg-type]
        1.0,
        2.0,
        attributes or {},
        error_category,
    )


def test_repeated_started_snapshots_coalesce_to_one_entry_per_event_id() -> None:
    buffer = BoundedProgressBuffer()
    buffer.subscribe("browser")

    for _ in range(1_000):
        for index in range(8):
            buffer.publish(event(f"run-1:{index:04d}"))

    assert buffer.pending_count == 8
    assert not buffer.detached

    for index in range(8):
        buffer.publish(event(f"run-1:{index:04d}", status="succeeded"))

    drained = buffer.drain()
    assert buffer.pending_count == 0
    assert [item.event_id for item in drained] == [
        f"run-1:{index:04d}" for index in range(8)
    ]
    assert all(item.status == "succeeded" for item in drained)


def test_critical_events_survive_a_flood_of_non_terminal_updates() -> None:
    buffer = BoundedProgressBuffer(capacity=256)
    buffer.subscribe("browser")

    for _ in range(1_000):
        buffer.publish(event("run-1:0001"))

    critical_ids: set[str] = set()
    for index, stage in enumerate(CRITICAL_STAGES, start=2):
        event_id = f"run-1:{index:04d}"
        critical_ids.add(event_id)
        buffer.publish(
            event(
                event_id,
                stage=stage,
                status="succeeded" if stage != "cancel_boundary" else "cancelled",
                error_category="authentication" if stage == "backend_switch" else None,
            )
        )

    assert buffer.pending_count <= 256
    drained = buffer.drain()
    assert critical_ids <= {item.event_id for item in drained}
    assert len({item.event_id for item in drained}) == len(drained)


def test_capacity_evicts_the_oldest_started_update_only() -> None:
    buffer = BoundedProgressBuffer(capacity=3)
    buffer.subscribe("browser")
    buffer.publish(event("run-1:0001", status="succeeded"))
    buffer.publish(event("run-1:0002"))
    buffer.publish(event("run-1:0003"))

    buffer.publish(event("run-1:0004"))

    drained = buffer.drain()
    assert [item.event_id for item in drained] == [
        "run-1:0001",
        "run-1:0003",
        "run-1:0004",
    ]
    assert buffer.pending_count == 0


def test_a_full_buffer_of_terminal_events_detaches_instead_of_raising() -> None:
    buffer = BoundedProgressBuffer(capacity=2)
    buffer.subscribe("browser")
    buffer.publish(event("run-1:0001", status="succeeded"))
    buffer.publish(event("run-1:0002", status="refused"))

    buffer.publish(event("run-1:0003", status="succeeded"))

    assert buffer.detached
    assert buffer.subscribers == ()
    assert [item.event_id for item in buffer.drain()] == [
        "run-1:0001",
        "run-1:0002",
    ]
    buffer.publish(event("run-1:0004", status="succeeded"))
    assert buffer.drain() == ()


def test_duplicate_subscription_is_rejected_and_detach_clears() -> None:
    buffer = BoundedProgressBuffer()
    consumer = object()
    buffer.subscribe(consumer)

    with pytest.raises(ValueError, match="already subscribed"):
        buffer.subscribe(consumer)

    buffer.detach()
    assert buffer.detached
    assert buffer.subscribers == ()
    with pytest.raises(RuntimeError, match="detached"):
        buffer.subscribe(object())


def test_closed_buffer_ignores_further_updates() -> None:
    buffer = BoundedProgressBuffer()
    buffer.subscribe("browser")

    buffer.close()
    buffer.publish(event("run-1:0001"))

    assert buffer.pending_count == 0
    assert buffer.drain() == ()


def test_a_failing_sanitizer_detaches_without_raising() -> None:
    def exploding_sanitizer(_event: TraceEvent) -> TraceEvent:
        raise RuntimeError("sanitizer exploded")

    buffer = BoundedProgressBuffer(sanitizer=exploding_sanitizer)
    buffer.subscribe("browser")

    buffer.publish(event("run-1:0001"))

    assert buffer.detached
    assert buffer.pending_count == 0


def test_drained_events_never_carry_forbidden_values() -> None:
    buffer = BoundedProgressBuffer()
    buffer.subscribe("browser")

    buffer.publish(
        event(
            "run-1:0001",
            stage="generation",
            status="succeeded",
            attributes={
                "backend_kind": "real",
                "api_key": SECRET,
                "model_id": "provider-model",
            },
        )
    )

    drained = buffer.drain()
    assert len(drained) == 1
    assert "api_key" not in drained[0].attributes
    assert SECRET not in repr(dict(drained[0].attributes))
    assert drained[0].attributes["model_id"] == "provider-model"


def test_capacity_must_be_a_positive_integer() -> None:
    with pytest.raises(ValueError):
        BoundedProgressBuffer(capacity=0)
    with pytest.raises(TypeError):
        BoundedProgressBuffer(capacity=True)
