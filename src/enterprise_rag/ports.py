import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .execution_trace import TraceScope
from .models import RerankedChunk, RetrievedChunk, StructuredGenerationRequest


@dataclass(frozen=True, slots=True)
class EncodedBatch:
    dense: tuple[tuple[float, ...], ...]
    sparse: tuple[tuple[tuple[int, float], ...], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.dense, tuple):
            raise TypeError("dense rows must be a tuple")
        if not isinstance(self.sparse, tuple):
            raise TypeError("sparse rows must be a tuple")
        if len(self.dense) != len(self.sparse):
            raise ValueError("encoded row counts must match")

        dimension: int | None = None
        for row in self.dense:
            if not isinstance(row, tuple):
                raise TypeError("each dense row must be a tuple")
            if dimension is None:
                dimension = len(row)
                if dimension <= 0:
                    raise ValueError("dense dimension must be positive")
            elif len(row) != dimension:
                raise ValueError("dense rows must share one dimension")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in row
            ):
                raise ValueError("dense values must be finite numbers")

        for row in self.sparse:
            if not isinstance(row, tuple):
                raise TypeError("each sparse row must be a tuple")
            token_ids: set[int] = set()
            for pair in row:
                if not isinstance(pair, tuple) or len(pair) != 2:
                    raise TypeError("sparse entries must be token-weight tuples")
                token_id, weight = pair
                if isinstance(token_id, bool) or not isinstance(token_id, int):
                    raise TypeError("sparse token IDs must be integers")
                if token_id < 0:
                    raise ValueError("sparse token IDs must be non-negative")
                if token_id in token_ids:
                    raise ValueError("sparse token IDs must be unique per row")
                token_ids.add(token_id)
                if (
                    isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not math.isfinite(weight)
                ):
                    raise ValueError("sparse weights must be finite numbers")


@runtime_checkable
class HybridEncoderPort(Protocol):
    backend_kind: str
    model_id: str
    model_revision: str
    code_revision: str

    async def encode(self, texts: tuple[str, ...]) -> EncodedBatch: ...


@runtime_checkable
class ClaimSupportScorerPort(Protocol):
    backend_kind: str

    async def score(
        self,
        claim: str,
        support_texts: tuple[str, ...],
    ) -> tuple[float, ...]: ...


@runtime_checkable
class RerankerPort(Protocol):
    async def rerank(
        self,
        question: str,
        candidates: tuple[RetrievedChunk, ...],
        top_n: int,
    ) -> tuple[RerankedChunk, ...]: ...


@runtime_checkable
class RetrieverPort(Protocol):
    async def retrieve(
        self,
        question: str,
        top_k: int,
        *,
        trace_scope: TraceScope | None = None,
    ) -> tuple[RetrievedChunk, ...]: ...


@runtime_checkable
class StructuredGeneratorPort(Protocol):
    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]: ...


# Compatibility name; structured generation is the canonical boundary.
GeneratorPort = StructuredGeneratorPort
