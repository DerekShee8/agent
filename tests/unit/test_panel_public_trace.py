"""The public trace boundary: whitelist, recursive redaction, and hard caps."""

from __future__ import annotations

from decimal import Decimal

import pytest

from enterprise_rag.execution_trace import RunTrace, TraceEvent, TraceRecorder
from enterprise_rag.panel.public_trace import (
    MAX_RANKED_ROWS,
    MAX_TEXT_CHARS,
    PUBLIC_STAGE_FIELDS,
    reconcile_terminal_events,
    sanitize_public_trace_event,
)
from tests.fakes import FakeClock

SECRET = "sk-live-should-never-surface"


def event(
    stage: str,
    *,
    attributes: dict[str, object] | None = None,
    status: str = "succeeded",
    event_id: str = "run-1:0001",
    parent_id: str | None = None,
    error_category: str | None = None,
    run_id: str = "run-1",
) -> TraceEvent:
    return TraceEvent(
        run_id,
        event_id,
        parent_id,
        stage,
        status,  # type: ignore[arg-type]
        1.0,
        2.0,
        attributes or {},
        error_category,
    )


@pytest.mark.parametrize("stage", sorted(PUBLIC_STAGE_FIELDS))
def test_sanitizer_keeps_only_whitelisted_stage_fields(stage: str) -> None:
    allowed = PUBLIC_STAGE_FIELDS[stage]
    attributes: dict[str, object] = {key: "value" for key in allowed}
    attributes["unlisted_field"] = "must not survive"

    sanitized = sanitize_public_trace_event(event(stage, attributes=attributes))

    assert set(sanitized.attributes) == set(allowed)
    assert sanitized.event_id == "run-1:0001"
    assert sanitized.stage == stage
    assert sanitized.status == "succeeded"
    assert sanitized.run_id == "run-1"


def test_sanitizer_drops_nested_credential_material_recursively() -> None:
    attributes = {
        "items": (
            {
                "chunk_id": "policy.md::0000",
                "api_key": SECRET,
                "nested": {"authorization": f"Bearer {SECRET}", "safe": "ok"},
            },
        ),
        "backend_kind": "real",
    }

    sanitized = sanitize_public_trace_event(event("reranking", attributes=attributes))
    serialized = repr(dict(sanitized.attributes))

    assert SECRET not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized
    row = sanitized.attributes["items"][0]  # type: ignore[index]
    assert row["chunk_id"] == "policy.md::0000"
    assert row["nested"] == {"safe": "ok"}


def test_sanitizer_caps_ranked_rows_and_preview_strings() -> None:
    rows = tuple(
        {"chunk_id": f"policy.md::{index:04d}", "text_preview": "x" * 500}
        for index in range(6)
    )

    sanitized = sanitize_public_trace_event(event("dense", attributes={"items": rows}))

    kept = sanitized.attributes["items"]
    assert len(kept) == MAX_RANKED_ROWS
    assert len(kept[0]["text_preview"]) == MAX_TEXT_CHARS


def test_sanitizer_keeps_short_scalar_sequences_intact() -> None:
    support_ids = tuple(f"policy.md::{index:04d}" for index in range(9))

    sanitized = sanitize_public_trace_event(
        event("evidence_gate", attributes={"support_ids": support_ids})
    )

    assert sanitized.attributes["support_ids"] == support_ids


def test_sanitizer_keeps_json_native_costs_and_usage() -> None:
    sanitized = sanitize_public_trace_event(
        event(
            "generation",
            attributes={
                "backend_kind": "real",
                "model_id": "provider-model",
                "usage": {"input": 10, "output": 4},
                "cost_cny": "0.003",
            },
        )
    )

    assert sanitized.attributes["cost_cny"] == "0.003"
    assert sanitized.attributes["usage"] == {"input": 10, "output": 4}


def test_decimal_costs_normalize_to_strings_for_future_callers() -> None:
    """``TraceEvent`` rejects Decimal outright, so this branch is defensive.

    Cost must be made JSON-native before it enters a trace; this guards any
    future caller that hands the sanitizer a Decimal-bearing structure.
    """

    from enterprise_rag.panel.public_trace import _sanitize_value

    assert _sanitize_value(Decimal("0.0030")) == "0.003"
    assert _sanitize_value({"cost_cny": Decimal("1.50")}) == {"cost_cny": "1.5"}


def test_sanitizer_leaves_unknown_stages_with_structure_only() -> None:
    sanitized = sanitize_public_trace_event(
        event(
            "some_future_stage",
            attributes={"anything": SECRET},
            error_category="protocol",
        )
    )

    assert sanitized.attributes == {}
    assert sanitized.stage == "some_future_stage"
    assert sanitized.error_category == "protocol"


def test_sanitizer_never_mutates_the_input_event() -> None:
    # TraceEvent already redacts a credential key at construction; the sanitizer
    # then drops the key entirely, and the original event is left untouched.
    original = event(
        "generation",
        attributes={"backend_kind": "real", "api_key": SECRET},
    )
    before = dict(original.attributes)

    sanitized = sanitize_public_trace_event(original)

    assert dict(original.attributes) == before
    assert before == {"backend_kind": "real", "api_key": "<redacted>"}
    assert "api_key" not in sanitized.attributes


def test_sanitizer_rejects_non_events() -> None:
    with pytest.raises(TypeError):
        sanitize_public_trace_event("not an event")  # type: ignore[arg-type]


def _traced_run() -> RunTrace:
    recorder = TraceRecorder("run-1", clock=FakeClock())
    with recorder.span("retrieval", attributes={"backend_kind": "real"}) as span:
        span.finish("succeeded", attributes={"items": ({"chunk_id": "a"},)})
    with recorder.span("evidence_gate", attributes={"decision": "refused"}):
        pass
    return recorder.finish("refused")


def test_reconcile_replaces_live_snapshots_with_the_authoritative_trace() -> None:
    trace = _traced_run()
    first, second = trace.events
    prefix = (
        event(
            "retrieval",
            status="started",
            event_id=first.event_id,
            attributes={"backend_kind": "real", "api_key": SECRET},
        ),
        event(
            "cancel_boundary",
            status="cancelled",
            event_id="run-1:9999",
        ),
    )

    merged = reconcile_terminal_events(prefix, trace)

    assert [item.event_id for item in merged] == [
        first.event_id,
        "run-1:9999",
        second.event_id,
    ]
    assert [item.event_id for item in merged].count(first.event_id) == 1
    replaced = merged[0]
    assert (
        replaced.parent_id,
        replaced.stage,
        replaced.status,
        replaced.error_category,
    ) == (
        first.parent_id,
        first.stage,
        first.status,
        first.error_category,
    )
    assert SECRET not in repr(dict(replaced.attributes))


def test_reconcile_rejects_a_non_trace() -> None:
    with pytest.raises(TypeError):
        reconcile_terminal_events((), "not a trace")
