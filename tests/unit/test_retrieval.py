from __future__ import annotations

import math

import pytest

from enterprise_rag.retrieval import (
    rank_dense,
    rank_sparse,
    reciprocal_rank_fusion,
)


def test_rrf_unions_lanes_and_uses_chunk_id_for_final_ties() -> None:
    dense = (("b", 0.9), ("a", 0.8))
    sparse = (("a", 4.0), ("b", 3.0))

    fused = reciprocal_rank_fusion(dense, sparse, rrf_k=60, top_k=2)

    assert [item.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].retrieval_score == fused[1].retrieval_score
    assert fused[0].dense_rank == 2
    assert fused[0].sparse_rank == 1


def test_rrf_preserves_missing_lane_as_none() -> None:
    fused = reciprocal_rank_fusion(
        (("dense-only", 0.75),),
        (("sparse-only", 3.0),),
        rrf_k=60,
        top_k=5,
    )

    assert [item.chunk_id for item in fused] == ["dense-only", "sparse-only"]
    assert fused[0].dense_score == 0.75
    assert fused[0].sparse_score is None
    assert fused[1].dense_rank is None
    assert fused[1].sparse_rank == 1


@pytest.mark.parametrize("top_k", [0, -1, True])
def test_ranking_rejects_invalid_top_k(top_k: object) -> None:
    with pytest.raises((TypeError, ValueError), match="top_k"):
        reciprocal_rank_fusion((), (), rrf_k=60, top_k=top_k)  # type: ignore[arg-type]


@pytest.mark.parametrize("rrf_k", [0, -1, True])
def test_rrf_rejects_invalid_rank_constant(rrf_k: object) -> None:
    with pytest.raises((TypeError, ValueError), match="rrf_k"):
        reciprocal_rank_fusion((), (), rrf_k=rrf_k, top_k=1)  # type: ignore[arg-type]


def test_dense_ranking_uses_cosine_and_stable_chunk_id_ties() -> None:
    ranked = rank_dense(
        (1.0, 0.0),
        ((1.0, 0.0), (2.0, 0.0), (0.0, 1.0)),
        ("b", "a", "c"),
        top_k=10,
    )

    assert [chunk_id for chunk_id, _score in ranked] == ["a", "b", "c"]
    assert ranked[0][1] == pytest.approx(1.0)
    assert len(ranked) == 3


@pytest.mark.parametrize(
    ("query", "vectors"),
    [
        ((0.0, 0.0), ((1.0, 0.0),)),
        ((1.0, 0.0), ((0.0, 0.0),)),
    ],
)
def test_dense_ranking_rejects_zero_norm(
    query: tuple[float, ...],
    vectors: tuple[tuple[float, ...], ...],
) -> None:
    with pytest.raises(ValueError, match="norm"):
        rank_dense(query, vectors, ("a",), top_k=1)


def test_sparse_ranking_uses_dot_product_and_stable_ties() -> None:
    ranked = rank_sparse(
        ((10, 2.0), (20, 0.5)),
        (
            ((10, 1.0),),
            ((10, 1.0),),
            ((20, 1.0),),
        ),
        ("b", "a", "c"),
        top_k=5,
    )

    assert ranked == (("a", 2.0), ("b", 2.0), ("c", 0.5))


def test_sparse_ranking_excludes_zero_score_non_matches() -> None:
    ranked = rank_sparse(
        ((10, 1.0),),
        (((20, 3.0),), ((10, 0.25),), ()),
        ("no-overlap", "match", "empty"),
        top_k=3,
    )

    assert ranked == (("match", 0.25),)


@pytest.mark.parametrize(
    ("dense", "sparse"),
    [
        ((("a", math.nan),), ()),
        ((), (("a", math.inf),)),
    ],
)
def test_rrf_rejects_non_finite_lane_scores(
    dense: tuple[tuple[str, float], ...],
    sparse: tuple[tuple[str, float], ...],
) -> None:
    with pytest.raises(ValueError, match="finite"):
        reciprocal_rank_fusion(dense, sparse, rrf_k=60, top_k=1)


def test_lane_rankings_reject_shape_mismatch_and_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="row count"):
        rank_dense((1.0,), ((1.0,),), ("a", "b"), top_k=1)
    with pytest.raises(ValueError, match="duplicate"):
        rank_sparse((), ((), ()), ("a", "a"), top_k=1)


@pytest.mark.asyncio
async def test_hybrid_retriever_emits_real_dense_sparse_and_fusion_children() -> None:
    from enterprise_rag.execution_trace import TraceRecorder, TraceScope
    from enterprise_rag.retrieval import HybridRetriever
    from tests.fakes import FakeClock
    from tests.integration.test_retriever_fake import FakeQueryEncoder, make_index

    recorder = TraceRecorder("run-1", clock=FakeClock())
    with recorder.span("retrieval") as retrieval:
        traced = await HybridRetriever(
            make_index(),
            FakeQueryEncoder(),
            rrf_k=60,
        ).retrieve(
            "policy question",
            5,
            trace_scope=TraceScope(recorder, retrieval.event_id),
        )
    trace = recorder.finish("succeeded")

    assert [(event.stage, event.parent_id) for event in trace.events] == [
        ("retrieval", None),
        ("dense", trace.events[0].event_id),
        ("sparse", trace.events[0].event_id),
        ("rrf_fusion", trace.events[0].event_id),
    ]
    assert all(event.run_id == "run-1" for event in trace.events)
    assert all(event.status == "succeeded" for event in trace.events)

    dense_items = trace.events[1].attributes["items"]
    assert [item["chunk_id"] for item in dense_items] == [
        "policy.md::0000",
        "policy.md::0001",
        "policy.md::0002",
    ]
    assert [item["rank"] for item in dense_items] == [1, 2, 3]
    assert dense_items[0]["score"] == pytest.approx(1.0)
    assert all(item["source"] == "policy.md" for item in dense_items)
    assert all(len(item["text_preview"]) <= 300 for item in dense_items)

    sparse_items = trace.events[2].attributes["items"]
    assert [item["chunk_id"] for item in sparse_items] == [
        "policy.md::0002",
        "policy.md::0001",
    ]
    assert sparse_items[0]["score"] == pytest.approx(3.0)

    fusion_items = trace.events[3].attributes["items"]
    assert [item["chunk_id"] for item in fusion_items] == [
        "policy.md::0002",
        "policy.md::0001",
        "policy.md::0000",
    ]
    assert fusion_items[0]["retrieval_rank"] == 1
    assert fusion_items[0]["dense_rank"] == 3
    assert fusion_items[0]["sparse_rank"] == 1
    assert fusion_items[0]["retrieval_score"] == pytest.approx(1 / 63 + 1 / 61)

    untraced = await HybridRetriever(
        make_index(),
        FakeQueryEncoder(),
        rrf_k=60,
    ).retrieve("policy question", 5)
    assert traced == untraced


@pytest.mark.asyncio
async def test_untraced_hybrid_retrieval_opens_no_spans() -> None:
    from enterprise_rag.retrieval import HybridRetriever
    from tests.integration.test_retriever_fake import FakeQueryEncoder, make_index

    results = await HybridRetriever(
        make_index(),
        FakeQueryEncoder(),
        rrf_k=60,
    ).retrieve("policy question", 2)

    assert [result.chunk_id for result in results] == [
        "policy.md::0001",
        "policy.md::0000",
    ]
