from __future__ import annotations

import re
from collections.abc import Mapping

import pytest

from enterprise_rag.agent_service import AgentService
from enterprise_rag.errors import ToolExecutionError, ToolValidationError
from enterprise_rag.models import (
    AgentAction,
    AnswerDraft,
    EvidenceClaim,
    Observation,
    ValidationIssue,
)
from enterprise_rag.rag_service import KnowledgeSearchResult
from tests.fakes import FakeClock, reranked

POLICY_ID = "policy.md::0000"
POLICY_TEXT = "P4 employees have a hotel cap of CNY 600 per night."


def action(name: str, **arguments: object) -> AgentAction:
    return AgentAction(name, arguments, None)


def finish(draft: AnswerDraft | None = None) -> AgentAction:
    return AgentAction("finish", {}, draft or grounded_draft())


def grounded_draft(
    *,
    support_ids: tuple[str, ...] = (POLICY_ID,),
) -> AnswerDraft:
    return AnswerDraft(
        answer="The P4 hotel cap is CNY 600 per night.",
        claims=(
            EvidenceClaim(
                "The P4 hotel cap is CNY 600 per night.",
                support_ids,
                ("hotel cap of CNY 600 per night",),
            ),
        ),
    )


class ScriptedPlanner:
    backend_kind = "fake"

    def __init__(
        self,
        actions: list[AgentAction],
        *,
        repaired: AnswerDraft | None = None,
    ) -> None:
        self._actions = iter(actions)
        self._repaired = repaired or grounded_draft()
        self.repair_count = 0
        self.observation_counts: list[int] = []

    async def next_action(
        self,
        task: str,
        observations: tuple[Observation, ...],
    ) -> AgentAction:
        assert task
        self.observation_counts.append(len(observations))
        return next(self._actions)

    async def repair_answer(
        self,
        task: str,
        observations: tuple[Observation, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        assert task
        assert observations
        assert draft
        assert issues
        self.repair_count += 1
        return self._repaired


class SwitchingPlanner(ScriptedPlanner):
    def __init__(self, actions: list[AgentAction]) -> None:
        super().__init__(actions)
        self._switch_category: str | None = "authentication"

    def consume_backend_switch(self) -> str | None:
        category = self._switch_category
        self._switch_category = None
        return category


class ScriptedKnowledge:
    backend_kind = "fake"

    def __init__(
        self,
        result: KnowledgeSearchResult | None = None,
        *,
        results: list[KnowledgeSearchResult] | None = None,
    ) -> None:
        default = result or KnowledgeSearchResult.found(
            (reranked(POLICY_ID, POLICY_TEXT),)
        )
        self._results = iter(results or [default])
        self._last_result = default
        self.questions: list[str] = []
        self.trace_bindings: list[tuple[object, str | None]] = []

    async def search_evidence(
        self,
        question: str,
        *,
        recorder: object | None = None,
        parent_id: str | None = None,
    ) -> KnowledgeSearchResult:
        self.questions.append(question)
        self.trace_bindings.append((recorder, parent_id))
        self._last_result = next(self._results, self._last_result)
        return self._last_result


class ScriptedTools:
    def __init__(
        self,
        *,
        not_found: set[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self._not_found = not_found or set()
        self._error = error

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> Observation:
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        record_id = next(iter(arguments.values()))
        assert isinstance(record_id, str)
        patterns = {
            "get_employee_profile": r"EMP-[0-9]{3}",
            "get_expense_claim": r"CLAIM-[0-9]{4}",
            "get_business_trip": r"TRIP-[0-9]{4}-[0-9]{3}",
        }
        if re.fullmatch(patterns[name], record_id) is None:
            raise ToolValidationError("tool arguments are invalid")
        observation_id = f"obs:{name}:{record_id}"
        if record_id in self._not_found:
            return Observation(observation_id, name, "not_found", {}, None)
        facts_by_tool: dict[str, dict[str, object]] = {
            "get_employee_profile": {
                "employee_id": record_id,
                "grade": "P4",
                "travel_tier": "standard",
            },
            "get_expense_claim": {
                "claim_id": record_id,
                "hotel_nightly_amount": 680,
                "status": "rejected",
                "rejection_code": "HOTEL_OVER_LIMIT",
            },
            "get_business_trip": {
                "trip_id": record_id,
                "destination": "Shanghai",
                "city_tier": "A",
                "vp_exception_approved": False,
            },
        }
        return Observation(
            observation_id,
            name,
            "found",
            facts_by_tool[name],
            f"record_id={record_id}",
        )


class ScriptedValidator:
    backend_kind = "fake"

    def __init__(
        self,
        outcomes: tuple[tuple[ValidationIssue, ...], ...] = ((),),
    ) -> None:
        self._outcomes = outcomes
        self.calls: list[
            tuple[AnswerDraft, tuple[object, ...], tuple[Observation, ...]]
        ] = []

    async def validate(
        self,
        draft: AnswerDraft,
        evidence: tuple[object, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[ValidationIssue, ...]:
        self.calls.append((draft, evidence, observations))
        return self._outcomes[len(self.calls) - 1]


def make_agent(
    actions: list[AgentAction],
    *,
    planner: ScriptedPlanner | None = None,
    knowledge: ScriptedKnowledge | None = None,
    tools: ScriptedTools | None = None,
    validator: ScriptedValidator | None = None,
    repaired: AnswerDraft | None = None,
    max_actions: int = 6,
) -> tuple[AgentService, ScriptedPlanner, ScriptedTools, ScriptedValidator]:
    from enterprise_rag.execution_trace import TraceRecorder

    active_planner = planner or ScriptedPlanner(actions, repaired=repaired)
    active_tools = tools or ScriptedTools()
    active_validator = validator or ScriptedValidator()
    clock = FakeClock()
    service = AgentService(
        planner=active_planner,
        rag=knowledge or ScriptedKnowledge(),
        tools=active_tools,
        validator=active_validator,
        max_actions=max_actions,
        trace_factory=lambda: TraceRecorder("agent-test", clock=clock),
    )
    return service, active_planner, active_tools, active_validator


@pytest.mark.asyncio
async def test_agent_trace_records_explicit_online_to_local_switch() -> None:
    planner = SwitchingPlanner(
        [
            action("search_knowledge", question="policy"),
            finish(),
        ]
    )
    service, _planner, _tools, _validator = make_agent(
        [],
        planner=planner,
    )

    result = await service.run("answer with explicit fallback")

    assert result.status == "answered"
    switch_events = [
        event for event in result.trace.events if event.stage == "backend_switch"
    ]
    assert len(switch_events) == 1
    assert switch_events[0].status == "degraded"
    assert switch_events[0].error_category == "authentication"
    assert switch_events[0].attributes == {
        "from_backend": "online",
        "to_backend": "local",
    }


@pytest.mark.asyncio
async def test_agent_uses_knowledge_and_two_fact_tools_then_finishes() -> None:
    service, planner, tools, _validator = make_agent(
        [
            action("search_knowledge", question="P4 hotel cap"),
            action("get_expense_claim", claim_id="CLAIM-1024"),
            action("get_business_trip", trip_id="TRIP-2409-017"),
            finish(),
        ]
    )

    result = await service.run("Explain CLAIM-1024")

    assert result.status == "answered"
    assert [call.name for call in result.tool_trace] == [
        "search_knowledge",
        "get_expense_claim",
        "get_business_trip",
    ]
    assert planner.observation_counts == [0, 1, 2, 3]
    assert [name for name, _arguments in tools.calls] == [
        "get_expense_claim",
        "get_business_trip",
    ]
    assert [source.chunk_id for source in result.sources] == [POLICY_ID]
    observation_events = [
        event for event in result.trace.events if event.stage == "agent_observation"
    ]
    assert [event.attributes["status"] for event in observation_events] == [
        "found",
        "found",
        "found",
    ]
    assert observation_events[0].attributes["observation_id"] == POLICY_ID


@pytest.mark.asyncio
async def test_agent_supports_zero_or_all_three_business_tools() -> None:
    zero_service, _planner, zero_tools, _validator = make_agent(
        [action("search_knowledge", question="policy"), finish()]
    )
    all_service, _planner, all_tools, _validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            action("get_employee_profile", employee_id="EMP-003"),
            action("get_expense_claim", claim_id="CLAIM-1024"),
            action("get_business_trip", trip_id="TRIP-2409-017"),
            finish(),
        ]
    )

    assert (await zero_service.run("policy only")).status == "answered"
    assert zero_tools.calls == []
    assert (await all_service.run("explain claim")).status == "answered"
    assert len(all_tools.calls) == 3


@pytest.mark.asyncio
async def test_duplicate_action_is_refused_before_second_execution() -> None:
    duplicate = action("get_expense_claim", claim_id="CLAIM-1024")
    service, _planner, tools, _validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            duplicate,
            duplicate,
        ]
    )

    result = await service.run("explain claim")

    assert result.status == "refused"
    assert result.refusal_reason == "duplicate_action"
    assert len(tools.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_action", "category"),
    [
        (action("search_knowledge", query="wrong field"), "generation_protocol"),
        (action("delete_claim", claim_id="CLAIM-1024"), "generation_protocol"),
        (action("get_expense_claim", claim_id="bad"), "tool_validation"),
    ],
)
async def test_malformed_or_unknown_actions_fail_closed(
    bad_action: AgentAction,
    category: str,
) -> None:
    service, _planner, _tools, _validator = make_agent([bad_action])

    result = await service.run("unsafe or malformed")

    assert result.status == "failed"
    assert result.error_category == category


@pytest.mark.asyncio
async def test_invalid_business_id_is_rejected_before_trace_records_value() -> None:
    unsafe_value = "C:/private/claim.json"
    service, _planner, tools, _validator = make_agent(
        [action("get_expense_claim", claim_id=unsafe_value)]
    )

    result = await service.run("reject malformed claim ID")

    assert result.status == "failed"
    assert result.error_category == "tool_validation"
    assert unsafe_value not in result.trace.to_json()
    assert tools.calls == []


@pytest.mark.asyncio
async def test_not_found_observation_is_visible_and_planning_can_continue() -> None:
    tools = ScriptedTools(not_found={"CLAIM-9999"})
    service, _planner, _tools, _validator = make_agent(
        [
            action("search_knowledge", question="policy"),
            action("get_expense_claim", claim_id="CLAIM-9999"),
            finish(),
        ],
        tools=tools,
    )

    result = await service.run("find unknown claim")

    assert result.status == "answered"
    assert result.observations[-1].status == "not_found"
    assert result.observations[-1].observation_id.endswith("CLAIM-9999")


@pytest.mark.asyncio
async def test_tool_crash_returns_typed_failure() -> None:
    service, _planner, _tools, _validator = make_agent(
        [action("get_expense_claim", claim_id="CLAIM-1024")],
        tools=ScriptedTools(error=ToolExecutionError("secret internal detail")),
    )

    result = await service.run("explain claim")

    assert result.status == "failed"
    assert result.error_category == "tool_execution"
    assert result.tool_trace[-1].status == "failed"


@pytest.mark.asyncio
async def test_rag_refusal_stays_visible_and_agent_can_replan() -> None:
    knowledge = ScriptedKnowledge(
        results=[
            KnowledgeSearchResult.refused("insufficient_evidence"),
            KnowledgeSearchResult.found((reranked(POLICY_ID, POLICY_TEXT),)),
        ]
    )
    service, _planner, tools, _validator = make_agent(
        [
            action("search_knowledge", question="unknown policy"),
            action("get_expense_claim", claim_id="CLAIM-1024"),
            action("search_knowledge", question="P4 hotel cap"),
            finish(),
        ],
        knowledge=knowledge,
    )

    result = await service.run("unsupported conclusion")

    assert result.status == "answered"
    assert [name for name, _arguments in tools.calls] == ["get_expense_claim"]
    assert result.observations[0].status == "not_found"
    assert result.tool_trace[0].status == "degraded"
    assert result.tool_trace[0].error_category == "evidence_insufficient"
    assert result.observations[-1].observation_id == POLICY_ID


@pytest.mark.asyncio
async def test_finish_without_knowledge_evidence_is_refused() -> None:
    service, _planner, _tools, validator = make_agent(
        [
            action("get_expense_claim", claim_id="CLAIM-1024"),
            finish(grounded_draft(support_ids=("obs:get_expense_claim:CLAIM-1024",))),
        ]
    )

    result = await service.run("invent policy from claim")

    assert result.status == "refused"
    assert result.refusal_reason == "knowledge_evidence_required"
    assert validator.calls == []


@pytest.mark.asyncio
async def test_six_action_limit_never_requests_a_seventh_action() -> None:
    actions = [
        action("search_knowledge", question=f"policy {index}")
        for index in range(1, 7)
    ]
    service, planner, _tools, _validator = make_agent(actions, max_actions=6)

    result = await service.run("keep searching")

    assert result.status == "refused"
    assert result.refusal_reason == "iteration_limit"
    assert len(planner.observation_counts) == 6
    assert len(result.tool_trace) == 6


@pytest.mark.asyncio
async def test_agent_keeps_only_policy_matching_grade_and_city_tier() -> None:
    matching_id = "policy.md::matching"
    knowledge = ScriptedKnowledge(
        KnowledgeSearchResult.found(
            (
                reranked(
                    "policy.md::distractor",
                    "P4 employees traveling to non-A cities have a CNY 480 cap.",
                    score=0.95,
                    rank=1,
                ),
                reranked(
                    matching_id,
                    "P4 employees traveling to A cities have a CNY 600 cap.",
                    score=0.90,
                    rank=2,
                ),
            )
        )
    )
    draft = grounded_draft(support_ids=(matching_id,))
    service, _planner, _tools, _validator = make_agent(
        [
            action("get_employee_profile", employee_id="EMP-003"),
            action("get_business_trip", trip_id="TRIP-2409-017"),
            action("search_knowledge", question="P4 A-city hotel cap"),
            finish(draft),
        ],
        knowledge=knowledge,
    )

    result = await service.run("find the applicable policy")

    assert result.status == "answered"
    knowledge_ids = [
        item.observation_id
        for item in result.observations
        if item.source == "search_knowledge"
    ]
    assert knowledge_ids == [matching_id]
