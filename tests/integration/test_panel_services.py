"""Service-level trace nesting and cooperative cancellation.

These tests drive the real ``HybridRetriever`` over the deterministic in-memory
index so the nested retrieval children are produced by production code, not by a
test double that fakes them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from enterprise_rag.agent_service import AgentService
from enterprise_rag.execution_control import CancellationToken, RunCancelled
from enterprise_rag.execution_trace import TraceEvent, TraceRecorder
from enterprise_rag.grounding import GateDecision
from enterprise_rag.models import (
    AgentAction,
    AnswerDraft,
    EvidenceClaim,
    Observation,
    ValidationIssue,
)
from enterprise_rag.panel.progress import BoundedProgressBuffer
from enterprise_rag.panel.public_trace import reconcile_terminal_events
from enterprise_rag.rag_service import RagService
from enterprise_rag.retrieval import HybridRetriever
from tests.fakes import (
    FakeClock,
    ScriptedAnswerGenerator,
    ScriptedGate,
    ScriptedReranker,
    ScriptedValidator,
    answer_draft,
    reranked,
    retrieved,
)
from tests.integration.test_retriever_fake import FakeQueryEncoder, make_index

POLICY_ID = "policy.md::0000"
SECOND_ID = "policy.md::0001"
POLICY_TEXT = "The hotel cap is CNY 600."


@dataclass
class _Harness:
    service: RagService
    calls: list[str]
    generator: ScriptedAnswerGenerator
    validator: ScriptedValidator


def make_traced_service(
    *,
    gate_allowed: bool = True,
    validation_outcomes: tuple[tuple[ValidationIssue, ...], ...] = ((),),
    cancellation: CancellationToken | None = None,
    retriever: object | None = None,
    generator: object | None = None,
    run_id: str = "run-rag",
    observer: object | None = None,
) -> _Harness:
    calls: list[str] = []
    evidence = (
        reranked(POLICY_ID, POLICY_TEXT, score=0.91, rank=1),
        reranked(SECOND_ID, "A VP can approve an exception.", score=0.8, rank=2),
    )
    decision = GateDecision(
        allowed=gate_allowed,
        evidence=evidence if gate_allowed else (),
        reason=None if gate_allowed else "insufficient_evidence",
    )
    active_generator = generator or ScriptedAnswerGenerator(calls, answer_draft())
    validator = ScriptedValidator(calls, validation_outcomes)
    service = RagService(
        retriever=retriever
        or HybridRetriever(make_index(), FakeQueryEncoder(), rrf_k=60),
        reranker=ScriptedReranker(calls, evidence),
        gate=ScriptedGate(calls, decision),
        generator=active_generator,
        validator=validator,
        retrieve_k=20,
        rerank_k=5,
        trace_factory=lambda: TraceRecorder(
            run_id,
            clock=FakeClock(),
            observer=observer,  # type: ignore[arg-type]
        ),
        cancellation=cancellation,
    )
    return _Harness(service, calls, active_generator, validator)


def _stages(result: object) -> list[str]:
    return [event.stage for event in result.trace.events]  # type: ignore[attr-defined]


def _by_stage(result: object) -> dict[str, object]:
    return {event.stage: event for event in result.trace.events}  # type: ignore[attr-defined]


def _action(name: str, **arguments: object) -> AgentAction:
    return AgentAction(name, arguments, None)


def _grounded_draft() -> AnswerDraft:
    return AnswerDraft(
        answer="The P4 hotel cap is CNY 600 per night.",
        claims=(
            EvidenceClaim(
                "The P4 hotel cap is CNY 600 per night.",
                (POLICY_ID,),
                (POLICY_TEXT,),
            ),
        ),
    )


class _Planner:
    backend_kind = "fake"

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = iter(actions)

    async def next_action(
        self,
        task: str,
        observations: tuple[Observation, ...],
    ) -> AgentAction:
        assert task
        assert observations is not None
        return next(self._actions)

    async def repair_answer(
        self,
        task: str,
        observations: tuple[Observation, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        assert task and draft and issues
        assert observations
        return draft


class _Tools:
    backend_kind = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> Observation:
        self.calls.append(name)
        record_id = next(iter(arguments.values()))
        return Observation(
            f"obs:{name}:{record_id}",
            name,
            "found",
            {
                "employee_id": "EMP-003",
                "grade": "P4",
                "travel_tier": "standard",
            },
            POLICY_TEXT,
        )


class _BlockingRetriever:
    backend_kind = "fake"

    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release
        self.calls = 0

    async def retrieve(
        self,
        question: str,
        top_k: int,
        *,
        trace_scope: object | None = None,
    ) -> tuple[object, ...]:
        assert question
        assert top_k > 0
        self.calls += 1
        self._started.set()
        await self._release.wait()
        return (retrieved(POLICY_ID, POLICY_TEXT),)


class _BlockingGenerator:
    backend_kind = "fake"

    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release
        self.calls = 0

    async def generate_answer(
        self,
        question: str,
        evidence: tuple[object, ...],
    ) -> AnswerDraft:
        assert question and evidence
        self.calls += 1
        self._started.set()
        await self._release.wait()
        return answer_draft()

    async def repair_answer(
        self,
        question: str,
        evidence: tuple[object, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        raise AssertionError("repair must not run after cancellation")


class _BlockingTools:
    backend_kind = "fake"

    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release
        self.calls = 0

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> Observation:
        self.calls += 1
        self._started.set()
        await self._release.wait()
        record_id = next(iter(arguments.values()))
        return Observation(
            f"obs:{name}:{record_id}",
            name,
            "found",
            {"employee_id": "EMP-003", "grade": "P4", "travel_tier": "standard"},
            POLICY_TEXT,
        )


@pytest.mark.asyncio
async def test_standalone_rag_nests_real_retrieval_children_in_order() -> None:
    harness = make_traced_service()

    result = await harness.service.ask("What is the P4 hotel cap?")

    assert result.status == "answered"
    trace = result.trace
    assert [event.stage for event in trace.events] == [
        "retrieval",
        "dense",
        "sparse",
        "rrf_fusion",
        "reranking",
        "evidence_gate",
        "generation",
        "grounding_validation",
    ]
    retrieval = trace.events[0]
    assert [event.parent_id for event in trace.events[1:4]] == [
        retrieval.event_id,
        retrieval.event_id,
        retrieval.event_id,
    ]
    assert all(event.parent_id is None for event in trace.events[4:])
    assert harness.calls == ["rerank", "gate", "generate", "validate"]


@pytest.mark.asyncio
async def test_standalone_rag_repair_chain_appends_two_more_stages() -> None:
    issue = ValidationIssue("quote_not_found", 0, "quote missing")
    harness = make_traced_service(validation_outcomes=((issue,), ()))

    result = await harness.service.ask("question")

    assert result.status == "answered"
    assert _stages(result)[-2:] == ["repair_generation", "repair_validation"]
    assert harness.generator.repair_count == 1
    assert harness.validator.call_count == 2


@pytest.mark.asyncio
async def test_gate_refusal_stops_before_generation_and_marks_both_refused() -> None:
    harness = make_traced_service(gate_allowed=False)

    result = await harness.service.ask("unknown question")

    assert result.status == "refused"
    assert "generation" not in _stages(result)
    gate = _by_stage(result)["evidence_gate"]
    assert gate.status == "refused"
    assert gate.error_category == "evidence_insufficient"
    assert result.trace.status == "refused"
    assert harness.generator.repair_count == 0
    assert harness.validator.call_count == 0


@pytest.mark.asyncio
async def test_rag_terminal_attributes_come_from_structured_outputs() -> None:
    harness = make_traced_service()

    result = await harness.service.ask("question")

    events = _by_stage(result)
    rows = events["reranking"].attributes["items"]  # type: ignore[attr-defined]
    assert [row["chunk_id"] for row in rows] == [POLICY_ID, SECOND_ID]
    assert rows[0]["source"] == "policy.md"
    assert rows[0]["text_preview"] == POLICY_TEXT
    assert rows[0]["rerank_rank"] == 1
    assert rows[0]["rerank_score"] == pytest.approx(0.91)
    assert rows[0]["retrieval_rank"] == 1

    gate = events["evidence_gate"].attributes  # type: ignore[attr-defined]
    assert gate["decision"] == "allowed"
    assert gate["support_ids"] == (POLICY_ID, SECOND_ID)
    assert gate["score"] == pytest.approx(0.91)
    assert "reason" not in gate

    validation = events["grounding_validation"].attributes  # type: ignore[attr-defined]
    assert validation["issue_count"] == 0
    assert validation["support_ids"] == (POLICY_ID,)
    assert validation["backend_kind"] == "fake"


@pytest.mark.asyncio
async def test_rag_terminal_attributes_never_carry_the_answer_or_a_prompt() -> None:
    harness = make_traced_service()

    result = await harness.service.ask("a secret prompt")

    serialized = result.trace.to_json()
    assert "a secret prompt" not in serialized
    assert "The hotel cap is CNY 600." in serialized


@pytest.mark.asyncio
async def test_agent_search_knowledge_owns_a_real_nested_rag_subtree() -> None:
    harness = make_traced_service(run_id="agent-run")
    tools = _Tools()
    agent = AgentService(
        planner=_Planner(
            [
                _action("search_knowledge", question="P4 hotel cap"),
                AgentAction("finish", {}, _grounded_draft()),
            ]
        ),
        rag=harness.service,
        tools=tools,
        validator=harness.validator,
        max_actions=6,
        trace_factory=lambda: TraceRecorder("agent-run", clock=FakeClock()),
    )

    result = await agent.run("Explain the P4 hotel cap")

    assert result.status == "answered"
    trace = result.trace
    assert {event.run_id for event in trace.events} == {trace.run_id}

    searches = [event for event in trace.events if event.stage == "search_knowledge"]
    assert len(searches) == 1
    search = searches[0]
    parent = next(event for event in trace.events if event.event_id == search.parent_id)
    assert parent.stage == "agent_action"
    assert parent.attributes["action"] == "search_knowledge"

    nested = {
        event.stage: event
        for event in trace.events
        if event.parent_id == search.event_id
    }
    assert set(nested) == {"retrieval", "reranking", "evidence_gate"}
    assert {
        event.stage
        for event in trace.events
        if event.parent_id == nested["retrieval"].event_id
    } == {"dense", "sparse", "rrf_fusion"}
    assert "generation" not in {event.stage for event in trace.events}

    finishes = [event for event in trace.events if event.stage == "agent_finish"]
    assert len(finishes) == 1
    assert {
        event.stage for event in trace.events if event.parent_id == finishes[0].event_id
    } == {"grounding_validation"}

    observations = [
        event for event in trace.events if event.stage == "agent_observation"
    ]
    assert [event.attributes["source"] for event in observations] == [
        "search_knowledge",
        "search_knowledge",
    ]
    assert [event.attributes["observation_id"] for event in observations] == [
        POLICY_ID,
        SECOND_ID,
    ]


@pytest.mark.asyncio
async def test_cancellation_after_retrieval_stops_before_reranking() -> None:
    token = CancellationToken()
    started, release = asyncio.Event(), asyncio.Event()
    retriever = _BlockingRetriever(started, release)
    harness = make_traced_service(cancellation=token, retriever=retriever)

    task = asyncio.create_task(harness.service.ask("question"))
    await started.wait()
    token.cancel()
    release.set()

    with pytest.raises(RunCancelled):
        await task
    assert retriever.calls == 1
    assert harness.calls == []


@pytest.mark.asyncio
async def test_cancellation_after_a_blocking_generator_skips_validation() -> None:
    token = CancellationToken()
    started, release = asyncio.Event(), asyncio.Event()
    generator = _BlockingGenerator(started, release)
    harness = make_traced_service(cancellation=token, generator=generator)

    task = asyncio.create_task(harness.service.ask("question"))
    await started.wait()
    token.cancel()
    release.set()

    with pytest.raises(RunCancelled):
        await task
    assert generator.calls == 1
    assert harness.validator.call_count == 0


@pytest.mark.asyncio
async def test_cancellation_after_an_agent_tool_is_never_a_failed_result() -> None:
    token = CancellationToken()
    started, release = asyncio.Event(), asyncio.Event()
    tools = _BlockingTools(started, release)
    harness = make_traced_service(run_id="agent-run")
    agent = AgentService(
        planner=_Planner(
            [
                _action("search_knowledge", question="P4 hotel cap"),
                _action("get_employee_profile", employee_id="EMP-003"),
                AgentAction("finish", {}, _grounded_draft()),
            ]
        ),
        rag=harness.service,
        tools=tools,
        validator=harness.validator,
        max_actions=6,
        trace_factory=lambda: TraceRecorder("agent-run", clock=FakeClock()),
        cancellation=token,
    )

    task = asyncio.create_task(agent.run("Explain the P4 hotel cap"))
    await started.wait()
    token.cancel()
    release.set()

    with pytest.raises(RunCancelled):
        await task
    assert tools.calls == 1
    assert harness.validator.call_count == 0


def _worst_case_completed_agent_trace(
    *,
    max_actions: int = 6,
    chunks_per_search: int = 5,
) -> object:
    """The largest completed Agent trace the services can emit.

    Six actions, each a knowledge search with a full nested retrieval subtree and
    its observations, plus one online-to-local switch, one grounding issue, one
    repair, and every terminal event.
    """

    recorder = TraceRecorder("agent-run", clock=FakeClock())
    for step in range(1, max_actions + 1):
        with recorder.span(
            "agent_plan",
            attributes={"backend_kind": "real", "step": step},
        ):
            pass
        with recorder.span(
            "agent_action",
            attributes={
                "action": "search_knowledge",
                "arguments": {"question": "policy question"},
                "step": step,
            },
        ) as action_span:
            with recorder.span(
                "search_knowledge",
                parent_id=action_span.event_id,
                attributes={"step": step},
            ) as search_span:
                with recorder.span(
                    "retrieval",
                    parent_id=search_span.event_id,
                    attributes={"backend_kind": "real"},
                ) as retrieval_span:
                    for stage in ("dense", "sparse"):
                        with recorder.span(
                            stage,
                            parent_id=retrieval_span.event_id,
                            attributes={"backend_kind": "real", "items": ()},
                        ):
                            pass
                    with recorder.span(
                        "rrf_fusion",
                        parent_id=retrieval_span.event_id,
                        attributes={"items": ()},
                    ):
                        pass
                with recorder.span(
                    "reranking",
                    parent_id=search_span.event_id,
                    attributes={"backend_kind": "real", "items": ()},
                ):
                    pass
                with recorder.span(
                    "evidence_gate",
                    parent_id=search_span.event_id,
                    attributes={"decision": "allowed"},
                ):
                    pass
                for index in range(chunks_per_search):
                    with recorder.span(
                        "agent_observation",
                        attributes={
                            "observation_id": f"chunk-{step}-{index}",
                            "source": "search_knowledge",
                            "status": "found",
                            "step": step,
                        },
                    ):
                        pass
        with recorder.span(
            "backend_switch",
            attributes={"from_backend": "online", "to_backend": "local"},
        ) as switch_span:
            switch_span.finish("degraded", error_category="authentication")
    with recorder.span(
        "agent_finish",
        attributes={"step": max_actions},
    ) as finish_span:
        with recorder.span(
            "grounding_validation",
            parent_id=finish_span.event_id,
            attributes={"backend_kind": "real"},
        ) as validation_span:
            validation_span.finish(
                "degraded",
                error_category="grounding_failed",
            )
        with recorder.span(
            "grounding_issue",
            parent_id=finish_span.event_id,
            attributes={
                "attempt": "initial",
                "claim_index": 0,
                "code": "quote_not_found",
            },
        ):
            pass
        with recorder.span(
            "repair_generation",
            parent_id=finish_span.event_id,
            attributes={"backend_kind": "real"},
        ):
            pass
        with recorder.span(
            "repair_validation",
            parent_id=finish_span.event_id,
            attributes={"backend_kind": "real"},
        ):
            pass
    return recorder.finish("succeeded")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    ["answered", "refused", "repair", "repair_refusal"],
)
async def test_progress_terminal_events_match_the_final_trace(shape: str) -> None:
    issue = ValidationIssue("quote_not_found", 0, "quote missing")
    outcomes = {
        "answered": ((),),
        "refused": ((),),
        "repair": ((issue,), ()),
        "repair_refusal": ((issue,), (issue,)),
    }[shape]
    buffer = BoundedProgressBuffer()
    buffer.subscribe("browser")
    harness = make_traced_service(
        gate_allowed=shape != "refused",
        validation_outcomes=outcomes,
        observer=buffer.publish,
    )

    result = await harness.service.ask("What is the P4 hotel cap?")

    merged = reconcile_terminal_events(buffer.drain(), result.trace)
    by_id = {event.event_id: event for event in merged}
    assert len(by_id) == len(result.trace.events)
    for final in result.trace.events:
        live = by_id[final.event_id]
        assert (
            live.parent_id,
            live.stage,
            live.status,
            live.error_category,
        ) == (
            final.parent_id,
            final.stage,
            final.status,
            final.error_category,
        )


@pytest.mark.asyncio
async def test_an_absent_detached_or_failing_observer_cannot_change_the_result() -> (
    None
):
    issue = ValidationIssue("quote_not_found", 0, "quote missing")
    baseline = await make_traced_service(
        validation_outcomes=((issue,), ()),
    ).service.ask("question")

    detached = BoundedProgressBuffer()
    detached.subscribe("browser")
    detached.detach()
    with_detached = await make_traced_service(
        validation_outcomes=((issue,), ()),
        observer=detached.publish,
    ).service.ask("question")

    def exploding(_event: TraceEvent) -> None:
        raise RuntimeError("observer exploded")

    with_exploding = await make_traced_service(
        validation_outcomes=((issue,), ()),
        observer=exploding,
    ).service.ask("question")

    for other in (with_detached, with_exploding):
        assert other.status == baseline.status
        assert other.answer == baseline.answer
        assert [event.stage for event in other.trace.events] == [
            event.stage for event in baseline.trace.events
        ]
        assert [event.status for event in other.trace.events] == [
            event.status for event in baseline.trace.events
        ]
    assert detached.detached


def test_worst_case_agent_paths_fit_inside_the_progress_buffer() -> None:
    completed = _worst_case_completed_agent_trace()
    completed_ids = {event.event_id for event in completed.events}  # type: ignore[attr-defined]
    assert len(completed_ids) < 256

    buffer = BoundedProgressBuffer()
    buffer.subscribe("browser")
    for event in completed.events:  # type: ignore[attr-defined]
        buffer.publish(event)

    drained = buffer.drain()
    assert completed_ids <= {event.event_id for event in drained}
    assert not buffer.detached

    cancel_boundary = TraceEvent(
        "agent-run",
        "agent-run:9999",
        None,
        "cancel_boundary",
        "cancelled",
        1.0,
        0.0,
        {},
        None,
    )
    cancelled_prefix = (
        *(
            TraceEvent(
                event.run_id,
                event.event_id,
                event.parent_id,
                event.stage,
                "started",
                event.started_at,
                0.0,
                event.attributes,
                None,
            )
            for event in completed.events  # type: ignore[attr-defined]
        ),
        cancel_boundary,
    )
    cancelled_ids = {event.event_id for event in cancelled_prefix}
    assert len(cancelled_ids) < 256

    cancelled_buffer = BoundedProgressBuffer()
    cancelled_buffer.subscribe("browser")
    for event in cancelled_prefix:
        cancelled_buffer.publish(event)

    assert not cancelled_buffer.detached
    assert cancelled_ids <= {event.event_id for event in cancelled_buffer.drain()}
