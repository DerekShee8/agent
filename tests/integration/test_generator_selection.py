from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest

from enterprise_rag.application import build_application
from enterprise_rag.cli import main
from enterprise_rag.config import AppConfig
from enterprise_rag.errors import ConfigurationError, ProviderAuthenticationError
from enterprise_rag.execution_trace import TraceRecorder
from enterprise_rag.generation import (
    AgentPlanner,
    AnswerGenerator,
    ExplicitLocalFallbackGenerator,
)
from enterprise_rag.grounding import GateDecision
from enterprise_rag.models import StructuredGenerationRequest
from enterprise_rag.provider_profiles import ProviderProfile
from enterprise_rag.rag_service import RagService
from tests.fakes import (
    FakeClock,
    ScriptedGate,
    ScriptedReranker,
    ScriptedRetriever,
    ScriptedValidator,
    reranked,
)
from tests.integration.test_cli import FakeApplication
from tests.integration.test_real_composition import (
    RealEncoderDouble,
    RealGeneratorDouble,
    RealRerankerDouble,
    RealSupportScorerDouble,
    _real_index,
)


class StructuredBackend:
    backend_kind = "real"

    def __init__(
        self,
        *,
        error: Exception | None = None,
    ) -> None:
        self.error = error
        self.calls = 0

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        del request
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {
            "answer": "The hotel cap is CNY 600.",
            "claims": [
                {
                    "text": "The hotel cap is CNY 600.",
                    "support_ids": ["policy.md::0000"],
                    "supporting_quotes": ["hotel cap is CNY 600"],
                }
            ],
        }


def service(generator: AnswerGenerator) -> RagService:
    calls: list[str] = []
    evidence = (
        reranked(
            "policy.md::0000",
            "The hotel cap is CNY 600.",
            score=0.91,
            rank=1,
        ),
    )
    return RagService(
        retriever=ScriptedRetriever(
            calls,
            tuple(item.candidate for item in evidence),
        ),
        reranker=ScriptedReranker(calls, evidence),
        gate=ScriptedGate(calls, GateDecision(True, evidence, None)),
        generator=generator,
        validator=ScriptedValidator(calls, ((),)),
        retrieve_k=20,
        rerank_k=5,
        trace_factory=lambda: TraceRecorder("run-selection", clock=FakeClock()),
    )


@pytest.mark.asyncio
async def test_online_failure_does_not_fallback_without_explicit_wrapper() -> None:
    online = StructuredBackend(
        error=ProviderAuthenticationError("secret provider detail")
    )
    local = StructuredBackend()

    result = await service(AnswerGenerator(online)).ask("question")

    assert result.status == "failed"
    assert result.error_category == "authentication"
    assert online.calls == 1
    assert local.calls == 0
    assert all(event.stage != "backend_switch" for event in result.trace.events)


@pytest.mark.asyncio
async def test_explicit_fallback_switches_only_generator_and_keeps_validation() -> None:
    online = StructuredBackend(
        error=ProviderAuthenticationError("secret provider detail")
    )
    local = StructuredBackend()
    selected = ExplicitLocalFallbackGenerator(online, local)

    result = await service(AnswerGenerator(selected)).ask("question")

    assert result.status == "answered"
    assert online.calls == 1
    assert local.calls == 1
    switch_events = [
        event for event in result.trace.events if event.stage == "backend_switch"
    ]
    assert len(switch_events) == 1
    assert switch_events[0].status == "degraded"
    assert switch_events[0].error_category == "authentication"
    assert switch_events[0].attributes == {
        "from_backend": "online",
        "to_backend": "local",
    }
    assert result.trace.events[-1].stage == "grounding_validation"


