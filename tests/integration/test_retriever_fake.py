from __future__ import annotations

from dataclasses import replace

import pytest

from enterprise_rag.errors import RetrievalProtocolError, RetrievalUnavailableError
from enterprise_rag.index_store import HybridIndex, IndexManifest
from enterprise_rag.ingestion import DocumentChunk, corpus_digest
from enterprise_rag.ports import EncodedBatch, RetrieverPort
from enterprise_rag.retrieval import HybridRetriever


def make_index() -> HybridIndex:
    chunks = (
        DocumentChunk("dense first", "policy.md", "policy.md::0000"),
        DocumentChunk("both lanes", "policy.md", "policy.md::0001"),
        DocumentChunk("sparse first", "policy.md", "policy.md::0002"),
    )
    manifest = IndexManifest(
        schema_version=1,
        backend_kind="fake",
        source_digest=corpus_digest(chunks),
        chunk_size=384,
        chunk_overlap=48,
        model_id="fake-hybrid-encoder",
        model_revision="fake-revision",
        code_revision="fake-code-revision",
        dimension=2,
        row_count=3,
        built_at="2026-10-09T00:00:00Z",
    )
    return HybridIndex(
        manifest=manifest,
        chunks=chunks,
        dense=((1.0, 0.0), (0.8, 0.6), (0.0, 1.0)),
        sparse=(((20, 1.0),), ((10, 2.0),), ((10, 3.0),)),
    )


class FakeQueryEncoder:
    backend_kind = "fake"
    model_id = "fake-hybrid-encoder"
    model_revision = "fake-revision"
    code_revision = "fake-code-revision"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        self.calls.append(texts)
        return EncodedBatch(
            dense=((1.0, 0.0),),
            sparse=(((10, 1.0),),),
        )


class WrongRowCountEncoder(FakeQueryEncoder):
    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        self.calls.append(texts)
        return EncodedBatch(
            dense=((1.0, 0.0), (0.0, 1.0)),
            sparse=(((10, 1.0),), ((20, 1.0),)),
        )


class FailingEncoder(FakeQueryEncoder):
    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        self.calls.append(texts)
        raise OSError("encoder unavailable")


@pytest.mark.asyncio
async def test_hybrid_retriever_encodes_once_and_preserves_lane_metadata() -> None:
    encoder = FakeQueryEncoder()
    retriever = HybridRetriever(make_index(), encoder, rrf_k=60)

    results = await retriever.retrieve("hotel cap", top_k=2)

    assert isinstance(retriever, RetrieverPort)
    assert encoder.calls == [("hotel cap",)]
    assert [result.chunk_id for result in results] == [
        "policy.md::0001",
        "policy.md::0000",
    ]
    assert results[0].dense_score == pytest.approx(0.8)
    assert results[0].dense_rank == 2
    assert results[0].sparse_score == 2.0
    assert results[0].sparse_rank == 2
    assert results[0].retrieval_rank == 1
    assert results[1].sparse_score is None
    assert results[1].sparse_rank is None


@pytest.mark.asyncio
async def test_hybrid_retriever_limits_to_corpus_and_is_repeatable() -> None:
    retriever = HybridRetriever(make_index(), FakeQueryEncoder(), rrf_k=60)

    first = await retriever.retrieve("hotel cap", top_k=20)
    second = await retriever.retrieve("hotel cap", top_k=20)

    assert first == second
    assert len(first) == 3


@pytest.mark.asyncio
async def test_hybrid_retriever_rejects_query_protocol_mismatch() -> None:
    retriever = HybridRetriever(make_index(), WrongRowCountEncoder(), rrf_k=60)

    with pytest.raises(RetrievalProtocolError, match="one row"):
        await retriever.retrieve("hotel cap", top_k=2)


@pytest.mark.asyncio
async def test_hybrid_retriever_does_not_fallback_when_encoder_fails() -> None:
    retriever = HybridRetriever(make_index(), FailingEncoder(), rrf_k=60)

    with pytest.raises(RetrievalUnavailableError, match="encoder"):
        await retriever.retrieve("hotel cap", top_k=2)


@pytest.mark.asyncio
async def test_hybrid_retriever_rejects_index_encoder_identity_mismatch() -> None:
    index = make_index()
    wrong_manifest = replace(index.manifest, model_revision="different-revision")
    wrong_index = HybridIndex(
        manifest=wrong_manifest,
        chunks=index.chunks,
        dense=index.dense,
        sparse=index.sparse,
    )
    retriever = HybridRetriever(wrong_index, FakeQueryEncoder(), rrf_k=60)

    with pytest.raises(RetrievalProtocolError, match="identity"):
        await retriever.retrieve("hotel cap", top_k=2)

