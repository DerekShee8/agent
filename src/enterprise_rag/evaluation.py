from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, Self, cast

from .agent_service import AgentService
from .errors import ToolExecutionError
from .execution_trace import TraceScope
from .models import (
    AgentAction,
    AgentResult,
    AnswerDraft,
    Observation,
    RagResult,
    RerankedChunk,
    RetrievedChunk,
    ValidationIssue,
)
from .rag_service import KnowledgeSearchResult

if TYPE_CHECKING:
    from .application import CommandOutcome

_LAYERS = frozenset({"retriever", "reranker", "rag", "agent", "all"})
_SAFE_AGENT_ACTIONS = frozenset(
    {
        "search_knowledge",
        "get_employee_profile",
        "get_expense_claim",
        "get_business_trip",
    }
)
AgentScenario = Literal[
    "live",
    "scripted_invalid_id",
    "scripted_tool_execution",
    "scripted_duplicate",
    "scripted_iteration_limit",
]
_AGENT_SCENARIOS = frozenset(
    {
        "live",
        "scripted_invalid_id",
        "scripted_tool_execution",
        "scripted_duplicate",
        "scripted_iteration_limit",
    }
)


@dataclass(frozen=True, slots=True)
class KnowledgeEvalCase:
    case_id: str
    question: str
    answerable: bool
    expected_chunk_ids: tuple[str, ...]
    required_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]
    expected_refusal_reason: str | None

    def __post_init__(self) -> None:
        _require_nonempty(self.case_id, "case_id")
        _require_nonempty(self.question, "question")
        _require_string_tuple(self.expected_chunk_ids, "expected_chunk_ids")
        _require_string_tuple(self.required_facts, "required_facts")
        _require_string_tuple(self.forbidden_facts, "forbidden_facts")
        if type(self.answerable) is not bool:
            raise TypeError("answerable must be a boolean")
        if self.answerable:
            if not self.expected_chunk_ids or not self.required_facts:
                raise ValueError("answerable case requires chunks and facts")
            if self.expected_refusal_reason is not None:
                raise ValueError("answerable case cannot expect refusal")
        else:
            if self.expected_chunk_ids or self.required_facts:
                raise ValueError("unanswerable case cannot expect chunks or facts")
            _require_nonempty(
                self.expected_refusal_reason,
                "expected_refusal_reason",
            )


@dataclass(frozen=True, slots=True)
class AgentEvalCase:
    case_id: str
    task: str
    expected_business_tools: tuple[str, ...]
    expected_arguments: tuple[Mapping[str, object], ...]
    expected_status: Literal["answered", "refused", "failed"]
    required_support_ids: tuple[str, ...]
    expected_refusal_reason: str | None
    expected_error_category: str | None
    scenario: AgentScenario = "live"

    def __post_init__(self) -> None:
        _require_nonempty(self.case_id, "case_id")
        _require_nonempty(self.task, "task")
        _require_string_tuple(
            self.expected_business_tools,
            "expected_business_tools",
        )
        _require_string_tuple(self.required_support_ids, "required_support_ids")
        if not isinstance(self.expected_arguments, tuple):
            raise TypeError("expected_arguments must be a tuple")
        if len(self.expected_arguments) != len(self.expected_business_tools):
            raise ValueError("expected tool and argument counts must match")
        frozen_arguments: list[Mapping[str, object]] = []
        for arguments in self.expected_arguments:
            if not isinstance(arguments, Mapping):
                raise TypeError("expected arguments must be mappings")
            frozen_arguments.append(MappingProxyType(dict(arguments)))
        object.__setattr__(self, "expected_arguments", tuple(frozen_arguments))
        if self.expected_status not in {"answered", "refused", "failed"}:
            raise ValueError("invalid expected_status")
        if self.scenario not in _AGENT_SCENARIOS:
            raise ValueError("invalid agent evaluation scenario")
        if self.expected_status == "answered":
            if not self.required_support_ids:
                raise ValueError("answered agent case requires support IDs")
            if (
                self.expected_refusal_reason is not None
                or self.expected_error_category is not None
            ):
                raise ValueError("answered case cannot expect a failure")
        elif self.expected_status == "refused":
            _require_nonempty(
                self.expected_refusal_reason,
                "expected_refusal_reason",
            )
            if self.expected_error_category is not None:
                raise ValueError("refused case cannot expect an error category")
        else:
            _require_nonempty(
                self.expected_error_category,
                "expected_error_category",
            )
            if self.expected_refusal_reason is not None:
                raise ValueError("failed case cannot expect a refusal reason")


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "metric name")
        if type(self.numerator) is not int or self.numerator < 0:
            raise ValueError("metric numerator must be a non-negative integer")
        if type(self.denominator) is not int or self.denominator < 0:
            raise ValueError("metric denominator must be a non-negative integer")

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def to_dict(self) -> dict[str, object]:
        return {
            "denominator": self.denominator,
            "numerator": self.numerator,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class LatencySummary:
    count: int
    p50_ms: float | None
    p95_ms: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
        }


