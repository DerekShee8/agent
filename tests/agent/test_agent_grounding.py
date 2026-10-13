from __future__ import annotations

import pytest

from enterprise_rag.models import AnswerDraft, EvidenceClaim, ValidationIssue
from enterprise_rag.rag_service import KnowledgeSearchResult
from tests.agent.test_agent_service import (
    POLICY_ID,
    POLICY_TEXT,
    ScriptedKnowledge,
    ScriptedValidator,
    action,
    finish,
    grounded_draft,
    make_agent,
)
from tests.fakes import reranked


@pytest.mark.asyncio
async def test_agent_repairs_once_then_returns_grounded_answer() -> None:
    issue = ValidationIssue("quote_not_found", 0, "quote is absent")
    validator = ScriptedValidator(((issue,), ()))
    repaired = grounded_draft()
    service, planner, _tools, _validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            finish(grounded_draft()),
        ],
        validator=validator,
        repaired=repaired,
    )

    result = await service.run("repair the draft")

    assert result.status == "answered"
    assert planner.repair_count == 1
    assert len(validator.calls) == 2
    assert result.claims == (
        EvidenceClaim(
            text=repaired.claims[0].text,
            support_ids=repaired.claims[0].support_ids,
            supporting_quotes=(POLICY_TEXT,),
        ),
    )


@pytest.mark.asyncio
async def test_agent_refuses_when_single_repair_remains_invalid() -> None:
    issue = ValidationIssue("claim_support_below_threshold", 0, "unsupported")
    validator = ScriptedValidator(((issue,), (issue,)))
    service, planner, _tools, _validator = make_agent(
        [action("search_knowledge", question="policy"), finish()],
        validator=validator,
    )

    result = await service.run("cannot ground")

    assert result.status == "refused"
    assert result.refusal_reason == "grounding_validation_failed"
    assert planner.repair_count == 1
    assert result.sources == ()
    issue_events = [
        event for event in result.trace.events if event.stage == "grounding_issue"
    ]
    assert [event.attributes["code"] for event in issue_events] == [
        "claim_support_below_threshold",
        "claim_support_below_threshold",
    ]
    assert [event.attributes["attempt"] for event in issue_events] == [
        "initial",
        "repair",
    ]


@pytest.mark.asyncio
async def test_tool_only_citation_cannot_replace_knowledge_citation() -> None:
    tool_id = "obs:get_expense_claim:CLAIM-1024"
    draft = grounded_draft(support_ids=(tool_id,))
    service, _planner, _tools, validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            action("get_expense_claim", claim_id="CLAIM-1024"),
            finish(draft),
        ]
    )

    result = await service.run("cite only a record")

    assert result.status == "refused"
    assert result.refusal_reason == "knowledge_evidence_required"
    assert validator.calls == []


@pytest.mark.asyncio
async def test_final_sources_exclude_uncited_knowledge_and_tool_observations() -> None:
    service, _planner, _tools, _validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            action("get_expense_claim", claim_id="CLAIM-1024"),
            finish(),
        ]
    )

    result = await service.run("cite policy")

    assert result.status == "answered"
    assert tuple(source.chunk_id for source in result.sources) == (POLICY_ID,)
    assert all(not source.chunk_id.startswith("obs:") for source in result.sources)


@pytest.mark.asyncio
async def test_unknown_support_id_is_never_accepted_if_validator_misses_it() -> None:
    draft = grounded_draft(support_ids=(POLICY_ID, "unknown-support"))
    service, _planner, _tools, _validator = make_agent(
        [action("search_knowledge", question="policy"), finish(draft)]
    )

    result = await service.run("attempt an unknown citation")

    assert result.status == "refused"
    assert result.refusal_reason == "grounding_validation_failed"
    assert result.sources == ()


@pytest.mark.asyncio
async def test_agent_answer_is_canonicalized_to_grounded_claim_text() -> None:
    claim_text = "The P4 hotel cap is CNY 600 per night."
    loose_draft = AnswerDraft(
        answer="A loosely worded answer that is not the claim.",
        claims=(
            EvidenceClaim(
                claim_text,
                (POLICY_ID,),
                ("hotel cap of CNY 600 per night",),
            ),
        ),
    )
    service, _planner, _tools, validator = make_agent(
        [action("search_knowledge", question="policy"), finish(loose_draft)]
    )

    result = await service.run("canonicalize the grounded answer")

    assert result.status == "answered"
    assert result.answer == claim_text
    assert validator.calls[0][0].answer == claim_text
    assert validator.calls[0][0].claims[0].supporting_quotes == (POLICY_TEXT,)


@pytest.mark.asyncio
async def test_agent_realigns_known_citation_to_chunk_containing_exact_quote() -> None:
    other_id = "policy.md::0001"
    draft = AnswerDraft(
        answer="The P4 hotel cap is CNY 600 per night.",
        claims=(
            EvidenceClaim(
                "The P4 hotel cap is CNY 600 per night.",
                (other_id,),
                ("hotel cap of CNY 600 per night",),
            ),
        ),
    )
    knowledge = KnowledgeSearchResult.found(
        (
            reranked(POLICY_ID, POLICY_TEXT, rank=1),
            reranked(other_id, "P5 hotel cap is CNY 850.", rank=2),
        )
    )
    service, _planner, _tools, _validator = make_agent(
        [action("search_knowledge", question="policy"), finish(draft)],
        knowledge=ScriptedKnowledge(knowledge),
    )

    result = await service.run("realign citation")

    assert result.status == "answered"
    assert tuple(source.chunk_id for source in result.sources) == (POLICY_ID,)
    assert result.claims[0].support_ids == (POLICY_ID,)


@pytest.mark.asyncio
async def test_agent_supplements_every_found_business_observation() -> None:
    required_ids = (
        "obs:get_employee_profile:EMP-003",
        "obs:get_expense_claim:CLAIM-1024",
        "obs:get_business_trip:TRIP-2409-017",
    )
    service, planner, _tools, _validator = make_agent(
        [
            action("get_expense_claim", claim_id="CLAIM-1024"),
            action("get_employee_profile", employee_id="EMP-003"),
            action("get_business_trip", trip_id="TRIP-2409-017"),
            action("search_knowledge", question="policy"),
            finish(),
        ],
    )

    result = await service.run("explain the rejected claim")

    assert result.status == "answered"
    assert planner.repair_count == 0
    cited_ids = {
        support_id
        for claim in result.claims
        for support_id in claim.support_ids
    }
    assert cited_ids == {POLICY_ID, *required_ids}
    assert result.answer is not None
    assert "CLAIM-1024" in result.answer


@pytest.mark.asyncio
async def test_agent_replaces_model_business_claim_with_canonical_record_claim(
) -> None:
    observation_id = "obs:get_employee_profile:EMP-003"
    draft = AnswerDraft(
        answer="Policy answer. Model-written employee claim.",
        claims=(
            grounded_draft().claims[0],
            EvidenceClaim(
                "Model-written employee claim.",
                (observation_id,),
                ("record_id=EMP-003",),
            ),
        ),
    )
    service, _planner, _tools, _validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            action("get_employee_profile", employee_id="EMP-003"),
            finish(draft),
        ]
    )

    result = await service.run("use canonical record facts")

    assert result.status == "answered"
    business_claims = tuple(
        claim
        for claim in result.claims
        if claim.support_ids == (observation_id,)
    )
    assert tuple(claim.text for claim in business_claims) == (
        "员工 EMP-003 的职级为 P4, 差旅等级为 standard.",
    )
