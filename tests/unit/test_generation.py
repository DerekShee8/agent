from __future__ import annotations

from collections.abc import Mapping

import pytest

from enterprise_rag.errors import GenerationProtocolError
from enterprise_rag.generation import (
    ACTION_SCHEMA,
    ANSWER_SCHEMA,
    AgentPlanner,
    AnswerGenerator,
    parse_agent_action,
    parse_answer_draft,
)
from enterprise_rag.models import (
    Observation,
    StructuredGenerationRequest,
    ValidationIssue,
)
from tests.fakes import answer_draft, reranked


def valid_payload() -> dict[str, object]:
    return {
        "answer": "The hotel cap is CNY 600.",
        "claims": [
            {
                "text": "The hotel cap is CNY 600.",
                "support_ids": ["policy.md::0000"],
                "supporting_quotes": ["The hotel cap is CNY 600."],
            }
        ],
    }


def test_parse_answer_draft_returns_immutable_contract() -> None:
    draft = parse_answer_draft(valid_payload())

    assert draft.answer == "The hotel cap is CNY 600."
    assert draft.claims[0].support_ids == ("policy.md::0000",)


def test_answer_schema_requires_cjk_in_answer() -> None:
    properties = ANSWER_SCHEMA["properties"]
    assert isinstance(properties, Mapping)
    answer = properties["answer"]
    assert isinstance(answer, Mapping)
    assert answer["pattern"] == r'^[^"\\\n\r]*[一-鿿][^"\\\n\r]*$'


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["claims"][0].update({"unexpected": True}),  # type: ignore[index]
        lambda payload: payload["claims"][0].update(  # type: ignore[index]
            {"support_ids": ["policy.md::0000", "policy.md::0000"]}
        ),
        lambda payload: payload.update({"answer": "Bad\x00answer"}),
    ],
)
def test_parse_answer_draft_rejects_untrusted_shapes(mutate: object) -> None:
    payload = valid_payload()
    mutate(payload)  # type: ignore[call-non-callable]

    with pytest.raises(GenerationProtocolError):
        parse_answer_draft(payload)


def test_parse_answer_draft_leaves_claim_alignment_to_grounding_validator() -> None:
    payload = valid_payload()
    payload["answer"] = "CNY 600"

    draft = parse_answer_draft(payload)

    assert draft.answer == "CNY 600"
    assert draft.claims[0].text == "The hotel cap is CNY 600."


class RecordingBackend:
    backend_kind = "fake"

    def __init__(self, payloads: list[Mapping[str, object]]) -> None:
        self._payloads = payloads
        self.requests: list[StructuredGenerationRequest] = []

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        self.requests.append(request)
        return self._payloads.pop(0)


@pytest.mark.asyncio
async def test_answer_generator_uses_same_schema_for_answer_and_repair() -> None:
    backend = RecordingBackend([valid_payload(), valid_payload()])
    generator = AnswerGenerator(backend)
    evidence = (reranked("policy.md::0000", "The hotel cap is CNY 600."),)

    first = await generator.generate_answer("What is the cap?", evidence)
    repaired = await generator.repair_answer(
        "What is the cap?",
        evidence,
        answer_draft(),
        (ValidationIssue("quote_not_found", 0, "quote missing"),),
    )

    assert first == repaired
    assert len(backend.requests) == 2
    assert backend.requests[0].schema == ANSWER_SCHEMA
    assert backend.requests[1].schema == ANSWER_SCHEMA
    assert "repair" in str(backend.requests[1].messages).lower()
    for request in backend.requests:
        system_message = request.messages[0]["content"]
        assert isinstance(system_message, str)
        assert "Chinese" in system_message
        assert "not merely an amount or identifier" in system_message
        assert "verbatim substring" in system_message
        assert "must appear verbatim in the answer" in system_message
        assert "exactly one claim" in system_message
        assert "If one evidence item directly answers the question" in system_message
        assert "do not add related rules" in system_message
    assert generator.backend_kind == "fake"


