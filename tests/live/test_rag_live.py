from __future__ import annotations

import json
import socket
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from enterprise_rag.application import RagAskRequest, build_application
from enterprise_rag.config import AppConfig
from enterprise_rag.generation import LlamaCppGenerator
from enterprise_rag.models import StructuredGenerationRequest


class InvalidOnceThenLocalGenerator:
    backend_kind = "real"

    def __init__(self, delegate: LlamaCppGenerator) -> None:
        self._delegate = delegate
        self._inject_invalid = False
        self.call_count = 0
        self.injected_count = 0

    def inject_invalid_once(self) -> None:
        self._inject_invalid = True

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        self.call_count += 1
        if self._inject_invalid:
            self._inject_invalid = False
            self.injected_count += 1
            return {
                "answer": "P4 员工每晚酒店住宿上限为 CNY 999。",
                "claims": [
                    {
                        "text": "P4 员工每晚酒店住宿上限为 CNY 999。",
                        "support_ids": ["travel-policy.md::0000"],
                        "supporting_quotes": ["住宿上限为 CNY 999"],
                    }
                ],
            }
        return await self._delegate.generate_json(request)


@pytest.mark.live
@pytest.mark.asyncio
async def test_real_rag_answers_refuses_before_generation_and_repairs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def block_network(
        _socket: socket.socket,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise AssertionError("real local RAG must not open a network socket")

    monkeypatch.setattr(socket.socket, "connect", block_network)
    config = AppConfig()
    holder: list[InvalidOnceThenLocalGenerator] = []

    def generator_factory(active_config: AppConfig) -> InvalidOnceThenLocalGenerator:
        generator = InvalidOnceThenLocalGenerator(
            LlamaCppGenerator.from_config(active_config)
        )
        holder.append(generator)
        return generator

    load_started = time.perf_counter()
    application = build_application(config, generator_factory=generator_factory)
    load_seconds = time.perf_counter() - load_started
    assert application.rag_service is not None
    assert application.rag_service.backend_kinds == ("real", "real", "real")
    assert len(holder) == 1
    generator = holder[0]

    question = "P4 员工去 A 类城市出差，每晚酒店住宿上限是多少？"  # noqa: RUF001
    answer_started = time.perf_counter()
    answered = await application.ask_rag(RagAskRequest(question, "local"))
    answer_seconds = time.perf_counter() - answer_started

    assert answered.status == "answered"
    assert answered.answer is not None and "600" in answered.answer
    assert {source.chunk_id for source in answered.sources} <= {
        "travel-policy.md::0000",
        "travel-policy.md::0001",
    }

    calls_before_refusal = generator.call_count
    refused = await application.ask_rag(
        RagAskRequest("火星办公室的宠物保险额度是多少？", "local")  # noqa: RUF001
    )
    assert refused.status == "refused"
    assert refused.refusal_reason == "insufficient_evidence"
    assert generator.call_count == calls_before_refusal
    assert [
        event.stage for event in refused.trace.events if event.parent_id is None
    ] == [
        "retrieval",
        "reranking",
        "evidence_gate",
    ]
    retrieval = next(
        event for event in refused.trace.events if event.stage == "retrieval"
    )
    assert [
        event.stage
        for event in refused.trace.events
        if event.parent_id == retrieval.event_id
    ] == ["dense", "sparse", "rrf_fusion"]

    generator.inject_invalid_once()
    repair_started = time.perf_counter()
    repaired = await application.ask_rag(RagAskRequest(question, "local"))
    repair_seconds = time.perf_counter() - repair_started

    assert generator.injected_count == 1
    assert (
        sum(event.stage == "repair_generation" for event in repaired.trace.events) == 1
    )
    assert repaired.status in {"answered", "refused"}
    if repaired.status == "answered":
        assert repaired.answer is not None and "600" in repaired.answer
        assert repaired.sources
    else:
        assert repaired.refusal_reason == "grounding_validation_failed"

    benchmark = {
        "answer_seconds": answer_seconds,
        "cold_pipeline_load_seconds": load_seconds,
        "forced_repair_seconds": repair_seconds,
        "generator_calls": generator.call_count,
        "repair_outcome": repaired.status,
    }
    output = Path(".local/benchmarks/task14-rag-live.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(benchmark, ensure_ascii=False, sort_keys=True))
