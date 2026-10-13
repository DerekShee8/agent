from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import enterprise_rag.generation as generation_module
from enterprise_rag.config import LOCAL_MODEL_FILENAME, AppConfig
from enterprise_rag.errors import (
    GenerationProtocolError,
    LocalModelMissingError,
    LocalModelResourceError,
    LocalModelTimeoutError,
    LocalModelUnavailableError,
)
from enterprise_rag.generation import ANSWER_SCHEMA, LlamaCppGenerator
from enterprise_rag.models import StructuredGenerationRequest

_DEFAULT_RESPONSE = object()


def valid_payload() -> dict[str, object]:
    return {
        "answer": "酒店上限是 CNY 600。",
        "claims": [
            {
                "text": "酒店上限是 CNY 600。",
                "support_ids": ["policy.md::0000"],
                "supporting_quotes": ["上限为 CNY 600"],
            }
        ],
    }


class FakeLlama:
    def __init__(
        self,
        response: object = _DEFAULT_RESPONSE,
        *,
        error: Exception | None = None,
    ) -> None:
        self._response = (
            _response(valid_payload()) if response is _DEFAULT_RESPONSE else response
        )
        self._error = error
        self.calls: list[dict[str, object]] = []

    def create_chat_completion(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class RecordingLlamaFactory:
    def __init__(self, instance: FakeLlama) -> None:
        self.instance = instance
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeLlama:
        self.calls.append(kwargs)
        return self.instance


class TemplateAwareFakeLlama(FakeLlama):
    def __init__(self) -> None:
        super().__init__()
        self.chat_format = "chat_template.default"
        self.chat_handler = None
        self.handler_calls: list[dict[str, object]] = []

        def base_handler(**kwargs: object) -> object:
            kwargs.pop("llama", None)
            self.handler_calls.append(dict(kwargs))
            return FakeLlama.create_chat_completion(self, **kwargs)

        self._chat_handlers = {self.chat_format: base_handler}

    def create_chat_completion(self, **kwargs: object) -> object:
        if not callable(self.chat_handler):
            raise TypeError("chat handler is not configured")
        return self.chat_handler(llama=self, **kwargs)


def _response(payload: object) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            }
        ]
    }


def _request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        messages=(
            {"role": "system", "content": "Use evidence only."},
            {"role": "user", "content": "What is the hotel cap?"},
        ),
        schema=ANSWER_SCHEMA,
        max_output_tokens=321,
    )


def _generator(llm: FakeLlama) -> LlamaCppGenerator:
    return LlamaCppGenerator(
        llm=llm,
        model_path=Path(LOCAL_MODEL_FILENAME),
        model_sha256="a" * 64,
    )


def test_local_generator_loads_only_verified_approved_gguf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    content = b"approved local model"
    expected_digest = hashlib.sha256(content).hexdigest()
    model_path = tmp_path / "model-cache" / LOCAL_MODEL_FILENAME
    model_path.parent.mkdir()
    model_path.write_bytes(content)
    factory = RecordingLlamaFactory(FakeLlama())
    monkeypatch.setattr(
        generation_module,
        "_LOCAL_MODEL_EXPECTED_BYTES",
        len(content),
    )
    monkeypatch.setattr(
        generation_module,
        "_LOCAL_MODEL_SHA256",
        expected_digest,
    )
    monkeypatch.setattr(
        generation_module,
        "_import_llama_runtime",
        lambda: factory,
    )
    config = AppConfig(
        model_cache_dir=model_path.parent,
        index_dir=tmp_path / "index",
        local_model_path=model_path,
    )

    generator = LlamaCppGenerator.from_config(config)

    assert factory.calls == [
        {
            "model_path": str(model_path.resolve()),
            "n_ctx": 8192,
            "n_gpu_layers": -1,
            "seed": 42,
            "verbose": False,
        }
    ]
    assert generator.model_path == model_path.resolve()
    assert generator.model_sha256 == expected_digest
    assert generator.backend_kind == "real"


