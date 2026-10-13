from dataclasses import FrozenInstanceError, fields

import pytest


def _retrieved_chunk(**overrides: object) -> object:
    from enterprise_rag.models import RetrievedChunk

    values = {
        "text": "Hotel reimbursement is capped at CNY 600.",
        "filename": "travel-policy.md",
        "chunk_id": "travel-policy.md::0001",
        "dense_score": 0.81,
        "dense_rank": 2,
        "sparse_score": 7.25,
        "sparse_rank": 1,
        "retrieval_score": 0.93,
        "retrieval_rank": 1,
    }
    values.update(overrides)
    return RetrievedChunk(**values)  # ty: ignore[invalid-argument-type]


def _source(chunk_id: str = "travel-policy.md::0001") -> object:
    from enterprise_rag.models import Source

    return Source(filename="travel-policy.md", chunk_id=chunk_id, score=0.97)


def _claim(support_id: str = "travel-policy.md::0001") -> object:
    from enterprise_rag.models import EvidenceClaim

    return EvidenceClaim(
        text="The hotel cap is CNY 600.",
        support_ids=(support_id,),
        supporting_quotes=("Hotel reimbursement is capped at CNY 600.",),
    )


def _observation(observation_id: str = "obs-001") -> object:
    from enterprise_rag.models import Observation

    return Observation(
        observation_id=observation_id,
        source="policy_search",
        status="found",
        facts={"hotel_limit_cny": 600},
        support_text="Hotel reimbursement is capped at CNY 600.",
    )


def test_core_contract_field_order_is_stable() -> None:
    from enterprise_rag.models import (
        AnswerDraft,
        EvidenceClaim,
        RagResult,
        RerankedChunk,
        RetrievedChunk,
    )

    assert [field.name for field in fields(RetrievedChunk)] == [
        "text",
        "filename",
        "chunk_id",
        "dense_score",
        "dense_rank",
        "sparse_score",
        "sparse_rank",
        "retrieval_score",
        "retrieval_rank",
    ]
    assert [field.name for field in fields(RerankedChunk)] == [
        "candidate",
        "rerank_score",
        "rerank_rank",
    ]
    assert [field.name for field in fields(EvidenceClaim)] == [
        "text",
        "support_ids",
        "supporting_quotes",
    ]
    assert [field.name for field in fields(AnswerDraft)] == ["answer", "claims"]
    assert [field.name for field in fields(RagResult)] == [
        "status",
        "answer",
        "sources",
        "refusal_reason",
        "error_category",
        "trace",
    ]


def test_retrieved_chunk_preserves_hybrid_lane_and_fused_scores() -> None:
    chunk = _retrieved_chunk()

    assert chunk.dense_score == 0.81
    assert chunk.dense_rank == 2
    assert chunk.sparse_score == 7.25
    assert chunk.sparse_rank == 1
    assert chunk.retrieval_score == 0.93
    assert chunk.retrieval_rank == 1


@pytest.mark.parametrize(
    "score_name",
    ["dense_score", "sparse_score", "retrieval_score"],
)
@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf")])
def test_retrieved_chunk_rejects_every_non_finite_score(
    score_name: str,
    invalid_score: float,
) -> None:
    with pytest.raises(ValueError, match=rf"{score_name} must be finite"):
        _retrieved_chunk(**{score_name: invalid_score})


@pytest.mark.parametrize(
    "rank_name",
    ["dense_rank", "sparse_rank", "retrieval_rank"],
)
@pytest.mark.parametrize("invalid_rank", [0, -1])
def test_retrieved_chunk_rejects_every_non_positive_rank(
    rank_name: str,
    invalid_rank: int,
) -> None:
    with pytest.raises(ValueError, match=rf"{rank_name} must be positive"):
        _retrieved_chunk(**{rank_name: invalid_rank})


@pytest.mark.parametrize(
    "rank_name",
    ["dense_rank", "sparse_rank", "retrieval_rank"],
)
@pytest.mark.parametrize("invalid_rank", [1.5, True])
def test_retrieved_chunk_rejects_non_integer_ranks(
    rank_name: str,
    invalid_rank: object,
) -> None:
    with pytest.raises(TypeError, match=rf"{rank_name} must be an integer"):
        _retrieved_chunk(**{rank_name: invalid_rank})


