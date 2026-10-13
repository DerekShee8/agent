from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Literal, Self

from .execution_trace import RunTrace, _freeze_mapping, _plain_value

ResultStatus = Literal["answered", "refused", "failed"]
ObservationStatus = Literal["found", "not_found"]
ToolCallStatus = Literal["succeeded", "failed", "skipped", "degraded"]

_RESULT_STATUSES = frozenset({"answered", "refused", "failed"})
_OBSERVATION_STATUSES = frozenset({"found", "not_found"})
_TOOL_CALL_STATUSES = frozenset({"succeeded", "failed", "skipped", "degraded"})
_DEFAULT_TRACE = RunTrace.empty()


def _require_nonempty(
    value: object,
    name: str,
    *,
    empty_message: str | None = None,
) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(empty_message or f"{name} must not be empty")


def _require_tuple(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")


def _require_members(
    values: tuple[object, ...],
    expected_type: type[object],
    name: str,
) -> None:
    if any(not isinstance(value, expected_type) for value in values):
        raise TypeError(
            f"{name} must contain only {expected_type.__name__}"
        )


def _require_unique(values: tuple[str, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


def _require_integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def _require_positive_integer(value: int, name: str) -> None:
    _require_integer(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_non_negative_integer(value: int, name: str) -> None:
    _require_integer(value, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_optional_lane(
    lane: str,
    score: float | None,
    rank: int | None,
) -> None:
    if (score is None) != (rank is None):
        raise ValueError(f"{lane} score and rank must be paired")
    if score is not None and not isfinite(score):
        raise ValueError(f"{lane}_score must be finite")
    if rank is not None:
        _require_positive_integer(rank, f"{lane}_rank")


def _validate_source_ids(sources: tuple[Source, ...]) -> None:
    source_ids = tuple(source.chunk_id for source in sources)
    _require_unique(source_ids, "source chunk_ids")


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    text: str
    filename: str
    chunk_id: str
    dense_score: float | None
    dense_rank: int | None
    sparse_score: float | None
    sparse_rank: int | None
    retrieval_score: float
    retrieval_rank: int

    def __post_init__(self) -> None:
        _require_nonempty(self.text, "text")
        _require_nonempty(self.filename, "filename")
        _require_nonempty(self.chunk_id, "chunk_id")
        _validate_optional_lane("dense", self.dense_score, self.dense_rank)
        _validate_optional_lane("sparse", self.sparse_score, self.sparse_rank)
        if not isfinite(self.retrieval_score):
            raise ValueError("retrieval_score must be finite")
        _require_positive_integer(self.retrieval_rank, "retrieval_rank")


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    candidate: RetrievedChunk
    rerank_score: float
    rerank_rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, RetrievedChunk):
            raise TypeError("candidate must be a RetrievedChunk")
        if not isfinite(self.rerank_score):
            raise ValueError("rerank_score must be finite")
        _require_positive_integer(self.rerank_rank, "rerank_rank")


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    text: str
    support_ids: tuple[str, ...]
    supporting_quotes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_tuple(self.support_ids, "support_ids")
        _require_tuple(self.supporting_quotes, "supporting_quotes")
        _require_nonempty(self.text, "claim text")
        if not self.support_ids:
            raise ValueError("support_ids must not be empty")
        if not self.supporting_quotes:
            raise ValueError("supporting_quotes must not be empty")
        for support_id in self.support_ids:
            _require_nonempty(
                support_id,
                "support_id",
                empty_message="support_ids must not contain empty values",
            )
        for quote in self.supporting_quotes:
            _require_nonempty(
                quote,
                "supporting_quote",
                empty_message="supporting_quotes must not contain empty values",
            )
        _require_unique(self.support_ids, "support_ids")


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    answer: str
    claims: tuple[EvidenceClaim, ...]

    def __post_init__(self) -> None:
        _require_tuple(self.claims, "claims")
        _require_members(self.claims, EvidenceClaim, "claims")
        _require_nonempty(self.answer, "answer")
        if not self.claims:
            raise ValueError("claims must not be empty")


@dataclass(frozen=True, slots=True)
class Source:
    filename: str
    chunk_id: str
    score: float

    def __post_init__(self) -> None:
        _require_nonempty(self.filename, "filename")
        _require_nonempty(self.chunk_id, "chunk_id")
        if not isfinite(self.score):
            raise ValueError("score must be finite")


@dataclass(frozen=True, slots=True)
class RagResult:
    status: ResultStatus
    answer: str | None
    sources: tuple[Source, ...]
    refusal_reason: str | None
    error_category: str | None
    trace: RunTrace

    def __post_init__(self) -> None:
        _require_tuple(self.sources, "sources")
        _require_members(self.sources, Source, "sources")
        if not isinstance(self.trace, RunTrace):
            raise TypeError("trace must be a RunTrace")
        _require_nonempty(self.status, "status")
        if self.answer is not None:
            _require_nonempty(
                self.answer,
                "answer",
                empty_message="answered result requires a nonempty answer",
            )
        if self.refusal_reason is not None:
            _require_nonempty(
                self.refusal_reason,
                "refusal_reason",
                empty_message="refused result requires refusal_reason",
            )
        if self.error_category is not None:
            _require_nonempty(
                self.error_category,
                "error_category",
                empty_message="failed result requires error_category",
            )
        if self.status not in _RESULT_STATUSES:
            raise ValueError(f"invalid result status: {self.status}")
        _validate_source_ids(self.sources)

        if self.status == "answered":
            if self.answer is None:
                raise ValueError("answered result requires a nonempty answer")
            if not self.sources:
                raise ValueError("answered result requires sources")
            if self.refusal_reason is not None:
                raise ValueError("answered result cannot have refusal_reason")
            if self.error_category is not None:
                raise ValueError("answered result cannot have error_category")
            return

        if self.status == "refused":
            if self.answer is not None:
                raise ValueError("refused result cannot expose an answer")
            if self.sources:
                raise ValueError("refused result cannot expose sources")
            if self.refusal_reason is None:
                raise ValueError("refused result requires refusal_reason")
            if self.error_category is not None:
                raise ValueError("refused result cannot have error_category")
            return

        if self.answer is not None:
            raise ValueError("failed result cannot expose an answer")
        if self.sources:
            raise ValueError("failed result cannot expose sources")
        if self.refusal_reason is not None:
            raise ValueError("failed result cannot have refusal_reason")
        if self.error_category is None:
            raise ValueError("failed result requires error_category")

    @classmethod
    def answered(
        cls,
        answer: str,
        sources: tuple[Source, ...],
        trace: RunTrace = _DEFAULT_TRACE,
    ) -> Self:
        return cls("answered", answer, sources, None, None, trace)

    @classmethod
    def refused(
        cls,
        refusal_reason: str,
        trace: RunTrace = _DEFAULT_TRACE,
    ) -> Self:
        return cls("refused", None, (), refusal_reason, None, trace)

    @classmethod
    def failed(
        cls,
        error_category: str,
        trace: RunTrace = _DEFAULT_TRACE,
    ) -> Self:
        return cls("failed", None, (), None, error_category, trace)


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    source: str
    status: ObservationStatus
    facts: Mapping[str, object]
    support_text: str | None

    def __post_init__(self) -> None:
        _require_nonempty(self.observation_id, "observation_id")
        _require_nonempty(self.source, "source")
        _require_nonempty(self.status, "status")
        if self.support_text is not None:
            _require_nonempty(
                self.support_text,
                "support_text",
                empty_message="found observation requires support_text",
            )
        if self.status not in _OBSERVATION_STATUSES:
            raise ValueError(f"invalid observation status: {self.status}")
        object.__setattr__(self, "facts", _freeze_mapping(self.facts))

        if self.status == "found":
            if not self.facts:
                raise ValueError("found observation requires facts")
            if self.support_text is None:
                raise ValueError("found observation requires support_text")
            return
        if self.facts:
            raise ValueError("not_found observation cannot contain facts")
        if self.support_text is not None:
            raise ValueError("not_found observation cannot contain support_text")

    @property
    def payload(self) -> Mapping[str, object]:
        return self.facts


@dataclass(frozen=True, slots=True)
class AgentAction:
    name: str
    arguments: Mapping[str, object]
    finish_draft: AnswerDraft | None

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "action name")
        if self.finish_draft is not None and not isinstance(
            self.finish_draft,
            AnswerDraft,
        ):
            raise TypeError("finish_draft must be an AnswerDraft")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))

    @property
    def draft(self) -> AnswerDraft | None:
        return self.finish_draft

    def fingerprint(self) -> str:
        finish_payload: object = None
        if self.finish_draft is not None:
            finish_payload = {
                "answer": self.finish_draft.answer,
                "claims": [
                    {
                        "text": claim.text,
                        "support_ids": list(claim.support_ids),
                        "supporting_quotes": list(claim.supporting_quotes),
                    }
                    for claim in self.finish_draft.claims
                ],
            }
        payload = {
            "name": self.name,
            "arguments": _plain_value(self.arguments),
            "finish_draft": finish_payload,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: Mapping[str, object]
    status: ToolCallStatus
    error_category: str | None

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "tool name")
        _require_nonempty(self.status, "status")
        if self.error_category is not None:
            _require_nonempty(
                self.error_category,
                "error_category",
                empty_message=f"{self.status} tool call requires error_category",
            )
        if self.status not in _TOOL_CALL_STATUSES:
            raise ValueError(f"invalid tool call status: {self.status}")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))
        if self.status in {"failed", "degraded"}:
            if self.error_category is None:
                raise ValueError(f"{self.status} tool call requires error_category")
        elif self.error_category is not None:
            raise ValueError(
                f"{self.status} tool call cannot have error_category"
            )


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: ResultStatus
    answer: str | None
    claims: tuple[EvidenceClaim, ...]
    tool_trace: tuple[ToolCall, ...]
    sources: tuple[Source, ...]
    observations: tuple[Observation, ...]
    refusal_reason: str | None
    error_category: str | None
    trace: RunTrace = _DEFAULT_TRACE

    def __post_init__(self) -> None:
        _require_tuple(self.claims, "claims")
        _require_tuple(self.tool_trace, "tool_trace")
        _require_tuple(self.sources, "sources")
        _require_tuple(self.observations, "observations")
        _require_members(self.claims, EvidenceClaim, "claims")
        _require_members(self.tool_trace, ToolCall, "tool_trace")
        _require_members(self.sources, Source, "sources")
        _require_members(self.observations, Observation, "observations")
        if not isinstance(self.trace, RunTrace):
            raise TypeError("trace must be a RunTrace")
        _require_nonempty(self.status, "status")
        if self.answer is not None:
            _require_nonempty(
                self.answer,
                "answer",
                empty_message="answered result requires a nonempty answer",
            )
        if self.refusal_reason is not None:
            _require_nonempty(
                self.refusal_reason,
                "refusal_reason",
                empty_message="refused result requires refusal_reason",
            )
        if self.error_category is not None:
            _require_nonempty(
                self.error_category,
                "error_category",
                empty_message="failed result requires error_category",
            )
        if self.status not in _RESULT_STATUSES:
            raise ValueError(f"invalid result status: {self.status}")
        _validate_source_ids(self.sources)
        observation_ids = tuple(
            observation.observation_id for observation in self.observations
        )
        _require_unique(observation_ids, "observation_ids")

        if self.status == "answered":
            if self.answer is None:
                raise ValueError("answered result requires a nonempty answer")
            if not self.claims:
                raise ValueError("answered result requires claims")
            if not self.sources:
                raise ValueError("answered result requires sources")
            if self.refusal_reason is not None:
                raise ValueError("answered result cannot have refusal_reason")
            if self.error_category is not None:
                raise ValueError("answered result cannot have error_category")
            return

        if self.status == "refused":
            if self.answer is not None:
                raise ValueError("refused result cannot expose an answer")
            if self.claims:
                raise ValueError("refused result cannot expose claims")
            if self.sources:
                raise ValueError("refused result cannot expose sources")
            if self.refusal_reason is None:
                raise ValueError("refused result requires refusal_reason")
            if self.error_category is not None:
                raise ValueError("refused result cannot have error_category")
            return

        if self.answer is not None:
            raise ValueError("failed result cannot expose an answer")
        if self.claims:
            raise ValueError("failed result cannot expose claims")
        if self.sources:
            raise ValueError("failed result cannot expose sources")
        if self.refusal_reason is not None:
            raise ValueError("failed result cannot have refusal_reason")
        if self.error_category is None:
            raise ValueError("failed result requires error_category")

    @classmethod
    def answered(
        cls,
        answer: str,
        *,
        claims: tuple[EvidenceClaim, ...] = (),
        tool_trace: tuple[ToolCall, ...] = (),
        sources: tuple[Source, ...] = (),
        observations: tuple[Observation, ...] = (),
        trace: RunTrace = _DEFAULT_TRACE,
    ) -> Self:
        return cls(
            "answered",
            answer,
            claims,
            tool_trace,
            sources,
            observations,
            None,
            None,
            trace,
        )

    @classmethod
    def refused(
        cls,
        refusal_reason: str,
        *,
        tool_trace: tuple[ToolCall, ...] = (),
        observations: tuple[Observation, ...] = (),
        trace: RunTrace = _DEFAULT_TRACE,
    ) -> Self:
        return cls(
            "refused",
            None,
            (),
            tool_trace,
            (),
            observations,
            refusal_reason,
            None,
            trace,
        )

    @classmethod
    def failed(
        cls,
        error_category: str,
        *,
        tool_trace: tuple[ToolCall, ...] = (),
        observations: tuple[Observation, ...] = (),
        trace: RunTrace = _DEFAULT_TRACE,
    ) -> Self:
        return cls(
            "failed",
            None,
            (),
            tool_trace,
            (),
            observations,
            None,
            error_category,
            trace,
        )


@dataclass(frozen=True, slots=True)
class StructuredGenerationRequest:
    messages: tuple[Mapping[str, object], ...]
    schema: Mapping[str, object]
    max_output_tokens: int

    def __post_init__(self) -> None:
        _require_tuple(self.messages, "messages")
        if not self.messages:
            raise ValueError("messages must not be empty")
        _require_positive_integer(self.max_output_tokens, "max_output_tokens")
        frozen_messages = tuple(
            _freeze_mapping(message, redact_sensitive=False)
            for message in self.messages
        )
        object.__setattr__(self, "messages", frozen_messages)
        object.__setattr__(
            self,
            "schema",
            _freeze_mapping(self.schema, redact_sensitive=False),
        )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    claim_index: int | None
    detail: str

    def __post_init__(self) -> None:
        _require_nonempty(self.code, "code")
        _require_nonempty(self.detail, "detail")
        if self.claim_index is not None:
            _require_non_negative_integer(self.claim_index, "claim_index")


# Compatibility imports resolve to the canonical Task 3 contracts and semantics.
QueryResult = RagResult
ToolTrace = ToolCall