@dataclass(frozen=True, slots=True)
class BackendDescriptor:
    name: str
    backend_kind: str
    model_id: str | None
    model_revision: str | None

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "backend name")
        _require_nonempty(self.backend_kind, "backend_kind")

    @classmethod
    def from_component(cls, name: str, component: object) -> Self:
        return cls(
            name=name,
            backend_kind=_string_attribute(component, "backend_kind", "unknown"),
            model_id=_optional_string_attribute(component, "model_id"),
            model_revision=_optional_string_attribute(
                component,
                "model_revision",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "backend_kind": self.backend_kind,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "name": self.name,
        }


class _RetrieverPort(Protocol):
    async def retrieve(
        self,
        question: str,
        top_k: int,
        *,
        trace_scope: TraceScope | None = None,
    ) -> tuple[RetrievedChunk, ...]: ...


class _RerankerPort(Protocol):
    async def rerank(
        self,
        question: str,
        candidates: tuple[RetrievedChunk, ...],
        top_n: int,
    ) -> tuple[RerankedChunk, ...]: ...


class _RagPort(Protocol):
    async def ask(self, question: str) -> RagResult: ...


class _AgentPort(Protocol):
    async def run(self, task: str) -> AgentResult: ...


class _CaseWithId(Protocol):
    @property
    def case_id(self) -> str: ...


class _ScenarioPlanner:
    backend_kind = "fake"

    def __init__(self, actions: tuple[AgentAction, ...]) -> None:
        self._actions = iter(actions)

    async def next_action(
        self,
        task: str,
        observations: tuple[Observation, ...],
    ) -> AgentAction:
        del task, observations
        return next(self._actions)

    async def repair_answer(
        self,
        task: str,
        observations: tuple[Observation, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft:
        del task, observations, draft, issues
        raise AssertionError("scripted failure scenarios never repair answers")


class _ScenarioKnowledge:
    backend_kind = "fake"

    def __init__(self) -> None:
        candidate = RetrievedChunk(
            text="P4 员工的每晚住宿上限为 CNY 600。",
            filename="scripted-policy.md",
            chunk_id="scripted-policy.md::0000",
            dense_score=1.0,
            dense_rank=1,
            sparse_score=1.0,
            sparse_rank=1,
            retrieval_score=1.0,
            retrieval_rank=1,
        )
        self._result = KnowledgeSearchResult.found(
            (RerankedChunk(candidate, 1.0, 1),)
        )

    async def search_evidence(
        self,
        question: str,
        *,
        recorder: object | None = None,
        parent_id: str | None = None,
    ) -> KnowledgeSearchResult:
        del question, recorder, parent_id
        return self._result


class _ScenarioTools:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> Observation:
        del name, arguments
        if self._fail:
            raise ToolExecutionError("scripted tool execution failure")
        raise AssertionError("unexpected tool execution")


class _ScenarioValidator:
    backend_kind = "fake"

    async def validate(
        self,
        draft: AnswerDraft,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[ValidationIssue, ...]:
        del draft, evidence, observations
        return ()


class ScriptedAgentScenarioRunner:
    backend_kind = "fake"

    async def run(self, case: AgentEvalCase) -> AgentResult:
        if case.scenario == "live":
            raise ValueError("live cases require the configured real agent")
        actions: tuple[AgentAction, ...]
        fail_tools = False
        if case.scenario == "scripted_invalid_id":
            actions = (
                AgentAction(
                    "get_employee_profile",
                    {"employee_id": "INVALID-003"},
                    None,
                ),
            )
        elif case.scenario == "scripted_tool_execution":
            actions = (
                AgentAction(
                    "get_expense_claim",
                    {"claim_id": "CLAIM-1024"},
                    None,
                ),
            )
            fail_tools = True
        elif case.scenario == "scripted_duplicate":
            action = AgentAction(
                "search_knowledge",
                {"question": "scripted policy"},
                None,
            )
            actions = (action, action)
        elif case.scenario == "scripted_iteration_limit":
            actions = tuple(
                AgentAction(
                    "search_knowledge",
                    {"question": f"scripted policy {number}"},
                    None,
                )
                for number in range(1, 7)
            )
        else:
            raise AssertionError("unreachable scenario")
        service = AgentService(
            planner=_ScenarioPlanner(actions),
            rag=_ScenarioKnowledge(),
            tools=_ScenarioTools(fail=fail_tools),
            validator=_ScenarioValidator(),
            max_actions=6,
        )
        return await service.run(case.task)


def load_knowledge_cases(path: Path) -> tuple[KnowledgeEvalCase, ...]:
    rows = _load_jsonl(path)
    cases = tuple(_knowledge_case_from_row(row) for row in rows)
    _require_unique_case_ids(cases)
    return cases


def load_agent_cases(path: Path) -> tuple[AgentEvalCase, ...]:
    rows = _load_jsonl(path)
    cases = tuple(_agent_case_from_row(row) for row in rows)
    _require_unique_case_ids(cases)
    return cases


def score_retriever(
    cases: Sequence[KnowledgeEvalCase],
    results: Mapping[str, Sequence[str]],
    *,
    k: int,
) -> MetricResult:
    _require_positive_integer(k, "k")
    eligible = tuple(case for case in cases if case.answerable)
    hits = sum(
        _has_expected(case, tuple(results.get(case.case_id, ()))[:k])
        for case in eligible
    )
    return MetricResult(f"recall_at_{k}", hits, len(eligible))


def score_reranker(
    cases: Sequence[KnowledgeEvalCase],
    retrieved: Mapping[str, Sequence[str]],
    reranked: Mapping[str, Sequence[str]],
    *,
    k: int,
) -> MetricResult:
    _require_positive_integer(k, "k")
    eligible = tuple(
        case
        for case in cases
        if case.answerable
        and _has_expected(case, retrieved.get(case.case_id, ()))
    )
    hits = sum(
        _has_expected(case, tuple(reranked.get(case.case_id, ()))[:k])
        for case in eligible
    )
    return MetricResult(f"conditional_hit_at_{k}", hits, len(eligible))


def score_rag(
    cases: Sequence[KnowledgeEvalCase],
    results: Mapping[str, RagResult],
) -> Mapping[str, MetricResult]:
    answerable = tuple(case for case in cases if case.answerable)
    unanswerable = tuple(case for case in cases if not case.answerable)
    grounded = sum(
        _rag_answer_is_correct(case, results.get(case.case_id))
        for case in answerable
    )
    rejected = sum(
        _rag_refusal_is_correct(case, results.get(case.case_id))
        for case in unanswerable
    )
    citations = sum(
        _rag_citations_are_valid(
            results.get(case.case_id),
            set(case.expected_chunk_ids),
        )
        for case in answerable
    )
    failure_categories = sum(
        _actual_failure(results.get(case.case_id))
        == case.expected_refusal_reason
        for case in unanswerable
    )
    return MappingProxyType(
        {
            "citation_validity": MetricResult(
                "citation_validity",
                citations,
                len(answerable),
            ),
            "failure_category_accuracy": MetricResult(
                "failure_category_accuracy",
                failure_categories,
                len(unanswerable),
            ),
            "grounded_answer_correctness": MetricResult(
                "grounded_answer_correctness",
                grounded,
                len(answerable),
            ),
            "no_answer_rejection": MetricResult(
                "no_answer_rejection",
                rejected,
                len(unanswerable),
            ),
        }
    )


def score_agent(
    cases: Sequence[AgentEvalCase],
    results: Mapping[str, AgentResult],
) -> Mapping[str, MetricResult]:
    tool_matches = 0
    support_matches = 0
    forbidden_actions = 0
    failure_eligible = 0
    failure_matches = 0
    status_matches = 0
    for case in cases:
        result = results.get(case.case_id)
        if result is None:
            continue
        status_matches += result.status == case.expected_status
        actual_calls = tuple(
            call
            for call in result.tool_trace
            if call.name != "search_knowledge"
        )
        expected_calls = tuple(
            zip(
                case.expected_business_tools,
                case.expected_arguments,
                strict=True,
            )
        )
        actual_pairs = tuple(
            (call.name, dict(call.arguments)) for call in actual_calls
        )
        expected_pairs = tuple(
            (name, dict(arguments)) for name, arguments in expected_calls
        )
        tool_matches += actual_pairs == expected_pairs
        forbidden_actions += sum(
            call.name not in _SAFE_AGENT_ACTIONS for call in result.tool_trace
        )
        actual_support_ids = {
            support_id
            for claim in result.claims
            for support_id in claim.support_ids
        }
        support_matches += set(case.required_support_ids).issubset(
            actual_support_ids
        )
        expected_failure = (
            case.expected_refusal_reason or case.expected_error_category
        )
        if expected_failure is not None:
            failure_eligible += 1
            failure_matches += _actual_failure(result) == expected_failure
    denominator = len(cases)
    return MappingProxyType(
        {
            "failure_category_accuracy": MetricResult(
                "failure_category_accuracy",
                failure_matches,
                failure_eligible,
            ),
            "final_status_accuracy": MetricResult(
                "final_status_accuracy",
                status_matches,
                denominator,
            ),
            "forbidden_action_count": MetricResult(
                "forbidden_action_count",
                forbidden_actions,
                denominator,
            ),
            "required_support_accuracy": MetricResult(
                "required_support_accuracy",
                support_matches,
                denominator,
            ),
            "tool_argument_accuracy": MetricResult(
                "tool_argument_accuracy",
                tool_matches,
                denominator,
            ),
        }
    )


def summarize_latency(values_ms: Sequence[float]) -> LatencySummary:
    values = tuple(float(value) for value in values_ms)
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("latencies must be finite and non-negative")
    if not values:
        return LatencySummary(0, None, None)
    ordered = tuple(sorted(values))
    return LatencySummary(
        len(ordered),
        _nearest_rank(ordered, 0.50),
        _nearest_rank(ordered, 0.95),
    )


def evidence_label_for(descriptors: Sequence[BackendDescriptor]) -> str:
    if descriptors and all(item.backend_kind == "real" for item in descriptors):
        return "real"
    return "fake-backed"


class LayeredEvaluator:
    def __init__(
        self,
        *,
        knowledge_cases: tuple[KnowledgeEvalCase, ...],
        agent_cases: tuple[AgentEvalCase, ...],
        retriever: _RetrieverPort,
        reranker: _RerankerPort,
        rag: _RagPort,
        agent: _AgentPort | None,
        retrieve_k: int,
        rerank_k: int,
        corpus_digest: str,
        descriptors: Mapping[str, tuple[BackendDescriptor, ...]],
        scripted_agent_runner: ScriptedAgentScenarioRunner | None = None,
        clock: Callable[[], float] = perf_counter,
        timestamp_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        _require_positive_integer(retrieve_k, "retrieve_k")
        _require_positive_integer(rerank_k, "rerank_k")
        _require_nonempty(corpus_digest, "corpus_digest")
        self._knowledge_cases = knowledge_cases
        self._agent_cases = agent_cases
        self._retriever = retriever
        self._reranker = reranker
        self._rag = rag
        self._agent = agent
        self._retrieve_k = retrieve_k
        self._rerank_k = rerank_k
        self._corpus_digest = corpus_digest
        self._descriptors = dict(descriptors)
        self._scripted_agent_runner = (
            scripted_agent_runner or ScriptedAgentScenarioRunner()
        )
        self._clock = clock
        self._timestamp_factory = timestamp_factory

    async def run(self, layer: str) -> CommandOutcome:
        from .application import CommandOutcome

        if layer not in _LAYERS:
            return CommandOutcome.failed("configuration", {"layer": layer})
        if layer == "all":
            reports: dict[str, Mapping[str, object]] = {}
            for selected in ("retriever", "reranker", "rag", "agent"):
                reports[selected] = await self._run_layer(selected)
            labels = tuple(report["evidence_label"] for report in reports.values())
            return CommandOutcome.succeeded(
                {
                    "case_count": len(self._knowledge_cases)
                    + len(self._agent_cases),
                    "corpus_digest": self._corpus_digest,
                    "evidence_label": (
                        "real" if labels and all(item == "real" for item in labels)
                        else "fake-backed"
                    ),
                    "layer": "all",
                    "reports": reports,
                    "timestamp": _timestamp(self._timestamp_factory()),
                }
            )
        return CommandOutcome.succeeded(await self._run_layer(layer))

    async def _run_layer(self, layer: str) -> Mapping[str, object]:
        if layer == "retriever":
            metrics, latency, cases = await self._run_retriever()
            case_count = len(self._knowledge_cases)
        elif layer == "reranker":
            metrics, latency, cases = await self._run_reranker()
            case_count = len(self._knowledge_cases)
        elif layer == "rag":
            metrics, latency, cases = await self._run_rag()
            case_count = len(self._knowledge_cases)
        else:
            metrics, latency, cases = await self._run_agent()
            case_count = len(self._agent_cases)
        descriptors = self._descriptors.get(layer, ())
        if layer == "agent" and any(
            case.scenario != "live" for case in self._agent_cases
        ):
            descriptors = (
                *descriptors,
                BackendDescriptor(
                    "scripted_control_scenarios",
                    "fake",
                    None,
                    None,
                ),
            )
        return MappingProxyType(
            {
                "backends": tuple(item.to_dict() for item in descriptors),
                "case_count": case_count,
                "cases": cases,
                "corpus_digest": self._corpus_digest,
                "evidence_label": evidence_label_for(descriptors),
                "latency": latency.to_dict(),
                "layer": layer,
                "metrics": {
                    name: metric.to_dict() for name, metric in metrics.items()
                },
                "timestamp": _timestamp(self._timestamp_factory()),
            }
        )

    async def _run_retriever(
        self,
    ) -> tuple[
        Mapping[str, MetricResult],
        LatencySummary,
        tuple[Mapping[str, object], ...],
    ]:
        results: dict[str, tuple[str, ...]] = {}
        latencies: list[float] = []
        details: list[Mapping[str, object]] = []
        for case in self._knowledge_cases:
            started = self._clock()
            chunks = await self._retriever.retrieve(case.question, self._retrieve_k)
            latency_ms = (self._clock() - started) * 1000
            latencies.append(latency_ms)
            chunk_ids = tuple(chunk.chunk_id for chunk in chunks)
            results[case.case_id] = chunk_ids
            details.append(
                MappingProxyType(
                    {
                        "case_id": case.case_id,
                        "expected_chunk_ids": case.expected_chunk_ids,
                        "hit": (
                            _has_expected(case, chunk_ids)
                            if case.answerable
                            else None
                        ),
                        "latency_ms": latency_ms,
                        "retrieved_chunk_ids": chunk_ids,
                    }
                )
            )
        metric = score_retriever(
            self._knowledge_cases,
            results,
            k=self._retrieve_k,
        )
        return (
            {metric.name: metric},
            summarize_latency(latencies),
            tuple(details),
        )

    async def _run_reranker(
        self,
    ) -> tuple[
        Mapping[str, MetricResult],
        LatencySummary,
        tuple[Mapping[str, object], ...],
    ]:
        retrieved: dict[str, tuple[str, ...]] = {}
        reranked: dict[str, tuple[str, ...]] = {}
        latencies: list[float] = []
        details: list[Mapping[str, object]] = []
        for case in self._knowledge_cases:
            candidates = await self._retriever.retrieve(
                case.question,
                self._retrieve_k,
            )
            retrieved[case.case_id] = tuple(
                chunk.chunk_id for chunk in candidates
            )
            started = self._clock()
            evidence = await self._reranker.rerank(
                case.question,
                candidates,
                self._rerank_k,
            )
            latency_ms = (self._clock() - started) * 1000
            latencies.append(latency_ms)
            reranked_ids = tuple(
                chunk.candidate.chunk_id for chunk in evidence
            )
            reranked[case.case_id] = reranked_ids
            eligible = case.answerable and _has_expected(
                case,
                retrieved[case.case_id],
            )
            details.append(
                MappingProxyType(
                    {
                        "case_id": case.case_id,
                        "conditional_hit": (
                            _has_expected(case, reranked_ids)
                            if eligible
                            else None
                        ),
                        "eligible": eligible,
                        "expected_chunk_ids": case.expected_chunk_ids,
                        "latency_ms": latency_ms,
                        "reranked_chunk_ids": reranked_ids,
                        "retrieved_chunk_ids": retrieved[case.case_id],
                    }
                )
            )
        metric = score_reranker(
            self._knowledge_cases,
            retrieved,
            reranked,
            k=self._rerank_k,
        )
        return (
            {metric.name: metric},
            summarize_latency(latencies),
            tuple(details),
        )

    async def _run_rag(
        self,
    ) -> tuple[
        Mapping[str, MetricResult],
        LatencySummary,
        tuple[Mapping[str, object], ...],
    ]:
        results: dict[str, RagResult] = {}
        latencies: list[float] = []
        details: list[Mapping[str, object]] = []
        for case in self._knowledge_cases:
            started = self._clock()
            result = await self._rag.ask(case.question)
            latency_ms = (self._clock() - started) * 1000
            results[case.case_id] = result
            latencies.append(latency_ms)
            details.append(
                MappingProxyType(
                    {
                        "answer": result.answer,
                        "case_id": case.case_id,
                        "citation_valid": _rag_citations_are_valid(
                            result,
                            set(case.expected_chunk_ids),
                        ),
                        "correct": (
                            _rag_answer_is_correct(case, result)
                            if case.answerable
                            else _rag_refusal_is_correct(case, result)
                        ),
                        "error_category": result.error_category,
                        "expected_chunk_ids": case.expected_chunk_ids,
                        "latency_ms": latency_ms,
                        "refusal_reason": result.refusal_reason,
                        "source_ids": tuple(
                            source.chunk_id for source in result.sources
                        ),
                        "status": result.status,
                    }
                )
            )
        return (
            score_rag(self._knowledge_cases, results),
            summarize_latency(latencies),
            tuple(details),
        )

    async def _run_agent(
        self,
    ) -> tuple[
        Mapping[str, MetricResult],
        LatencySummary,
        tuple[Mapping[str, object], ...],
    ]:
        if self._agent is None:
            raise ValueError("agent evaluation requires an agent service")
        results: dict[str, AgentResult] = {}
        latencies: list[float] = []
        details: list[Mapping[str, object]] = []
        for case in self._agent_cases:
            started = self._clock()
            if case.scenario == "live":
                result = await self._agent.run(case.task)
            else:
                result = await self._scripted_agent_runner.run(case)
            latency_ms = (self._clock() - started) * 1000
            results[case.case_id] = result
            latencies.append(latency_ms)
            details.append(
                MappingProxyType(
                    {
                        "case_id": case.case_id,
                        "evidence_label": (
                            "real" if case.scenario == "live" else "fake-backed"
                        ),
                        "error_category": result.error_category,
                        "latency_ms": latency_ms,
                        "refusal_reason": result.refusal_reason,
                        "status": result.status,
                        "scenario": case.scenario,
                        "support_ids": tuple(
                            support_id
                            for claim in result.claims
                            for support_id in claim.support_ids
                        ),
                        "tool_calls": tuple(
                            {
                                "arguments": dict(call.arguments),
                                "name": call.name,
                                "status": call.status,
                            }
                            for call in result.tool_trace
                        ),
                    }
                )
            )
        return (
            score_agent(self._agent_cases, results),
            summarize_latency(latencies),
            tuple(details),
        )


def _load_jsonl(path: Path) -> tuple[Mapping[str, object], ...]:
    if not isinstance(path, Path):
        raise TypeError("evaluation path must be a Path")
    rows: list[Mapping[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError("evaluation data is unavailable") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid evaluation JSON on line {line_number}"
            ) from error
        if not isinstance(row, dict):
            raise ValueError("evaluation rows must be JSON objects")
        rows.append(row)
    return tuple(rows)


def _knowledge_case_from_row(row: Mapping[str, object]) -> KnowledgeEvalCase:
    expected_keys = {
        "answerable",
        "case_id",
        "expected_chunk_ids",
        "expected_refusal_reason",
        "forbidden_facts",
        "question",
        "required_facts",
    }
    _require_exact_keys(row, expected_keys)
    return KnowledgeEvalCase(
        case_id=_required_string(row, "case_id"),
        question=_required_string(row, "question"),
        answerable=_required_bool(row, "answerable"),
        expected_chunk_ids=_string_list(row, "expected_chunk_ids"),
        required_facts=_string_list(row, "required_facts"),
        forbidden_facts=_string_list(row, "forbidden_facts"),
        expected_refusal_reason=_optional_string(
            row,
            "expected_refusal_reason",
        ),
    )


def _agent_case_from_row(row: Mapping[str, object]) -> AgentEvalCase:
    expected_keys = {
        "case_id",
        "expected_arguments",
        "expected_business_tools",
        "expected_error_category",
        "expected_refusal_reason",
        "expected_status",
        "required_support_ids",
        "scenario",
        "task",
    }
    _require_exact_keys(row, expected_keys)
    raw_arguments = row["expected_arguments"]
    if not isinstance(raw_arguments, list) or any(
        not isinstance(item, dict) for item in raw_arguments
    ):
        raise ValueError("expected_arguments must be a list of objects")
    return AgentEvalCase(
        case_id=_required_string(row, "case_id"),
        task=_required_string(row, "task"),
        expected_business_tools=_string_list(row, "expected_business_tools"),
        expected_arguments=tuple(
            cast(dict[str, object], item) for item in raw_arguments
        ),
        expected_status=cast(
            Literal["answered", "refused", "failed"],
            _required_string(row, "expected_status"),
        ),
        required_support_ids=_string_list(row, "required_support_ids"),
        expected_refusal_reason=_optional_string(
            row,
            "expected_refusal_reason",
        ),
        expected_error_category=_optional_string(
            row,
            "expected_error_category",
        ),
        scenario=cast(AgentScenario, _required_string(row, "scenario")),
    )


def _rag_answer_is_correct(
    case: KnowledgeEvalCase,
    result: RagResult | None,
) -> bool:
    if result is None or result.status != "answered" or result.answer is None:
        return False
    normalized = result.answer.casefold()
    has_required = all(
        fact.casefold() in normalized for fact in case.required_facts
    )
    has_forbidden = any(
        fact.casefold() in normalized for fact in case.forbidden_facts
    )
    has_relevant_source = bool(
        {source.chunk_id for source in result.sources}
        & set(case.expected_chunk_ids)
    )
    return has_required and not has_forbidden and has_relevant_source


def _rag_refusal_is_correct(
    case: KnowledgeEvalCase,
    result: RagResult | None,
) -> bool:
    return (
        result is not None
        and result.status == "refused"
        and result.refusal_reason == case.expected_refusal_reason
    )


def _rag_citations_are_valid(
    result: RagResult | None,
    allowed_chunk_ids: set[str],
) -> bool:
    if result is None or result.status != "answered" or not result.sources:
        return False
    actual = {source.chunk_id for source in result.sources}
    return actual.issubset(allowed_chunk_ids)


def _actual_failure(result: RagResult | AgentResult | None) -> str | None:
    if result is None:
        return None
    return result.refusal_reason or result.error_category


def _has_expected(
    case: KnowledgeEvalCase,
    actual_ids: Sequence[str],
) -> bool:
    return bool(set(case.expected_chunk_ids) & set(actual_ids))


def _nearest_rank(values: tuple[float, ...], percentile: float) -> float:
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("evaluation timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_unique_case_ids(cases: Sequence[_CaseWithId]) -> None:
    ids = tuple(case.case_id for case in cases)
    if len(ids) != len(set(ids)):
        raise ValueError("evaluation case IDs must be unique")


def _require_exact_keys(row: Mapping[str, object], expected: set[str]) -> None:
    if set(row) != expected:
        raise ValueError("evaluation row fields do not match the schema")


def _required_string(row: Mapping[str, object], key: str) -> str:
    value = row[key]
    _require_nonempty(value, key)
    return cast(str, value)


def _optional_string(row: Mapping[str, object], key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    _require_nonempty(value, key)
    return cast(str, value)


def _required_bool(row: Mapping[str, object], key: str) -> bool:
    value = row[key]
    if type(value) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return value


def _string_list(row: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = row[key]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return tuple(value)


def _require_nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_string_tuple(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicates")


def _require_positive_integer(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _string_attribute(component: object, name: str, default: str) -> str:
    value = getattr(component, name, default)
    return value if isinstance(value, str) and value.strip() else default


def _optional_string_attribute(component: object, name: str) -> str | None:
    value = getattr(component, name, None)
    return value if isinstance(value, str) and value.strip() else None
