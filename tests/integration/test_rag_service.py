from __future__ import annotations

import pytest

from enterprise_rag.errors import RerankerUnavailableError
from enterprise_rag.execution_trace import TraceRecorder
from enterprise_rag.grounding import GateDecision
from enterprise_rag.models import ValidationIssue
from enterprise_rag.rag_service import RagService
from tests.fakes import (
    FakeClock,
    ScriptedAnswerGenerator,
    ScriptedGate,
    ScriptedReranker,
    ScriptedRetriever,
    ScriptedValidator,
    answer_draft,
    reranked,
    retrieval_failure,
)


def make_service(
    calls: list[str],
    *,
    gate_allowed: bool = True,
    validation_outcomes: tuple[tuple[ValidationIssue, ...], ...] = ((),),
    initial_support_ids: tuple[str, ...] = ("policy.md::0000",),
    repaired_support_ids: tuple[str, ...] | None = None,
    initial_answer: str = "The hotel cap is CNY 600.",
    initial_claim: str = "The hotel cap is CNY 600.",
    retrieval_error: Exception | None = None,
    rerank_error: Exception | None = None,
) -> tuple[RagService, ScriptedAnswerGenerator, ScriptedValidator]:
    evidence = (
        reranked(
            "policy.md::0000",
            "The hotel cap is CNY 600.",
            score=0.91,
            rank=1,
        ),
        reranked(
            "policy.md::0001",
            "A VP can approve an exception before travel.",
            score=0.8,
            rank=2,
        ),
    )
    candidates = tuple(item.candidate for item in evidence)
    decision = GateDecision(
        allowed=gate_allowed,
        evidence=evidence if gate_allowed else (),
        reason=None if gate_allowed else "insufficient_evidence",
    )
    generator = ScriptedAnswerGenerator(
        calls,
        answer_draft(
            answer=initial_answer,
            claim=initial_claim,
            support_ids=initial_support_ids,
        ),
        answer_draft(
            support_ids=repaired_support_ids or initial_support_ids,
        ),
    )
    validator = ScriptedValidator(calls, validation_outcomes)
    clock = FakeClock()
    service = RagService(
        retriever=ScriptedRetriever(calls, candidates, retrieval_error),
        reranker=ScriptedReranker(calls, evidence, rerank_error),
        gate=ScriptedGate(calls, decision),
        generator=generator,
        validator=validator,
        retrieve_k=20,
        rerank_k=5,
        trace_factory=lambda: TraceRecorder("run-test", clock=clock),
    )
    return service, generator, validator


@pytest.mark.asyncio
async def test_knowledge_search_stops_after_evidence_gate_and_returns_chunks() -> None:
    calls: list[str] = []
    service, generator, validator = make_service(calls)

    result = await service.search_evidence("What is the P4 hotel cap?")

    assert result.status == "found"
    assert [item.candidate.chunk_id for item in result.evidence] == [
        "policy.md::0000",
        "policy.md::0001",
    ]
    assert calls == ["retrieve", "rerank", "gate"]
    assert generator.repair_count == 0
    assert validator.call_count == 0


@pytest.mark.asyncio
async def test_knowledge_search_preserves_gate_refusal() -> None:
    calls: list[str] = []
    service, _generator, _validator = make_service(calls, gate_allowed=False)

    result = await service.search_evidence("unknown policy")

    assert result.status == "refused"
    assert result.refusal_reason == "insufficient_evidence"
    assert result.evidence == ()
    assert calls == ["retrieve", "rerank", "gate"]