def test_parse_agent_action_accepts_only_exact_allowlisted_shapes() -> None:
    search = parse_agent_action(
        {"action": "search_knowledge", "question": "P4 hotel cap"}
    )
    lookup = parse_agent_action(
        {"action": "get_expense_claim", "claim_id": "CLAIM-1024"}
    )
    final = parse_agent_action({"action": "finish", "draft": valid_payload()})

    assert search.name == "search_knowledge"
    assert dict(search.arguments) == {"question": "P4 hotel cap"}
    assert lookup.name == "get_expense_claim"
    assert final.finish_draft is not None

    with pytest.raises(GenerationProtocolError):
        parse_agent_action(
            {
                "action": "get_expense_claim",
                "claim_id": "CLAIM-1024",
                "path": "C:/secret",
            }
        )
    with pytest.raises(GenerationProtocolError):
        parse_agent_action({"action": "delete_claim", "claim_id": "CLAIM-1024"})


@pytest.mark.asyncio
async def test_agent_planner_uses_action_schema_and_structured_observations() -> None:
    backend = RecordingBackend(
        [{"action": "search_knowledge", "question": "P4 hotel cap"}]
    )
    planner = AgentPlanner(backend)
    observation = Observation(
        "obs:get_expense_claim:CLAIM-1024",
        "get_expense_claim",
        "found",
        {"status": "rejected"},
        "claim status is rejected",
    )

    result = await planner.next_action("Explain the hotel policy", (observation,))

    assert result.name == "search_knowledge"
    assert backend.requests[0].schema == ACTION_SCHEMA
    assert "CLAIM-1024" in str(backend.requests[0].messages)
    assert "reasoning" in str(backend.requests[0].messages).lower()
    system_prompt = backend.requests[0].messages[0]["content"]
    assert isinstance(system_prompt, str)
    assert "inspect those records first" in system_prompt
    assert "omit record IDs" in system_prompt


@pytest.mark.asyncio
async def test_agent_planner_resolves_ids_before_model() -> None:
    backend = RecordingBackend([])
    planner = AgentPlanner(backend)

    first = await planner.next_action(
        "Explain EMP-003 and CLAIM-1024",
        (),
    )
    claim = Observation(
        "obs:get_expense_claim:CLAIM-1024",
        "get_expense_claim",
        "found",
        {
            "claim_id": "CLAIM-1024",
            "employee_id": "EMP-003",
            "trip_id": "TRIP-2409-017",
        },
        "claim facts",
    )
    second = await planner.next_action(
        "Explain EMP-003 and CLAIM-1024",
        (claim,),
    )

    assert first.name == "get_expense_claim"
    assert dict(first.arguments) == {"claim_id": "CLAIM-1024"}
    assert second.name == "get_employee_profile"
    assert dict(second.arguments) == {"employee_id": "EMP-003"}
    assert backend.requests == []


@pytest.mark.asyncio
async def test_agent_planner_builds_policy_query_from_business_facts() -> None:
    backend = RecordingBackend([])
    planner = AgentPlanner(backend)
    observations = (
        Observation(
            "obs:get_expense_claim:CLAIM-1024",
            "get_expense_claim",
            "found",
            {
                "claim_id": "CLAIM-1024",
                "employee_id": "EMP-003",
                "trip_id": "TRIP-2409-017",
                "hotel_nightly_amount": 680,
                "rejection_code": "HOTEL_OVER_LIMIT",
            },
            "claim facts",
        ),
        Observation(
            "obs:get_employee_profile:EMP-003",
            "get_employee_profile",
            "found",
            {"employee_id": "EMP-003", "grade": "P4"},
            "employee facts",
        ),
        Observation(
            "obs:get_business_trip:TRIP-2409-017",
            "get_business_trip",
            "found",
            {
                "trip_id": "TRIP-2409-017",
                "destination": "上海",
                "city_tier": "A",
                "vp_exception_approved": False,
            },
            "trip facts",
        ),
    )

    result = await planner.next_action("解释 CLAIM-1024", observations)

    assert result.name == "search_knowledge"
    question = result.arguments["question"]
    assert isinstance(question, str)
    assert "P4" in question
    assert "上海" in question
    assert "A 类城市" in question
    assert "CNY 680" not in question
    assert "CLAIM-1024" not in question
    assert backend.requests == []


