from collections.abc import Mapping

import pytest


@pytest.mark.asyncio
async def test_retriever_and_reranker_ports_accept_structural_implementations() -> None:
    from enterprise_rag.execution_trace import TraceScope
    from enterprise_rag.models import RerankedChunk, RetrievedChunk
    from enterprise_rag.ports import RerankerPort, RetrieverPort

    class ExampleRetriever:
        async def retrieve(
            self,
            question: str,
            top_k: int,
            *,
            trace_scope: TraceScope | None = None,
        ) -> tuple[RetrievedChunk, ...]:
            assert question
            assert top_k > 0
            assert trace_scope is None
            return ()

    class ExampleReranker:
        async def rerank(
            self,
            question: str,
            candidates: tuple[RetrievedChunk, ...],
            top_n: int,
        ) -> tuple[RerankedChunk, ...]:
            assert question
            assert candidates == ()
            assert top_n > 0
            return ()

    retriever: RetrieverPort = ExampleRetriever()
    reranker: RerankerPort = ExampleReranker()

    assert isinstance(retriever, RetrieverPort)
    assert isinstance(reranker, RerankerPort)
    assert await retriever.retrieve("What is the hotel cap?", 20) == ()
    assert await reranker.rerank("What is the hotel cap?", (), 5) == ()


@pytest.mark.asyncio
async def test_structured_generator_port_uses_request_and_read_only_mapping() -> None:
    from enterprise_rag.models import StructuredGenerationRequest
    from enterprise_rag.ports import GeneratorPort, StructuredGeneratorPort

    class ExampleGenerator:
        async def generate_json(
            self,
            request: StructuredGenerationRequest,
        ) -> Mapping[str, object]:
            assert request.max_output_tokens == 128
            return {"answer": "CNY 600"}

    class GenerateOnly:
        async def generate(
            self,
            request: StructuredGenerationRequest,
        ) -> Mapping[str, object]:
            return {"request": request}

    generator: StructuredGeneratorPort = ExampleGenerator()
    request = StructuredGenerationRequest(
        messages=({"role": "user", "content": "What is the hotel cap?"},),
        schema={"type": "object"},
        max_output_tokens=128,
    )

    assert isinstance(generator, StructuredGeneratorPort)
    assert not isinstance(GenerateOnly(), StructuredGeneratorPort)
    assert GeneratorPort is StructuredGeneratorPort
    assert await generator.generate_json(request) == {"answer": "CNY 600"}
