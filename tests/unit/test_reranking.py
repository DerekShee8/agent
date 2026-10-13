from __future__ import annotations

import math

import pytest

from enterprise_rag.reranking import reranked_from_scores
from tests.fakes import retrieved


def test_reranked_from_scores_orders_by_score_then_chunk_id() -> None:
    candidates = (
        retrieved("b", "second", rank=1),
        retrieved("a", "first", rank=2),
        retrieved("c", "third", rank=3),
    )

    results = reranked_from_scores(candidates, (0.5, 0.5, 0.4), top_n=2)

    assert [item.candidate.chunk_id for item in results] == ["a", "b"]
    assert [item.rerank_rank for item in results] == [1, 2]
    assert [item.rerank_score for item in results] == [0.5, 0.5]


def test_reranked_from_scores_allows_top_n_larger_than_candidates() -> None:
    candidate = retrieved("a", "only")

    results = reranked_from_scores((candidate,), (0.8,), top_n=5)

    assert len(results) == 1
    assert results[0].candidate is candidate


@pytest.mark.parametrize("score", [math.nan, math.inf, -math.inf])
def test_reranked_from_scores_rejects_non_finite_scores(score: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        reranked_from_scores((retrieved("a", "text"),), (score,), top_n=1)


def test_reranked_from_scores_rejects_count_and_id_errors() -> None:
    candidate = retrieved("a", "text")
    with pytest.raises(ValueError, match="count"):
        reranked_from_scores((candidate,), (), top_n=1)
    with pytest.raises(ValueError, match="unique"):
        reranked_from_scores((candidate, candidate), (0.8, 0.7), top_n=2)

