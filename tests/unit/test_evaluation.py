from __future__ import annotations

from pathlib import Path

from enterprise_rag.evaluation import (
    AgentEvalCase,
    BackendDescriptor,
    KnowledgeEvalCase,
    LayeredEvaluator,
    ScriptedAgentScenarioRunner,
    evidence_label_for,
    load_agent_cases,
    load_knowledge_cases,
    score_agent,
    score_rag,
    score_reranker,
    score_retriever,
    summarize_latency,
)
from enterprise_rag.execution_trace import RunTrace
from enterprise_rag.models import (
    AgentResult,
    EvidenceClaim,
    RagResult,
    Source,
    ToolCall,
)
from tests.fakes import ScriptedReranker, ScriptedRetriever, reranked, retrieved


def knowledge_case(
    case_id: str,
    *,
    answerable: bool = True,
    expected: tuple[str, ...] = ("c1",),
) -> KnowledgeEvalCase:
    return KnowledgeEvalCase(
        case_id=case_id,
        question=f"question {case_id}",
        answerable=answerable,
        expected_chunk_ids=expected,
        required_facts=("CNY 600",) if answerable else (),
        forbidden_facts=("CNY 850",),
        expected_refusal_reason=None if answerable else "insufficient_evidence",
    )


def test_recall_at_20_counts_answerable_cases_only() -> None:
    cases = (
        knowledge_case("K01", expected=("c1",)),
        knowledge_case("K19", answerable=False, expected=()),
    )

    metric = score_retriever(cases, {"K01": ("c1",), "K19": ()}, k=20)

    assert metric.name == "recall_at_20"
    assert metric.numerator == 1
    assert metric.denominator == 1
    assert metric.value == 1.0


def test_reranker_hit_at_5_is_conditional_on_retrieval() -> None:
    cases = (
        knowledge_case("K01", expected=("c1",)),
        knowledge_case("K02", expected=("c2",)),
    )
    retrieved = {"K01": ("c1", "x"), "K02": ("x",)}
    reranked = {"K01": ("x", "c1"), "K02": ("x",)}

    metric = score_reranker(cases, retrieved, reranked, k=5)

    assert metric.name == "conditional_hit_at_5"
    assert (metric.numerator, metric.denominator) == (1, 1)


def test_rag_scores_grounding_rejection_citations_and_categories() -> None:
    cases = (
        knowledge_case("K01"),
        knowledge_case("K02", expected=("c2",)),
        knowledge_case("K19", answerable=False, expected=()),
    )
    results = {
        "K01": RagResult.answered(
            "P4 上限为 CNY 600。",
            (Source("policy.md", "c1", 0.9),),
        ),
        "K02": RagResult.answered(
            "P4 上限为 CNY 600。",
            (Source("policy.md", "c2", 0.9),),
        ),
        "K19": RagResult.refused("insufficient_evidence"),
    }

    metrics = score_rag(cases, results)

    assert metrics["grounded_answer_correctness"].value == 1.0
    assert metrics["no_answer_rejection"].value == 1.0
    assert metrics["citation_validity"].value == 1.0
    assert metrics["failure_category_accuracy"].value == 1.0


def test_rag_citation_validity_rejects_chunk_allowed_only_for_another_case() -> None:
    cases = (
        knowledge_case("K01", expected=("c1",)),
        knowledge_case("K02", expected=("c2",)),
    )
    results = {
        "K01": RagResult.answered(
            "P4 上限为 CNY 600。",
            (Source("policy.md", "c2", 0.9),),
        ),
        "K02": RagResult.answered(
            "P4 上限为 CNY 600。",
            (Source("policy.md", "c2", 0.9),),
        ),
    }

    metric = score_rag(cases, results)["citation_validity"]

    assert (metric.numerator, metric.denominator) == (1, 2)


