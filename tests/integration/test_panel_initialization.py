"""Staged panel initialization: real durations, shared work, and clean failure.

Counting factories prove each heavy component is created once, blocking loaders
run off the event loop, and a failing stage closes exactly what it created.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from enterprise_rag.agent_tools import ToolRegistry
from enterprise_rag.application import RetrievalRuntimeComponents
from enterprise_rag.config import AppConfig
from enterprise_rag.errors import ConfigurationError, RerankerUnavailableError
from enterprise_rag.execution_control import CancellationToken
from enterprise_rag.index_store import HybridIndex, IndexManifest
from enterprise_rag.ingestion import DocumentChunk, corpus_digest
from enterprise_rag.models import (
    RerankedChunk,
    RetrievedChunk,
    StructuredGenerationRequest,
)
from enterprise_rag.panel.contracts import RunRequest, RunSpec
from enterprise_rag.panel.runtime import (
    INITIALIZATION_STAGES,
    PanelRuntime,
)
from enterprise_rag.ports import EncodedBatch

RECORDS_ROOT = Path("data/sample_records")


class CountingEncoder:
    backend_kind = "real"
    model_id = "test/gte"
    model_revision = "a" * 40
    code_revision = "b" * 40

    def __init__(self) -> None:
        self.close_calls = 0

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        return EncodedBatch(
            dense=tuple((1.0, 0.0) for _ in texts),
            sparse=tuple(((101, 1.0),) for _ in texts),
        )

    async def aclose(self) -> None:
        self.close_calls += 1


class CountingReranker:
    backend_kind = "real"

    def __init__(self) -> None:
        self.close_calls = 0

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


class CountingScorer:
    backend_kind = "real"

    async def score(
        self,
        claim: str,
        support_texts: tuple[str, ...],
    ) -> tuple[float, ...]:
        del claim
        return tuple(0.9 for _ in support_texts)


class CountingLocalGenerator:
    backend_kind = "real"

    def __init__(self) -> None:
        self.close_calls = 0

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> dict[str, object]:
        del request
        return {"ok": True}

    def close(self) -> None:
        self.close_calls += 1


def config(tmp_path: Path) -> AppConfig:
    cache = tmp_path / "model-cache"
    return AppConfig(
        model_cache_dir=cache,
        index_dir=tmp_path / "index",
        local_model_path=cache / "model.gguf",
    )


def real_index() -> HybridIndex:
    chunks = (
        DocumentChunk("P4 员工住宿上限 CNY 600。", "policy.md", "policy.md::0000"),
    )
    return HybridIndex(
        manifest=IndexManifest(
            schema_version=1,
            backend_kind="real",
            source_digest=corpus_digest(chunks),
            chunk_size=384,
            chunk_overlap=48,
            model_id=CountingEncoder.model_id,
            model_revision=CountingEncoder.model_revision,
            code_revision=CountingEncoder.code_revision,
            dimension=2,
            row_count=1,
            built_at="2026-10-12T00:00:00Z",
        ),
        chunks=chunks,
        dense=((1.0, 0.0),),
        sparse=(((101, 1.0),),),
    )


class Harness:
    def __init__(self, tmp_path: Path, *, fail_reranker: bool = False) -> None:
        self.calls: list[str] = []
        self.threads: dict[str, int] = {}
        self.index = real_index()
        self.encoder = CountingEncoder()
        self.reranker = CountingReranker()
        self.local = CountingLocalGenerator()
        self.fail_reranker = fail_reranker
        self.runtime = PanelRuntime(
            config(tmp_path),
            index_loader=self.load_index,
            encoder_factory=self.build_encoder,
            reranker_factory=self.build_reranker,
            support_scorer_factory=lambda _reranker: CountingScorer(),
            tool_loader=self.load_tools,
            local_factory=self.build_local,
            profile_loader=lambda _path: (),
            records_root=RECORDS_ROOT,
        )

    def _note(self, name: str) -> None:
        self.calls.append(name)
        self.threads[name] = threading.get_ident()

    def load_index(self, _path: Path) -> HybridIndex:
        self._note("index")
        return self.index

    def build_encoder(self, _config: AppConfig) -> CountingEncoder:
        self._note("encoder")
        return self.encoder

    def build_reranker(self, _config: AppConfig) -> CountingReranker:
        self._note("reranker")
        if self.fail_reranker:
            raise RerankerUnavailableError("reranker unavailable")
        return self.reranker

    def load_tools(self, _root: Path) -> ToolRegistry:
        self._note("tools")
        return ToolRegistry.from_directory(RECORDS_ROOT)

    def build_local(self, _config: AppConfig) -> CountingLocalGenerator:
        self._note("local")
        return self.local


@pytest.mark.asyncio
async def test_initialization_reports_the_six_stages_in_order(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    seen: list[str] = []

    stages = await harness.runtime.initialize(
        lambda stage: seen.append(f"{stage.name}:{stage.status}")
    )

    assert [stage.name for stage in stages] == list(INITIALIZATION_STAGES)
    assert all(stage.status == "succeeded" for stage in stages)
    assert all(stage.duration_ms >= 0 for stage in stages)
    assert seen == [f"{name}:succeeded" for name in INITIALIZATION_STAGES]
    assert harness.runtime.state == "ready"
    assert harness.runtime.runtime_ready_seconds is not None
    assert harness.calls.count("index") == 1
    assert harness.calls.count("encoder") == 1
    assert harness.calls.count("reranker") == 1
    assert harness.calls.count("tools") == 1
    assert harness.calls.count("local") == 1


@pytest.mark.asyncio
async def test_blocking_loaders_run_off_the_event_loop_thread(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    loop_thread = threading.get_ident()

    await harness.runtime.initialize()

    for name in ("index", "encoder", "reranker", "tools", "local"):
        assert harness.threads[name] != loop_thread, name


@pytest.mark.asyncio
async def test_two_concurrent_initializers_share_one_load(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = harness.load_index

    def gated_index_loader(path: Path) -> HybridIndex:
        index = original(path)
        started.set()
        assert release.wait(timeout=10.0)
        return index

    harness.runtime._index_loader = gated_index_loader
    first = asyncio.create_task(harness.runtime.initialize())
    assert await asyncio.to_thread(started.wait, 10.0)
    second = asyncio.create_task(harness.runtime.initialize())
    release.set()

    stages_a, stages_b = await asyncio.gather(first, second)

    assert stages_a == stages_b
    assert harness.calls.count("index") == 1
    assert harness.calls.count("local") == 1


@pytest.mark.asyncio
async def test_unavailable_profiles_stay_a_warning(tmp_path: Path) -> None:
    def failing_profile_loader(_path: Path) -> tuple[()]:
        raise ConfigurationError("profiles unavailable")

    harness = Harness(tmp_path)
    harness.runtime._profile_loader = failing_profile_loader

    stages = await harness.runtime.initialize()

    checking = next(stage for stage in stages if stage.name == "CHECKING_CONFIG")
    assert checking.status == "succeeded"
    assert checking.warnings == ("online provider profiles are unavailable",)
    assert harness.runtime.state == "ready"


@pytest.mark.asyncio
async def test_a_failing_stage_disables_run_and_closes_created_products_once(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, fail_reranker=True)

    stages = await harness.runtime.initialize()

    failed = [stage for stage in stages if stage.status == "failed"]
    assert [stage.name for stage in failed] == ["LOADING_RERANKER"]
    assert failed[0].error_category == "reranker_unavailable"
    assert failed[0].public_error is not None
    assert harness.runtime.state == "initialization_failed"
    assert harness.encoder.close_calls == 1
    assert harness.reranker.close_calls == 0
    assert harness.local.close_calls == 0

    spec = RunSpec(
        run_id="run-1",
        created_at=0.0,
        mode="rag",
        scenario_id="scenario",
        question_or_task="question",
        generator="local",
        provider_name=None,
        provider_fingerprint=None,
        fallback_local=False,
        online_authorization=None,
    )
    with pytest.raises(ConfigurationError, match="not ready"):
        await harness.runtime.application_for(spec, CancellationToken())

    await harness.runtime.aclose()
    assert harness.encoder.close_calls == 1


@pytest.mark.asyncio
async def test_shutdown_after_partial_failure_closes_every_product_once(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, fail_reranker=True)
    await harness.runtime.initialize()

    await harness.runtime.aclose()
    await harness.runtime.aclose()

    assert harness.encoder.close_calls == 1
    assert harness.local.close_calls == 0


@pytest.mark.asyncio
async def test_application_for_reuses_one_template_and_binds_a_new_application(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    await harness.runtime.initialize()
    runtime = harness.runtime

    def local_spec(run_id: str) -> RunSpec:
        return RunSpec(
            run_id=run_id,
            created_at=0.0,
            mode="rag",
            scenario_id="scenario",
            question_or_task="question",
            generator="local",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
            online_authorization=None,
        )

    first = await runtime.application_for(local_spec("run-1"), CancellationToken())
    second = await runtime.application_for(local_spec("run-2"), CancellationToken())

    assert first is not second
    assert first.rag_service is not second.rag_service
    assert first.rag_service is not None and second.rag_service is not None
    assert first.rag_service._retriever is second.rag_service._retriever
    assert len(runtime._templates) == 1
    assert harness.calls.count("local") == 1


@pytest.mark.asyncio
async def test_ready_stage_reuses_the_shared_retrieval_products(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)

    stages = await harness.runtime.initialize()

    assert [stage.name for stage in stages][-1] == "READY"
    shared = harness.runtime._shared
    assert shared is not None
    assert isinstance(shared.retriever, object)
    assert shared.encoder is harness.encoder
    assert shared.reranker is harness.reranker
    assert shared.tools is not None
    assert isinstance(shared.index_builder, object)
    assert RetrievalRuntimeComponents.__name__ == "RetrievalRuntimeComponents"


@pytest.mark.asyncio
async def test_preflight_needs_a_provider_and_a_ready_runtime(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    await harness.runtime.initialize()

    with pytest.raises(ConfigurationError):
        await harness.runtime.registry.preflight(
            RunRequest(
                run_id="run-1",
                created_at=0.0,
                mode="rag",
                scenario_id="scenario",
                question_or_task="question",
                generator="online",
                provider_name="relay",
                provider_fingerprint="fp",
                fallback_local=False,
            )
        )
