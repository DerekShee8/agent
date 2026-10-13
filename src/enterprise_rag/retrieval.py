from __future__ import annotations

import asyncio
import hashlib
import math
import os
import sys
import threading
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_REVISION,
    RERANKER_IMPLEMENTATION_ID,
    RERANKER_IMPLEMENTATION_REVISION,
    AppConfig,
)
from .errors import RetrievalProtocolError, RetrievalUnavailableError
from .execution_trace import TraceScope, trace_stage
from .index_store import HybridIndex
from .ingestion import DocumentChunk
from .models import RetrievedChunk
from .ports import EncodedBatch, HybridEncoderPort

RankedLaneItem = tuple[str, float]
SparseRow = tuple[tuple[int, float], ...]

_TRANSFORMERS_VERSION = "4.39.1"
_AUDITED_REMOTE_CODE = {
    "configuration.py": (
        7127,
        "3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4"
    ),
    "modeling.py": (
        59023,
        "374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0"
    ),
}


@dataclass(frozen=True, slots=True)
class FusedItem:
    chunk_id: str
    dense_score: float | None
    dense_rank: int | None
    sparse_score: float | None
    sparse_rank: int | None
    retrieval_score: float
    retrieval_rank: int


@dataclass(slots=True)
class _FusionRow:
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    sparse_rank: int | None = None
    rrf: float = 0.0


class GteHybridEncoder:
    backend_kind = "real"
    model_id = EMBEDDING_MODEL_ID
    model_revision = EMBEDDING_MODEL_REVISION
    code_revision = RERANKER_IMPLEMENTATION_REVISION

    def __init__(
        self,
        *,
        tokenizer: Any,
        model: Any,
        device: str | None = None,
    ) -> None:
        torch = _import_torch()
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

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        device: str | None = None,
    ) -> GteHybridEncoder:
        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
        torch = _import_torch()
        selected_device = torch.device(
            device or ("cuda" if torch.cuda.is_available() else "cpu")
        )
        if selected_device.type == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA was requested but is unavailable")
        dtype = torch.float16 if selected_device.type == "cuda" else torch.float32

        resolved_cache_dir = config.model_cache_dir.resolve()
        modules_cache = _prepare_audited_module_cache(resolved_cache_dir)
        AutoTokenizer, AutoModelForTokenClassification = (
            _import_transformers_runtime(modules_cache)
        )

        shared = {
            "revision": EMBEDDING_MODEL_REVISION,
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
            tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_ID, **shared)
            model = AutoModelForTokenClassification.from_pretrained(
                EMBEDDING_MODEL_ID,
                **shared,
                torch_dtype=dtype,
            )
        _verify_materialized_remote_code(modules_cache)
        return cls(
            tokenizer=tokenizer,
            model=model,
            device=str(selected_device),
        )

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch:
        if (
            not isinstance(texts, tuple)
            or not texts
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise ValueError("texts must be a non-empty tuple of non-blank strings")

        return await asyncio.to_thread(self._encode_sync, texts)

    def _encode_sync(self, texts: tuple[str, ...]) -> EncodedBatch:
        with self._inference_lock:
            return self._encode_sync_locked(texts)

    def _encode_sync_locked(self, texts: tuple[str, ...]) -> EncodedBatch:

        tokenized = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            return_tensors="pt",
        )
        if not isinstance(tokenized, dict):
            tokenized = dict(tokenized)
        try:
            inputs = {
                name: value.to(device=self._device)
                for name, value in tokenized.items()
            }
            input_ids = inputs["input_ids"]
            attention_mask = inputs["attention_mask"]
        except (AttributeError, KeyError, TypeError) as error:
            raise RetrievalProtocolError(
                "tokenizer returned an invalid tensor batch"
            ) from error

        with self._torch.inference_mode():
            output = self._model(**inputs)
            dense_tensor = self._torch.nn.functional.normalize(
                output.last_hidden_state[:, 0, :],
                p=2,
                dim=1,
            )
            sparse_tensor = self._torch.relu(output.logits).squeeze(-1)

        dense_rows = (
            dense_tensor.detach()
            .to("cpu", dtype=self._torch.float32)
            .tolist()
        )
        dense = tuple(
            tuple(float(value) for value in row)
            for row in dense_rows
        )
        token_rows = input_ids.detach().to("cpu").tolist()
        mask_rows = attention_mask.detach().to("cpu").tolist()
        weight_rows = (
            sparse_tensor.detach().to("cpu", dtype=self._torch.float32).tolist()
        )
        special_ids = {
            int(token_id)
            for token_id in getattr(self._tokenizer, "all_special_ids", ())
        }
        pad_token_id = getattr(self._tokenizer, "pad_token_id", None)
        if isinstance(pad_token_id, int):
            special_ids.add(pad_token_id)

        sparse: list[tuple[tuple[int, float], ...]] = []
        for token_ids, attention, weights in zip(
            token_rows,
            mask_rows,
            weight_rows,
            strict=True,
        ):
            maxima: dict[int, float] = {}
            for token_id, attended, weight in zip(
                token_ids,
                attention,
                weights,
                strict=True,
            ):
                normalized_id = int(token_id)
                normalized_weight = float(weight)
                if (
                    not attended
                    or normalized_id in special_ids
                    or normalized_weight <= 0.0
                ):
                    continue
                maxima[normalized_id] = max(
                    maxima.get(normalized_id, 0.0),
                    normalized_weight,
                )
            sparse.append(tuple(sorted(maxima.items())))

        return EncodedBatch(dense=dense, sparse=tuple(sparse))


