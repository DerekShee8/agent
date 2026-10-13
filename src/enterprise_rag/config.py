from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from enterprise_rag.errors import ConfigurationError

EMBEDDING_MODEL_ID = "Alibaba-NLP/gte-multilingual-base"
EMBEDDING_MODEL_REVISION = "087a024525fd6e2fe749cb4679d218d8bcc95bdd"
RERANKER_MODEL_ID = "Alibaba-NLP/gte-multilingual-reranker-base"
RERANKER_MODEL_REVISION = "0acb790751daef0c1a7c5191122056bd16ef9f09"
RERANKER_IMPLEMENTATION_ID = "Alibaba-NLP/new-impl"
RERANKER_IMPLEMENTATION_REVISION = "40ced75c3017eb27626c9d4ea981bde21a2662f4"
LOCAL_MODEL_REPOSITORY_ID = "Qwen/Qwen3-4B-GGUF"
LOCAL_MODEL_FILENAME = "Qwen3-4B-Q5_K_M.gguf"
LOCAL_MODEL_REVISION = "bc640142c66e1fdd12af0bd68f40445458f3869b"


_UNSAFE_SINGLE_LAYER_OVERRIDES = (
    "RAG_AGENT_RETRIEVER",
    "RAG_AGENT_RERANKER",
    "RAG_AGENT_RETRIEVER_MODEL",
    "RAG_AGENT_RERANKER_MODEL",
    "RAG_AGENT_BASE_URL",
    "RAG_AGENT_API_KEY",
)
_SUPPORTED_ENV_OVERRIDES = (
    "RAG_AGENT_GENERATOR",
    "RAG_AGENT_RETRIEVE_K",
    "RAG_AGENT_RERANK_K",
    "RAG_AGENT_RRF_K",
    "RAG_AGENT_MIN_RERANK_SCORE",
    "RAG_AGENT_MIN_CLAIM_SUPPORT_SCORE",
    "RAG_AGENT_CHUNK_SIZE",
    "RAG_AGENT_CHUNK_OVERLAP",
    "RAG_AGENT_AGENT_MAX_ACTIONS",
    "RAG_AGENT_MAX_REPAIRS",
    "RAG_AGENT_MODEL_CACHE_DIR",
    "RAG_AGENT_INDEX_DIR",
    "RAG_AGENT_LOCAL_MODEL_PATH",
    "RAG_AGENT_ALLOW_MODEL_DOWNLOAD",
    "RAG_AGENT_ALLOW_NETWORK",
    "RAG_AGENT_ONLINE_TIMEOUT_SECONDS",
)
_INTEGER_TEXT = re.compile(r"[+-]?\d+\Z")


def _parse_int(name: str, value: str) -> int:
    if not _INTEGER_TEXT.fullmatch(value):
        raise ConfigurationError(f"{name} must be an integer")
    try:
        return int(value)
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer") from None


