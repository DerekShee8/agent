from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_rag.config import (
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_REVISION,
    RERANKER_IMPLEMENTATION_REVISION,
    AppConfig,
)
from enterprise_rag.index_store import build_index, load_index
from enterprise_rag.ingestion import chunk_documents, corpus_digest, load_documents
from enterprise_rag.retrieval import GteHybridEncoder, HybridRetriever


@pytest.mark.live
@pytest.mark.asyncio
async def test_real_gte_hybrid_retriever_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    config = AppConfig(index_dir=tmp_path / "index")
    document_root = Path("data/sample_docs")
    encoder = GteHybridEncoder.from_config(config)

    manifest = await build_index(
        document_root,
        config.index_dir,
        encoder,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )
    index = load_index(config.index_dir)

    expected_digest = corpus_digest(
        chunk_documents(
            load_documents(document_root),
            config.chunk_size,
            config.chunk_overlap,
        )
    )
    assert manifest.backend_kind == "real"
    assert manifest.model_id == EMBEDDING_MODEL_ID
    assert manifest.model_revision == EMBEDDING_MODEL_REVISION
    assert manifest.code_revision == RERANKER_IMPLEMENTATION_REVISION
    assert manifest.source_digest == expected_digest
    assert manifest.row_count == 6
    assert manifest.dimension == 768

    retriever = HybridRetriever(index, encoder, rrf_k=config.rrf_k)
    cases = (
        (
            "POLICY-TRAVEL-04 §1.1 适用范围",
            "travel-policy.md::0000",
            2,
        ),
        (
            "一笔交通支出超过五十元, 提交报销时必须准备哪种凭证?",
            "expense-policy.md::0000",
            3,
        ),
        (
            "For a P4 employee traveling to a non-tier-A city, 每晚住宿上限是多少?",
            "travel-policy.md::0001",
            3,
        ),
    )
    observed: dict[str, int] = {}
    for question, expected_chunk_id, maximum_rank in cases:
        results = await retriever.retrieve(question, top_k=config.retrieve_k)
        assert [item.retrieval_rank for item in results] == list(
            range(1, len(results) + 1)
        )
        result_by_id = {item.chunk_id: item for item in results}
        ranks = ", ".join(
            f"{item.chunk_id}={item.retrieval_rank}/"
            f"d{item.dense_rank}/s{item.sparse_rank}"
            for item in results
        )
        assert expected_chunk_id in result_by_id, ranks
        expected = result_by_id[expected_chunk_id]
        assert expected.retrieval_rank <= maximum_rank, ranks
        assert expected.filename == expected_chunk_id.split("::", maxsplit=1)[0]
        observed[expected_chunk_id] = expected.retrieval_rank

    import torch
    import transformers

    assert transformers.__version__ == "4.39.1"
    print(
        json.dumps(
            {
                "model_id": encoder.model_id,
                "model_revision": encoder.model_revision,
                "code_revision": encoder.code_revision,
                "transformers": transformers.__version__,
                "torch": torch.__version__,
                "device": encoder.device,
                "dtype": encoder.dtype,
                "ranks": observed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
