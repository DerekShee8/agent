from __future__ import annotations

import math

import pytest

from enterprise_rag.errors import RerankerProtocolError
from enterprise_rag.grounding import EvidenceGate, GroundingValidator
from enterprise_rag.models import AnswerDraft, EvidenceClaim, Observation
from tests.fakes import FakeSupportScorer, answer_draft, reranked


def test_evidence_gate_filters_below_threshold_and_refuses_empty_result() -> None:
    gate = EvidenceGate(min_score=0.5)
    evidence = (
        reranked("low", "weak evidence", score=0.49, rank=1),
        reranked("high", "strong evidence", score=0.75, rank=2),
    )

    decision = gate.decide(evidence)

    assert decision.allowed is True
    assert [item.candidate.chunk_id for item in decision.evidence] == ["high"]
    assert decision.reason is None
    refused = gate.decide((evidence[0],))
    assert refused.allowed is False
    assert refused.evidence == ()
    assert refused.reason == "insufficient_evidence"


@pytest.mark.asyncio
async def test_grounding_validator_accepts_exact_grounded_claim() -> None:
    evidence = (
        reranked(
            "policy.md::0000",
            "For P4 employees, the hotel cap is CNY 600 in Shanghai.",
        ),
    )
    scorer = FakeSupportScorer((0.91,))
    validator = GroundingValidator(scorer, min_claim_support_score=0.5)
    draft = answer_draft(
        answer="The hotel cap is CNY 600.",
        claim="The hotel cap is CNY 600.",
        quotes=("the hotel cap is CNY 600",),
    )

    assert await validator.validate(draft, evidence, ()) == ()
    assert scorer.calls == [
        (
            "The hotel cap is CNY 600.",
            ("For P4 employees, the hotel cap is CNY 600 in Shanghai.",),
        )
    ]


@pytest.mark.asyncio
async def test_grounding_validator_reports_unknown_support_and_bad_quote() -> None:
    validator = GroundingValidator(
        FakeSupportScorer((0.9,)),
        min_claim_support_score=0.5,
    )
    evidence = (reranked("known", "The policy says CNY 600."),)
    draft = answer_draft(
        support_ids=("missing",),
        quotes=("CNY 900",),
    )

    issues = await validator.validate(draft, evidence, ())

    assert {issue.code for issue in issues} >= {"unknown_support_id", "quote_not_found"}


@pytest.mark.asyncio
async def test_grounding_validator_requires_facts_in_quotes_and_support_score() -> None:
    validator = GroundingValidator(
        FakeSupportScorer((0.49,)),
        min_claim_support_score=0.5,
    )
    evidence = (
        reranked(
            "policy.md::0000",
            "CLAIM-1024 was rejected. The hotel cap is CNY 600.",
        ),
    )
    draft = answer_draft(
        answer="CLAIM-1024 exceeded the CNY 600 cap by CNY 80.",
        claim="CLAIM-1024 exceeded the CNY 600 cap by CNY 80.",
        quotes=("CLAIM-1024 was rejected. The hotel cap is CNY 600.",),
    )

    issues = await validator.validate(draft, evidence, ())

    assert {issue.code for issue in issues} >= {
        "fact_not_in_quote",
        "claim_support_below_threshold",
    }


@pytest.mark.asyncio
async def test_grounding_validator_accepts_found_observation_support() -> None:
    observation = Observation(
        observation_id="claim-observation",
        source="get_claim_status",
        status="found",
        facts={"claim_id": "CLAIM-1024", "status": "rejected"},
        support_text="CLAIM-1024 has status rejected.",
    )
    validator = GroundingValidator(
        FakeSupportScorer((0.9,)),
        min_claim_support_score=0.5,
    )
    draft = answer_draft(
        answer="CLAIM-1024 has status rejected.",
        claim="CLAIM-1024 has status rejected.",
        support_ids=("claim-observation",),
        quotes=("CLAIM-1024 has status rejected",),
    )

    assert await validator.validate(draft, (), (observation,)) == ()


@pytest.mark.asyncio
async def test_grounding_validator_detects_claim_missing_from_answer() -> None:
    validator = GroundingValidator(
        FakeSupportScorer((0.9,)),
        min_claim_support_score=0.5,
    )
    draft = AnswerDraft(
        answer="A different conclusion.",
        claims=(
            EvidenceClaim(
                text="The hotel cap is CNY 600.",
                support_ids=("policy.md::0000",),
                supporting_quotes=("The hotel cap is CNY 600.",),
            ),
        ),
    )

    issues = await validator.validate(
        draft,
        (reranked("policy.md::0000", "The hotel cap is CNY 600."),),
        (),
    )

    assert "claim_not_in_answer" in {issue.code for issue in issues}


@pytest.mark.asyncio
@pytest.mark.parametrize("scores", [(math.nan,), (0.8, 0.7)])
async def test_grounding_validator_rejects_scorer_protocol_errors(
    scores: tuple[float, ...],
) -> None:
    validator = GroundingValidator(
        FakeSupportScorer(scores),
        min_claim_support_score=0.5,
    )

    with pytest.raises(RerankerProtocolError, match="support scorer"):
        await validator.validate(
            answer_draft(),
            (reranked("policy.md::0000", "The hotel cap is CNY 600."),),
            (),
        )