def _prepare_audited_module_cache(
    cache_dir: Path,
    *,
    expected_files: Mapping[str, tuple[int, str]] | None = None,
) -> Path:
    expected = dict(expected_files or _AUDITED_REMOTE_CODE)
    source_dir = (
        cache_dir
        / f"models--{RERANKER_IMPLEMENTATION_ID.replace('/', '--')}"
        / "snapshots"
        / RERANKER_IMPLEMENTATION_REVISION
    )
    _verify_code_directory(source_dir, expected, require_all=True)

    modules_cache = cache_dir / "modules"
    modules_cache.mkdir(parents=True, exist_ok=True)
    for revision_dir in _materialized_revision_directories(modules_cache):
        _verify_code_directory(revision_dir, expected, require_all=False)
    return modules_cache


def _verify_materialized_remote_code(modules_cache: Path) -> None:
    revision_directories = _materialized_revision_directories(modules_cache)
    if not revision_directories:
        raise RetrievalUnavailableError(
            "audited remote code was not materialized in the isolated cache"
        )
    for revision_dir in revision_directories:
        _verify_code_directory(
            revision_dir,
            _AUDITED_REMOTE_CODE,
            require_all=True,
        )


def _materialized_revision_directories(modules_cache: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in modules_cache.rglob(RERANKER_IMPLEMENTATION_REVISION)
            if path.is_dir()
        )
    )