@pytest.mark.parametrize(
    ("overrides", "lane"),
    [
        ({"dense_score": None}, "dense"),
        ({"dense_rank": None}, "dense"),
        ({"sparse_score": None}, "sparse"),
        ({"sparse_rank": None}, "sparse"),
    ],
)
def test_retrieved_chunk_requires_optional_lane_score_rank_pairs(
    overrides: dict[str, object],
    lane: str,
) -> None:
    with pytest.raises(ValueError, match=rf"{lane} score and rank must be paired"):
        _retrieved_chunk(**overrides)


def test_retrieved_chunk_allows_an_absent_optional_lane() -> None:
    chunk = _retrieved_chunk(dense_score=None, dense_rank=None)

    assert chunk.dense_score is None
    assert chunk.dense_rank is None


def test_reranked_chunk_composes_candidate_and_validates_rank() -> None:
    from enterprise_rag.models import RerankedChunk

    candidate = _retrieved_chunk()
    reranked = RerankedChunk(candidate=candidate, rerank_score=0.98, rerank_rank=1)

    assert reranked.candidate is candidate
    assert reranked.rerank_score == 0.98
    assert reranked.rerank_rank == 1

    with pytest.raises(ValueError, match="rerank_rank must be positive"):
        RerankedChunk(candidate=candidate, rerank_score=0.98, rerank_rank=0)


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), float("-inf")])
def test_reranked_chunk_rejects_non_finite_score(invalid_score: float) -> None:
    from enterprise_rag.models import RerankedChunk

    with pytest.raises(ValueError, match="rerank_score must be finite"):
        RerankedChunk(
            candidate=_retrieved_chunk(),
            rerank_score=invalid_score,
            rerank_rank=1,
        )


@pytest.mark.parametrize("invalid_rank", [1.5, True])
def test_reranked_chunk_rejects_non_integer_rank(invalid_rank: object) -> None:
    from enterprise_rag.models import RerankedChunk

    with pytest.raises(TypeError, match="rerank_rank must be an integer"):
        RerankedChunk(
            candidate=_retrieved_chunk(),
            rerank_score=0.98,
            rerank_rank=invalid_rank,  # ty: ignore[invalid-argument-type]
        )


def test_reranked_chunk_rejects_non_chunk_candidate() -> None:
    from enterprise_rag.models import RerankedChunk

    with pytest.raises(TypeError, match="candidate must be a RetrievedChunk"):
        RerankedChunk(
            candidate=object(),  # ty: ignore[invalid-argument-type]
            rerank_score=0.98,
            rerank_rank=1,
        )


def test_evidence_claim_requires_immutable_nonempty_support() -> None:
    from enterprise_rag.models import EvidenceClaim

    with pytest.raises(TypeError, match="support_ids must be a tuple"):
        EvidenceClaim(
            text="A claim",
            support_ids=["chunk-1"],  # ty: ignore[invalid-argument-type]
            supporting_quotes=("A quote",),
        )
    with pytest.raises(TypeError, match="supporting_quotes must be a tuple"):
        EvidenceClaim(
            text="A claim",
            support_ids=("chunk-1",),
            supporting_quotes=["A quote"],  # ty: ignore[invalid-argument-type]
        )
    with pytest.raises(ValueError, match="claim text must not be empty"):
        EvidenceClaim(text=" ", support_ids=("chunk-1",), supporting_quotes=("q",))
    with pytest.raises(ValueError, match="support_ids must not be empty"):
        EvidenceClaim(text="A claim", support_ids=(), supporting_quotes=("q",))
    with pytest.raises(ValueError, match="supporting_quotes must not be empty"):
        EvidenceClaim(text="A claim", support_ids=("chunk-1",), supporting_quotes=())


def test_evidence_claim_rejects_duplicate_or_blank_evidence() -> None:
    from enterprise_rag.models import EvidenceClaim

    with pytest.raises(ValueError, match="support_ids must be unique"):
        EvidenceClaim(
            text="A claim",
            support_ids=("chunk-1", "chunk-1"),
            supporting_quotes=("quote one", "quote two"),
        )
    with pytest.raises(ValueError, match="support_ids must not contain empty values"):
        EvidenceClaim(
            text="A claim",
            support_ids=(" ",),
            supporting_quotes=("a quote",),
        )
    with pytest.raises(
        ValueError,
        match="supporting_quotes must not contain empty values",
    ):
        EvidenceClaim(
            text="A claim",
            support_ids=("chunk-1",),
            supporting_quotes=(" ",),
        )