def test_agent_scores_exact_ordered_tools_and_forbidden_actions() -> None:
    case = AgentEvalCase(
        case_id="A05",
        task="解释 CLAIM-1024",
        expected_business_tools=("get_expense_claim", "get_business_trip"),
        expected_arguments=(
            {"claim_id": "CLAIM-1024"},
            {"trip_id": "TRIP-2409-017"},
        ),
        expected_status="answered",
        required_support_ids=("c1", "obs:get_expense_claim:CLAIM-1024"),
        expected_refusal_reason=None,
        expected_error_category=None,
    )
    result = AgentResult.answered(
        "有制度和报销记录支持。",
        claims=(
            EvidenceClaim(
                "结论",
                ("c1", "obs:get_expense_claim:CLAIM-1024"),
                ("依据",),
            ),
        ),
        tool_trace=(
            ToolCall("search_knowledge", {"question": "上限"}, "succeeded", None),
            ToolCall(
                "get_expense_claim",
                {"claim_id": "CLAIM-1024"},
                "succeeded",
                None,
            ),
            ToolCall(
                "get_business_trip",
                {"trip_id": "TRIP-2409-017"},
                "succeeded",
                None,
            ),
        ),
        sources=(Source("policy.md", "c1", 0.9),),
        trace=RunTrace.empty(),
    )

    metrics = score_agent((case,), {"A05": result})

    assert metrics["tool_argument_accuracy"].value == 1.0
    assert metrics["required_support_accuracy"].value == 1.0
    assert metrics["forbidden_action_count"].numerator == 0


def test_latency_uses_nearest_rank_percentiles() -> None:
    summary = summarize_latency((10.0, 20.0, 30.0, 40.0, 50.0))

    assert summary.count == 5
    assert summary.p50_ms == 30.0
    assert summary.p95_ms == 50.0


def test_fake_backend_can_never_be_labelled_real() -> None:
    descriptors = (
        BackendDescriptor("retriever", "real", "gte", "revision-1"),
        BackendDescriptor("generator", "fake", "scripted", "test"),
    )

    assert evidence_label_for(descriptors) == "fake-backed"


class UnusedRag:
    async def ask(self, question: str) -> RagResult:
        raise AssertionError(f"unexpected RAG call: {question}")


def _unused_clock() -> float:
    return 0.0


async def test_layer_report_keeps_raw_counts_and_fake_label() -> None:
    calls: list[str] = []
    candidate = retrieved("c1", "P4 上限为 CNY 600。")
    evaluator = LayeredEvaluator(
        knowledge_cases=(knowledge_case("K01"),),
        agent_cases=(),
        retriever=ScriptedRetriever(calls, (candidate,)),
        reranker=ScriptedReranker(calls, (reranked("c1", candidate.text),)),
        rag=UnusedRag(),
        agent=None,
        retrieve_k=20,
        rerank_k=5,
        corpus_digest="a" * 64,
        descriptors={
            "retriever": (
                BackendDescriptor("encoder", "fake", "scripted", "test"),
            )
        },
        clock=_unused_clock,
    )

    outcome = await evaluator.run("retriever")

    assert outcome.status == "succeeded"
    assert outcome.data["evidence_label"] == "fake-backed"
    assert outcome.data["case_count"] == 1
    assert outcome.data["cases"][0]["case_id"] == "K01"
    assert outcome.data["cases"][0]["hit"] is True
    metric = outcome.data["metrics"]["recall_at_20"]
    assert metric["numerator"] == 1
    assert metric["denominator"] == 1


def test_fixed_eval_files_have_24_plus_12_unique_cases() -> None:
    knowledge = load_knowledge_cases(Path("evals/knowledge.jsonl"))
    agent = load_agent_cases(Path("evals/agent.jsonl"))

    assert tuple(case.case_id for case in knowledge) == tuple(
        f"K{number:02d}" for number in range(1, 25)
    )
    assert tuple(case.case_id for case in agent) == tuple(
        f"A{number:02d}" for number in range(1, 13)
    )
    assert sum(case.answerable for case in knowledge) == 18
    assert all(
        case.expected_refusal_reason == "insufficient_evidence"
        for case in knowledge[18:]
    )


async def test_scripted_agent_scenarios_execute_real_loop_failures() -> None:
    runner = ScriptedAgentScenarioRunner()
    expectations = (
        ("scripted_invalid_id", "failed", "tool_validation"),
        ("scripted_tool_execution", "failed", "tool_execution"),
        ("scripted_duplicate", "refused", "duplicate_action"),
        ("scripted_iteration_limit", "refused", "iteration_limit"),
    )

    for index, (scenario, status, category) in enumerate(expectations, start=9):
        case = AgentEvalCase(
            case_id=f"A{index:02d}",
            task="scripted control-flow case",
            expected_business_tools=(),
            expected_arguments=(),
            expected_status=status,
            required_support_ids=(),
            expected_refusal_reason=(
                category if status == "refused" else None
            ),
            expected_error_category=(category if status == "failed" else None),
            scenario=scenario,
        )

        result = await runner.run(case)

        assert result.status == status
        assert (result.refusal_reason or result.error_category) == category
