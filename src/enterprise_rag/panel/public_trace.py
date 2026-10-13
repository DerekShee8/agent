"""The public trace boundary: one whitelist, one denylist, hard size caps.

Everything a browser can see about a run passes through
:func:`sanitize_public_trace_event`. This module has no NiceGUI import and never
inspects an answer, so a display bug can never change a business conclusion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from enum import Enum

from ..execution_trace import TraceEvent, _freeze_mapping

MAX_RANKED_ROWS = 5
MAX_TEXT_CHARS = 300

# The canonical per-stage whitelist. ``panel.projection`` imports this mapping
# rather than defining a second one.
PUBLIC_STAGE_FIELDS: Mapping[str, frozenset[str]] = {
    "retrieval": frozenset({"backend_kind"}),
    "dense": frozenset({"items", "backend_kind"}),
    "sparse": frozenset({"items", "backend_kind"}),
    "rrf_fusion": frozenset({"items"}),
    "reranking": frozenset({"items", "backend_kind"}),
    "evidence_gate": frozenset(
        {"threshold", "score", "support_ids", "decision", "reason"}
    ),
    "generation": frozenset(
        {
            "backend_kind",
            "model_id",
            "model_revision",
            "model_digest",
            "usage",
            "cost_cny",
        }
    ),
    "grounding_validation": frozenset({"issue_count", "support_ids", "backend_kind"}),
    "grounding_issue": frozenset({"attempt", "claim_index", "code"}),
    "repair_generation": frozenset(
        {
            "backend_kind",
            "model_id",
            "model_revision",
            "model_digest",
            "usage",
            "cost_cny",
        }
    ),
    "repair_validation": frozenset({"issue_count", "support_ids", "backend_kind"}),
    "backend_switch": frozenset({"from_backend", "to_backend"}),
    "agent_plan": frozenset({"step", "backend_kind"}),
    "agent_action": frozenset({"step", "action", "arguments"}),
    "search_knowledge": frozenset({"step"}),
    "agent_observation": frozenset({"step", "observation_id", "source", "status"}),
    "agent_finish": frozenset({"step"}),
    "cancel_boundary": frozenset(),
}

# Credential-ish key fragments are rejected anywhere in a nested structure.
_DENIED_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "secret",
    "password",
    "passwd",
    "credential",
)
# Exact matches, so public counters such as ``max_output_tokens`` survive.
_DENIED_KEYS = frozenset(
    {
        "token",
        "key",
        "prompt",
        "system_prompt",
        "system-prompt",
        "user_prompt",
        "traceback",
        "stack_trace",
        "environment",
        "environ",
        "env",
    }
)


def _is_denied_key(key: str) -> bool:
    folded = key.casefold()
    if folded in _DENIED_KEYS:
        return True
    return any(fragment in folded for fragment in _DENIED_KEY_FRAGMENTS)


def _sanitize_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _sanitize_value(item)
            for key, item in value.items()
            if isinstance(key, str) and not _is_denied_key(key)
        }
    if isinstance(value, (list, tuple)):
        items = tuple(_sanitize_value(item) for item in value)
        if all(isinstance(item, Mapping) for item in items):
            # Ranked rows are the only sequences the panel shows one row per line.
            return items[:MAX_RANKED_ROWS]
        return items
    if isinstance(value, str):
        return value[:MAX_TEXT_CHARS]
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return _sanitize_value(value.value)
    return value


def sanitize_public_trace_event(event: TraceEvent) -> TraceEvent:
    """Return a public-safe copy of one event; the input is never mutated.

    Unknown stages keep their structural fields and lose every attribute.
    """

    if not isinstance(event, TraceEvent):
        raise TypeError("event must be a TraceEvent")
    allowed = PUBLIC_STAGE_FIELDS.get(event.stage, frozenset())
    attributes = {
        key: _sanitize_value(value)
        for key, value in event.attributes.items()
        if key in allowed and not _is_denied_key(key)
    }
    return replace(event, attributes=_freeze_mapping(attributes))


def reconcile_terminal_events(
    progress_prefix: tuple[TraceEvent, ...],
    trace: object,
) -> tuple[TraceEvent, ...]:
    """Merge live progress with the authoritative final trace.

    Every final trace event appears exactly once with the trace's own
    ``event_id``/``parent_id``/``stage``/``status``/``error_category``, while its
    displayed attributes stay public-safe. This never invents a business
    conclusion and never modifies the trace.
    """

    from ..execution_trace import RunTrace

    if not isinstance(trace, RunTrace):
        raise TypeError("trace must be a RunTrace")
    if not isinstance(progress_prefix, tuple):
        raise TypeError("progress_prefix must be a tuple")
    if any(not isinstance(event, TraceEvent) for event in progress_prefix):
        raise TypeError("progress_prefix must contain only TraceEvent")

    final_by_id = {
        event.event_id: sanitize_public_trace_event(event) for event in trace.events
    }
    merged: list[TraceEvent] = []
    positions: dict[str, int] = {}
    for event in progress_prefix:
        if event.event_id in positions:
            continue
        positions[event.event_id] = len(merged)
        merged.append(final_by_id.get(event.event_id, event))
    for event in trace.events:
        if event.event_id in positions:
            continue
        positions[event.event_id] = len(merged)
        merged.append(final_by_id[event.event_id])
    return tuple(merged)
