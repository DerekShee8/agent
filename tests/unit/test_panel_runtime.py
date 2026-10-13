"""Staged runtime construction and per-run Application binding.

The stage builders are the seam the panel runtime uses: each heavy component is
loaded once, combined without I/O, and then bound to a fresh single-run
Application. These tests pin that ownership boundary with counting factories.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

import pytest

from enterprise_rag.agent_tools import ToolRegistry
from enterprise_rag.application import (
    ApplicationTemplate,
    RerankingRuntimeComponents,
    RetrievalRuntimeComponents,
    SharedRuntimeComponents,
    aclose_runtime_components,
    assemble_shared_runtime_components,
    build_runtime_reranking,
    build_runtime_retrieval,
    load_runtime_index,
    load_runtime_tools,
)
from enterprise_rag.config import AppConfig
from enterprise_rag.errors import ConfigurationError
from enterprise_rag.execution_control import CancellationToken, RunCancelled
from enterprise_rag.execution_trace import TraceRecorder
from enterprise_rag.grounding import EvidenceGate, GroundingValidator
from enterprise_rag.index_store import HybridIndex, IndexManifest
from enterprise_rag.ingestion import DocumentChunk, corpus_digest
from enterprise_rag.models import (
    RerankedChunk,
    RetrievedChunk,
    StructuredGenerationRequest,
)
from enterprise_rag.ports import EncodedBatch

RECORDS_ROOT = Path("data/sample_records")


class _RealEncoder:
    backend_kind = "real"
    model_id = "test/gte"
    model_revision = "a" * 40
    code_revision = "b" * 40

    def __init__(self) -> None:
        self.aclose_calls = 0

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        return EncodedBatch(
            dense=tuple((1.0, 0.0) for _ in texts),
            sparse=tuple(((101, 1.0),) for _ in texts),
        )

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _RealReranker:
    backend_kind = "real"

    def __init__(self) -> None:
        self.close_calls = 0
        self.close_thread: int | None = None

    async def rerank(
        self,
        question: str,
        candidates: tuple[RetrievedChunk, ...],
        top_n: int,
    ) -> tuple[RerankedChunk, ...]:
        del question
        return tuple(
            RerankedChunk(candidate, 0.9, rank)
            for rank, candidate in enumerate(candidates[:top_n], start=1)
        )

    def close(self) -> None:
        self.close_calls += 1
        self.close_thread = threading.get_ident()


class _RealScorer:
    backend_kind = "real"

    async def score(
        self,
        claim: str,
        support_texts: tuple[str, ...],
    ) -> tuple[float, ...]:
        del claim
        return tuple(0.9 for _ in support_texts)


class _RealGenerator:
    backend_kind = "real"

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        del request
        return {"answer": "CNY 600", "claims": []}


class _FakeGenerator(_RealGenerator):
    backend_kind = "fake"


def _config(tmp_path: Path) -> AppConfig:
    model_cache = tmp_path / "model-cache"
    return AppConfig(
        model_cache_dir=model_cache,
        index_dir=tmp_path / "index",
        local_model_path=model_cache / "model.gguf",
    )


def _real_index() -> HybridIndex:
    chunks = (
        DocumentChunk(
            "P4 员工的酒店住宿上限为 CNY 600。",
            "policy.md",
            "policy.md::0000",
        ),
    )
    return HybridIndex(
        manifest=IndexManifest(
            schema_version=1,
            backend_kind="real",
            source_digest=corpus_digest(chunks),
            chunk_size=384,
            chunk_overlap=48,
            model_id=_RealEncoder.model_id,
            model_revision=_RealEncoder.model_revision,
            code_revision=_RealEncoder.code_revision,
            dimension=2,
            row_count=1,
            built_at="2026-10-12T00:00:00Z",
        ),
        chunks=chunks,
        dense=((1.0, 0.0),),
        sparse=(((101, 1.0),),),
    )


def _shared(
    tmp_path: Path,
    *,
    encoder: object | None = None,
    reranker: object | None = None,
    support_scorer: object | None = None,
    tools: ToolRegistry | None = None,
) -> SharedRuntimeComponents:
    config = _config(tmp_path)
    active_encoder = encoder or _RealEncoder()
    active_reranker = reranker or _RealReranker()
    active_scorer = support_scorer or _RealScorer()
    retrieval = RetrievalRuntimeComponents(
        index=_real_index(),
        encoder=active_encoder,  # type: ignore[arg-type]
        retriever=build_runtime_retrieval(
            config,
            _real_index(),
            encoder_factory=lambda _config: active_encoder,  # type: ignore[arg-type,return-value]
        ).retriever,
    )
    reranking = RerankingRuntimeComponents(
        reranker=active_reranker,  # type: ignore[arg-type]
        support_scorer=active_scorer,  # type: ignore[arg-type]
        gate=EvidenceGate(min_score=0.5),
        validator=GroundingValidator(
            active_scorer,  # type: ignore[arg-type]
            min_claim_support_score=0.5,
        ),
    )
    return assemble_shared_runtime_components(
        config,
        retrieval=retrieval,
        reranking=reranking,
        tools=tools or ToolRegistry.from_directory(RECORDS_ROOT),
    )


def _local_template(shared: SharedRuntimeComponents) -> ApplicationTemplate:
    return ApplicationTemplate(
        shared=shared,
        generator_kind="local",
        provider_name=None,
        provider_fingerprint=None,
        fallback_local=False,
    )


def test_stage_builders_create_each_heavy_component_exactly_once(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    config = _config(tmp_path)
    index = _real_index()

    def index_loader(_path: Path) -> HybridIndex:
        calls.append("index")
        return index

    def encoder_factory(_config: AppConfig) -> _RealEncoder:
        calls.append("encoder")
        return _RealEncoder()

    def reranker_factory(_config: AppConfig) -> _RealReranker:
        calls.append("reranker")
        return _RealReranker()

    def scorer_factory(_reranker: object) -> _RealScorer:
        calls.append("scorer")
        return _RealScorer()

    def tool_loader(_root: Path) -> ToolRegistry:
        calls.append("tools")
        return ToolRegistry.from_directory(RECORDS_ROOT)

    loaded = load_runtime_index(config, index_loader=index_loader)
    retrieval = build_runtime_retrieval(
        config,
        loaded,
        encoder_factory=encoder_factory,
    )
    reranking = build_runtime_reranking(
        config,
        reranker_factory=reranker_factory,
        support_scorer_factory=scorer_factory,
    )
    tools = load_runtime_tools(RECORDS_ROOT, tool_loader=tool_loader)
    shared = assemble_shared_runtime_components(
        config,
        retrieval=retrieval,
        reranking=reranking,
        tools=tools,
    )

    assert calls == ["index", "encoder", "reranker", "scorer", "tools"]
    assert shared.index is index
    assert shared.encoder is retrieval.encoder
    assert shared.retriever is retrieval.retriever
    assert shared.reranker is reranking.reranker
    assert shared.support_scorer is reranking.support_scorer
    assert shared.gate is reranking.gate
    assert shared.validator is reranking.validator
    assert shared.tools is tools
    assert shared.index_builder is not None


def test_shared_runtime_components_hold_no_generator_or_run_state() -> None:
    names = {item.name for item in fields(SharedRuntimeComponents)}

    assert names == {
        "config",
        "index",
        "encoder",
        "retriever",
        "reranker",
        "support_scorer",
        "gate",
        "validator",
        "tools",
        "index_builder",
    }
    assert not any("generator" in name for name in names)
    assert not any("authorization" in name for name in names)


def test_assemble_shared_runtime_components_performs_no_io(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert not config.index_dir.exists()

    shared = assemble_shared_runtime_components(
        config,
        retrieval=RetrievalRuntimeComponents(
            index=_real_index(),
            encoder=_RealEncoder(),
            retriever=build_runtime_retrieval(
                config,
                _real_index(),
                encoder_factory=lambda _config: _RealEncoder(),
            ).retriever,
        ),
        reranking=RerankingRuntimeComponents(
            reranker=_RealReranker(),
            support_scorer=_RealScorer(),
            gate=EvidenceGate(min_score=0.5),
            validator=GroundingValidator(
                _RealScorer(),
                min_claim_support_score=0.5,
            ),
        ),
        tools=ToolRegistry.from_directory(RECORDS_ROOT),
    )

    assert shared.config is config
    assert not config.index_dir.exists()


def test_two_bindings_share_heavy_components_but_not_run_state(
    tmp_path: Path,
) -> None:
    shared = _shared(tmp_path)
    template = _local_template(shared)
    first_generator = _RealGenerator()
    second_generator = _RealGenerator()
    first_token = CancellationToken()
    second_token = CancellationToken()

    first = template.bind(
        generator_backend=first_generator,
        trace_factory=lambda: TraceRecorder("run-1"),
        cancellation=first_token,
    )
    second = template.bind(
        generator_backend=second_generator,
        trace_factory=lambda: TraceRecorder("run-2"),
        cancellation=second_token,
    )

    assert first is not second
    assert first.rag_service is not second.rag_service
    assert first.agent_service is not second.agent_service

    first_rag = first.rag_service
    second_rag = second.rag_service
    assert first_rag is not None and second_rag is not None
    assert first_rag._retriever is shared.retriever
    assert second_rag._retriever is shared.retriever
    assert first_rag._reranker is shared.reranker
    assert first_rag._gate is shared.gate
    assert first_rag._validator is shared.validator
    assert first_rag._generator._backend is first_generator
    assert second_rag._generator._backend is second_generator
    assert first_rag._cancellation is first_token
    assert second_rag._cancellation is second_token
    assert first.agent_service._tools is shared.tools
    assert first.rag_service._trace_factory().run_id == "run-1"
    assert second.rag_service._trace_factory().run_id == "run-2"


@pytest.mark.asyncio
async def test_bound_applications_do_not_share_cancellation_state(
    tmp_path: Path,
) -> None:
    shared = _shared(tmp_path)
    template = _local_template(shared)
    first_token = CancellationToken()
    second_token = CancellationToken()
    first = template.bind(
        generator_backend=_RealGenerator(),
        trace_factory=lambda: TraceRecorder("run-1"),
        cancellation=first_token,
    )
    second = template.bind(
        generator_backend=_RealGenerator(),
        trace_factory=lambda: TraceRecorder("run-2"),
        cancellation=second_token,
    )

    first_token.cancel()

    assert first.rag_service is not None and second.rag_service is not None
    with pytest.raises(RunCancelled):
        await first.rag_service.ask("question")
    assert second_token.cancelled is False


def test_bound_application_exposes_rag_and_agent_without_panel_extras(
    tmp_path: Path,
) -> None:
    template = _local_template(_shared(tmp_path))

    application = template.bind(
        generator_backend=_RealGenerator(),
        trace_factory=lambda: TraceRecorder("run-1"),
        cancellation=CancellationToken(),
    )

    assert application.rag_service is not None
    assert application.agent_service is not None
    assert application.evaluator is None
    assert application.diagnostics is None
    assert application.generator_kind == "local"
    assert application.provider_name is None
    assert application.fallback_local_enabled is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"generator_kind": "hybrid"}, "generator_kind must be local or online"),
        ({"provider_name": "relay"}, "local template cannot bind a provider"),
        (
            {"provider_fingerprint": "fp"},
            "local template cannot bind a profile fingerprint",
        ),
        ({"fallback_local": True}, "local template cannot enable fallback"),
        (
            {"generator_kind": "online"},
            "online template requires a provider name",
        ),
        (
            {"generator_kind": "online", "provider_name": "relay"},
            "online template requires a profile fingerprint",
        ),
    ],
)
def test_template_rejects_inconsistent_selection(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    settings: dict[str, object] = {
        "generator_kind": "local",
        "provider_name": None,
        "provider_fingerprint": None,
        "fallback_local": False,
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        ApplicationTemplate(shared=_shared(tmp_path), **settings)  # type: ignore[arg-type]


def test_template_bind_rejects_a_fake_production_generator(tmp_path: Path) -> None:
    template = _local_template(_shared(tmp_path))

    with pytest.raises(ConfigurationError, match="generator"):
        template.bind(
            generator_backend=_FakeGenerator(),
            trace_factory=lambda: TraceRecorder("run-1"),
            cancellation=CancellationToken(),
        )


@pytest.mark.asyncio
async def test_aclose_runtime_components_closes_unique_owned_resources_once(
    tmp_path: Path,
) -> None:
    encoder = _RealEncoder()
    reranker = _RealReranker()
    shared = _shared(
        tmp_path,
        encoder=encoder,
        reranker=reranker,
        support_scorer=reranker,
    )
    main_thread = threading.get_ident()

    await aclose_runtime_components(shared)

    assert encoder.aclose_calls == 1
    assert reranker.close_calls == 1
    assert reranker.close_thread is not None
    assert reranker.close_thread != main_thread


@pytest.mark.asyncio
async def test_aclose_runtime_components_ignores_components_without_a_hook(
    tmp_path: Path,
) -> None:
    shared = _shared(tmp_path)

    await aclose_runtime_components(shared)


class _ScriptedPlannerBackend:
    backend_kind = "fake"

    def __init__(self, payloads: list[object]) -> None:
        self._payloads = list(payloads)
        self.calls = 0

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        del request
        self.calls += 1
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload  # type: ignore[return-value]


def test_preflight_request_formula_matches_the_documented_worst_case() -> None:
    from enterprise_rag.panel.runtime import agent_max_requests, rag_max_requests

    assert rag_max_requests() == 2
    assert agent_max_requests(6) == 8
    with pytest.raises(ValueError):
        agent_max_requests(0)


@pytest.mark.asyncio
async def test_agent_planner_needs_no_more_requests_than_the_run_preflight() -> None:
    """Bind the run-level request cap to the planner's real control flow.

    One attempt per action, plus one final-answer protocol retry, plus one
    grounding repair. If ``AgentPlanner`` ever gains another attempt, the
    observed call count exceeds the preflighted cap and this test fails.
    """

    from enterprise_rag.errors import GenerationProtocolError
    from enterprise_rag.generation import AgentPlanner
    from enterprise_rag.models import Observation, ValidationIssue
    from enterprise_rag.panel.runtime import agent_max_requests

    max_actions = 6
    action_payload = {"action": "get_employee_profile", "employee_id": "EMP-003"}
    draft_payload = {
        "answer": "grounded answer",
        "claims": [
            {
                "text": "grounded answer",
                "support_ids": ["policy.md::0000"],
                "supporting_quotes": ["quoted evidence"],
            }
        ],
    }
    # The finish path parses its payload as a draft, and its protocol retry parses
    # the retry payload as a draft too, so both entries below are draft payloads.
    backend = _ScriptedPlannerBackend(
        [action_payload] * (max_actions - 1)
        + [GenerationProtocolError("unparseable finish"), draft_payload]
        + [draft_payload]
    )
    planner = AgentPlanner(backend)
    observation = Observation(
        "obs:search_knowledge:abc",
        "search_knowledge",
        "found",
        {"filename": "policy.md", "retrieval_rank": 1},
        "supporting text",
    )

    for _ in range(max_actions - 1):
        action = await planner.next_action("explain the policy", ())
        assert action.name == "get_employee_profile"

    finish_action = await planner.next_action("explain the policy", (observation,))
    assert finish_action.name == "finish"
    assert finish_action.finish_draft is not None
    await planner.repair_answer(
        "explain the policy",
        (observation,),
        finish_action.finish_draft,
        (ValidationIssue("quote_not_found", 0, "quote missing"),),
    )

    assert backend.calls == agent_max_requests(max_actions)
