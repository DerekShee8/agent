from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

import enterprise_rag.reranking as reranking_module
from enterprise_rag.config import (
    RERANKER_IMPLEMENTATION_REVISION,
    RERANKER_MODEL_ID,
    RERANKER_MODEL_REVISION,
    AppConfig,
)
from enterprise_rag.errors import (
    RerankerProtocolError,
    RerankerTimeoutError,
    RerankerUnavailableError,
)
from enterprise_rag.grounding import GteClaimSupportScorer
from enterprise_rag.reranking import GteReranker
from tests.fakes import retrieved

torch = pytest.importorskip("torch")


class FakePairTokenizer:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[tuple[str, str], ...], dict[str, object]]] = []

    def __call__(
        self,
        pairs: tuple[tuple[str, str], ...],
        **kwargs: object,
    ) -> dict[str, object]:
        self.calls.append((pairs, kwargs))
        batch_size = len(pairs)
        return {
            "input_ids": torch.ones((batch_size, 3), dtype=torch.long),
            "attention_mask": torch.ones((batch_size, 3), dtype=torch.long),
        }


class FakeSequenceModel:
    def __init__(
        self,
        scores: tuple[float, ...],
        *,
        error: Exception | None = None,
    ) -> None:
        self._scores = scores
        self._error = error
        self.forward_calls = 0
        self.eval_calls = 0
        self.to_calls: list[tuple[str, torch.dtype]] = []

    def eval(self) -> FakeSequenceModel:
        self.eval_calls += 1
        return self

    def to(self, *, device: object, dtype: torch.dtype) -> FakeSequenceModel:
        self.to_calls.append((str(device), dtype))
        return self

    def __call__(self, **inputs: object) -> SimpleNamespace:
        self.forward_calls += 1
        if self._error is not None:
            raise self._error
        assert torch.is_grad_enabled() is False
        assert inputs["return_dict"] is True
        return SimpleNamespace(
            logits=torch.tensor(self._scores, dtype=torch.float32).reshape(-1, 1)
        )


def candidates_b_then_a():
    return (
        retrieved("b", "second", rank=1),
        retrieved("a", "first", rank=2),
    )


@pytest.mark.asyncio
async def test_reranker_orders_stable_ties_and_uses_512_token_pairs() -> None:
    tokenizer = FakePairTokenizer()
    model = FakeSequenceModel((0.5, 0.5))
    reranker = GteReranker(tokenizer=tokenizer, model=model, device="cpu")
    candidates = candidates_b_then_a()

    result = await reranker.rerank("question", candidates, top_n=2)

    assert [item.candidate.chunk_id for item in result] == ["a", "b"]
    assert [item.rerank_score for item in result] == [0.5, 0.5]
    assert [item.rerank_rank for item in result] == [1, 2]
    assert result[0].candidate is candidates[1]
    assert tokenizer.calls == [
        (
            (("question", "second"), ("question", "first")),
            {
                "padding": True,
                "truncation": True,
                "max_length": 512,
                "return_tensors": "pt",
            },
        )
    ]
    assert model.forward_calls == 1
    assert model.eval_calls == 1
    assert model.to_calls == [("cpu", torch.float32)]
    assert reranker.device == "cpu"
    assert reranker.dtype == "float32"


@pytest.mark.asyncio
async def test_reranker_allows_top_n_larger_than_candidates() -> None:
    reranker = GteReranker(
        tokenizer=FakePairTokenizer(),
        model=FakeSequenceModel((0.8,)),
        device="cpu",
    )

    result = await reranker.rerank(
        "question",
        (retrieved("only", "policy"),),
        top_n=5,
    )

    assert len(result) == 1
    assert result[0].rerank_score == pytest.approx(0.8)


@pytest.mark.asyncio
@pytest.mark.parametrize("top_n", [0, -1, True, 1.5])
async def test_reranker_rejects_invalid_bounds_without_inference(top_n: object) -> None:
    model = FakeSequenceModel((0.8,))
    reranker = GteReranker(
        tokenizer=FakePairTokenizer(),
        model=model,
        device="cpu",
    )

    with pytest.raises((TypeError, ValueError), match="top_n"):
        await reranker.rerank("question", (retrieved("a", "policy"),), top_n)

    assert model.forward_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [math.nan, math.inf, -math.inf])
