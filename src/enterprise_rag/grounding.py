from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from .errors import RerankerProtocolError
from .models import (
    AnswerDraft,
    Observation,
    RerankedChunk,
    ValidationIssue,
)
from .ports import ClaimSupportScorerPort
from .reranking import GteReranker

_DATE_PATTERN = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b")
_CURRENCY_PATTERN = re.compile(
    r"\b(?:CNY|RMB|USD|EUR|JPY)\s*[0-9][0-9,]*(?:\.[0-9]+)?\b",
    re.IGNORECASE,
)
_BUSINESS_ID_PATTERN = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b")
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*(?![A-Za-z0-9])")


class GteClaimSupportScorer:
    """Claim-support scorer that reuses an already loaded GTE reranker."""

    backend_kind = "real"

    def __init__(self, reranker: GteReranker) -> None:
        if not isinstance(reranker, GteReranker):
            raise TypeError("reranker must be a GteReranker")
        self._reranker = reranker

    @property
    def model_identity(self) -> tuple[str, str, str]:
        return self._reranker.model_identity

    async def score(
        self,
        claim: str,
        support_texts: tuple[str, ...],
    ) -> tuple[float, ...]:
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError("claim must be a non-blank string")
        if not isinstance(support_texts, tuple) or any(
            not isinstance(text, str) or not text.strip() for text in support_texts
        ):
            raise ValueError("support_texts must be a tuple of non-blank strings")
        return await self._reranker.score_pairs(
            tuple((claim, text) for text in support_texts)
        )


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    evidence: tuple[RerankedChunk, ...]
    reason: str | None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a boolean")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, RerankedChunk) for item in self.evidence
        ):
            raise TypeError("evidence must be a tuple of RerankedChunk values")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("reason must be a non-empty string")
        if self.allowed:
            if not self.evidence:
                raise ValueError("allowed decision requires evidence")
            if self.reason is not None:
                raise ValueError("allowed decision cannot have a reason")
        else:
            if self.evidence:
                raise ValueError("refused decision cannot expose evidence")
            if self.reason is None:
                raise ValueError("refused decision requires a reason")


class EvidenceGate:
    backend_kind = "programmatic"

    def __init__(self, *, min_score: float) -> None:
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not math.isfinite(min_score)
            or not 0 <= min_score <= 1
        ):
            raise ValueError("min_score must be a finite number in [0, 1]")
        self._min_score = float(min_score)

    @property
    def min_score(self) -> float:
        """The public threshold this gate applies, for truthful trace reporting."""

        return self._min_score

    def decide(self, evidence: tuple[RerankedChunk, ...]) -> GateDecision:
        if not isinstance(evidence, tuple) or any(
            not isinstance(item, RerankedChunk) for item in evidence
        ):
            raise TypeError("evidence must be a tuple of RerankedChunk values")
        eligible = tuple(
            item
            for item in evidence
            if item.rerank_score >= self._min_score
            and item.candidate.text.strip()
            and item.candidate.chunk_id
            and item.candidate.filename
        )
        if not eligible:
            return GateDecision(False, (), "insufficient_evidence")
        return GateDecision(True, eligible, None)


class GroundingValidator:
    backend_kind = "programmatic"

    def __init__(
        self,
        scorer: ClaimSupportScorerPort,
        *,
        min_claim_support_score: float,
    ) -> None:
        if (
            isinstance(min_claim_support_score, bool)
            or not isinstance(min_claim_support_score, (int, float))
            or not math.isfinite(min_claim_support_score)
            or not 0 <= min_claim_support_score <= 1
        ):
            raise ValueError(
                "min_claim_support_score must be a finite number in [0, 1]"
            )
        self._scorer = scorer
        self._min_score = float(min_claim_support_score)

    async def validate(
        self,
        draft: AnswerDraft,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[ValidationIssue, ...]:
        if not isinstance(draft, AnswerDraft):
            raise TypeError("draft must be an AnswerDraft")
        if not isinstance(evidence, tuple) or any(
            not isinstance(item, RerankedChunk) for item in evidence
        ):
            raise TypeError("evidence must contain RerankedChunk values")
        if not isinstance(observations, tuple) or any(
            not isinstance(item, Observation) for item in observations
        ):
            raise TypeError("observations must contain Observation values")

        support_map = self._build_support_map(evidence, observations)
        issues: list[ValidationIssue] = []
        normalized_answer = _normalize(draft.answer)

        for claim_index, claim in enumerate(draft.claims):
            if _normalize(claim.text) not in normalized_answer:
                issues.append(
                    ValidationIssue(
                        "claim_not_in_answer",
                        claim_index,
                        "claim text is absent from the final answer",
                    )
                )

            missing_ids = tuple(
                support_id
                for support_id in claim.support_ids
                if support_id not in support_map
            )
            for support_id in missing_ids:
                issues.append(
                    ValidationIssue(
                        "unknown_support_id",
                        claim_index,
                        f"unknown support ID: {support_id}",
                    )
                )
            cited_texts = tuple(
                support_map[support_id]
                for support_id in claim.support_ids
                if support_id in support_map
            )
            normalized_supports = tuple(_normalize(text) for text in cited_texts)

            for quote in claim.supporting_quotes:
                normalized_quote = _normalize(quote)
                if not any(
                    normalized_quote in support_text
                    for support_text in normalized_supports
                ):
                    issues.append(
                        ValidationIssue(
                            "quote_not_found",
                            claim_index,
                            "supporting quote is not present in cited support",
                        )
                    )

            normalized_quotes = " ".join(
                _normalize(quote) for quote in claim.supporting_quotes
            )
            for fact in sorted(_required_facts(claim.text)):
                if _normalize(fact) not in normalized_quotes:
                    issues.append(
                        ValidationIssue(
                            "fact_not_in_quote",
                            claim_index,
                            f"claim fact is absent from quotes: {fact}",
                        )
                    )

            if cited_texts:
                scores = await self._scorer.score(claim.text, cited_texts)
                if (
                    not isinstance(scores, tuple)
                    or len(scores) != len(cited_texts)
                    or any(
                        isinstance(score, bool)
                        or not isinstance(score, (int, float))
                        or not math.isfinite(score)
                        for score in scores
                    )
                ):
                    raise RerankerProtocolError(
                        "support scorer returned invalid scores"
                    )
                if max(float(score) for score in scores) < self._min_score:
                    issues.append(
                        ValidationIssue(
                            "claim_support_below_threshold",
                            claim_index,
                            "claim support score is below threshold",
                        )
                    )

        return tuple(issues)

    @staticmethod
    def _build_support_map(
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
    ) -> dict[str, str]:
        support_map: dict[str, str] = {}
        for item in evidence:
            chunk_id = item.candidate.chunk_id
            if chunk_id in support_map:
                raise RerankerProtocolError("duplicate evidence support ID")
            support_map[chunk_id] = item.candidate.text
        for observation in observations:
            if observation.status != "found" or observation.support_text is None:
                continue
            if observation.observation_id in support_map:
                raise RerankerProtocolError("duplicate observation support ID")
            support_map[observation.observation_id] = observation.support_text
        return support_map


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _required_facts(claim: str) -> set[str]:
    facts: set[str] = set()
    for pattern in (
        _DATE_PATTERN,
        _CURRENCY_PATTERN,
        _BUSINESS_ID_PATTERN,
        _NUMBER_PATTERN,
    ):
        facts.update(match.group(0) for match in pattern.finditer(claim))
    return facts
