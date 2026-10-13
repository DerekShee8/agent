from __future__ import annotations

import inspect
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from enterprise_rag.application import RagAskRequest, build_application
from enterprise_rag.config import LOCAL_MODEL_FILENAME, AppConfig
from enterprise_rag.errors import (
    ConfigurationError,
    IndexUnavailableError,
    LocalModelMissingError,
)
from enterprise_rag.index_store import HybridIndex, IndexManifest
from enterprise_rag.ingestion import DocumentChunk, corpus_digest
from enterprise_rag.models import (
    RerankedChunk,
    RetrievedChunk,
    StructuredGenerationRequest,
)
from enterprise_rag.ports import EncodedBatch


class RealEncoderDouble:
    backend_kind = "real"
    model_id = "test/real-encoder"
    model_revision = "a" * 40
    code_revision = "b" * 40

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        return EncodedBatch(
            dense=tuple((1.0, 0.0) for _ in texts),
            sparse=tuple(((101, 1.0),) for _ in texts),
        )


class RealRerankerDouble:
    backend_kind = "real"

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


class RealGeneratorDouble:
    backend_kind = "real"

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        del request
        return {
            "answer": "酒店住宿上限为 CNY 600。",
            "claims": [
                {
                    "text": "酒店住宿上限为 CNY 600。",
                    "support_ids": ["policy.md::0000"],
                    "supporting_quotes": ["住宿上限为 CNY 600"],
                }
            ],
        }


class RealSupportScorerDouble:
    backend_kind = "real"

    async def score(
        self,
        claim: str,
        support_texts: tuple[str, ...],
    ) -> tuple[float, ...]:
        del claim
        return tuple(0.9 for _ in support_texts)


class FakeGeneratorDouble(RealGeneratorDouble):
    backend_kind = "fake"


def test_production_composition_constructs_real_backends_once(tmp_path: Path) -> None:
    calls: list[str] = []
    index = _real_index()

    def load(_path: Path) -> HybridIndex:
        calls.append("index")
        return index

    def encoder(_config: AppConfig) -> RealEncoderDouble:
        calls.append("encoder")
        return RealEncoderDouble()

    def reranker(_config: AppConfig) -> RealRerankerDouble:
        calls.append("reranker")
        return RealRerankerDouble()

    def generator(_config: AppConfig) -> RealGeneratorDouble:
        calls.append("generator")
        return RealGeneratorDouble()

    def scorer(_reranker: object) -> RealSupportScorerDouble:
        calls.append("scorer")
        return RealSupportScorerDouble()

    application = build_application(
        _config(tmp_path),
        index_loader=load,
        encoder_factory=encoder,
        reranker_factory=reranker,
        generator_factory=generator,
        support_scorer_factory=scorer,
        compose_agent=True,
        records_root=Path("data/sample_records"),
    )

    assert application.rag_service is not None
    assert application.agent_service is not None
    assert application.rag_service.backend_kinds == ("real", "real", "real")
    assert calls == ["index", "encoder", "reranker", "generator", "scorer"]


def test_production_composition_rejects_fake_backend(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="generator"):
        build_application(
            _config(tmp_path),
            index_loader=lambda _path: _real_index(),
            encoder_factory=lambda _config: RealEncoderDouble(),
            reranker_factory=lambda _config: RealRerankerDouble(),
            generator_factory=lambda _config: FakeGeneratorDouble(),
            support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
        )


def test_production_composition_fails_closed_when_index_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(IndexUnavailableError):
        build_application(_config(tmp_path))


def test_production_composition_fails_closed_when_model_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(LocalModelMissingError):
        build_application(
            _config(tmp_path),
            index_loader=lambda _path: _real_index(),
            encoder_factory=lambda _config: RealEncoderDouble(),
            reranker_factory=lambda _config: RealRerankerDouble(),
            support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
        )


def test_legacy_facade_signature_keeps_every_original_parameter() -> None:
    parameters = inspect.signature(build_application).parameters
    original_defaults: dict[str, object] = {
        "config": None,
        "encoder_factory": None,
        "reranker_factory": None,
        "generator_factory": None,
        "online_generator_factory": None,
        "support_scorer_factory": None,
        "index_loader": None,  # compared by name only; the default is a function
        "profile_loader": None,
        "compose_rag": True,
        "compose_agent": False,
        "compose_evaluation": False,
        "compose_diagnostics": False,
        "evaluation_layer": None,
        "records_root": Path("data/sample_records"),
        "knowledge_cases_path": Path("evals/knowledge.jsonl"),
        "agent_cases_path": Path("evals/agent.jsonl"),
        "generator_choice": None,
        "provider_name": None,
        "fallback_local": False,
        "provider_profiles_path": Path("config/providers.toml"),
    }

    for name, default in original_defaults.items():
        assert name in parameters, name
        if name in {"index_loader", "profile_loader"}:
            assert parameters[name].default is not inspect.Parameter.empty
            continue
        assert parameters[name].default == default

    added = set(parameters) - set(original_defaults)
    assert added == {"trace_factory", "cancellation"}
    for name in added:
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


@pytest.mark.asyncio
async def test_legacy_facade_still_fails_closed_on_a_mismatched_request(
    tmp_path: Path,
) -> None:
    application = build_application(
        _config(tmp_path),
        index_loader=lambda _path: _real_index(),
        encoder_factory=lambda _config: RealEncoderDouble(),
        reranker_factory=lambda _config: RealRerankerDouble(),
        generator_factory=lambda _config: RealGeneratorDouble(),
        support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
        generator_choice="local",
    )

    mismatched = await application.ask_rag(
        RagAskRequest("question", "online", provider="relay-demo")
    )

    assert mismatched.status == "failed"
    assert mismatched.error_category == "configuration"


def test_cli_evaluation_and_doctor_composition_never_touch_panel_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_modules = (
        "enterprise_rag.panel",
        "enterprise_rag.panel.app",
        "enterprise_rag.panel.runtime",
    )
    for module in panel_modules:
        monkeypatch.delitem(sys.modules, module, raising=False)

    application = build_application(
        _config(tmp_path),
        index_loader=lambda _path: _real_index(),
        encoder_factory=lambda _config: RealEncoderDouble(),
        reranker_factory=lambda _config: RealRerankerDouble(),
        generator_factory=lambda _config: RealGeneratorDouble(),
        support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
        compose_agent=True,
        compose_evaluation=True,
        evaluation_layer="all",
        compose_diagnostics=True,
        knowledge_cases_path=Path("evals/knowledge.jsonl"),
        agent_cases_path=Path("evals/agent.jsonl"),
    )

    assert application.rag_service is not None
    assert application.agent_service is not None
    assert application.evaluator is not None
    assert application.diagnostics is not None
    for module in panel_modules:
        assert module not in sys.modules


def _config(tmp_path: Path) -> AppConfig:
    model_cache = tmp_path / "model-cache"
    return AppConfig(
        model_cache_dir=model_cache,
        index_dir=tmp_path / "index",
        local_model_path=model_cache / LOCAL_MODEL_FILENAME,
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
            model_id=RealEncoderDouble.model_id,
            model_revision=RealEncoderDouble.model_revision,
            code_revision=RealEncoderDouble.code_revision,
            dimension=2,
            row_count=1,
            built_at="2026-10-10T00:00:00Z",
        ),
        chunks=chunks,
        dense=((1.0, 0.0),),
        sparse=(((101, 1.0),),),
    )
