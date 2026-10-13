from __future__ import annotations

import json
from collections.abc import Mapping

from .application import CommandOutcome
from .execution_trace import RunTrace, TraceEvent
from .models import AgentResult, RagResult, Source


def render_rag_result(
    result: RagResult,
    *,
    output_format: str,
    include_trace: bool,
) -> str:
    payload = {
        "answer": result.answer,
        "error_category": result.error_category,
        "refusal_reason": result.refusal_reason,
        "sources": [_source_payload(source) for source in result.sources],
        "status": result.status,
        "trace": _trace_events(result.trace) if include_trace else [],
    }
    return _render_result_payload(
        payload,
        result.trace,
        output_format=output_format,
        include_trace=include_trace,
    )


def render_agent_result(
    result: AgentResult,
    *,
    output_format: str,
    include_trace: bool,
) -> str:
    payload = {
        "answer": result.answer,
        "claims": [
            {
                "support_ids": list(claim.support_ids),
                "supporting_quotes": list(claim.supporting_quotes),
                "text": claim.text,
            }
            for claim in result.claims
        ],
        "error_category": result.error_category,
        "observations": [
            {
                "facts": _plain(observation.facts),
                "observation_id": observation.observation_id,
                "source": observation.source,
                "status": observation.status,
                "support_text": observation.support_text,
            }
            for observation in result.observations
        ],
        "refusal_reason": result.refusal_reason,
        "sources": [_source_payload(source) for source in result.sources],
        "status": result.status,
        "tool_trace": [
            {
                "arguments": _plain(call.arguments),
                "error_category": call.error_category,
                "name": call.name,
                "status": call.status,
            }
            for call in result.tool_trace
        ],
        "trace": _trace_events(result.trace) if include_trace else [],
    }
    return _render_result_payload(
        payload,
        result.trace,
        output_format=output_format,
        include_trace=include_trace,
    )


def render_command_outcome(outcome: CommandOutcome) -> str:
    return _json(
        {
            "data": _plain(outcome.data),
            "error_category": outcome.error_category,
            "status": outcome.status,
        }
    )


def _render_result_payload(
    payload: dict[str, object],
    trace: RunTrace,
    *,
    output_format: str,
    include_trace: bool,
) -> str:
    if output_format == "json":
        return _json(payload)
    if output_format != "text":
        raise ValueError("output_format must be text or json")

    lines = [f"status: {payload['status']}"]
    if payload.get("answer") is not None:
        lines.append(f"answer: {payload['answer']}")
    if payload.get("refusal_reason") is not None:
        lines.append(f"refusal_reason: {payload['refusal_reason']}")
    if payload.get("error_category") is not None:
        lines.append(f"error_category: {payload['error_category']}")
    sources = payload.get("sources")
    if isinstance(sources, list) and sources:
        lines.append("sources:")
        lines.extend(
            f"- {source['chunk_id']} ({source['filename']}, {source['score']:.6f})"
            for source in sources
        )
    lines.append(f"duration_ms: {_total_duration(trace):.3f}")
    if include_trace:
        lines.append("trace:")
        lines.extend(
            f"- {event.stage}: {event.status} ({event.duration_ms:.3f} ms)"
            for event in trace.events
        )
    return "\n".join(lines)


def _source_payload(source: Source) -> dict[str, object]:
    return {
        "chunk_id": source.chunk_id,
        "filename": source.filename,
        "score": source.score,
    }


def _trace_events(trace: RunTrace) -> list[dict[str, object]]:
    return [_trace_event_payload(event) for event in trace.events]


def _trace_event_payload(event: TraceEvent) -> dict[str, object]:
    return {
        "attributes": _plain(event.attributes),
        "duration_ms": event.duration_ms,
        "error_category": event.error_category,
        "event_id": event.event_id,
        "parent_id": event.parent_id,
        "stage": event.stage,
        "started_at": event.started_at,
        "status": event.status,
    }


def _total_duration(trace: RunTrace) -> float:
    return sum(
        event.duration_ms for event in trace.events if event.parent_id is None
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