@pytest.mark.asyncio
async def test_rag_runs_retrieve_rerank_gate_generate_validate_in_order() -> None:
    calls: list[str] = []
    service, _generator, _validator = make_service(calls)

    result = await service.ask("What is the P4 hotel cap?")

    assert result.status == "answered"
    assert calls == ["retrieve", "rerank", "gate", "generate", "validate"]
    assert [event.stage for event in result.trace.events] == [
        "retrieval",
        "reranking",
        "evidence_gate",
        "generation",
        "grounding_validation",
    ]
    assert result.trace.events[0].attributes["backend_kind"] == "fake"
    assert result.trace.events[1].attributes["backend_kind"] == "fake"
    assert result.trace.events[3].attributes["backend_kind"] == "fake"


@pytest.mark.asyncio
async def test_rag_canonicalizes_answer_to_grounded_claim_before_validation() -> None:
    calls: list[str] = []
    claim = "P5 tier-A hotel cap is CNY 850."
    service, _generator, validator = make_service(
        calls,
        initial_answer="P5 A-city cap is CNY 850.",
        initial_claim=claim,
    )

    result = await service.ask("What is the P5 hotel cap?")

    assert result.status == "answered"
    assert result.answer == claim
    assert validator.drafts[0].answer == claim
    assert validator.drafts[0].claims[0].supporting_quotes == (
        "The hotel cap is CNY 600.",
    )


@pytest.mark.asyncio
async def test_gate_refusal_never_calls_generator_or_validator() -> None:
    calls: list[str] = []
    service, generator, validator = make_service(calls, gate_allowed=False)

    result = await service.ask("unknown question")

    assert result.status == "refused"
    assert result.refusal_reason == "insufficient_evidence"
    assert result.answer is None
    assert result.sources == ()
    assert calls == ["retrieve", "rerank", "gate"]
    assert generator.repair_count == 0
    assert validator.call_count == 0


@pytest.mark.asyncio
async def test_retrieval_failure_stops_all_later_stages() -> None:
    calls: list[str] = []
    service, _generator, _validator = make_service(
        calls,
        retrieval_error=retrieval_failure(),
    )

    result = await service.ask("question")

    assert result.status == "failed"
    assert result.error_category == "retrieval_unavailable"
    assert calls == ["retrieve"]


@pytest.mark.asyncio
async def test_reranker_failure_stops_gate_and_generation() -> None:
    calls: list[str] = []
    service, _generator, _validator = make_service(
        calls,
        rerank_error=RerankerUnavailableError("reranker unavailable"),
    )

    result = await service.ask("question")

    assert result.status == "failed"
    assert result.error_category == "reranker_unavailable"
    assert calls == ["retrieve", "rerank"]


@pytest.mark.asyncio
async def test_invalid_draft_triggers_exactly_one_successful_repair() -> None:
    calls: list[str] = []
    issue = ValidationIssue("quote_not_found", 0, "quote missing")
    service, generator, validator = make_service(
        calls,
        validation_outcomes=((issue,), ()),
    )

    result = await service.ask("question")

    assert result.status == "answered"
    assert calls == [
        "retrieve",
        "rerank",
        "gate",
        "generate",
        "validate",
        "repair",
        "validate",
    ]
    assert generator.repair_count == 1
    assert validator.call_count == 2


@pytest.mark.asyncio
async def test_invalid_repair_becomes_programmatic_refusal() -> None:
    calls: list[str] = []
    issue = ValidationIssue("unsupported_claim", 0, "not grounded")
    service, generator, validator = make_service(
        calls,
        validation_outcomes=((issue,), (issue,)),
    )

    result = await service.ask("question")

    assert result.status == "refused"
    assert result.refusal_reason == "grounding_validation_failed"
    assert result.answer is None
    assert result.sources == ()
    assert generator.repair_count == 1
    assert validator.call_count == 2


@pytest.mark.asyncio
async def test_answer_sources_are_only_cited_subset_of_allowed_evidence() -> None:
    calls: list[str] = []
    service, _generator, _validator = make_service(calls)

    result = await service.ask("question")

    assert [source.chunk_id for source in result.sources] == ["policy.md::0000"]
    assert "policy.md::0001" not in {source.chunk_id for source in result.sources}