def _verify_code_directory(
    directory: Path,
    expected_files: Mapping[str, tuple[int, str]],
    *,
    require_all: bool,
) -> None:
    if require_all and not directory.is_dir():
        raise RetrievalUnavailableError("audited remote-code snapshot is missing")

    allowed_names = {*expected_files, "__init__.py"}
    if directory.is_dir():
        unexpected = sorted(
            path.name
            for path in directory.glob("*.py")
            if path.name not in allowed_names
        )
        if unexpected:
            raise RetrievalUnavailableError(
                "audited remote-code directory contains an unexpected Python file"
            )

    for filename, (expected_bytes, expected_digest) in expected_files.items():
        path = directory / filename
        if not path.is_file():
            if require_all:
                raise RetrievalUnavailableError(
                    f"audited remote-code file is missing: {filename}"
                )
            continue
        if path.stat().st_size != expected_bytes:
            raise RetrievalUnavailableError(
                f"audited remote-code size mismatch: {filename}"
            )
        if _sha256_file(path) != expected_digest:
            raise RetrievalUnavailableError(
                f"audited remote-code digest mismatch: {filename}"
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _import_transformers_runtime(modules_cache: Path) -> tuple[Any, Any]:
    resolved_modules_cache = modules_cache.resolve()
    os.environ["HF_MODULES_CACHE"] = str(resolved_modules_cache)

    import transformers
    from transformers import (
        AutoModelForTokenClassification,
        AutoTokenizer,
        dynamic_module_utils,
    )
    from transformers.utils import hub as transformers_hub

    if transformers.__version__ != _TRANSFORMERS_VERSION:
        raise RetrievalUnavailableError(
            "the retrieval runtime has an incompatible Transformers version"
        )
    configured_caches = (
        Path(transformers_hub.HF_MODULES_CACHE).resolve(),
        Path(dynamic_module_utils.HF_MODULES_CACHE).resolve(),
    )
    if any(path != resolved_modules_cache for path in configured_caches):
        raise RetrievalUnavailableError(
            "Transformers was imported before the audited module cache was configured"
        )
    for module_name, module in tuple(sys.modules.items()):
        if not module_name.startswith("transformers_modules."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is not None and not Path(module_file).resolve().is_relative_to(
            resolved_modules_cache
        ):
            raise RetrievalUnavailableError(
                "remote model code was imported outside the audited module cache"
            )
    return AutoTokenizer, AutoModelForTokenClassification


def _import_torch() -> Any:
    try:
        import torch
    except ImportError as error:
        raise RetrievalUnavailableError(
            "the retrieval runtime is not installed"
        ) from error
    return torch


def rank_dense(
    query: tuple[float, ...],
    vectors: tuple[tuple[float, ...], ...],
    chunk_ids: tuple[str, ...],
    *,
    top_k: int,
) -> tuple[RankedLaneItem, ...]:
    """Rank dense rows by cosine similarity, then stable chunk identity."""

    _validate_positive_integer("top_k", top_k)
    _validate_chunk_ids(chunk_ids, expected_count=len(vectors))
    if not isinstance(query, tuple) or not query:
        raise ValueError("dense query dimension must be positive")
    query_values = _validate_dense_row(query, dimension=len(query))
    query_norm = math.sqrt(sum(value * value for value in query_values))
    if query_norm == 0.0:
        raise ValueError("dense query norm must be non-zero")

    ranked: list[RankedLaneItem] = []
    for chunk_id, vector in zip(chunk_ids, vectors, strict=True):
        values = _validate_dense_row(vector, dimension=len(query_values))
        vector_norm = math.sqrt(sum(value * value for value in values))
        if vector_norm == 0.0:
            raise ValueError("dense vector norm must be non-zero")
        dot_product = sum(
            query_value * vector_value
            for query_value, vector_value in zip(
                query_values,
                values,
                strict=True,
            )
        )
        score = dot_product / (query_norm * vector_norm)
        if not math.isfinite(score):
            raise ValueError("dense score must be finite")
        ranked.append((chunk_id, score))

    ranked.sort(key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:top_k])


def rank_sparse(
    query: SparseRow,
    vectors: tuple[SparseRow, ...],
    chunk_ids: tuple[str, ...],
    *,
    top_k: int,
) -> tuple[RankedLaneItem, ...]:
    """Rank sparse rows by token-weight dot product and stable identity."""

    _validate_positive_integer("top_k", top_k)
    _validate_chunk_ids(chunk_ids, expected_count=len(vectors))
    query_weights = _sparse_mapping(query)

    ranked: list[RankedLaneItem] = []
    for chunk_id, vector in zip(chunk_ids, vectors, strict=True):
        vector_weights = _sparse_mapping(vector)
        score = sum(
            query_weight * vector_weights.get(token_id, 0.0)
            for token_id, query_weight in query_weights.items()
        )
        if not math.isfinite(score):
            raise ValueError("sparse score must be finite")
        if score > 0.0:
            ranked.append((chunk_id, score))

    ranked.sort(key=lambda item: (-item[1], item[0]))
    return tuple(ranked[:top_k])


def reciprocal_rank_fusion(
    dense: tuple[RankedLaneItem, ...],
    sparse: tuple[RankedLaneItem, ...],
    *,
    rrf_k: int,
    top_k: int,
) -> tuple[FusedItem, ...]:
    """Union two ranked lanes and fuse ranks with deterministic tie-breaking."""

    _validate_positive_integer("rrf_k", rrf_k)
    _validate_positive_integer("top_k", top_k)
    dense_rows = _validate_ranked_lane("dense", dense)
    sparse_rows = _validate_ranked_lane("sparse", sparse)

    by_id: dict[str, _FusionRow] = {}
    for lane_name, rows in (("dense", dense_rows), ("sparse", sparse_rows)):
        for rank, (chunk_id, score) in enumerate(rows, start=1):
            row = by_id.setdefault(chunk_id, _FusionRow())
            if lane_name == "dense":
                row.dense_score = score
                row.dense_rank = rank
            else:
                row.sparse_score = score
                row.sparse_rank = rank
            row.rrf += 1.0 / (rrf_k + rank)

    ordered = sorted(
        by_id.items(),
        key=lambda pair: (-pair[1].rrf, pair[0]),
    )
    return tuple(
        FusedItem(
            chunk_id=chunk_id,
            dense_score=row.dense_score,
            dense_rank=row.dense_rank,
            sparse_score=row.sparse_score,
            sparse_rank=row.sparse_rank,
            retrieval_score=row.rrf,
            retrieval_rank=rank,
        )
        for rank, (chunk_id, row) in enumerate(ordered[:top_k], start=1)
    )


_TEXT_PREVIEW_LIMIT = 300


def _text_preview(chunk: DocumentChunk) -> str:
    return chunk.text[:_TEXT_PREVIEW_LIMIT]


def _lane_items(
    ranked: tuple[RankedLaneItem, ...],
    chunks_by_id: Mapping[str, DocumentChunk],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "chunk_id": chunk_id,
            "source": chunks_by_id[chunk_id].filename,
            "text_preview": _text_preview(chunks_by_id[chunk_id]),
            "score": score,
            "rank": rank,
        }
        for rank, (chunk_id, score) in enumerate(ranked, start=1)
    )