def test_local_generator_rejects_missing_model_before_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "model-cache" / LOCAL_MODEL_FILENAME
    config = AppConfig(
        model_cache_dir=model_path.parent,
        index_dir=tmp_path / "index",
        local_model_path=model_path,
    )
    imported = False

    def unexpected_import() -> Any:
        nonlocal imported
        imported = True
        raise AssertionError("runtime must not be imported")

    monkeypatch.setattr(
        generation_module,
        "_import_llama_runtime",
        unexpected_import,
    )

    with pytest.raises(LocalModelMissingError):
        LlamaCppGenerator.from_config(config)

    assert imported is False


def test_local_generator_maps_value_error_during_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    content = b"approved local model"
    model_path = tmp_path / "model-cache" / LOCAL_MODEL_FILENAME
    model_path.parent.mkdir()
    model_path.write_bytes(content)
    monkeypatch.setattr(
        generation_module,
        "_LOCAL_MODEL_EXPECTED_BYTES",
        len(content),
    )
    monkeypatch.setattr(
        generation_module,
        "_LOCAL_MODEL_SHA256",
        hashlib.sha256(content).hexdigest(),
    )

    def failing_factory(**_kwargs: object) -> None:
        raise ValueError("failed to load model")

    monkeypatch.setattr(
        generation_module,
        "_import_llama_runtime",
        lambda: failing_factory,
    )
    config = AppConfig(
        model_cache_dir=model_path.parent,
        index_dir=tmp_path / "index",
        local_model_path=model_path,
    )

    with pytest.raises(LocalModelUnavailableError):
        LlamaCppGenerator.from_config(config)


@pytest.mark.parametrize("failure", ["size", "digest"])
def test_local_generator_rejects_unverified_model(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    content = b"model"
    model_path = tmp_path / "model-cache" / LOCAL_MODEL_FILENAME
    model_path.parent.mkdir()
    model_path.write_bytes(content)
    monkeypatch.setattr(
        generation_module,
        "_LOCAL_MODEL_EXPECTED_BYTES",
        len(content) + (1 if failure == "size" else 0),
    )
    monkeypatch.setattr(
        generation_module,
        "_LOCAL_MODEL_SHA256",
        "0" * 64 if failure == "digest" else hashlib.sha256(content).hexdigest(),
    )

    config = AppConfig(
        model_cache_dir=model_path.parent,
        index_dir=tmp_path / "index",
        local_model_path=model_path,
    )
    with pytest.raises(LocalModelResourceError):
        LlamaCppGenerator.from_config(config)


@pytest.mark.asyncio
async def test_local_generator_sends_deterministic_schema_request() -> None:
    llama = FakeLlama()
    generator = _generator(llama)

    payload = await generator.generate_json(_request())

    assert payload == valid_payload()
    assert len(llama.calls) == 1
    call = llama.calls[0]
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 321
    assert call["response_format"] == {
        "type": "json_object",
        "schema": generation_module._thaw_json(ANSWER_SCHEMA),
    }
    messages = call["messages"]
    assert isinstance(messages, list)
    assert messages[-1]["content"].endswith("/no_think")
    assert "only" in messages[0]["content"].lower()
    assert "reasoning" in messages[0]["content"].lower()


@pytest.mark.asyncio
async def test_local_generator_disables_template_thinking() -> None:
    llama = TemplateAwareFakeLlama()
    generator = _generator(llama)

    payload = await generator.generate_json(_request())

    assert payload == valid_payload()
    assert len(llama.handler_calls) == 1
    assert llama.handler_calls[0]["enable_thinking"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "not-json"}}]},
        _response(["not", "an", "object"]),
    ],
)
async def test_local_generator_rejects_missing_or_invalid_json(
    response: object,
) -> None:
    generator = _generator(FakeLlama(response))

    with pytest.raises(GenerationProtocolError):
        await generator.generate_json(_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend_error", "expected_error"),
    [
        (TimeoutError("slow"), LocalModelTimeoutError),
        (MemoryError("out of memory"), LocalModelResourceError),
        (ValueError("model context unavailable"), LocalModelUnavailableError),
        (RuntimeError("backend unavailable"), LocalModelUnavailableError),
    ],
)
async def test_local_generator_maps_backend_failures(
    backend_error: Exception,
    expected_error: type[Exception],
) -> None:
    generator = _generator(FakeLlama(error=backend_error))

    with pytest.raises(expected_error):
        await generator.generate_json(_request())
