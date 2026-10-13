"""Projection from authoritative records to immutable, public-safe view models.

Every displayed value comes from a structured field of ``PanelRunRecord``. This
module never inspects answer text to decide a state, never recomputes a Gate
decision, never filters sources, and never invents a trace node. It reuses the
single public whitelist from :mod:`enterprise_rag.panel.public_trace`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ..models import AgentResult, RagResult
from .contracts import PanelRunRecord, PanelTerminalState
from .public_trace import sanitize_public_trace_event

_StatePresentation = tuple[str, str, str]

_STATE_PRESENTATION: Mapping[PanelTerminalState, _StatePresentation] = {
    "answered": ("Answered", "check_circle", "positive"),
    "refused": ("Refused", "blocked", "warning"),
    "blocked": ("Blocked", "lock", "grey"),
    "failed": ("Failed", "error", "negative"),
    "cancelled": ("Cancelled", "cancel", "grey"),
}

_STATUS_PRESENTATION: Mapping[str, _StatePresentation] = {
    "started": ("Running", "pending", "grey"),
    "succeeded": ("Succeeded", "check_circle", "positive"),
    "refused": ("Refused", "blocked", "warning"),
    "failed": ("Failed", "error", "negative"),
    "skipped": ("Skipped", "skip_next", "grey"),
    "degraded": ("Degraded", "warning", "warning"),
    "cancelled": ("Cancelled", "cancel", "grey"),
}

_STAGE_LABELS: Mapping[str, str] = {
    "retrieval": "Retrieval",
    "dense": "Dense lane",
    "sparse": "Sparse lane",
    "rrf_fusion": "RRF fusion",
    "reranking": "Reranking",
    "evidence_gate": "Evidence Gate",
    "generation": "Generation",
    "grounding_validation": "Grounding validation",
    "grounding_issue": "Grounding issue",
    "repair_generation": "Repair generation",
    "repair_validation": "Repair validation",
    "backend_switch": "Backend switch",
    "agent_plan": "Agent plan",
    "agent_action": "Agent action",
    "search_knowledge": "Knowledge search",
    "agent_observation": "Observation",
    "agent_finish": "Agent finish",
    "cancel_boundary": "Cancellation",
}

InspectorSection = Literal["trace", "sources", "observations"]


@dataclass(frozen=True, slots=True)
class SourceViewModel:
    chunk_id: str
    filename: str
    score: float
    label: str


@dataclass(frozen=True, slots=True)
class ToolCallViewModel:
    name: str
    status: str
    status_text: str
    status_icon: str
    error_category: str | None
    label: str


@dataclass(frozen=True, slots=True)
class ObservationViewModel:
    observation_id: str
    source: str
    status: str
    label: str


@dataclass(frozen=True, slots=True)
class TraceNodeViewModel:
    event_id: str
    parent_id: str | None
    stage: str
    label: str
    status: str
    status_text: str
    status_icon: str
    status_color: str
    error_category: str | None
    duration_ms: float
    attributes: Mapping[str, object]
    children: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InspectorViewModel:
    event_id: str | None
    title: str
    status_text: str
    status_icon: str
    duration_ms: float
    rows: tuple[tuple[str, str], ...]
    empty_message: str | None


@dataclass(frozen=True, slots=True)
class PanelRunViewModel:
    run_id: str
    state: PanelTerminalState
    state_text: str
    state_icon: str
    state_color: str
    mode: str
    scenario_id: str
    question_or_task: str
    generator: str
    provider_name: str | None
    answer: str | None
    refusal_reason: str | None
    error_category: str | None
    public_error: str | None
    sources: tuple[SourceViewModel, ...]
    tool_calls: tuple[ToolCallViewModel, ...]
    observations: tuple[ObservationViewModel, ...]
    trace_nodes: tuple[TraceNodeViewModel, ...]
    inspector: InspectorViewModel
    initialization_duration_ms: float
    run_duration_ms: float


@dataclass(frozen=True, slots=True)
class PanelHistoryEntryViewModel:
    run_id: str
    state: PanelTerminalState
    state_text: str
    state_icon: str
    state_color: str
    scenario_id: str
    question_or_task: str
    run_duration_ms: float


def project_history(
    records: tuple[PanelRunRecord, ...],
) -> tuple[PanelHistoryEntryViewModel, ...]:
    return tuple(
        PanelHistoryEntryViewModel(
            run_id=record.run_id,
            state=record.state,
            state_text=_STATE_PRESENTATION[record.state][0],
            state_icon=_STATE_PRESENTATION[record.state][1],
            state_color=_STATE_PRESENTATION[record.state][2],
            scenario_id=record.request.scenario_id,
            question_or_task=record.request.question_or_task,
            run_duration_ms=record.run_duration_ms,
        )
        for record in records
    )


def project_run(
    record: PanelRunRecord,
    *,
    selected_event_id: str | None = None,
) -> PanelRunViewModel:
    """Build one run's view model from structured fields only."""

    if not isinstance(record, PanelRunRecord):
        raise TypeError("record must be a PanelRunRecord")
    state_text, state_icon, state_color = _STATE_PRESENTATION[record.state]
    nodes = _trace_nodes(record)
    result = record.result
    return PanelRunViewModel(
        run_id=record.run_id,
        state=record.state,
        state_text=state_text,
        state_icon=state_icon,
        state_color=state_color,
        mode=record.request.mode,
        scenario_id=record.request.scenario_id,
        question_or_task=record.request.question_or_task,
        generator=record.request.generator,
        provider_name=record.request.provider_name,
        answer=result.answer if isinstance(result, (RagResult, AgentResult)) else None,
        refusal_reason=(
            result.refusal_reason
            if isinstance(result, (RagResult, AgentResult))
            else None
        ),
        error_category=record.error_category,
        public_error=record.public_error,
        sources=_sources(result),
        tool_calls=_tool_calls(result),
        observations=_observations(result),
        trace_nodes=nodes,
        inspector=project_inspector(nodes, selected_event_id),
        initialization_duration_ms=record.initialization_duration_ms,
        run_duration_ms=record.run_duration_ms,
    )


