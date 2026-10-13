from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from types import SimpleNamespace

import pytest

from enterprise_rag.errors import (
    ProfileNotAllowedError,
    ProviderAuthenticationError,
    ProviderBalanceError,
    ProviderInvalidRequestError,
    ProviderPermissionError,
    ProviderProtocolError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from enterprise_rag.generation import OnlineGenerator, OnlineUsageSnapshot
from enterprise_rag.models import StructuredGenerationRequest
from enterprise_rag.provider_profiles import ProviderProfile


class ProviderResponseError(Exception):
    def __init__(
        self,
        status_code: int,
        *,
        code: str | None = None,
        secret: str = "",
    ) -> None:
        super().__init__(f"provider failure {secret}")
        self.status_code = status_code
        self.body: Mapping[str, object] = (
            {"error": {"code": code}} if code is not None else {}
        )


class FakeCompletions:
    def __init__(
        self,
        payload: Mapping[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload or {"ok": True}
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=(
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(self.payload),
                    )
                ),
            )
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class RecordingClientFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeClient:
        self.calls.append(dict(kwargs))
        return self.client


def profile(**overrides: object) -> ProviderProfile:
    values: dict[str, object] = {
        "name": "relay",
        "base_url": "https://relay.example.invalid/v1",
        "model_id": "deepseek-v4.1-flash",
        "api_key_env": "RAG_AGENT_RELAY_API_KEY",
        "privacy_terms_url": "https://relay.example.invalid/privacy",
        "input_price_per_million": Decimal("1.25"),
        "output_price_per_million": Decimal("2.5"),
        "request_limit": 3,
        "budget_limit_cny": Decimal("1"),
        "synthetic_data_only": True,
        "enabled": True,
    }
    values.update(overrides)
    return ProviderProfile(**values)  # type: ignore[arg-type]


def request(*, max_output_tokens: int = 64) -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        messages=(
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "synthetic demo input"},
        ),
        schema={
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
            "additionalProperties": False,
        },
        max_output_tokens=max_output_tokens,
    )


@pytest.mark.asyncio
async def test_online_generator_uses_profile_schema_and_zero_sdk_retries() -> None:
    completions = FakeCompletions({"ok": True})
    factory = RecordingClientFactory(FakeClient(completions))
    generator = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=12.5,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "resolved-secret"},
        client_factory=factory,
    )

    payload = await generator.generate_json(request())

    assert payload == {"ok": True}
    assert factory.calls == [
        {
            "api_key": "resolved-secret",
            "base_url": "https://relay.example.invalid/v1",
            "timeout": 12.5,
            "max_retries": 0,
        }
    ]
    assert completions.calls == [
        {
            "model": "deepseek-v4.1-flash",
            "messages": [
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "synthetic demo input"},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "enterprise_rag_output",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "required": ["ok"],
                        "properties": {"ok": {"type": "boolean"}},
                        "additionalProperties": False,
                    },
                },
            },
            "temperature": 0,
            "max_completion_tokens": 64,
        }
    ]
    assert "resolved-secret" not in repr(generator)


@pytest.mark.asyncio
async def test_online_generator_enforces_network_and_request_caps_before_call() -> None:
    completions = FakeCompletions()
    factory = RecordingClientFactory(FakeClient(completions))
    blocked = OnlineGenerator(
        profile(),
        allow_network=False,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        client_factory=factory,
    )

    with pytest.raises(ProfileNotAllowedError):
        await blocked.generate_json(request())
    assert factory.calls == []

    limited = OnlineGenerator(
        profile(request_limit=1),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        client_factory=factory,
    )
    assert await limited.generate_json(request()) == {"ok": True}
    with pytest.raises(ProfileNotAllowedError, match="request limit"):
        await limited.generate_json(request())
    assert len(completions.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderResponseError(401), ProviderAuthenticationError),
        (ProviderResponseError(403), ProviderPermissionError),
        (ProviderResponseError(402), ProviderBalanceError),
        (
            ProviderResponseError(429, code="insufficient_quota"),
            ProviderQuotaError,
        ),
        (ProviderResponseError(429), ProviderRateLimitError),
        (TimeoutError("timed out"), ProviderTimeoutError),
        (ProviderResponseError(503), ProviderUnavailableError),
        (ProviderResponseError(400), ProviderInvalidRequestError),
        (
            ProviderResponseError(400, code="json_schema_not_supported"),
            ProviderProtocolError,
        ),
    ],
)
async def test_online_generator_maps_provider_failures_without_secret_leaks(
    error: Exception,
    expected: type[Exception],
) -> None:
    secret = "relay-secret-value"
    if isinstance(error, ProviderResponseError):
        error.args = (f"provider failure {secret}",)
    completions = FakeCompletions(error=error)
    generator = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": secret},
        client_factory=RecordingClientFactory(FakeClient(completions)),
    )

    with pytest.raises(expected) as caught:
        await generator.generate_json(request())

    assert secret not in str(caught.value)
    assert "relay.example.invalid" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [None, "not-json", "[]"],
)
async def test_online_generator_rejects_unconstrained_or_malformed_content(
    content: str | None,
) -> None:
    completions = FakeCompletions()

    async def create(**kwargs: object) -> object:
        completions.calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=(SimpleNamespace(message=SimpleNamespace(content=content)),)
        )

    completions.create = create  # type: ignore[method-assign]
    generator = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        client_factory=RecordingClientFactory(FakeClient(completions)),
    )

    with pytest.raises(ProviderProtocolError):
        await generator.generate_json(request())