async def test_reranker_maps_non_finite_logits_to_protocol_error(score: float) -> None:
    reranker = GteReranker(
        tokenizer=FakePairTokenizer(),
        model=FakeSequenceModel((score,)),
        device="cpu",
    )

    with pytest.raises(RerankerProtocolError, match="finite"):
        await reranker.rerank("question", (retrieved("a", "policy"),), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend_error", "expected_error"),
    [
        (TimeoutError("slow"), RerankerTimeoutError),
        (RuntimeError("backend failed"), RerankerUnavailableError),
    ],
)
async def test_reranker_maps_backend_failures_to_typed_errors(
    backend_error: Exception,
    expected_error: type[Exception],
) -> None:
    reranker = GteReranker(
        tokenizer=FakePairTokenizer(),
        model=FakeSequenceModel((0.8,), error=backend_error),
        device="cpu",
    )

    with pytest.raises(expected_error):
        await reranker.rerank("question", (retrieved("a", "policy"),), 1)


@pytest.mark.asyncio
async def test_claim_support_scorer_reuses_loaded_reranker_model() -> None:
    model = FakeSequenceModel((0.9, -0.2))
    reranker = GteReranker(
        tokenizer=FakePairTokenizer(),
        model=model,
        device="cpu",
    )
    scorer = GteClaimSupportScorer(reranker)

    scores = await scorer.score("claim", ("support one", "support two"))

    assert scores == pytest.approx((0.9, -0.2))
    assert model.forward_calls == 1
    assert scorer.model_identity == reranker.model_identity


def test_reranker_loads_only_pinned_local_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tokenizer = FakePairTokenizer()
    model = FakeSequenceModel((0.5,))
    tokenizer_call: dict[str, object] = {}
    model_call: dict[str, object] = {}

    def load_tokenizer(model_id: str, **kwargs: object) -> FakePairTokenizer:
        tokenizer_call.update({"model_id": model_id, **kwargs})
        return tokenizer

    def load_model(model_id: str, **kwargs: object) -> FakeSequenceModel:
        model_call.update({"model_id": model_id, **kwargs})
        return model

    auto_tokenizer = SimpleNamespace(from_pretrained=load_tokenizer)
    auto_model = SimpleNamespace(from_pretrained=load_model)
    modules_cache = tmp_path / "modules"
    modules_cache.mkdir()
    monkeypatch.setattr(
        reranking_module,
        "_prepare_audited_module_cache",
        lambda _cache_dir: modules_cache,
    )
    monkeypatch.setattr(
        reranking_module,
        "_import_reranker_runtime",
        lambda path: (auto_tokenizer, auto_model),
    )
    monkeypatch.setattr(
        reranking_module,
        "_verify_materialized_remote_code",
        lambda _modules_cache: None,
    )
    monkeypatch.delenv("HF_MODULES_CACHE", raising=False)
    cache_dir = tmp_path / "model-cache"
    config = AppConfig(
        model_cache_dir=cache_dir,
        index_dir=tmp_path / "index",
        local_model_path=cache_dir / "model.gguf",
    )

    reranker = GteReranker.from_config(config, device="cpu")

    shared_expected = {
        "model_id": RERANKER_MODEL_ID,
        "revision": RERANKER_MODEL_REVISION,
        "code_revision": RERANKER_IMPLEMENTATION_REVISION,
        "trust_remote_code": True,
        "local_files_only": True,
        "cache_dir": cache_dir.resolve(),
    }
    assert tokenizer_call == shared_expected
    assert model_call == {**shared_expected, "torch_dtype": torch.float32}
    assert reranker.model_identity == (
        RERANKER_MODEL_ID,
        RERANKER_MODEL_REVISION,
        RERANKER_IMPLEMENTATION_REVISION,
    )