def test_answer_draft_requires_nonempty_answer_and_claim_tuple() -> None:
    from enterprise_rag.models import AnswerDraft

    claim = _claim()
    draft = AnswerDraft(answer="The hotel cap is CNY 600.", claims=(claim,))
    assert draft.claims == (claim,)

    with pytest.raises(TypeError, match="claims must be a tuple"):
        AnswerDraft(
            answer="The hotel cap is CNY 600.",
            claims=[claim],  # ty: ignore[invalid-argument-type]
        )
    with pytest.raises(ValueError, match="answer must not be empty"):
        AnswerDraft(answer=" ", claims=(claim,))
    with pytest.raises(ValueError, match="claims must not be empty"):
        AnswerDraft(answer="An answer", claims=())


def test_answer_draft_rejects_non_claim_members() -> None:
    from enterprise_rag.models import AnswerDraft

    with pytest.raises(TypeError, match="claims must contain only EvidenceClaim"):
        AnswerDraft(
            answer="An answer",
            claims=(object(),),  # ty: ignore[invalid-argument-type]
        )


def test_rag_result_answered_factory_builds_only_answered_state() -> None:
    from enterprise_rag.models import RagResult

    source = _source()
    result = RagResult.answered("The hotel cap is CNY 600.", (source,))

    assert result.status == "answered"
    assert result.answer == "The hotel cap is CNY 600."
    assert result.sources == (source,)
    assert result.refusal_reason is None
    assert result.error_category is None
    assert result.trace.run_id == "untraced"
    assert result.trace.events == ()


def test_rag_result_refused_and_failed_factories_are_mutually_exclusive() -> None:
    from enterprise_rag.models import RagResult

    refused = RagResult.refused("insufficient_evidence")
    failed = RagResult.failed("generation_protocol")

    assert refused.status == "refused"
    assert refused.answer is None
    assert refused.sources == ()
    assert refused.refusal_reason == "insufficient_evidence"
    assert refused.error_category is None
    assert failed.status == "failed"
    assert failed.answer is None
    assert failed.sources == ()
    assert failed.refusal_reason is None
    assert failed.error_category == "generation_protocol"


def test_rag_result_rejects_invalid_direct_states() -> None:
    from enterprise_rag.execution_trace import RunTrace
    from enterprise_rag.models import RagResult

    trace = RunTrace.empty()
    source = _source()

    with pytest.raises(ValueError, match="answered result requires a nonempty answer"):
        RagResult("answered", " ", (source,), None, None, trace)
    with pytest.raises(ValueError, match="answered result requires sources"):
        RagResult("answered", "answer", (), None, None, trace)
    with pytest.raises(ValueError, match="answered result cannot have refusal_reason"):
        RagResult("answered", "answer", (source,), "reason", None, trace)
    with pytest.raises(ValueError, match="answered result cannot have error_category"):
        RagResult("answered", "answer", (source,), None, "error", trace)
    with pytest.raises(ValueError, match="refused result requires refusal_reason"):
        RagResult("refused", None, (), None, None, trace)
    with pytest.raises(ValueError, match="refused result cannot expose an answer"):
        RagResult("refused", "answer", (), "reason", None, trace)
    with pytest.raises(ValueError, match="refused result cannot expose sources"):
        RagResult("refused", None, (source,), "reason", None, trace)
    with pytest.raises(ValueError, match="refused result cannot have error_category"):
        RagResult("refused", None, (), "reason", "error", trace)
    with pytest.raises(ValueError, match="failed result requires error_category"):
        RagResult("failed", None, (), None, None, trace)
    with pytest.raises(ValueError, match="failed result cannot expose an answer"):
        RagResult("failed", "answer", (), None, "error", trace)
    with pytest.raises(ValueError, match="failed result cannot expose sources"):
        RagResult("failed", None, (source,), None, "error", trace)
    with pytest.raises(ValueError, match="failed result cannot have refusal_reason"):
        RagResult("failed", None, (), "reason", "error", trace)


def test_rag_result_rejects_mutable_or_duplicate_sources() -> None:
    from enterprise_rag.models import RagResult

    source = _source()
    with pytest.raises(TypeError, match="sources must be a tuple"):
        RagResult.answered(
            "An answer",
            [source],  # ty: ignore[invalid-argument-type]
        )
    with pytest.raises(ValueError, match="source chunk_ids must be unique"):
        RagResult.answered("An answer", (source, source))
    with pytest.raises(TypeError, match="sources must contain only Source"):
        RagResult.answered(
            "An answer",
            (object(),),  # ty: ignore[invalid-argument-type]
        )


