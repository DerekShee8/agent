from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol, Self
from uuid import uuid4

from .errors import ErrorCategory
from .execution_control import CancellationToken, RunCancelled
from .execution_trace import TraceRecorder, TraceScope, trace_span
from .grounding import GateDecision
from .models import (
    AnswerDraft,
    EvidenceClaim,
    Observation,
    RagResult,
    RerankedChunk,
    Source,
    ValidationIssue,
)
from .ports import RerankerPort, RetrieverPort


class _GatePort(Protocol):
    backend_kind: str

    def decide(self, evidence: tuple[RerankedChunk, ...]) -> GateDecision: ...


class _AnswerGeneratorPort(Protocol):
    backend_kind: str

    async def generate_answer(
        self,
        question: str,
        evidence: tuple[RerankedChunk, ...],
    ) -> AnswerDraft: ...

    async def repair_answer(
        self,
        question: str,
        evidence: tuple[RerankedChunk, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft: ...


class _ValidatorPort(Protocol):
    backend_kind: str

    async def validate(
        self,
        draft: AnswerDraft,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[ValidationIssue, ...]: ...


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    """Evidence-gated knowledge search result for the Agent knowledge tool."""

    status: Literal["found", "refused", "failed"]
    evidence: tuple[RerankedChunk, ...]
    refusal_reason: str | None
    error_category: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, RerankedChunk) for item in self.evidence
        ):
            raise TypeError("evidence must contain RerankedChunk values")
        if self.status == "found":
            if not self.evidence:
                raise ValueError("found search requires evidence")
            if self.refusal_reason is not None or self.error_category is not None:
                raise ValueError("found search cannot expose a failure")
            return
        if self.evidence:
            raise ValueError("non-found search cannot expose evidence")
        if self.status == "refused":
            if (
                not isinstance(self.refusal_reason, str)
                or not self.refusal_reason.strip()
            ):
                raise ValueError("refused search requires refusal_reason")
            if self.error_category is not None:
                raise ValueError("refused search cannot expose error_category")
            return
        if self.status != "failed":
            raise ValueError(f"invalid knowledge search status: {self.status}")
        if not isinstance(self.error_category, str) or not self.error_category.strip():
            raise ValueError("failed search requires error_category")
        if self.refusal_reason is not None:
            raise ValueError("failed search cannot expose refusal_reason")

    @classmethod
    def found(cls, evidence: tuple[RerankedChunk, ...]) -> Self:
        return cls("found", evidence, None, None)

    @classmethod
    def refused(cls, reason: str) -> Self:
        return cls("refused", (), reason, None)

    @classmethod
    def failed(cls, category: str) -> Self:
        return cls("failed", (), None, category)


class RagService:
    backend_kind = "composed"

    def __init__(
        self,
        *,
        retriever: RetrieverPort,
        reranker: RerankerPort,
        gate: _GatePort,
        generator: _AnswerGeneratorPort,
        validator: _ValidatorPort,
        retrieve_k: int,
        rerank_k: int,
        trace_factory: Callable[[], TraceRecorder] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        _positive_integer("retrieve_k", retrieve_k)
        _positive_integer("rerank_k", rerank_k)
        if retrieve_k < rerank_k:
            raise ValueError("retrieve_k must be greater than or equal to rerank_k")
        self._retriever = retriever
        self._reranker = reranker
        self._gate = gate
        self._generator = generator
        self._validator = validator
        self._retrieve_k = retrieve_k
        self._rerank_k = rerank_k
        self._trace_factory = trace_factory or _default_trace_factory
        self._cancellation = cancellation

    def _check_cancelled(self) -> None:
        """Observe the bound token at a stage boundary, if one is bound."""

        token = self._cancellation
        if token is not None:
            token.raise_if_cancelled()

    @property
    def backend_kinds(self) -> tuple[str, str, str]:
        return (
            _backend_kind(self._retriever),
            _backend_kind(self._reranker),
            _backend_kind(self._generator),
        )

    async def search_evidence(
        self,
        question: str,
        *,
        recorder: TraceRecorder | None = None,
        parent_id: str | None = None,
    ) -> KnowledgeSearchResult:
        """Run retrieval, reranking, and the Evidence Gate without generation.

        Standalone callers pass no recorder and stay untraced. The Agent passes its
        own recorder and the ``search_knowledge`` parent so the same pipeline appears
        as a real subtree instead of a second implementation.
        """

        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        if recorder is None and parent_id is not None:
            raise ValueError("parent_id requires a recorder")
        try:
            decision = await self._search_pipeline(
                question,
                recorder=recorder,
                parent_id=parent_id,
            )
        except RunCancelled:
            raise
        except Exception as error:
            return KnowledgeSearchResult.failed(_error_category(error))
        if not decision.allowed:
            return KnowledgeSearchResult.refused(
                decision.reason or "insufficient_evidence"
            )
        return KnowledgeSearchResult.found(decision.evidence)

    async def _search_pipeline(
        self,
        question: str,
        *,
        recorder: TraceRecorder | None,
        parent_id: str | None,
    ) -> GateDecision:
        """One retrieval/reranking/gate path shared by ``ask`` and the Agent."""

        self._check_cancelled()
        with trace_span(
            recorder,
            "retrieval",
            parent_id=parent_id,
            attributes={"backend_kind": _backend_kind(self._retriever)},
        ) as retrieval_span:
            scope = (
                TraceScope(recorder, retrieval_span.event_id)
                if recorder is not None and retrieval_span is not None
                else None
            )
            candidates = await self._retriever.retrieve(
                question,
                self._retrieve_k,
                trace_scope=scope,
            )
        self._check_cancelled()
        with trace_span(
            recorder,
            "reranking",
            parent_id=parent_id,
            attributes={"backend_kind": _backend_kind(self._reranker)},
        ) as rerank_span:
            evidence = await self._reranker.rerank(
                question,
                candidates,
                self._rerank_k,
            )
            if rerank_span is not None:
                rerank_span.finish(
                    "succeeded",
                    attributes={"items": _rerank_rows(evidence)},
                )
        self._check_cancelled()
        with trace_span(
            recorder,
            "evidence_gate",
            parent_id=parent_id,
            attributes={"backend_kind": _backend_kind(self._gate)},
        ) as gate_span:
            decision = self._gate.decide(evidence)
            if gate_span is not None:
                gate_span.finish(
                    "succeeded" if decision.allowed else "refused",
                    error_category=(
                        None
                        if decision.allowed
                        else ErrorCategory.EVIDENCE_INSUFFICIENT.value
                    ),
                    attributes=_gate_attributes(decision, evidence, self._gate),
                )
        self._check_cancelled()
        return decision

    async def ask(self, question: str) -> RagResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        recorder = self._trace_factory()
        try:
            decision = await self._search_pipeline(
                question,
                recorder=recorder,
                parent_id=None,
            )
            if not decision.allowed:
                return RagResult.refused(
                    decision.reason or "insufficient_evidence",
                    trace=recorder.finish("refused"),
                )

            self._check_cancelled()
            with recorder.span(
                "generation",
                attributes=_generator_attributes(self._generator),
            ) as generation_span:
                draft = await self._generator.generate_answer(
                    question,
                    decision.evidence,
                )
                draft = _canonicalize_answer(draft, decision.evidence)
                generation_span.finish(
                    "succeeded",
                    attributes=_generator_attributes(self._generator),
                )
            _record_backend_switch(recorder, self._generator)
            self._check_cancelled()
            with recorder.span(
                "grounding_validation",
                attributes={"backend_kind": _backend_kind(self._validator)},
            ) as validation_span:
                issues = await self._validator.validate(
                    draft,
                    decision.evidence,
                    (),
                )
                validation_span.finish(
                    "degraded" if issues else "succeeded",
                    error_category=(
                        ErrorCategory.GROUNDING_FAILED.value if issues else None
                    ),
                    attributes=_validation_attributes(
                        draft,
                        issues,
                        self._validator,
                    ),
                )
            self._check_cancelled()

            if issues:
                with recorder.span(
                    "repair_generation",
                    attributes=_generator_attributes(self._generator),
                ) as repair_generation_span:
                    draft = await self._generator.repair_answer(
                        question,
                        decision.evidence,
                        draft,
                        issues,
                    )
                    draft = _canonicalize_answer(draft, decision.evidence)
                    repair_generation_span.finish(
                        "succeeded",
                        attributes=_generator_attributes(self._generator),
                    )
                _record_backend_switch(recorder, self._generator)
                self._check_cancelled()
                with recorder.span(
                    "repair_validation",
                    attributes={"backend_kind": _backend_kind(self._validator)},
                ) as repair_span:
                    repaired_issues = await self._validator.validate(
                        draft,
                        decision.evidence,
                        (),
                    )
                    repair_span.finish(
                        "refused" if repaired_issues else "succeeded",
                        error_category=(
                            ErrorCategory.GROUNDING_FAILED.value
                            if repaired_issues
                            else None
                        ),
                        attributes=_validation_attributes(
                            draft,
                            repaired_issues,
                            self._validator,
                        ),
                    )
                self._check_cancelled()
                if repaired_issues:
                    return RagResult.refused(
                        "grounding_validation_failed",
                        trace=recorder.finish("refused"),
                    )

            sources = _sources_for_draft(draft, decision.evidence)
            if sources is None:
                return RagResult.refused(
                    "grounding_validation_failed",
                    trace=recorder.finish("refused"),
                )
            return RagResult.answered(
                draft.answer,
                sources,
                trace=recorder.finish("succeeded"),
            )
        except RunCancelled:
            raise
        except Exception as error:
            return RagResult.failed(
                _error_category(error),
                trace=recorder.finish("failed"),
            )


_TEXT_PREVIEW_LIMIT = 300
_JSON_SCALARS = (str, int, float)


def _rerank_rows(
    evidence: tuple[RerankedChunk, ...],
) -> tuple[Mapping[str, object], ...]:
    """Public reranking rows: chunk identity, preview, and both score pairs."""

    return tuple(
        {
            "chunk_id": item.candidate.chunk_id,
            "source": item.candidate.filename,
            "text_preview": item.candidate.text[:_TEXT_PREVIEW_LIMIT],
            "retrieval_score": item.candidate.retrieval_score,
            "retrieval_rank": item.candidate.retrieval_rank,
            "rerank_score": item.rerank_score,
            "rerank_rank": item.rerank_rank,
        }
        for item in evidence
    )


def _gate_attributes(
    decision: GateDecision,
    evidence: tuple[RerankedChunk, ...],
    gate: object,
) -> dict[str, object]:
    """Gate facts taken from the decision itself, never from the UI or the answer."""

    scores = tuple(item.rerank_score for item in evidence)
    attributes: dict[str, object] = {
        "threshold": getattr(gate, "min_score", None),
        "score": max(scores) if scores else None,
        "support_ids": tuple(item.candidate.chunk_id for item in decision.evidence),
        "decision": "allowed" if decision.allowed else "refused",
        "reason": decision.reason,
    }
    return {key: value for key, value in attributes.items() if value is not None}


def _draft_support_ids(draft: AnswerDraft) -> tuple[str, ...]:
    ids: list[str] = []
    for claim in draft.claims:
        for support_id in claim.support_ids:
            if support_id not in ids:
                ids.append(support_id)
    return tuple(ids)


def _validation_attributes(
    draft: AnswerDraft,
    issues: tuple[ValidationIssue, ...],
    validator: object,
) -> dict[str, object]:
    return {
        "issue_count": len(issues),
        "support_ids": _draft_support_ids(draft),
        "backend_kind": _backend_kind(validator),
    }


def _generator_attributes(generator: object) -> dict[str, object]:
    """Public generation provenance plus any usage snapshot the backend exposes.

    The generator wrapper only mirrors ``backend_kind``, so the model identity is
    read from the backend it wraps when that backend is reachable.
    """

    attributes: dict[str, object] = {"backend_kind": _backend_kind(generator)}
    backend = getattr(generator, "backend", generator)
    for source_name, attribute_name in (
        ("model_id", "model_id"),
        ("model_revision", "model_revision"),
        ("model_sha256", "model_digest"),
    ):
        value = getattr(backend, source_name, None)
        if isinstance(value, str) and value:
            attributes[attribute_name] = value
    usage = getattr(backend, "usage", None)
    if isinstance(usage, Mapping):
        attributes["usage"] = dict(usage)
    cost = getattr(backend, "cost_cny", None)
    if isinstance(cost, _JSON_SCALARS) and not isinstance(cost, bool):
        attributes["cost_cny"] = cost
    return attributes


def _sources_for_draft(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
) -> tuple[Source, ...] | None:
    cited_ids = {
        support_id
        for claim in draft.claims
        for support_id in claim.support_ids
    }
    evidence_by_id = {item.candidate.chunk_id: item for item in evidence}
    if not cited_ids or not cited_ids <= evidence_by_id.keys():
        return None
    return tuple(
        Source(
            filename=item.candidate.filename,
            chunk_id=item.candidate.chunk_id,
            score=item.rerank_score,
        )
        for item in evidence
        if item.candidate.chunk_id in cited_ids
    )


def _canonicalize_answer(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
) -> AnswerDraft:
    support_texts = {
        item.candidate.chunk_id: item.candidate.text for item in evidence
    }
    claims = tuple(
        EvidenceClaim(
            text=claim.text,
            support_ids=claim.support_ids,
            supporting_quotes=tuple(
                support_texts[support_id]
                for support_id in claim.support_ids
                if support_id in support_texts
            )
            or claim.supporting_quotes,
        )
        for claim in draft.claims
    )
    return AnswerDraft(
        answer=" ".join(claim.text for claim in claims),
        claims=claims,
    )


def _positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _backend_kind(component: object) -> str:
    value = getattr(component, "backend_kind", "unknown")
    return value if isinstance(value, str) and value else "unknown"


def _record_backend_switch(
    recorder: TraceRecorder,
    generator: object,
) -> None:
    consume = getattr(generator, "consume_backend_switch", None)
    if not callable(consume):
        return
    category = consume()
    if not isinstance(category, str) or not category:
        return
    with recorder.span(
        "backend_switch",
        attributes={
            "from_backend": "online",
            "to_backend": "local",
        },
    ) as span:
        span.finish("degraded", error_category=category)


def _error_category(error: Exception) -> str:
    category = getattr(error, "category", ErrorCategory.PROTOCOL)
    if isinstance(category, Enum):
        category = category.value
    if isinstance(category, str) and category:
        return category
    return ErrorCategory.PROTOCOL.value


def _default_trace_factory() -> TraceRecorder:
    return TraceRecorder(f"rag-{uuid4().hex}")