@pytest.mark.asyncio
async def test_agent_planner_reports_an_explicit_online_to_local_switch() -> None:
    online = StructuredBackend(
        error=ProviderAuthenticationError("secret provider detail")
    )
    local = StructuredBackend()

    async def local_action(
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        del request
        local.calls += 1
        return {"action": "refuse", "reason": "provider unavailable"}

    local.generate_json = local_action  # type: ignore[method-assign]
    planner = AgentPlanner(ExplicitLocalFallbackGenerator(online, local))

    action = await planner.next_action("policy question", ())

    assert action.name == "refuse"
    assert planner.consume_backend_switch() == "authentication"
    assert planner.consume_backend_switch() is None


def registered_profile() -> ProviderProfile:
    return ProviderProfile(
        name="relay",
        base_url="https://relay.example.invalid/v1",
        model_id="deepseek-v4.1-flash",
        api_key_env="RAG_AGENT_RELAY_API_KEY",
        privacy_terms_url="https://relay.example.invalid/privacy",
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
        request_limit=2,
        budget_limit_cny=Decimal("1"),
        synthetic_data_only=True,
        enabled=True,
    )


def composition_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        index_dir=tmp_path / "index",
        model_cache_dir=tmp_path / "models",
        local_model_path=tmp_path / "models" / "Qwen3-4B-Q5_K_M.gguf",
        allow_network=True,
    )


def test_composition_selects_registered_online_backend_without_loading_local(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def online(_config: AppConfig, selected: ProviderProfile) -> StructuredBackend:
        calls.append(f"online:{selected.name}")
        return StructuredBackend()

    def local(_config: AppConfig) -> RealGeneratorDouble:
        calls.append("local")
        return RealGeneratorDouble()

    application = build_application(
        composition_config(tmp_path),
        generator_choice="online",
        provider_name="relay",
        provider_profiles_path=tmp_path / "providers.toml",
        profile_loader=lambda _path: (registered_profile(),),
        online_generator_factory=online,
        generator_factory=local,
        index_loader=lambda _path: _real_index(),
        encoder_factory=lambda _config: RealEncoderDouble(),
        reranker_factory=lambda _config: RealRerankerDouble(),
        support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
    )

    assert application.generator_kind == "online"
    assert application.provider_name == "relay"
    assert application.fallback_local_enabled is False
    assert calls == ["online:relay"]


def test_online_composition_builds_local_backend_only_for_explicit_fallback(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def local(_config: AppConfig) -> RealGeneratorDouble:
        calls.append("local")
        return RealGeneratorDouble()

    application = build_application(
        composition_config(tmp_path),
        generator_choice="online",
        provider_name="relay",
        fallback_local=True,
        provider_profiles_path=tmp_path / "providers.toml",
        profile_loader=lambda _path: (registered_profile(),),
        online_generator_factory=lambda _config, _profile: StructuredBackend(),
        generator_factory=local,
        index_loader=lambda _path: _real_index(),
        encoder_factory=lambda _config: RealEncoderDouble(),
        reranker_factory=lambda _config: RealRerankerDouble(),
        support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
    )

    assert application.generator_kind == "online"
    assert application.fallback_local_enabled is True
    assert calls == ["local"]


def test_missing_online_profile_fails_before_loading_heavy_backends(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def load_index(_path: Path) -> object:
        calls.append("index")
        return _real_index()

    with pytest.raises(ConfigurationError, match="profile"):
        build_application(
            composition_config(tmp_path),
            generator_choice="online",
            provider_name="missing",
            provider_profiles_path=tmp_path / "providers.toml",
            profile_loader=lambda _path: (),
            index_loader=load_index,  # type: ignore[arg-type]
            encoder_factory=lambda _config: RealEncoderDouble(),
            reranker_factory=lambda _config: RealRerankerDouble(),
            support_scorer_factory=lambda _reranker: RealSupportScorerDouble(),
        )

    assert calls == []


def test_cli_passes_online_profile_and_explicit_fallback_to_composition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def build(**kwargs: object) -> FakeApplication:
        calls.append(dict(kwargs))
        return FakeApplication()

    monkeypatch.setattr("enterprise_rag.cli.build_application", build)

    exit_code = main(
        [
            "rag",
            "ask",
            "question",
            "--generator",
            "online",
            "--provider",
            "relay",
            "--fallback-local",
            "--format",
            "json",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    assert calls == [
        {
            "compose_rag": True,
            "compose_agent": False,
            "generator_choice": "online",
            "provider_name": "relay",
            "fallback_local": True,
        }
    ]


def test_cli_requires_registered_provider_name_for_online_generation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "rag",
            "ask",
            "question",
            "--generator",
            "online",
        ],
        application=FakeApplication(),
    )

    assert exit_code == 2
    assert "--provider" in capsys.readouterr().err