def test_agent_action_owns_sanitized_arguments_and_fingerprint_is_stable() -> None:
    from enterprise_rag.models import AgentAction

    arguments = {
        "query": "hotel policy",
        "credentials": {"TOKEN": "top-secret"},
        "filters": ["travel", "hotel"],
    }
    action = AgentAction(
        name="policy_search",
        arguments=arguments,
        finish_draft=None,
    )
    equivalent = AgentAction(
        name="policy_search",
        arguments={
            "filters": ["travel", "hotel"],
            "credentials": {"TOKEN": "different-secret"},
            "query": "hotel policy",
        },
        finish_draft=None,
    )

    arguments["query"] = "mutated"
    arguments["filters"].append("changed")  # ty: ignore[unresolved-attribute]

    assert action.arguments["query"] == "hotel policy"
    assert action.arguments["filters"] == ("travel", "hotel")
    assert action.arguments["credentials"]["TOKEN"] == "<redacted>"  # ty: ignore[index-not-subscriptable]
    assert action.fingerprint() == equivalent.fingerprint()
    with pytest.raises(TypeError):
        action.arguments["query"] = "cannot mutate"  # ty: ignore[index-assignment]


def test_agent_action_fingerprint_covers_finish_draft() -> None:
    from enterprise_rag.models import AgentAction, AnswerDraft

    draft = AnswerDraft(answer="Grounded answer", claims=(_claim(),))
    tool_action = AgentAction("finish", {}, None)
    finish_action = AgentAction("finish", {}, draft)

    assert tool_action.fingerprint() != finish_action.fingerprint()


def test_agent_action_rejects_non_draft_finish_payload() -> None:
    from enterprise_rag.models import AgentAction

    with pytest.raises(TypeError, match="finish_draft must be an AnswerDraft"):
        AgentAction(
            "finish",
            {},
            object(),  # ty: ignore[invalid-argument-type]
        )


def test_agent_action_draft_is_read_only_finish_draft_alias() -> None:
    from enterprise_rag.models import AgentAction, AnswerDraft

    draft = AnswerDraft("Grounded answer", (_claim(),))
    action = AgentAction("finish", {}, draft)

    assert action.draft is action.finish_draft
    assert isinstance(type(action).draft, property)
    assert type(action).draft.fset is None
    with pytest.raises(FrozenInstanceError):
        setattr(draft, "answer", "mutated")  # noqa: B010


