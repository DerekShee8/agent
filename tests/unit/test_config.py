from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from enterprise_rag.config import (
    EMBEDDING_MODEL_ID,
    EMBEDDING_MODEL_REVISION,
    LOCAL_MODEL_FILENAME,
    LOCAL_MODEL_REPOSITORY_ID,
    LOCAL_MODEL_REVISION,
    RERANKER_IMPLEMENTATION_ID,
    RERANKER_IMPLEMENTATION_REVISION,
    RERANKER_MODEL_ID,
    RERANKER_MODEL_REVISION,
    AppConfig,
)
from enterprise_rag.errors import ConfigurationError

CONFIG_ENV_NAMES = (
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


@pytest.fixture(autouse=True)
def clear_config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIG_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_default_config_is_exactly_offline_and_pinned() -> None:
    config = AppConfig.from_env()

    assert config == AppConfig(
        generator="local",
        retrieve_k=20,
        rerank_k=5,
        rrf_k=60,
        min_rerank_score=0.20,
        min_claim_support_score=0.40,
        chunk_size=384,
        chunk_overlap=48,
        agent_max_actions=6,
        max_repairs=1,
        model_cache_dir=Path(".local/model-cache"),
        index_dir=Path(".local/index"),
        local_model_path=Path(".local/model-cache/Qwen3-4B-Q5_K_M.gguf"),
        allow_model_download=False,
        allow_network=False,
        online_timeout_seconds=45.0,
    )
    assert EMBEDDING_MODEL_ID == "Alibaba-NLP/gte-multilingual-base"
    assert EMBEDDING_MODEL_REVISION == "087a024525fd6e2fe749cb4679d218d8bcc95bdd"
    assert RERANKER_MODEL_ID == "Alibaba-NLP/gte-multilingual-reranker-base"
    assert RERANKER_MODEL_REVISION == "0acb790751daef0c1a7c5191122056bd16ef9f09"
    assert RERANKER_IMPLEMENTATION_ID == "Alibaba-NLP/new-impl"
    assert RERANKER_IMPLEMENTATION_REVISION == (
        "40ced75c3017eb27626c9d4ea981bde21a2662f4"
    )
    assert LOCAL_MODEL_REPOSITORY_ID == "Qwen/Qwen3-4B-GGUF"
    assert LOCAL_MODEL_FILENAME == "Qwen3-4B-Q5_K_M.gguf"
    assert LOCAL_MODEL_REVISION == "bc640142c66e1fdd12af0bd68f40445458f3869b"


def test_from_env_parses_every_supported_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = Path("runtime-cache")
    values = {
        "RAG_AGENT_GENERATOR": "online",
        "RAG_AGENT_RETRIEVE_K": "30",
        "RAG_AGENT_RERANK_K": "7",
        "RAG_AGENT_RRF_K": "80",
        "RAG_AGENT_MIN_RERANK_SCORE": "0.75",
        "RAG_AGENT_MIN_CLAIM_SUPPORT_SCORE": "0.625",
        "RAG_AGENT_CHUNK_SIZE": "512",
        "RAG_AGENT_CHUNK_OVERLAP": "64",
        "RAG_AGENT_AGENT_MAX_ACTIONS": "9",
        "RAG_AGENT_MAX_REPAIRS": "0",
        "RAG_AGENT_MODEL_CACHE_DIR": str(cache),
        "RAG_AGENT_INDEX_DIR": "runtime-index",
        "RAG_AGENT_LOCAL_MODEL_PATH": str(cache / "model.gguf"),
        "RAG_AGENT_ALLOW_MODEL_DOWNLOAD": "true",
        "RAG_AGENT_ALLOW_NETWORK": "TRUE",
        "RAG_AGENT_ONLINE_TIMEOUT_SECONDS": "12.5",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    config = AppConfig.from_env()

    assert config.generator == "online"
    assert config.retrieve_k == 30
    assert config.rerank_k == 7
    assert config.rrf_k == 80
    assert config.min_rerank_score == 0.75
    assert config.min_claim_support_score == 0.625
    assert config.chunk_size == 512
    assert config.chunk_overlap == 64
    assert config.agent_max_actions == 9
    assert config.max_repairs == 0
    assert config.model_cache_dir == cache
    assert config.index_dir == Path("runtime-index")
    assert config.local_model_path == cache / "model.gguf"
    assert config.allow_model_download is True
    assert config.allow_network is True
    assert config.online_timeout_seconds == 12.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RAG_AGENT_GENERATOR", "hybrid"),
        ("RAG_AGENT_RETRIEVE_K", "0"),
        ("RAG_AGENT_RERANK_K", "-1"),
        ("RAG_AGENT_RRF_K", "1.5"),
        ("RAG_AGENT_CHUNK_SIZE", "abc"),
        ("RAG_AGENT_AGENT_MAX_ACTIONS", "0"),
        ("RAG_AGENT_MAX_REPAIRS", "-1"),
        ("RAG_AGENT_ALLOW_NETWORK", "yes"),
        ("RAG_AGENT_ALLOW_MODEL_DOWNLOAD", "1"),
        ("RAG_AGENT_MODEL_CACHE_DIR", ""),
        ("RAG_AGENT_LOCAL_MODEL_PATH", ""),
    ],
)
def test_from_env_rejects_invalid_scalar_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=name):
        AppConfig.from_env()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retrieve_k": True},
        {"rerank_k": 0},
        {"rrf_k": -1},
        {"chunk_size": True},
        {"agent_max_actions": 0},
        {"max_repairs": True},
        {"max_repairs": -1},
        {"retrieve_k": 4, "rerank_k": 5},
        {"chunk_overlap": -1},
        {"chunk_size": 10, "chunk_overlap": 10},
        {"chunk_size": 10, "chunk_overlap": 11},
    ],
)
def test_config_enforces_integer_and_relational_invariants(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("min_rerank_score", float("nan")),
        ("min_rerank_score", float("inf")),
        ("min_rerank_score", -0.01),
        ("min_rerank_score", 1.01),
        ("min_claim_support_score", float("-inf")),
        ("min_claim_support_score", 1.1),
        ("online_timeout_seconds", float("nan")),
        ("online_timeout_seconds", float("inf")),
        ("online_timeout_seconds", 0.0),
        ("online_timeout_seconds", -1.0),
    ],
)
def test_config_rejects_invalid_thresholds_and_timeout(
    field_name: str, value: float
) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig(**{field_name: value})


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-0.5"])
def test_from_env_rejects_nonfinite_or_nonpositive_timeout(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RAG_AGENT_ONLINE_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigurationError, match="ONLINE_TIMEOUT"):
        AppConfig.from_env()


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-0.1", "1.1"])
def test_from_env_rejects_invalid_thresholds(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RAG_AGENT_MIN_RERANK_SCORE", value)

    with pytest.raises(ConfigurationError, match="MIN_RERANK_SCORE"):
        AppConfig.from_env()


def test_local_model_path_must_be_inside_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"

    with pytest.raises(ConfigurationError, match="inside model_cache_dir"):
        AppConfig(model_cache_dir=cache, local_model_path=cache / ".." / "escape.gguf")


def test_local_model_path_rejects_sibling_prefix_trick(tmp_path: Path) -> None:
    cache = tmp_path / "model-cache"
    sibling = tmp_path / "model-cache-evil" / "model.gguf"

    with pytest.raises(ConfigurationError, match="inside model_cache_dir"):
        AppConfig(model_cache_dir=cache, local_model_path=sibling)


@pytest.mark.parametrize(
    "name",
    [
        "RAG_AGENT_RETRIEVER",
        "RAG_AGENT_RERANKER",
        "RAG_AGENT_RETRIEVER_MODEL",
        "RAG_AGENT_RERANKER_MODEL",
        "RAG_AGENT_BASE_URL",
        "RAG_AGENT_API_KEY",
    ],
)
def test_single_layer_overrides_fail_closed_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    secret = "injected-super-secret-value"
    monkeypatch.setenv(name, secret)

    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigurationError) as caught:
        AppConfig.from_env()

    assert name in str(caught.value)
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in caplog.text


def test_config_is_frozen_and_repr_has_no_environment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "credential-must-not-appear"
    monkeypatch.setenv("RAG_AGENT_RELAY_API_KEY", secret)
    config = AppConfig.from_env()

    with pytest.raises((FrozenInstanceError, AttributeError)):
        config.generator = "online"  # type: ignore[misc]

    assert secret not in repr(config)
    assert not hasattr(config, "api_key")
