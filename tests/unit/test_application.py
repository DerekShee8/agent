from __future__ import annotations

from pathlib import Path

import pytest

from enterprise_rag.application import (
    AgentRunRequest,
    Application,
    CommandOutcome,
    RagAskRequest,
    build_application,
)
from enterprise_rag.config import AppConfig
from enterprise_rag.index_store import load_index
from enterprise_rag.models import AgentResult
from enterprise_rag.ports import EncodedBatch


class CountingEncoder:
    backend_kind = "real"
    model_id = "test/gte"
    model_revision = "a" * 40
    code_revision = "b" * 40

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        self.calls.append(texts)
        return EncodedBatch(
            dense=tuple((1.0, 0.0) for _ in texts),
            sparse=tuple(((101, 1.0),) for _ in texts),
        )


class CountingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task: str) -> AgentResult:
        del task
        self.calls += 1
        return AgentResult.failed("protocol")


class StubIndexBuilder:
    async def build(self, document_root: Path) -> CommandOutcome:
        del document_root
        return CommandOutcome.succeeded({})


@pytest.mark.asyncio
async def test_agent_request_cannot_switch_composed_generator() -> None:
    agent = CountingAgent()
    application = Application(
        rag_service=None,
        generator_kind="local",
        agent_service=agent,
        index_builder=StubIndexBuilder(),
        evaluator=None,
        diagnostics=None,
    )

    result = await application.run_agent(
        AgentRunRequest("Explain CLAIM-1024", "online", provider="relay-demo")
    )

    assert result.status == "failed"
    assert result.error_category == "configuration"
    assert agent.calls == 0


@pytest.mark.asyncio
async def test_builder_only_composition_is_lazy_and_unfinished_services_fail_closed(
    tmp_path: Path,
) -> None:
    factory_calls: list[AppConfig] = []

    def factory(config: AppConfig) -> CountingEncoder:
        factory_calls.append(config)
        return CountingEncoder()

    config = _test_config(tmp_path)
    application = build_application(
        config,
        encoder_factory=factory,
        compose_rag=False,
    )

    rag_result = await application.ask_rag(RagAskRequest("question", "local"))
    diagnostics = await application.run_diagnostics()

    assert rag_result.status == "failed"
    assert rag_result.error_category == "configuration"
    assert diagnostics.status == "failed"
    assert diagnostics.error_category == "configuration"
    assert factory_calls == []


@pytest.mark.asyncio
async def test_real_index_builder_caches_encoder_and_reports_pinned_identity(
    tmp_path: Path,
) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "policy.md").write_text(
        "# POLICY-1\n\nEmployees must attach a receipt.",
        encoding="utf-8",
    )
    encoder = CountingEncoder()
    factory_calls: list[AppConfig] = []

    def factory(config: AppConfig) -> CountingEncoder:
        factory_calls.append(config)
        return encoder

    config = _test_config(tmp_path)
    application = build_application(
        config,
        encoder_factory=factory,
        compose_rag=False,
    )

    assert factory_calls == []
    outcome = await application.build_knowledge(documents)
    manifest = load_index(config.index_dir).manifest

    assert factory_calls == [config]
    assert len(encoder.calls) == 1
    assert outcome.status == "succeeded"
    assert dict(outcome.data) == {
        "backend_kind": "real",
        "model_id": "test/gte",
        "model_revision": "a" * 40,
        "code_revision": "b" * 40,
        "source_digest": manifest.source_digest,
        "row_count": 1,
        "index_dir": str(config.index_dir),
    }
    assert manifest.backend_kind == "real"
    assert manifest.model_id == "test/gte"


def _test_config(tmp_path: Path) -> AppConfig:
    cache_dir = tmp_path / "model-cache"
    return AppConfig(
        model_cache_dir=cache_dir,
        index_dir=tmp_path / "index",
        local_model_path=cache_dir / "model.gguf",
    )
