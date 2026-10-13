from __future__ import annotations

import asyncio
import math
import threading
import warnings
from pathlib import Path
from typing import Any

from .config import (
    RERANKER_IMPLEMENTATION_REVISION,
    RERANKER_MODEL_ID,
    RERANKER_MODEL_REVISION,
    AppConfig,
)
from .errors import (
    RerankerProtocolError,
    RerankerTimeoutError,
    RerankerUnavailableError,
    RetrievalUnavailableError,
)
from .models import RerankedChunk, RetrievedChunk
from .retrieval import (
    _import_transformers_runtime,
    _prepare_audited_module_cache,
    _verify_materialized_remote_code,
)


class GteReranker:
    """Pinned, offline GTE sequence-classification reranker."""

    backend_kind = "real"
    model_id = RERANKER_MODEL_ID
    model_revision = RERANKER_MODEL_REVISION
    code_revision = RERANKER_IMPLEMENTATION_REVISION

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        device: str | None = None,
    ) -> None:
        torch = _import_reranker_torch()
        selected_device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        if selected_device.type == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA was requested but is unavailable")
        dtype = torch.float16 if selected_device.type == "cuda" else torch.float32

        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model.to(device=selected_device, dtype=dtype)
        self._model.eval()
        self._device = selected_device
        self._dtype = dtype
        self._inference_lock = threading.Lock()

    @property
    def device(self) -> str:
        return str(self._device)

    @property
    def dtype(self) -> str:
        return str(self._dtype).removeprefix("torch.")

    @property
    def model_identity(self) -> tuple[str, str, str]:
        return (self.model_id, self.model_revision, self.code_revision)

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        device: str | None = None,
    ) -> GteReranker:
        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
        torch = _import_reranker_torch()
        selected_device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        if selected_device.type == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA was requested but is unavailable")
        dtype = torch.float16 if selected_device.type == "cuda" else torch.float32

        resolved_cache_dir = config.model_cache_dir.resolve()
        try:
            modules_cache = _prepare_audited_module_cache(resolved_cache_dir)
            auto_tokenizer, auto_model = _import_reranker_runtime(modules_cache)
            shared = {
                "revision": RERANKER_MODEL_REVISION,
                "code_revision": RERANKER_IMPLEMENTATION_REVISION,
                "trust_remote_code": True,
                "local_files_only": True,
                "cache_dir": resolved_cache_dir,
            }
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"`resume_download` is deprecated.*",
                    category=FutureWarning,
                )
                tokenizer = auto_tokenizer.from_pretrained(
                    RERANKER_MODEL_ID,
                    **shared,
                )
                model = auto_model.from_pretrained(
                    RERANKER_MODEL_ID,
                    **shared,
                    torch_dtype=dtype,
                )
            _verify_materialized_remote_code(modules_cache)
        except RetrievalUnavailableError as error:
            raise RerankerUnavailableError(str(error)) from error
        except (ImportError, OSError) as error:
            raise RerankerUnavailableError(
                "the reranker runtime or pinned local artifact is unavailable"
            ) from error

        return cls(
            tokenizer=tokenizer,
            model=model,
            device=str(selected_device),
        )

    async def rerank(
        self,
        question: str,
        candidates: tuple[RetrievedChunk, ...],
        top_n: int,
    ) -> tuple[RerankedChunk, ...]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-blank string")
        _validate_top_n(top_n)
        _validate_candidates(candidates)
        if not candidates:
            return ()

        scores = await self.score_pairs(
            tuple((question, candidate.text) for candidate in candidates)
        )
        return reranked_from_scores(candidates, scores, top_n=top_n)

    async def score_pairs(
        self,
        pairs: tuple[tuple[str, str], ...],
    ) -> tuple[float, ...]:
        _validate_pairs(pairs)
        if not pairs:
            return ()
        return await asyncio.to_thread(self._score_pairs_sync, pairs)

    def _score_pairs_sync(
        self,
        pairs: tuple[tuple[str, str], ...],
    ) -> tuple[float, ...]:
        with self._inference_lock:
            return self._score_pairs_sync_locked(pairs)

    def _score_pairs_sync_locked(
        self,
        pairs: tuple[tuple[str, str], ...],
    ) -> tuple[float, ...]:
        try:
            tokenized = self._tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            if not isinstance(tokenized, dict):
                tokenized = dict(tokenized)
            inputs = {
                name: value.to(device=self._device) for name, value in tokenized.items()
            }
        except TimeoutError as error:
            raise RerankerTimeoutError("reranker tokenization timed out") from error
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise RerankerProtocolError(
                "reranker tokenizer returned an invalid tensor batch"
            ) from error
        except (OSError, RuntimeError) as error:
            raise RerankerUnavailableError(
                "reranker tokenization is unavailable"
            ) from error

        try:
            with self._torch.inference_mode():
                output = self._model(**inputs, return_dict=True)
        except TimeoutError as error:
            raise RerankerTimeoutError("reranker inference timed out") from error
        except (OSError, RuntimeError) as error:
            raise RerankerUnavailableError("reranker inference failed") from error
        except (AttributeError, TypeError, ValueError) as error:
            raise RerankerProtocolError("reranker rejected the tensor batch") from error

        try:
            values = (
                output.logits.reshape(-1)
                .detach()
                .to("cpu", dtype=self._torch.float32)
                .tolist()
            )
            scores = tuple(float(value) for value in values)
        except (AttributeError, TypeError, ValueError, RuntimeError) as error:
            raise RerankerProtocolError("reranker returned invalid logits") from error
        if len(scores) != len(pairs):
            raise RerankerProtocolError(
                "reranker logit count must match the input pair count"
            )
        if any(not math.isfinite(score) for score in scores):
            raise RerankerProtocolError("reranker logits must be finite")
        return scores


