from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import uuid4

from .errors import ErrorCategory, GenerationProtocolError, ToolValidationError
from .execution_control import CancellationToken, RunCancelled
from .execution_trace import TraceRecorder
from .models import (
    AgentAction,
    AgentResult,
    AnswerDraft,
    EvidenceClaim,
    Observation,
    RerankedChunk,
    Source,
    ToolCall,
    ValidationIssue,
)
from .rag_service import KnowledgeSearchResult

BUSINESS_TOOLS = frozenset(
    {"get_employee_profile", "get_expense_claim", "get_business_trip"}
)

_ACTION_ARGUMENTS = {
    "search_knowledge": frozenset({"question"}),
    "get_employee_profile": frozenset({"employee_id"}),
    "get_expense_claim": frozenset({"claim_id"}),
    "get_business_trip": frozenset({"trip_id"}),
    "finish": frozenset(),
    "refuse": frozenset({"reason"}),
}
_BUSINESS_ARGUMENT_PATTERNS = {
    "get_employee_profile": ("employee_id", re.compile(r"EMP-[0-9]{3}")),
    "get_expense_claim": ("claim_id", re.compile(r"CLAIM-[0-9]{4}")),
    "get_business_trip": (
        "trip_id",
        re.compile(r"TRIP-[0-9]{4}-[0-9]{3}"),
    ),
}


class _PlannerPort(Protocol):
    backend_kind: str

    async def next_action(
        self,
        task: str,
        observations: tuple[Observation, ...],
    ) -> AgentAction: ...

    async def repair_answer(
        self,
        task: str,
        observations: tuple[Observation, ...],
        draft: AnswerDraft,
        issues: tuple[ValidationIssue, ...],
    ) -> AnswerDraft: ...


class _KnowledgeSearchPort(Protocol):
    backend_kind: str

    async def search_evidence(
        self,
        question: str,
        *,
        recorder: TraceRecorder | None = None,
        parent_id: str | None = None,
    ) -> KnowledgeSearchResult: ...


class _ToolRegistryPort(Protocol):
    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> Observation: ...


