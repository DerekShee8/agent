from __future__ import annotations

from collections.abc import Callable

from enterprise_rag.errors import RetrievalUnavailableError
from enterprise_rag.execution_trace import TraceScope
from enterprise_rag.grounding import GateDecision
from enterprise_rag.models import (
    AnswerDraft,
    EvidenceClaim,
    Observation,
    RerankedChunk,
    RetrievedChunk,
    ValidationIssue,
)


def retrieved(
    chunk_id: str,
    text: str,
    *,
    rank: int = 1,
) -> RetrievedChunk:
    return RetrievedChunk(
        text=text,
        filename="policy.md",
        chunk_id=chunk_id,
        dense_score=0.8,
        dense_rank=rank,
        sparse_score=2.0,
        sparse_rank=rank,
        retrieval_score=0.03,
        retrieval_rank=rank,
    )


def reranked(
    chunk_id: str,
    text: str,
    *,
    score: float = 0.9,
    rank: int = 1,
) -> RerankedChunk:
    return RerankedChunk(
        candidate=retrieved(chunk_id, text, rank=rank),
        rerank_score=score,
        rerank_rank=rank,
    )


def answer_draft(
    *,
    answer: str = "The hotel cap is CNY 600.",
    claim: str = "The hotel cap is CNY 600.",
    support_ids: tuple[str, ...] = ("policy.md::0000",),
    quotes: tuple[str, ...] = ("The hotel cap is CNY 600.",),
) -> AnswerDraft:
    return AnswerDraft(
        answer=answer,
        claims=(
            EvidenceClaim(
                text=claim,
                support_ids=support_ids,
                supporting_quotes=quotes,
            ),
        ),
    )


class ScriptedRetriever:
    backend_kind = "fake"

    def __init__(
        self,
        calls: list[str],
        candidates: tuple[RetrievedChunk, ...],
        error: Exception | None = None,
    ) -> None:
        self._calls = calls
        self._candidates = candidates
        self._error = error
        self.trace_scopes: list[TraceScope | None] = []

    async def retrieve(
        self,
        question: str,
        top_k: int,
        *,
        trace_scope: TraceScope | None = None,
    ) -> tuple[RetrievedChunk, ...]:
        assert question
        assert top_k > 0
        self.trace_scopes.append(trace_scope)
        self._calls.append("retrieve")
        if self._error is not None:
            raise self._error
        return self._candidates


class ScriptedReranker:
    backend_kind = "fake"

    def __init__(
        self,
        calls: list[str],
        evidence: tuple[RerankedChunk, ...],
        error: Exception | None = None,
    ) -> None:
        self._calls = calls
        self._evidence = evidence
        self._error = error

    async def rerank(
        self,
        question: str,
        candidates: tuple[RetrievedChunk, ...],
        top_n: int,
    ) -> tuple[RerankedChunk, ...]:
        assert question
        assert candidates
        assert top_n > 0
        self._calls.append("rerank")
        if self._error is not None:
            raise self._error
        return self._evidence


class ScriptedGate:
    backend_kind = "programmatic"

    def __init__(
        self,
        calls: list[str],
        decision: GateDecision,
    ) -> None:
        self._calls = calls
        self._decision = decision

    def decide(self, evidence: tuple[RerankedChunk, ...]) -> GateDecision:
        assert evidence
        self._calls.append("gate")
        return self._decision


class ScriptedAnswerGenerator:
    backend_kind = "fake"

    def __init__(
        self,
        calls: list[str],
        initial: AnswerDraft,
        repaired: AnswerDraft | None = None,
    ) -> None:
        self._calls = calls
        self._initial = initial
        self._repaired = repaired or initial
        self.repair_count = 0

    async def generate_answer(
        self,
        question: str,
        evidence: tuple[RerankedChunk, ...],
    ) -> AnswerDraft:
        assert question
        assert evidence
        self._calls.append("generate")
        return self._initial

    async def repair_answer(
        self,
        question: str,
        evidence: tuple[RerankedChunk, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        assert question
        assert evidence
        assert draft
        assert issues
        self._calls.append("repair")
        self.repair_count += 1
        return self._repaired


class ScriptedValidator:
    backend_kind = "fake"

    def __init__(
        self,
        calls: list[str],
        outcomes: tuple[tuple[ValidationIssue, ...], ...],
    ) -> None:
        self._calls = calls
        self._outcomes = outcomes
        self.call_count = 0
        self.drafts: list[AnswerDraft] = []

    async def validate(
        self,
        draft: AnswerDraft,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[ValidationIssue, ...]:
        assert draft
        assert evidence
        assert observations == ()
        self._calls.append("validate")
        self.drafts.append(draft)
        outcome = self._outcomes[self.call_count]
        self.call_count += 1
        return outcome


class FakeSupportScorer:
    backend_kind = "fake"

    def __init__(
        self,
        scores: tuple[float, ...] | Callable[[tuple[str, ...]], tuple[float, ...]],
    ) -> None:
        self._scores = scores
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def score(
        self,
        claim: str,
        support_texts: tuple[str, ...],
    ) -> tuple[float, ...]:
        self.calls.append((claim, support_texts))
        if callable(self._scores):
            return self._scores(support_texts)
        return self._scores


class FakeClock:
    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        self._value += 0.001
        return self._value


def retrieval_failure() -> RetrievalUnavailableError:
    return RetrievalUnavailableError("retriever unavailable")