def _fusion_items(
    fused: tuple[FusedItem, ...],
    chunks_by_id: Mapping[str, DocumentChunk],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "chunk_id": item.chunk_id,
            "source": chunks_by_id[item.chunk_id].filename,
            "text_preview": _text_preview(chunks_by_id[item.chunk_id]),
            "dense_rank": item.dense_rank,
            "sparse_rank": item.sparse_rank,
            "retrieval_score": item.retrieval_score,
            "retrieval_rank": item.retrieval_rank,
        }
        for item in fused
    )


class HybridRetriever:
    backend_kind = "real"

    def __init__(
        self,
        index: HybridIndex,
        encoder: HybridEncoderPort,
        *,
        rrf_k: int,
    ) -> None:
        if not isinstance(index, HybridIndex):
            raise TypeError("index must be a HybridIndex")
        _validate_positive_integer("rrf_k", rrf_k)
        self._index = index
        self._encoder = encoder
        self._rrf_k = rrf_k

    async def retrieve(
        self,
        question: str,
        top_k: int,
        *,
        trace_scope: TraceScope | None = None,
    ) -> tuple[RetrievedChunk, ...]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        _validate_positive_integer("top_k", top_k)
        if trace_scope is not None and not isinstance(trace_scope, TraceScope):
            raise TypeError("trace_scope must be a TraceScope")

        try:
            self._validate_encoder_identity()
            encoded = await self._encoder.encode((question,))
        except RetrievalProtocolError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise RetrievalProtocolError(
                "query encoder returned an invalid encoded batch"
            ) from error
        except Exception as error:
            raise RetrievalUnavailableError("query encoder is unavailable") from error

        chunks_by_id = {chunk.chunk_id: chunk for chunk in self._index.chunks}
        try:
            if not isinstance(encoded, EncodedBatch):
                raise RetrievalProtocolError("query encoder must return EncodedBatch")
            if len(encoded.dense) != 1 or len(encoded.sparse) != 1:
                raise RetrievalProtocolError(
                    "query encoder must return exactly one row"
                )
            if len(encoded.dense[0]) != self._index.manifest.dimension:
                raise RetrievalProtocolError(
                    "query dense dimension does not match the index"
                )

            chunk_ids = tuple(chunk.chunk_id for chunk in self._index.chunks)
            with trace_stage(trace_scope, "dense") as dense_span:
                dense = rank_dense(
                    encoded.dense[0],
                    self._index.dense,
                    chunk_ids,
                    top_k=top_k,
                )
                if dense_span is not None:
                    dense_span.finish(
                        "succeeded",
                        attributes={"items": _lane_items(dense, chunks_by_id)},
                    )
            with trace_stage(trace_scope, "sparse") as sparse_span:
                sparse = rank_sparse(
                    encoded.sparse[0],
                    self._index.sparse,
                    chunk_ids,
                    top_k=top_k,
                )
                if sparse_span is not None:
                    sparse_span.finish(
                        "succeeded",
                        attributes={"items": _lane_items(sparse, chunks_by_id)},
                    )
            with trace_stage(trace_scope, "rrf_fusion") as fusion_span:
                fused = reciprocal_rank_fusion(
                    dense,
                    sparse,
                    rrf_k=self._rrf_k,
                    top_k=top_k,
                )
                if fusion_span is not None:
                    fusion_span.finish(
                        "succeeded",
                        attributes={"items": _fusion_items(fused, chunks_by_id)},
                    )
        except RetrievalProtocolError:
            raise
        except (TypeError, ValueError) as error:
            raise RetrievalProtocolError("hybrid lane ranking failed") from error

        return tuple(
            RetrievedChunk(
                text=chunks_by_id[item.chunk_id].text,
                filename=chunks_by_id[item.chunk_id].filename,
                chunk_id=item.chunk_id,
                dense_score=item.dense_score,
                dense_rank=item.dense_rank,
                sparse_score=item.sparse_score,
                sparse_rank=item.sparse_rank,
                retrieval_score=item.retrieval_score,
                retrieval_rank=item.retrieval_rank,
            )
            for item in fused
        )

    def _validate_encoder_identity(self) -> None:
        manifest = self._index.manifest
        expected = (
            manifest.backend_kind,
            manifest.model_id,
            manifest.model_revision,
            manifest.code_revision,
        )
        actual = (
            self._encoder.backend_kind,
            self._encoder.model_id,
            self._encoder.model_revision,
            self._encoder.code_revision,
        )
        if actual != expected:
            raise RetrievalProtocolError(
                "query encoder identity does not match the index identity"
            )