class _ValidatorPort(Protocol):
    backend_kind: str

    async def validate(
        self,
        draft: AnswerDraft,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[ValidationIssue, ...]: ...


@dataclass(frozen=True, slots=True)
class _AnsweredFinish:
    """A decided, grounded final answer, before the run recorder is closed."""

    answer: str
    claims: tuple[EvidenceClaim, ...]
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class _RefusedFinish:
    """A decided final refusal, before the run recorder is closed."""

    reason: str


_FinishOutcome = _AnsweredFinish | _RefusedFinish


class AgentService:
    """Bounded Plan-Act-Observe loop with evidence-gated final answers."""

    def __init__(
        self,
        *,
        planner: _PlannerPort,
        rag: _KnowledgeSearchPort,
        tools: _ToolRegistryPort,
        validator: _ValidatorPort,
        max_actions: int,
        trace_factory: Callable[[], TraceRecorder] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        if type(max_actions) is not int or max_actions <= 0:
            raise ValueError("max_actions must be a positive integer")
        self._planner = planner
        self._rag = rag
        self._tools = tools
        self._validator = validator
        self._max_actions = max_actions
        self._trace_factory = trace_factory or _default_trace_factory
        self._cancellation = cancellation

    def _check_cancelled(self) -> None:
        """Observe the bound token at a safe boundary, if one is bound."""

        token = self._cancellation
        if token is not None:
            token.raise_if_cancelled()

    async def run(self, task: str) -> AgentResult:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")

        recorder = self._trace_factory()
        observations: list[Observation] = []
        tool_trace: list[ToolCall] = []
        evidence_by_id: dict[str, RerankedChunk] = {}
        seen_actions: set[str] = set()

        try:
            for step in range(1, self._max_actions + 1):
                self._check_cancelled()
                with recorder.span(
                    "agent_plan",
                    attributes={
                        "backend_kind": _backend_kind(self._planner),
                        "step": step,
                    },
                ):
                    action = await self._planner.next_action(
                        task,
                        tuple(observations),
                    )
                    _validate_action(action)
                self._check_cancelled()
                _record_backend_switch(recorder, self._planner)

                fingerprint = action.fingerprint()
                if fingerprint in seen_actions:
                    return AgentResult.refused(
                        "duplicate_action",
                        tool_trace=tuple(tool_trace),
                        observations=tuple(observations),
                        trace=recorder.finish("refused"),
                    )
                seen_actions.add(fingerprint)

                action_attributes = {
                    "action": action.name,
                    "arguments": action.arguments,
                    "step": step,
                }
                if action.name == "search_knowledge":
                    self._check_cancelled()
                    with recorder.span(
                        "agent_action",
                        attributes=action_attributes,
                    ) as action_span:
                        with recorder.span(
                            "search_knowledge",
                            parent_id=action_span.event_id,
                            attributes={"step": step},
                        ) as search_span:
                            result = await self._rag.search_evidence(
                                _string_argument(action, "question"),
                                recorder=recorder,
                                parent_id=search_span.event_id,
                            )
                            if result.status == "failed":
                                category = (
                                    result.error_category
                                    or ErrorCategory.PROTOCOL.value
                                )
                                search_span.finish(
                                    "failed",
                                    error_category=category,
                                )
                                action_span.finish(
                                    "failed",
                                    error_category=category,
                                )
                            elif result.status == "refused":
                                search_span.finish(
                                    "refused",
                                    error_category=(
                                        ErrorCategory.EVIDENCE_INSUFFICIENT.value
                                    ),
                                )
                                action_span.finish(
                                    "refused",
                                    error_category=(
                                        ErrorCategory.EVIDENCE_INSUFFICIENT.value
                                    ),
                                )
                    self._check_cancelled()
                    if result.status == "failed":
                        category = result.error_category or ErrorCategory.PROTOCOL.value
                        tool_trace.append(
                            ToolCall(
                                action.name,
                                action.arguments,
                                "failed",
                                category,
                            )
                        )
                        return AgentResult.failed(
                            category,
                            tool_trace=tuple(tool_trace),
                            observations=tuple(observations),
                            trace=recorder.finish("failed"),
                        )
                    if result.status == "refused":
                        tool_trace.append(
                            ToolCall(
                                action.name,
                                action.arguments,
                                "degraded",
                                ErrorCategory.EVIDENCE_INSUFFICIENT.value,
                            )
                        )
                        observation = _refused_search_observation(
                            _string_argument(action, "question")
                        )
                        observations.append(observation)
                        _record_observation(recorder, observation, step)
                        self._check_cancelled()
                        continue
                    tool_trace.append(
                        ToolCall(action.name, action.arguments, "succeeded", None)
                    )
                    applicable_evidence = _select_applicable_policy_evidence(
                        result.evidence,
                        tuple(observations),
                    )
                    added_observations = _add_knowledge_evidence(
                        applicable_evidence,
                        evidence_by_id,
                        observations,
                    )
                    for observation in added_observations:
                        _record_observation(recorder, observation, step)
                    self._check_cancelled()
                    continue

                if action.name in BUSINESS_TOOLS:
                    self._check_cancelled()
                    try:
                        with recorder.span(
                            "agent_action",
                            attributes=action_attributes,
                        ):
                            observation = await self._tools.call(
                                action.name,
                                action.arguments,
                            )
                    except RunCancelled:
                        raise
                    except Exception as error:
                        category = _error_category(error)
                        tool_trace.append(
                            ToolCall(
                                action.name,
                                action.arguments,
                                "failed",
                                category,
                            )
                        )
                        return AgentResult.failed(
                            category,
                            tool_trace=tuple(tool_trace),
                            observations=tuple(observations),
                            trace=recorder.finish("failed"),
                        )
                    self._check_cancelled()
                    observations.append(observation)
                    _record_observation(recorder, observation, step)
                    tool_trace.append(
                        ToolCall(action.name, action.arguments, "succeeded", None)
                    )
                    continue

                if action.name == "refuse":
                    with recorder.span(
                        "agent_action",
                        attributes=action_attributes,
                    ) as action_span:
                        action_span.finish("refused")
                    return AgentResult.refused(
                        "agent_requested_refusal",
                        tool_trace=tuple(tool_trace),
                        observations=tuple(observations),
                        trace=recorder.finish("refused"),
                    )

                if action.name == "finish":
                    with recorder.span(
                        "agent_action",
                        attributes=action_attributes,
                    ):
                        pass
                    self._check_cancelled()
                    return await self._finish(
                        task=task,
                        draft=action.finish_draft,
                        evidence=tuple(evidence_by_id.values()),
                        observations=tuple(observations),
                        tool_trace=tuple(tool_trace),
                        recorder=recorder,
                        step=step,
                    )

            return AgentResult.refused(
                "iteration_limit",
                tool_trace=tuple(tool_trace),
                observations=tuple(observations),
                trace=recorder.finish("refused"),
            )
        except RunCancelled:
            raise
        except Exception as error:
            return AgentResult.failed(
                _error_category(error),
                tool_trace=tuple(tool_trace),
                observations=tuple(observations),
                trace=recorder.finish("failed"),
            )

    async def _finish(
        self,
        *,
        task: str,
        draft: AnswerDraft | None,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
        tool_trace: tuple[ToolCall, ...],
        recorder: TraceRecorder,
        step: int,
    ) -> AgentResult:
        if draft is None:
            raise GenerationProtocolError("finish action requires a draft")
        with recorder.span(
            "agent_finish",
            attributes={"step": step},
        ) as finish_span:
            outcome = await self._evaluate_finish(
                task=task,
                draft=draft,
                evidence=evidence,
                observations=observations,
                recorder=recorder,
                parent_id=finish_span.event_id,
            )
            if isinstance(outcome, _RefusedFinish):
                finish_span.finish("refused")
        if isinstance(outcome, _AnsweredFinish):
            return AgentResult.answered(
                outcome.answer,
                claims=outcome.claims,
                tool_trace=tool_trace,
                sources=outcome.sources,
                observations=observations,
                trace=recorder.finish("succeeded"),
            )
        return AgentResult.refused(
            outcome.reason,
            tool_trace=tool_trace,
            observations=observations,
            trace=recorder.finish("refused"),
        )

    async def _evaluate_finish(
        self,
        *,
        task: str,
        draft: AnswerDraft,
        evidence: tuple[RerankedChunk, ...],
        observations: tuple[Observation, ...],
        recorder: TraceRecorder,
        parent_id: str,
    ) -> _FinishOutcome:
        """Decide the final answer without closing the run recorder.

        The caller owns ``recorder.finish`` so the terminal ``agent_finish`` span
        can close first; every early return here is an outcome, not a result.
        """

        if not evidence or not _cites_knowledge(draft, evidence):
            return _RefusedFinish("knowledge_evidence_required")

        business_observations = tuple(
            item for item in observations if item.source != "search_knowledge"
        )
        draft = _realign_known_support_ids(
            draft,
            evidence,
            business_observations,
        )
        draft = _supplement_business_claims(draft, business_observations)
        draft = _canonicalize_answer(
            draft,
            evidence,
            business_observations,
        )
        self._check_cancelled()
        with recorder.span(
            "grounding_validation",
            parent_id=parent_id,
            attributes={"backend_kind": _backend_kind(self._validator)},
        ) as validation_span:
            validation_issues = await self._validator.validate(
                draft,
                evidence,
                business_observations,
            )
            issues = (
                *validation_issues,
                *_missing_business_support_issues(
                    draft,
                    business_observations,
                ),
            )
            validation_span.finish(
                "degraded" if issues else "succeeded",
                error_category=(
                    ErrorCategory.GROUNDING_FAILED.value if issues else None
                ),
                attributes=_validation_attributes(draft, issues, self._validator),
            )
        _record_validation_issues(
            recorder,
            issues,
            attempt="initial",
            parent_id=parent_id,
        )
        self._check_cancelled()

        if issues:
            with recorder.span(
                "repair_generation",
                parent_id=parent_id,
                attributes={"backend_kind": _backend_kind(self._planner)},
            ):
                draft = await self._planner.repair_answer(
                    task,
                    observations,
                    draft,
                    issues,
                )
                draft = _realign_known_support_ids(
                    draft,
                    evidence,
                    business_observations,
                )
                draft = _supplement_business_claims(
                    draft,
                    business_observations,
                )
                draft = _canonicalize_answer(
                    draft,
                    evidence,
                    business_observations,
                )
            _record_backend_switch(recorder, self._planner)
            self._check_cancelled()
            if not _cites_knowledge(draft, evidence):
                return _RefusedFinish("knowledge_evidence_required")
            with recorder.span(
                "repair_validation",
                parent_id=parent_id,
                attributes={"backend_kind": _backend_kind(self._validator)},
            ) as repair_span:
                repaired_validation_issues = await self._validator.validate(
                    draft,
                    evidence,
                    business_observations,
                )
                repaired_issues = (
                    *repaired_validation_issues,
                    *_missing_business_support_issues(
                        draft,
                        business_observations,
                    ),
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
            _record_validation_issues(
                recorder,
                repaired_issues,
                attempt="repair",
                parent_id=parent_id,
            )
            self._check_cancelled()
            if repaired_issues:
                return _RefusedFinish("grounding_validation_failed")

        if not _all_support_ids_known(draft, evidence, business_observations):
            return _RefusedFinish("grounding_validation_failed")
        sources = _sources_for_draft(draft, evidence)
        if not sources:
            return _RefusedFinish("grounding_validation_failed")
        return _AnsweredFinish(draft.answer, draft.claims, sources)


def _validate_action(action: object) -> None:
    if not isinstance(action, AgentAction):
        raise GenerationProtocolError("planner returned an invalid action")
    expected = _ACTION_ARGUMENTS.get(action.name)
    if expected is None:
        raise GenerationProtocolError("planner selected an unknown action")
    if set(action.arguments) != expected:
        raise GenerationProtocolError("planner action fields are invalid")
    if action.name == "finish":
        if action.finish_draft is None:
            raise GenerationProtocolError("finish action requires a draft")
        return
    if action.finish_draft is not None:
        raise GenerationProtocolError("non-finish action cannot contain a draft")
    for value in action.arguments.values():
        if not isinstance(value, str) or not value.strip():
            raise GenerationProtocolError("planner action values are invalid")
    business_schema = _BUSINESS_ARGUMENT_PATTERNS.get(action.name)
    if business_schema is not None:
        argument_name, pattern = business_schema
        value = action.arguments[argument_name]
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ToolValidationError("tool arguments are invalid")


def _string_argument(action: AgentAction, name: str) -> str:
    value = action.arguments[name]
    if not isinstance(value, str):
        raise GenerationProtocolError("planner action value is invalid")
    return value


def _add_knowledge_evidence(
    evidence: tuple[RerankedChunk, ...],
    evidence_by_id: dict[str, RerankedChunk],
    observations: list[Observation],
) -> tuple[Observation, ...]:
    added: list[Observation] = []
    for item in evidence:
        chunk = item.candidate
        if chunk.chunk_id in evidence_by_id:
            continue
        evidence_by_id[chunk.chunk_id] = item
        observation = Observation(
            observation_id=chunk.chunk_id,
            source="search_knowledge",
            status="found",
            facts={
                "filename": chunk.filename,
                "retrieval_rank": chunk.retrieval_rank,
                "retrieval_score": chunk.retrieval_score,
                "rerank_rank": item.rerank_rank,
                "rerank_score": item.rerank_score,
            },
            support_text=chunk.text,
        )
        observations.append(observation)
        added.append(observation)
    return tuple(added)


def _record_observation(
    recorder: TraceRecorder,
    observation: Observation,
    step: int,
) -> None:
    with recorder.span(
        "agent_observation",
        attributes={
            "observation_id": observation.observation_id,
            "source": observation.source,
            "status": observation.status,
            "step": step,
        },
    ):
        pass


def _record_validation_issues(
    recorder: TraceRecorder,
    issues: tuple[ValidationIssue, ...],
    *,
    attempt: str,
    parent_id: str,
) -> None:
    for issue in issues:
        with recorder.span(
            "grounding_issue",
            parent_id=parent_id,
            attributes={
                "attempt": attempt,
                "claim_index": issue.claim_index,
                "code": issue.code,
            },
        ):
            pass


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


def _draft_support_ids(draft: AnswerDraft) -> tuple[str, ...]:
    ids: list[str] = []
    for claim in draft.claims:
        for support_id in claim.support_ids:
            if support_id not in ids:
                ids.append(support_id)
    return tuple(ids)


def _select_applicable_policy_evidence(
    evidence: tuple[RerankedChunk, ...],
    observations: tuple[Observation, ...],
) -> tuple[RerankedChunk, ...]:
    employee = next(
        (
            item
            for item in observations
            if item.source == "get_employee_profile" and item.status == "found"
        ),
        None,
    )
    trip = next(
        (
            item
            for item in observations
            if item.source == "get_business_trip" and item.status == "found"
        ),
        None,
    )
    if employee is None or trip is None:
        return evidence
    grade = employee.facts.get("grade")
    city_tier = trip.facts.get("city_tier")
    if not isinstance(grade, str) or not isinstance(city_tier, str):
        return evidence

    normalized_grade = grade.casefold()
    if city_tier == "A":
        phrases = (
            f"{normalized_grade} 员工前往 a 类城市",
            f"{normalized_grade} employee traveling to a cities",
            f"{normalized_grade} employees traveling to a cities",
        )
    else:
        phrases = (
            f"{normalized_grade} 员工前往非 a 类城市",
            f"{normalized_grade} employee traveling to non-a cities",
            f"{normalized_grade} employees traveling to non-a cities",
        )
    matching = tuple(
        item
        for item in evidence
        if any(
            phrase in _normalize_text(item.candidate.text)
            for phrase in phrases
        )
    )
    return matching or evidence


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _refused_search_observation(question: str) -> Observation:
    digest = hashlib.sha256(question.strip().encode()).hexdigest()[:16]
    return Observation(
        observation_id=f"obs:search_knowledge:{digest}",
        source="search_knowledge",
        status="not_found",
        facts={},
        support_text=None,
    )


def _cites_knowledge(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
) -> bool:
    evidence_ids = {item.candidate.chunk_id for item in evidence}
    return any(
        support_id in evidence_ids
        for claim in draft.claims
        for support_id in claim.support_ids
    )


def _sources_for_draft(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
) -> tuple[Source, ...]:
    cited_ids = {
        support_id
        for claim in draft.claims
        for support_id in claim.support_ids
    }
    return tuple(
        Source(
            filename=item.candidate.filename,
            chunk_id=item.candidate.chunk_id,
            score=item.rerank_score,
        )
        for item in evidence
        if item.candidate.chunk_id in cited_ids
    )


def _all_support_ids_known(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
    observations: tuple[Observation, ...],
) -> bool:
    allowed_ids = {item.candidate.chunk_id for item in evidence}
    allowed_ids.update(
        item.observation_id for item in observations if item.status == "found"
    )
    cited_ids = {
        support_id
        for claim in draft.claims
        for support_id in claim.support_ids
    }
    return bool(cited_ids) and cited_ids <= allowed_ids


def _canonicalize_answer(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
    observations: tuple[Observation, ...],
) -> AnswerDraft:
    support_texts = {
        item.candidate.chunk_id: item.candidate.text for item in evidence
    }
    support_texts.update(
        {
            item.observation_id: item.support_text
            for item in observations
            if item.status == "found" and item.support_text is not None
        }
    )
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


def _realign_known_support_ids(
    draft: AnswerDraft,
    evidence: tuple[RerankedChunk, ...],
    observations: tuple[Observation, ...],
) -> AnswerDraft:
    support_texts = {
        item.candidate.chunk_id: item.candidate.text for item in evidence
    }
    support_texts.update(
        {
            item.observation_id: item.support_text
            for item in observations
            if item.status == "found" and item.support_text is not None
        }
    )
    known_ids = support_texts.keys()
    claims: list[EvidenceClaim] = []
    for claim in draft.claims:
        support_ids = claim.support_ids
        if all(support_id in known_ids for support_id in support_ids):
            matched_ids: list[str] = []
            every_quote_matched = True
            for quote in claim.supporting_quotes:
                normalized_quote = _normalize_text(quote)
                quote_matches = tuple(
                    support_id
                    for support_id, support_text in support_texts.items()
                    if normalized_quote in _normalize_text(support_text)
                )
                if not quote_matches:
                    every_quote_matched = False
                    break
                for support_id in quote_matches:
                    if support_id not in matched_ids:
                        matched_ids.append(support_id)
            if every_quote_matched and matched_ids:
                support_ids = tuple(matched_ids)
        claims.append(
            EvidenceClaim(
                text=claim.text,
                support_ids=support_ids,
                supporting_quotes=claim.supporting_quotes,
            )
        )
    return AnswerDraft(answer=draft.answer, claims=tuple(claims))


def _supplement_business_claims(
    draft: AnswerDraft,
    observations: tuple[Observation, ...],
) -> AnswerDraft:
    found_observations = tuple(
        observation
        for observation in observations
        if observation.status == "found"
        and observation.support_text is not None
    )
    business_ids = {
        observation.observation_id for observation in found_observations
    }
    knowledge_claims: list[EvidenceClaim] = []
    for claim in draft.claims:
        remaining_ids = tuple(
            support_id
            for support_id in claim.support_ids
            if support_id not in business_ids
        )
        if not remaining_ids:
            continue
        knowledge_claims.append(
            EvidenceClaim(
                text=claim.text,
                support_ids=remaining_ids,
                supporting_quotes=claim.supporting_quotes,
            )
        )
    additions: list[EvidenceClaim] = []
    for observation in found_observations:
        support_text = observation.support_text
        if support_text is None:
            raise AssertionError("found observation support text was lost")
        additions.append(
            EvidenceClaim(
                text=_business_claim_text(observation),
                support_ids=(observation.observation_id,),
                supporting_quotes=(support_text,),
            )
        )
    return AnswerDraft(
        answer=draft.answer,
        claims=(*knowledge_claims, *additions),
    )


def _business_claim_text(observation: Observation) -> str:
    facts = observation.facts
    if observation.source == "get_employee_profile":
        employee_id = facts.get("employee_id")
        grade = facts.get("grade")
        travel_tier = facts.get("travel_tier")
        if all(
            isinstance(value, str) and value
            for value in (employee_id, grade, travel_tier)
        ):
            return (
                f"员工 {employee_id} 的职级为 {grade}, "
                f"差旅等级为 {travel_tier}."
            )
    elif observation.source == "get_expense_claim":
        claim_id = facts.get("claim_id")
        amount = facts.get("hotel_nightly_amount")
        status = facts.get("status")
        rejection_code = facts.get("rejection_code")
        if (
            isinstance(claim_id, str)
            and type(amount) is int
            and isinstance(status, str)
            and isinstance(rejection_code, str)
        ):
            return (
                f"报销单 {claim_id} 的每晚住宿金额为 {amount}, "
                f"状态为 {status}, 拒绝代码为 {rejection_code}."
            )
    elif observation.source == "get_business_trip":
        trip_id = facts.get("trip_id")
        destination = facts.get("destination")
        city_tier = facts.get("city_tier")
        approved = facts.get("vp_exception_approved")
        if (
            isinstance(trip_id, str)
            and isinstance(destination, str)
            and isinstance(city_tier, str)
            and type(approved) is bool
        ):
            approval_text = str(approved).lower()
            return (
                f"行程 {trip_id} 的目的地为 {destination}, "
                f"城市等级为 {city_tier}, "
                f"VP 例外批准字段为 {approval_text}."
            )
    return f"业务记录 {observation.observation_id} 已成功读取."


def _missing_business_support_issues(
    draft: AnswerDraft,
    observations: tuple[Observation, ...],
) -> tuple[ValidationIssue, ...]:
    cited_ids = {
        support_id
        for claim in draft.claims
        for support_id in claim.support_ids
    }
    return tuple(
        ValidationIssue(
            "missing_required_observation",
            None,
            f"required Observation is not cited: {observation.observation_id}",
        )
        for observation in observations
        if observation.status == "found"
        and observation.observation_id not in cited_ids
    )


def _backend_kind(component: object) -> str:
    value = getattr(component, "backend_kind", "unknown")
    return value if isinstance(value, str) and value else "unknown"


def _record_backend_switch(
    recorder: TraceRecorder,
    planner: object,
) -> None:
    consume = getattr(planner, "consume_backend_switch", None)
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
    return TraceRecorder(f"agent-{uuid4().hex}")