def test_observation_enforces_stable_grounding_shape_and_snapshot() -> None:
    from enterprise_rag.models import Observation

    facts = {"amount": 600, "metadata": {"currency": "CNY"}}
    observation = Observation(
        observation_id="obs-001",
        source="policy_search",
        status="found",
        facts=facts,
        support_text="Hotel cap: CNY 600.",
    )
    facts["amount"] = 999

    assert observation.facts["amount"] == 600
    assert observation.facts["metadata"]["currency"] == "CNY"  # ty: ignore[index-not-subscriptable]
    assert not hasattr(observation, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(observation, "status", "not_found")  # noqa: B010

    missing = Observation("obs-002", "policy_search", "not_found", {}, None)
    assert missing.support_text is None


def test_observation_rejects_incoherent_found_and_not_found_states() -> None:
    from enterprise_rag.models import Observation

    with pytest.raises(ValueError, match="found observation requires facts"):
        Observation("obs", "tool", "found", {}, "support")
    with pytest.raises(ValueError, match="found observation requires support_text"):
        Observation("obs", "tool", "found", {"fact": True}, " ")
    with pytest.raises(ValueError, match="not_found observation cannot contain facts"):
        Observation("obs", "tool", "not_found", {"fact": True}, None)
    with pytest.raises(
        ValueError,
        match="not_found observation cannot contain support_text",
    ):
        Observation("obs", "tool", "not_found", {}, "unexpected")


def test_observation_payload_is_read_only_facts_alias() -> None:
    observation = _observation()

    assert observation.payload is observation.facts
    assert isinstance(type(observation).payload, property)
    assert type(observation).payload.fset is None
    with pytest.raises(TypeError):
        observation.payload["new"] = "mutated"  # ty: ignore[index-assignment]


def test_tool_call_exposes_name_and_sanitized_immutable_arguments() -> None:
    from enterprise_rag.models import ToolCall

    arguments = {"timezone": "Asia/Shanghai", "api_key": "secret"}
    call = ToolCall(
        name="current_time",
        arguments=arguments,
        status="succeeded",
        error_category=None,
    )
    arguments["timezone"] = "UTC"

    assert call.name == "current_time"
    assert call.arguments["timezone"] == "Asia/Shanghai"
    assert call.arguments["api_key"] == "<redacted>"
    with pytest.raises(ValueError, match="failed tool call requires error_category"):
        ToolCall("current_time", {}, "failed", None)


def test_agent_result_mirrors_terminal_states_and_keeps_diagnostics() -> None:
    from enterprise_rag.models import AgentResult, ToolCall

    claim = _claim()
    source = _source()
    observation = _observation()
    call = ToolCall("policy_search", {"query": "hotel"}, "succeeded", None)

    answered = AgentResult.answered(
        "The hotel cap is CNY 600.",
        claims=(claim,),
        sources=(source,),
        observations=(observation,),
        tool_trace=(call,),
    )
    refused = AgentResult.refused(
        "insufficient_evidence",
        observations=(observation,),
        tool_trace=(call,),
    )
    failed = AgentResult.failed(
        "tool_execution",
        observations=(observation,),
        tool_trace=(call,),
    )

    assert answered.status == "answered"
    assert answered.claims == (claim,)
    assert answered.sources == (source,)
    assert answered.observations == (observation,)
    assert answered.tool_trace[0].name == "policy_search"
    assert refused.status == "refused"
    assert refused.answer is None
    assert refused.claims == ()
    assert refused.sources == ()
    assert refused.refusal_reason == "insufficient_evidence"
    assert failed.status == "failed"
    assert failed.answer is None
    assert failed.claims == ()
    assert failed.sources == ()
    assert failed.error_category == "tool_execution"


@pytest.mark.parametrize(
    "field_name",
    ["claims", "tool_trace", "sources", "observations"],
)
def test_agent_result_rejects_mutable_collections(field_name: str) -> None:
    from enterprise_rag.models import AgentResult

    values: dict[str, object] = {
        "status": "answered",
        "answer": "An answer",
        "claims": (),
        "tool_trace": (),
        "sources": (),
        "observations": (),
        "refusal_reason": None,
        "error_category": None,
    }
    values[field_name] = []

    with pytest.raises(TypeError, match=rf"{field_name} must be a tuple"):
        AgentResult(**values)  # ty: ignore[invalid-argument-type]


def test_agent_result_rejects_evidence_that_survives_non_answer_states() -> None:
    from enterprise_rag.models import AgentResult

    with pytest.raises(ValueError, match="refused result cannot expose claims"):
        AgentResult(
            "refused",
            None,
            (_claim(),),
            (),
            (),
            (),
            "insufficient_evidence",
            None,
        )
    with pytest.raises(ValueError, match="failed result cannot expose sources"):
        AgentResult(
            "failed",
            None,
            (),
            (),
            (_source(),),
            (),
            None,
            "tool_execution",
        )


def test_agent_result_answer_requires_grounded_claims_and_sources() -> None:
    from enterprise_rag.execution_trace import RunTrace
    from enterprise_rag.models import AgentResult

    with pytest.raises(ValueError, match="answered result requires claims"):
        AgentResult(
            "answered",
            "answer",
            (),
            (),
            (_source(),),
            (),
            None,
            None,
            RunTrace.empty(),
        )
    with pytest.raises(ValueError, match="answered result requires sources"):
        AgentResult.answered("answer", claims=(_claim(),))


@pytest.mark.parametrize(
    ("field_name", "expected_type"),
    [
        ("claims", "EvidenceClaim"),
        ("tool_trace", "ToolCall"),
        ("sources", "Source"),
        ("observations", "Observation"),
    ],
)
def test_agent_result_rejects_non_domain_members(
    field_name: str,
    expected_type: str,
) -> None:
    from enterprise_rag.models import AgentResult

    values: dict[str, object] = {
        "status": "answered",
        "answer": "answer",
        "claims": (_claim(),),
        "tool_trace": (),
        "sources": (_source(),),
        "observations": (),
        "refusal_reason": None,
        "error_category": None,
    }
    values[field_name] = (object(),)

    with pytest.raises(
        TypeError,
        match=rf"{field_name} must contain only {expected_type}",
    ):
        AgentResult(**values)  # ty: ignore[invalid-argument-type]


def test_agent_result_rejects_duplicate_evidence_ids() -> None:
    from enterprise_rag.models import AgentResult

    with pytest.raises(ValueError, match="source chunk_ids must be unique"):
        AgentResult.answered(
            "answer",
            claims=(_claim(),),
            sources=(_source(), _source()),
        )
    with pytest.raises(ValueError, match="observation_ids must be unique"):
        AgentResult.answered(
            "answer",
            claims=(_claim(),),
            sources=(_source(),),
            observations=(_observation(), _observation()),
        )


def test_structured_generation_request_owns_nested_data() -> None:
    from enterprise_rag.models import StructuredGenerationRequest

    message = {"role": "user", "content": ["What is the hotel cap?"]}
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    request = StructuredGenerationRequest(
        messages=(message,),
        schema=schema,
        max_output_tokens=256,
    )
    message["role"] = "system"
    schema["required"].append("unexpected")  # ty: ignore[unresolved-attribute]

    assert request.messages[0]["role"] == "user"
    assert request.schema["required"] == ("answer",)
    with pytest.raises(TypeError):
        request.schema["type"] = "array"  # ty: ignore[index-assignment]


def test_structured_generation_request_requires_tuple_and_positive_budget() -> None:
    from enterprise_rag.models import StructuredGenerationRequest

    with pytest.raises(TypeError, match="messages must be a tuple"):
        StructuredGenerationRequest(
            messages=[{"role": "user", "content": "hi"}],  # ty: ignore[invalid-argument-type]
            schema={"type": "object"},
            max_output_tokens=1,
        )
    with pytest.raises(ValueError, match="messages must not be empty"):
        StructuredGenerationRequest((), {"type": "object"}, 1)
    with pytest.raises(ValueError, match="max_output_tokens must be positive"):
        StructuredGenerationRequest(
            ({"role": "user", "content": "hi"},),
            {"type": "object"},
            0,
        )


@pytest.mark.parametrize("invalid_limit", [1.5, True])
def test_structured_generation_request_rejects_non_integer_budget(
    invalid_limit: object,
) -> None:
    from enterprise_rag.models import StructuredGenerationRequest

    with pytest.raises(TypeError, match="max_output_tokens must be an integer"):
        StructuredGenerationRequest(
            ({"role": "user", "content": "hi"},),
            {"type": "object"},
            invalid_limit,  # ty: ignore[invalid-argument-type]
        )


def test_validation_issue_supports_claim_and_global_issues() -> None:
    from enterprise_rag.models import ValidationIssue

    claim_issue = ValidationIssue("missing_support", 0, "Claim has no evidence.")
    global_issue = ValidationIssue("invalid_shape", None, "Output was not an object.")

    assert claim_issue.claim_index == 0
    assert global_issue.claim_index is None
    with pytest.raises(ValueError, match="claim_index must be non-negative"):
        ValidationIssue("missing_support", -1, "bad index")


@pytest.mark.parametrize("invalid_index", [0.5, True])
def test_validation_issue_rejects_non_integer_claim_index(
    invalid_index: object,
) -> None:
    from enterprise_rag.models import ValidationIssue

    with pytest.raises(TypeError, match="claim_index must be an integer"):
        ValidationIssue(
            "missing_support",
            invalid_index,  # ty: ignore[invalid-argument-type]
            "bad index",
        )


def test_public_string_contract_reports_a_clear_type_error() -> None:
    from enterprise_rag.models import ValidationIssue

    with pytest.raises(TypeError, match="code must be a string"):
        ValidationIssue(
            123,  # ty: ignore[invalid-argument-type]
            None,
            "invalid code",
        )


@pytest.mark.parametrize(
    ("case", "field_name"),
    [
        ("retrieved_text", "text"),
        ("retrieved_filename", "filename"),
        ("retrieved_chunk_id", "chunk_id"),
        ("source_filename", "filename"),
        ("source_chunk_id", "chunk_id"),
        ("claim_text", "claim text"),
        ("claim_support_id", "support_id"),
        ("claim_quote", "supporting_quote"),
        ("draft_answer", "answer"),
        ("rag_status", "status"),
        ("rag_answer", "answer"),
        ("rag_refusal_reason", "refusal_reason"),
        ("rag_error_category", "error_category"),
        ("observation_id", "observation_id"),
        ("observation_source", "source"),
        ("observation_status", "status"),
        ("observation_support", "support_text"),
        ("action_name", "action name"),
        ("tool_name", "tool name"),
        ("tool_status", "status"),
        ("tool_error_category", "error_category"),
        ("agent_status", "status"),
        ("agent_answer", "answer"),
        ("agent_refusal_reason", "refusal_reason"),
        ("agent_error_category", "error_category"),
        ("issue_detail", "detail"),
    ],
)
def test_every_public_model_string_path_rejects_non_strings(
    case: str,
    field_name: str,
) -> None:
    from enterprise_rag.execution_trace import RunTrace
    from enterprise_rag.models import (
        AgentAction,
        AgentResult,
        AnswerDraft,
        EvidenceClaim,
        Observation,
        RagResult,
        RetrievedChunk,
        Source,
        ToolCall,
        ValidationIssue,
    )

    invalid: object = 123
    source = _source()
    claim = _claim()
    constructors = {
        "retrieved_text": lambda: RetrievedChunk(
            invalid, "policy.md", "chunk-1", 0.8, 1, None, None, 0.8, 1
        ),
        "retrieved_filename": lambda: RetrievedChunk(
            "text", invalid, "chunk-1", 0.8, 1, None, None, 0.8, 1
        ),
        "retrieved_chunk_id": lambda: RetrievedChunk(
            "text", "policy.md", invalid, 0.8, 1, None, None, 0.8, 1
        ),
        "source_filename": lambda: Source(invalid, "chunk-1", 0.9),
        "source_chunk_id": lambda: Source("policy.md", invalid, 0.9),
        "claim_text": lambda: EvidenceClaim(invalid, ("chunk-1",), ("quote",)),
        "claim_support_id": lambda: EvidenceClaim(
            "claim",
            (invalid,),
            ("quote",),
        ),
        "claim_quote": lambda: EvidenceClaim(
            "claim",
            ("chunk-1",),
            (invalid,),
        ),
        "draft_answer": lambda: AnswerDraft(invalid, (claim,)),
        "rag_status": lambda: RagResult(
            invalid,
            None,
            (),
            None,
            None,
            RunTrace.empty(),
        ),
        "rag_answer": lambda: RagResult.answered(invalid, (source,)),
        "rag_refusal_reason": lambda: RagResult.refused(invalid),
        "rag_error_category": lambda: RagResult.failed(invalid),
        "observation_id": lambda: Observation(
            invalid, "tool", "found", {"fact": True}, "support"
        ),
        "observation_source": lambda: Observation(
            "obs", invalid, "found", {"fact": True}, "support"
        ),
        "observation_status": lambda: Observation(
            "obs", "tool", invalid, {}, None
        ),
        "observation_support": lambda: Observation(
            "obs", "tool", "found", {"fact": True}, invalid
        ),
        "action_name": lambda: AgentAction(invalid, {}, None),
        "tool_name": lambda: ToolCall(invalid, {}, "succeeded", None),
        "tool_status": lambda: ToolCall("tool", {}, invalid, None),
        "tool_error_category": lambda: ToolCall(
            "tool",
            {},
            "failed",
            invalid,
        ),
        "agent_status": lambda: AgentResult(
            invalid,
            None,
            (),
            (),
            (),
            (),
            None,
            None,
        ),
        "agent_answer": lambda: AgentResult.answered(invalid),
        "agent_refusal_reason": lambda: AgentResult.refused(invalid),
        "agent_error_category": lambda: AgentResult.failed(invalid),
        "issue_detail": lambda: ValidationIssue("code", None, invalid),
    }

    with pytest.raises(TypeError, match=rf"{field_name} must be a string"):
        constructors[case]()


def test_representative_contracts_are_frozen_and_slotted() -> None:
    from enterprise_rag.models import RagResult, StructuredGenerationRequest

    chunk = _retrieved_chunk()
    result = RagResult.answered("answer", (_source(),))
    request = StructuredGenerationRequest(
        ({"role": "user", "content": "question"},),
        {"type": "object"},
        16,
    )

    for value in (chunk, result, request):
        assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(result, "status", "failed")  # noqa: B010