def _validate_positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_chunk_ids(chunk_ids: object, *, expected_count: int) -> None:
    if not isinstance(chunk_ids, tuple):
        raise TypeError("chunk_ids must be a tuple")
    if len(chunk_ids) != expected_count:
        raise ValueError("chunk ID row count must match vector row count")
    if any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in chunk_ids):
        raise ValueError("chunk IDs must be non-empty strings")
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("chunk IDs must not contain duplicates")


def _validate_dense_row(
    row: object,
    *,
    dimension: int,
) -> tuple[float, ...]:
    if not isinstance(row, tuple):
        raise TypeError("dense row must be a tuple")
    if len(row) != dimension:
        raise ValueError("dense row dimension mismatch")
    values: list[float] = []
    for value in row:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("dense values must be finite numbers")
        values.append(float(value))
    return tuple(values)


def _sparse_mapping(row: object) -> dict[int, float]:
    if not isinstance(row, tuple):
        raise TypeError("sparse row must be a tuple")
    mapping: dict[int, float] = {}
    for pair in row:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError("sparse entries must be token-weight tuples")
        token_id, weight = pair
        if isinstance(token_id, bool) or not isinstance(token_id, int):
            raise TypeError("sparse token IDs must be integers")
        if token_id < 0:
            raise ValueError("sparse token IDs must be non-negative")
        if token_id in mapping:
            raise ValueError("sparse token IDs must be unique per row")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
        ):
            raise ValueError("sparse weights must be finite numbers")
        mapping[token_id] = float(weight)
    return mapping


def _validate_ranked_lane(
    name: str,
    rows: object,
) -> tuple[RankedLaneItem, ...]:
    if not isinstance(rows, tuple):
        raise TypeError(f"{name} lane must be a tuple")
    validated: list[RankedLaneItem] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{name} lane entries must be chunk-score tuples")
        chunk_id, score = item
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError(f"{name} lane chunk IDs must be non-empty strings")
        if chunk_id in seen:
            raise ValueError(f"{name} lane contains a duplicate chunk ID")
        seen.add(chunk_id)
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError(f"{name} lane scores must be finite numbers")
        validated.append((chunk_id, float(score)))
    return tuple(validated)