def project_inspector(
    nodes: tuple[TraceNodeViewModel, ...],
    selected_event_id: str | None,
) -> InspectorViewModel:
    """Selecting a node changes only the Inspector."""

    selected = None
    if selected_event_id is not None:
        selected = next(
            (node for node in nodes if node.event_id == selected_event_id),
            None,
        )
    if selected is None:
        return InspectorViewModel(
            event_id=None,
            title="Inspector",
            status_text="",
            status_icon="info",
            duration_ms=0.0,
            rows=(),
            empty_message="Select a pipeline node to inspect its public detail.",
        )
    return InspectorViewModel(
        event_id=selected.event_id,
        title=selected.label,
        status_text=selected.status_text,
        status_icon=selected.status_icon,
        duration_ms=selected.duration_ms,
        rows=_attribute_rows(selected.attributes),
        empty_message=None,
    )


def _trace_nodes(record: PanelRunRecord) -> tuple[TraceNodeViewModel, ...]:
    events = tuple(
        sanitize_public_trace_event(event) for event in record.progress_events
    )
    children: dict[str, list[str]] = {}
    for event in events:
        if event.parent_id is not None:
            children.setdefault(event.parent_id, []).append(event.event_id)
    nodes: list[TraceNodeViewModel] = []
    for event in events:
        status_text, status_icon, status_color = _STATUS_PRESENTATION.get(
            event.status,
            (event.status, "help", "grey"),
        )
        nodes.append(
            TraceNodeViewModel(
                event_id=event.event_id,
                parent_id=event.parent_id,
                stage=event.stage,
                label=_STAGE_LABELS.get(event.stage, event.stage),
                status=event.status,
                status_text=status_text,
                status_icon=status_icon,
                status_color=status_color,
                error_category=event.error_category,
                duration_ms=event.duration_ms,
                attributes=event.attributes,
                children=tuple(children.get(event.event_id, ())),
            )
        )
    return tuple(nodes)


def _sources(result: object) -> tuple[SourceViewModel, ...]:
    if not isinstance(result, (RagResult, AgentResult)):
        return ()
    return tuple(
        SourceViewModel(
            chunk_id=source.chunk_id,
            filename=source.filename,
            score=source.score,
            label=f"{source.filename}::{source.chunk_id.rsplit('::', 1)[-1]}",
        )
        for source in result.sources
    )


def _tool_calls(result: object) -> tuple[ToolCallViewModel, ...]:
    if not isinstance(result, AgentResult):
        return ()
    calls: list[ToolCallViewModel] = []
    for call in result.tool_trace:
        status_text, status_icon, _color = _STATUS_PRESENTATION.get(
            call.status,
            (call.status, "help", "grey"),
        )
        calls.append(
            ToolCallViewModel(
                name=call.name,
                status=call.status,
                status_text=status_text,
                status_icon=status_icon,
                error_category=call.error_category,
                label=call.name.replace("_", " "),
            )
        )
    return tuple(calls)


def _observations(result: object) -> tuple[ObservationViewModel, ...]:
    if not isinstance(result, AgentResult):
        return ()
    return tuple(
        ObservationViewModel(
            observation_id=observation.observation_id,
            source=observation.source,
            status=observation.status,
            label=f"{observation.source.replace('_', ' ')}: {observation.status}",
        )
        for observation in result.observations
    )


def _attribute_rows(
    attributes: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key.replace("_", " "), _display_value(value))
        for key, value in attributes.items()
    )


def _display_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        mappings = [item for item in value if isinstance(item, Mapping)]
        if mappings:
            return f"{len(mappings)} row(s)"
        return ", ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={value[key]}" for key in value)
    return str(value)
