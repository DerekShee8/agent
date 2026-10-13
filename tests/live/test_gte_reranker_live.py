from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from enterprise_rag.config import AppConfig
from enterprise_rag.grounding import GteClaimSupportScorer
from enterprise_rag.ingestion import chunk_documents, load_documents
from enterprise_rag.models import RetrievedChunk
from enterprise_rag.reranking import GteReranker


@pytest.mark.live
@pytest.mark.asyncio
async def test_real_gte_reranker_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    config = AppConfig()
    reranker = GteReranker.from_config(config)
    chunks = chunk_documents(
        load_documents(Path("data/sample_docs")),
        config.chunk_size,
        config.chunk_overlap,
    )
    candidates = tuple(
        RetrievedChunk(
            text=chunk.text,
            filename=chunk.filename,
            chunk_id=chunk.chunk_id,
            dense_score=None,
            dense_rank=None,
            sparse_score=None,
            sparse_rank=None,
            retrieval_score=1.0 / rank,
            retrieval_rank=rank,
        )
        for rank, chunk in enumerate(chunks, start=1)
    )
    cases = (
        (
            "POLICY-TRAVEL-04 §3.2 规定 P4 去 A 类城市的住宿上限是多少?",
            "travel-policy.md::0000",
            2,
        ),
        (
            "一笔交通费超过五十元, 报销时必须准备哪种凭证?",
            "expense-policy.md::0000",
            2,
        ),
        (
            "For a P4 employee traveling to a non-tier-A city, 每晚住宿上限是多少?",
            "travel-policy.md::0001",
            2,
        ),
    )
    observed: dict[str, int] = {}
    for question, expected_chunk_id, maximum_rank in cases:
        results = await reranker.rerank(question, candidates, top_n=5)
        assert [item.rerank_rank for item in results] == list(
            range(1, len(results) + 1)
        )
        assert all(math.isfinite(item.rerank_score) for item in results)
        result_by_id = {item.candidate.chunk_id: item for item in results}
        ranks = ", ".join(
            f"{item.candidate.chunk_id}={item.rerank_rank}/{item.rerank_score:.6f}"
            for item in results
        )
        assert expected_chunk_id in result_by_id, ranks
        expected = result_by_id[expected_chunk_id]
        assert expected.rerank_rank <= maximum_rank, ranks
        assert (
            expected.candidate.filename == expected_chunk_id.split("::", maxsplit=1)[0]
        )
        observed[expected_chunk_id] = expected.rerank_rank

    scorer = GteClaimSupportScorer(reranker)
    support_scores = await scorer.score(
        "P4 员工前往 A 类城市每晚住宿上限为 CNY 600。",
        (candidates[-2].text,),
    )
    assert len(support_scores) == 1
    assert math.isfinite(support_scores[0])
    assert scorer.model_identity == reranker.model_identity

    import torch
    import transformers

    assert transformers.__version__ == "4.39.1"
    print(
        json.dumps(
            {
                "model_id": reranker.model_id,
                "model_revision": reranker.model_revision,
                "code_revision": reranker.code_revision,
                "transformers": transformers.__version__,
                "torch": torch.__version__,
                "device": reranker.device,
                "dtype": reranker.dtype,
                "ranks": observed,
                "shared_claim_scorer": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