def _validate_top_n(top_n: int) -> None:
    if isinstance(top_n, bool) or not isinstance(top_n, int):
        raise TypeError("top_n must be an integer")
    if top_n <= 0:
        raise ValueError("top_n must be positive")


def _validate_candidates(candidates: tuple[RetrievedChunk, ...]) -> None:
    if not isinstance(candidates, tuple) or any(
        not isinstance(candidate, RetrievedChunk) for candidate in candidates
    ):
        raise TypeError("candidates must be a tuple of RetrievedChunk values")
    chunk_ids = tuple(candidate.chunk_id for candidate in candidates)
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("reranker candidates must have unique chunk IDs")


def _validate_pairs(pairs: tuple[tuple[str, str], ...]) -> None:
    if not isinstance(pairs, tuple):
        raise TypeError("pairs must be a tuple")
    for pair in pairs:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(not isinstance(value, str) or not value.strip() for value in pair)
        ):
            raise ValueError("pairs must contain non-blank string pairs")


def _import_reranker_runtime(modules_cache: Path) -> tuple[Any, Any]:
    auto_tokenizer, _ = _import_transformers_runtime(modules_cache)
    from transformers import AutoModelForSequenceClassification

    return auto_tokenizer, AutoModelForSequenceClassification


def _import_reranker_torch() -> Any:
    try:
        import torch
    except ImportError as error:
        raise RerankerUnavailableError(
            "the reranker runtime is not installed"
        ) from error
    return torch


def reranked_from_scores(
    candidates: tuple[RetrievedChunk, ...],
    scores: tuple[float, ...],
    *,
    top_n: int,
) -> tuple[RerankedChunk, ...]:
    """Build deterministic reranked results from backend scores."""

    if isinstance(top_n, bool) or not isinstance(top_n, int):
        raise TypeError("top_n must be an integer")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if not isinstance(candidates, tuple) or not isinstance(scores, tuple):
        raise TypeError("candidates and scores must be tuples")
    if len(candidates) != len(scores):
        raise ValueError("reranker score count must match candidate count")
    if any(not isinstance(candidate, RetrievedChunk) for candidate in candidates):
        raise TypeError("candidates must contain RetrievedChunk values")
    chunk_ids = tuple(candidate.chunk_id for candidate in candidates)
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("reranker candidates must have unique chunk IDs")

    rows: list[tuple[RetrievedChunk, float]] = []
    for candidate, score in zip(candidates, scores, strict=True):
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError("reranker scores must be finite numbers")
        rows.append((candidate, float(score)))
    rows.sort(key=lambda row: (-row[1], row[0].chunk_id))
    return tuple(
        RerankedChunk(candidate, score, rank)
        for rank, (candidate, score) in enumerate(rows[:top_n], start=1)
    )