def _parse_float(name: str, value: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number") from None


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ConfigurationError(f"{name} must be either true or false")


def _parse_generator(
    name: str, value: str
) -> Literal["local", "online"]:
    if value == "local":
        return "local"
    if value == "online":
        return "online"
    raise ConfigurationError(f"{name} must be either local or online")


def _parse_path(name: str, value: str) -> Path:
    if not value:
        raise ConfigurationError(f"{name} must be a nonempty path")
    return Path(value)


def _read_env[T](
    name: str,
    default: T,
    parser: Callable[[str, str], T],
) -> T:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return parser(name, raw_value)


def _require_actual_int(name: str, value: object, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        comparison = "positive" if minimum == 1 else "nonnegative"
        raise ConfigurationError(f"{name} must be an actual {comparison} integer")


def _require_threshold(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be a finite number in [0, 1]")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ConfigurationError(f"{name} must be a finite number in [0, 1]")


@dataclass(frozen=True, slots=True)
class AppConfig:
    generator: Literal["local", "online"] = "local"
    retrieve_k: int = 20
    rerank_k: int = 5
    rrf_k: int = 60
    min_rerank_score: float = 0.20
    min_claim_support_score: float = 0.40
    chunk_size: int = 384
    chunk_overlap: int = 48
    agent_max_actions: int = 6
    max_repairs: int = 1
    model_cache_dir: Path = Path(".local/model-cache")
    index_dir: Path = Path(".local/index")
    local_model_path: Path = Path(".local/model-cache/Qwen3-4B-Q5_K_M.gguf")
    allow_model_download: bool = False
    allow_network: bool = False
    online_timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        if self.generator not in ("local", "online"):
            raise ConfigurationError("generator must be either local or online")

        for name in (
            "retrieve_k",
            "rerank_k",
            "rrf_k",
            "chunk_size",
            "agent_max_actions",
        ):
            _require_actual_int(name, getattr(self, name), minimum=1)
        _require_actual_int("max_repairs", self.max_repairs, minimum=0)
        _require_actual_int("chunk_overlap", self.chunk_overlap, minimum=0)

        if self.retrieve_k < self.rerank_k:
            raise ConfigurationError(
                "retrieve_k must be greater than or equal to rerank_k"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ConfigurationError("chunk_overlap must be less than chunk_size")

        _require_threshold("min_rerank_score", self.min_rerank_score)
        _require_threshold(
            "min_claim_support_score", self.min_claim_support_score
        )
        if (
            isinstance(self.online_timeout_seconds, bool)
            or not isinstance(self.online_timeout_seconds, (int, float))
            or not math.isfinite(self.online_timeout_seconds)
            or self.online_timeout_seconds <= 0
        ):
            raise ConfigurationError(
                "online_timeout_seconds must be finite and positive"
            )

        for name in ("model_cache_dir", "index_dir", "local_model_path"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not str(value):
                raise ConfigurationError(f"{name} must be a nonempty Path")
        if type(self.allow_model_download) is not bool:
            raise ConfigurationError("allow_model_download must be a boolean")
        if type(self.allow_network) is not bool:
            raise ConfigurationError("allow_network must be a boolean")

        cache = self.model_cache_dir.resolve(strict=False)
        model = self.local_model_path.resolve(strict=False)
        if not model.is_relative_to(cache):
            raise ConfigurationError(
                "local_model_path must resolve inside model_cache_dir"
            )

    @classmethod
    def from_env(cls) -> AppConfig:
        for name in _UNSAFE_SINGLE_LAYER_OVERRIDES:
            if name in os.environ:
                raise ConfigurationError(
                    f"{name} is not an allowed configuration override"
                )

        configured_names = tuple(
            name for name in _SUPPORTED_ENV_OVERRIDES if name in os.environ
        )
        defaults = cls()
        try:
            return cls(
                generator=_read_env(
                    "RAG_AGENT_GENERATOR", defaults.generator, _parse_generator
                ),
                retrieve_k=_read_env(
                    "RAG_AGENT_RETRIEVE_K", defaults.retrieve_k, _parse_int
                ),
                rerank_k=_read_env(
                    "RAG_AGENT_RERANK_K", defaults.rerank_k, _parse_int
                ),
                rrf_k=_read_env("RAG_AGENT_RRF_K", defaults.rrf_k, _parse_int),
                min_rerank_score=_read_env(
                    "RAG_AGENT_MIN_RERANK_SCORE",
                    defaults.min_rerank_score,
                    _parse_float,
                ),
                min_claim_support_score=_read_env(
                    "RAG_AGENT_MIN_CLAIM_SUPPORT_SCORE",
                    defaults.min_claim_support_score,
                    _parse_float,
                ),
                chunk_size=_read_env(
                    "RAG_AGENT_CHUNK_SIZE", defaults.chunk_size, _parse_int
                ),
                chunk_overlap=_read_env(
                    "RAG_AGENT_CHUNK_OVERLAP", defaults.chunk_overlap, _parse_int
                ),
                agent_max_actions=_read_env(
                    "RAG_AGENT_AGENT_MAX_ACTIONS",
                    defaults.agent_max_actions,
                    _parse_int,
                ),
                max_repairs=_read_env(
                    "RAG_AGENT_MAX_REPAIRS", defaults.max_repairs, _parse_int
                ),
                model_cache_dir=_read_env(
                    "RAG_AGENT_MODEL_CACHE_DIR",
                    defaults.model_cache_dir,
                    _parse_path,
                ),
                index_dir=_read_env(
                    "RAG_AGENT_INDEX_DIR", defaults.index_dir, _parse_path
                ),
                local_model_path=_read_env(
                    "RAG_AGENT_LOCAL_MODEL_PATH",
                    defaults.local_model_path,
                    _parse_path,
                ),
                allow_model_download=_read_env(
                    "RAG_AGENT_ALLOW_MODEL_DOWNLOAD",
                    defaults.allow_model_download,
                    _parse_bool,
                ),
                allow_network=_read_env(
                    "RAG_AGENT_ALLOW_NETWORK", defaults.allow_network, _parse_bool
                ),
                online_timeout_seconds=_read_env(
                    "RAG_AGENT_ONLINE_TIMEOUT_SECONDS",
                    defaults.online_timeout_seconds,
                    _parse_float,
                ),
            )
        except ConfigurationError as error:
            context = ", ".join(configured_names) or "RAG_AGENT configuration"
            raise ConfigurationError(f"Invalid {context}: {error}") from None