class ClosableFakeClient(FakeClient):
    def __init__(self, completions: FakeCompletions) -> None:
        super().__init__(completions)
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


@pytest.mark.asyncio
async def test_online_generator_closes_each_per_call_client_exactly_once() -> None:
    client = ClosableFakeClient(FakeCompletions())
    generator = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        client_factory=RecordingClientFactory(client),
    )

    assert await generator.generate_json(request()) == {"ok": True}
    assert client.aclose_calls == 1

    failing = ClosableFakeClient(
        FakeCompletions(error=ProviderResponseError(503)),
    )
    blocked = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        client_factory=RecordingClientFactory(failing),
    )

    with pytest.raises(ProviderUnavailableError):
        await blocked.generate_json(request())
    assert failing.aclose_calls == 1


@pytest.mark.asyncio
async def test_online_generator_usage_snapshot_exposes_only_counters() -> None:
    completions = FakeCompletions()
    generator = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "relay-secret-value"},
        client_factory=RecordingClientFactory(FakeClient(completions)),
    )

    assert await generator.usage_snapshot() == OnlineUsageSnapshot(0, Decimal(0))

    await generator.generate_json(request())
    snapshot = await generator.usage_snapshot()

    assert snapshot.request_number == 1
    assert snapshot.spent_cny > Decimal(0)
    assert "relay-secret-value" not in repr(snapshot)
    assert set(snapshot.__slots__) == {"request_number", "spent_cny"}


class FakeLlama:
    def __init__(self, *, with_close: bool = True) -> None:
        self.close_calls = 0
        if not with_close:
            self.close = None  # type: ignore[method-assign]

    def close(self) -> None:
        self.close_calls += 1


def _llama_generator(llm: object) -> object:
    from pathlib import Path

    from enterprise_rag.generation import LlamaCppGenerator

    return LlamaCppGenerator(
        llm=llm,
        model_path=Path("model.gguf"),
        model_sha256="a" * 64,
    )


class AsyncCloseOnlyFakeClient(FakeClient):
    """Mimics SDKs whose async client exposes only an async ``close``."""

    def __init__(self, completions: FakeCompletions) -> None:
        super().__init__(completions)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class SyncCloseOnlyFakeClient(FakeClient):
    def __init__(self, completions: FakeCompletions) -> None:
        super().__init__(completions)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_type",
    [AsyncCloseOnlyFakeClient, SyncCloseOnlyFakeClient],
)
async def test_online_generator_closes_a_close_only_client_once(
    client_type: type[FakeClient],
) -> None:
    """An async ``close`` must be awaited, not called and dropped un-awaited."""

    client = client_type(FakeCompletions())
    generator = OnlineGenerator(
        profile(),
        allow_network=True,
        timeout_seconds=10,
        input_token_cap=1_000,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        client_factory=RecordingClientFactory(client),
    )

    assert await generator.generate_json(request()) == {"ok": True}
    assert client.close_calls == 1  # type: ignore[attr-defined]


def test_llama_cpp_generator_close_is_idempotent_and_optional() -> None:
    llm = FakeLlama()
    generator = _llama_generator(llm)

    generator.close()  # type: ignore[attr-defined]
    generator.close()  # type: ignore[attr-defined]

    assert llm.close_calls == 1

    silent = _llama_generator(FakeLlama(with_close=False))
    silent.close()  # type: ignore[attr-defined]