@pytest.mark.asyncio
async def test_agent_planner_requests_direct_answer_after_knowledge() -> None:
    backend = RecordingBackend([valid_payload()])
    planner = AgentPlanner(backend)
    knowledge = Observation(
        "policy.md::0000",
        "search_knowledge",
        "found",
        {"filename": "policy.md"},
        "The hotel cap is CNY 600.",
    )
    claim = Observation(
        "obs:get_expense_claim:CLAIM-1024",
        "get_expense_claim",
        "found",
        {"status": "rejected"},
        "claim status is rejected",
    )

    result = await planner.next_action("Explain the cap", (claim, knowledge))

    assert result.name == "finish"
    assert result.finish_draft is not None
    assert backend.requests[0].schema == ANSWER_SCHEMA
    user_content = backend.requests[0].messages[1]["content"]
    assert isinstance(user_content, str)
    assert (
        '"required_business_support_ids":["obs:get_expense_claim:CLAIM-1024"]'
        in user_content
    )
    system_content = backend.requests[0].messages[0]["content"]
    assert isinstance(system_content, str)
    assert "claim text must appear verbatim in the answer" in system_content
    assert "quote must be a verbatim substring" in system_content


@pytest.mark.asyncio
async def test_agent_planner_retries_final_protocol_once_with_compact_request(
) -> None:
    backend = RecordingBackend([{}, valid_payload()])
    planner = AgentPlanner(backend)
    knowledge = Observation(
        "policy.md::0000",
        "search_knowledge",
        "found",
        {"filename": "policy.md"},
        "The hotel cap is CNY 600.",
    )
    claim = Observation(
        "obs:get_expense_claim:CLAIM-1024",
        "get_expense_claim",
        "found",
        {"status": "rejected"},
        "claim status is rejected",
    )

    result = await planner.next_action("Explain the cap", (claim, knowledge))

    assert result.name == "finish"
    assert len(backend.requests) == 2
    retry = backend.requests[1]
    assert retry.max_output_tokens == 512
    assert "exactly one concise knowledge claim" in str(retry.messages)
    assert "required_business_support_ids" not in str(retry.messages)


@pytest.mark.asyncio
async def test_agent_planner_stops_after_one_final_protocol_retry() -> None:
    backend = RecordingBackend([{}, {}])
    planner = AgentPlanner(backend)
    knowledge = Observation(
        "policy.md::0000",
        "search_knowledge",
        "found",
        {"filename": "policy.md"},
        "The hotel cap is CNY 600.",
    )

    with pytest.raises(GenerationProtocolError):
        await planner.next_action("Explain the cap", (knowledge,))

    assert len(backend.requests) == 2


@pytest.mark.asyncio
async def test_agent_planner_repair_uses_answer_schema() -> None:
    backend = RecordingBackend([valid_payload()])
    planner = AgentPlanner(backend)
    observation = Observation(
        "policy.md::0000",
        "search_knowledge",
        "found",
        {"filename": "policy.md"},
        "The hotel cap is CNY 600.",
    )

    repaired = await planner.repair_answer(
        "Explain the cap",
        (observation,),
        answer_draft(),
        (ValidationIssue("quote_not_found", 0, "quote missing"),),
    )

    assert repaired.answer == "The hotel cap is CNY 600."
    assert backend.requests[0].schema == ANSWER_SCHEMA
    repair_prompt = backend.requests[0].messages[0]["content"]
    assert isinstance(repair_prompt, str)
    assert "claim text must appear verbatim in the answer" in repair_prompt
    assert "quote must be a verbatim substring" in repair_prompt
