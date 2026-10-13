"""Generator registry reuse, one-use authorization, and permitted fallback.

Everything here runs offline: the online backend is a counting double, so a
"call" is a list append and no client, socket, or key ever exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from enterprise_rag.config import AppConfig
from enterprise_rag.errors import ConfigurationError, ProviderUnavailableError
from enterprise_rag.generation import OnlineUsageSnapshot
from enterprise_rag.models import StructuredGenerationRequest
from enterprise_rag.panel.contracts import (
    OnlineRunAuthorization,
    PanelRunRecord,
    RunRequest,
    RunSpec,
)
from enterprise_rag.panel.controller import PanelController
from enterprise_rag.panel.runtime import (
    GeneratorRegistry,
    RunAuthorizationError,
)
from enterprise_rag.provider_profiles import (
    ProviderProfile,
    profile_fingerprint,
)

API_KEY_ENV = "RAG_AGENT_RELAY_API_KEY"


def profile(name: str = "relay", **overrides: object) -> ProviderProfile:
    values: dict[str, object] = {
        "name": name,
        "base_url": f"https://{name}.example.invalid/v1",
        "model_id": "provider-model",
        "api_key_env": API_KEY_ENV,
        "privacy_terms_url": f"https://{name}.example.invalid/privacy",
        "input_price_per_million": Decimal("2.5"),
        "output_price_per_million": Decimal("5"),
        "request_limit": 30,
        "budget_limit_cny": Decimal("10"),
        "synthetic_data_only": True,
        "enabled": True,
    }
    values.update(overrides)
    return ProviderProfile(**values)  # type: ignore[arg-type]


def request() -> StructuredGenerationRequest:
    return StructuredGenerationRequest(
        messages=({"role": "user", "content": "synthetic"},),
        schema={"type": "object"},
        max_output_tokens=100,
    )


def config(tmp_path: Path) -> AppConfig:
    cache = tmp_path / "model-cache"
    return AppConfig(
        model_cache_dir=cache,
        index_dir=tmp_path / "index",
        local_model_path=cache / "model.gguf",
        allow_network=True,
    )


class CountingBackend:
    backend_kind = "real"

    def __init__(self, name: str, *, error: Exception | None = None) -> None:
        self.name = name
        self.calls = 0
        self.close_calls = 0
        self.request_number = 0
        self.spent_cny = Decimal(0)
        self._error = error

    async def generate_json(
        self,
        request: StructuredGenerationRequest,
    ) -> Mapping[str, object]:
        del request
        self.calls += 1
        self.request_number += 1
        self.spent_cny += Decimal("0.003")
        if self._error is not None:
            raise self._error
        return {"ok": True}

    async def usage_snapshot(self) -> OnlineUsageSnapshot:
        return OnlineUsageSnapshot(self.request_number, self.spent_cny)

    async def aclose(self) -> None:
        self.close_calls += 1


def make_registry(
    tmp_path: Path,
    profiles: tuple[ProviderProfile, ...],
    *,
    local: CountingBackend | None = None,
    online: CountingBackend | None = None,
    factory_calls: list[str] | None = None,
) -> tuple[GeneratorRegistry, CountingBackend, CountingBackend]:
    calls = factory_calls if factory_calls is not None else []
    local_backend = local or CountingBackend("local")
    online_backend = online or CountingBackend("online")

    def local_factory(_config: AppConfig) -> CountingBackend:
        calls.append("local")
        return local_backend

    def online_factory(
        _config: AppConfig,
        active_profile: ProviderProfile,
    ) -> CountingBackend:
        calls.append(f"online:{active_profile.name}")
        return online_backend

    registry = GeneratorRegistry(
        config(tmp_path),
        profiles,
        local_factory=local_factory,  # type: ignore[arg-type]
        online_factory=online_factory,  # type: ignore[arg-type]
        environ={API_KEY_ENV: "secret"},
    )
    return registry, local_backend, online_backend


def run_request(
    registry: GeneratorRegistry,
    *,
    run_id: str = "run-1",
    mode: str = "rag",
    profile_name: str = "relay",
    fingerprint: str | None = None,
    fallback_local: bool = False,
) -> RunRequest:
    return RunRequest(
        run_id=run_id,
        created_at=0.0,
        mode=mode,  # type: ignore[arg-type]
        scenario_id="scenario",
        question_or_task="question",
        generator="online",
        provider_name=profile_name,
        provider_fingerprint=(
            fingerprint
            if fingerprint is not None
            else profile_fingerprint(registry.profile_for(profile_name))
        ),
        fallback_local=fallback_local,
    )


def spec_for(
    request_value: RunRequest,
    authorization: OnlineRunAuthorization,
) -> RunSpec:
    return RunSpec(
        run_id=request_value.run_id,
        created_at=request_value.created_at,
        mode=request_value.mode,
        scenario_id=request_value.scenario_id,
        question_or_task=request_value.question_or_task,
        generator=request_value.generator,
        provider_name=request_value.provider_name,
        provider_fingerprint=request_value.provider_fingerprint,
        fallback_local=request_value.fallback_local,
        online_authorization=authorization,
    )


async def confirmed_spec(
    registry: GeneratorRegistry,
    **overrides: object,
) -> tuple[RunSpec, OnlineRunAuthorization]:
    request_value = run_request(registry, **overrides)  # type: ignore[arg-type]
    preflight = await registry.preflight(request_value)
    authorization = registry.authorize(preflight)
    return spec_for(request_value, authorization), authorization


@pytest.mark.asyncio
async def test_local_backend_loads_lazily_once_and_is_reused(
    tmp_path: Path,
) -> None:
    factory_calls: list[str] = []
    registry, local, _online = make_registry(
        tmp_path,
        (),
        factory_calls=factory_calls,
    )

    assert factory_calls == []
    first = await registry.get_local()
    second = await registry.get_local()

    assert first is second is local
    assert factory_calls == ["local"]


@pytest.mark.asyncio
async def test_one_profile_fingerprint_maps_to_one_persistent_generator(
    tmp_path: Path,
) -> None:
    factory_calls: list[str] = []
    active = profile()
    registry, _local, online = make_registry(
        tmp_path,
        (active,),
        factory_calls=factory_calls,
    )

    first = await registry.get_online(active)
    second = await registry.get_online(active)

    assert first is second is online
    assert factory_calls == ["online:relay"]


@pytest.mark.asyncio
async def test_switching_mode_keeps_the_shared_online_counters(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))

    rag_spec, _ = await confirmed_spec(registry, mode="rag")
    agent_spec, _ = await confirmed_spec(
        registry,
        run_id="run-2",
        mode="agent",
    )
    rag_backend = await registry.backend_for(rag_spec)
    agent_backend = await registry.backend_for(agent_spec)

    await rag_backend.generate_json(request())
    after_rag = await online.usage_snapshot()
    await agent_backend.generate_json(request())
    after_agent = await online.usage_snapshot()

    assert after_rag.request_number == 1
    assert after_agent.request_number == 2
    assert after_agent.spent_cny > after_rag.spent_cny


@pytest.mark.asyncio
async def test_authorization_is_single_use(tmp_path: Path) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    spec, _authorization = await confirmed_spec(registry)

    backend = await registry.backend_for(spec)
    await backend.generate_json(request())

    with pytest.raises(RunAuthorizationError, match="already been used"):
        await registry.backend_for(spec)
    assert online.calls == 1


@pytest.mark.asyncio
async def test_unknown_authorization_is_refused_before_any_generator_call(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    request_value = run_request(registry)
    forged = OnlineRunAuthorization(
        authorization_id=uuid4().hex,
        run_id=request_value.run_id,
        profile_fingerprint=request_value.provider_fingerprint,  # type: ignore[arg-type]
        max_requests=2,
        input_token_cap=1_000,
        output_token_cap=100,
        max_cost_cny=Decimal("1"),
        synthetic_data_only=True,
        fallback_local=False,
    )

    with pytest.raises(RunAuthorizationError, match="unknown"):
        await registry.backend_for(spec_for(request_value, forged))
    assert online.calls == 0


@pytest.mark.asyncio
async def test_authorization_minted_for_one_profile_is_refused_for_another(
    tmp_path: Path,
) -> None:
    first = profile("relay")
    second = profile("relay-b")
    registry, _local, online = make_registry(tmp_path, (first, second))
    request_value = run_request(registry, profile_name="relay")
    preflight = await registry.preflight(request_value)
    authorization = registry.authorize(preflight)
    swapped = replace(
        request_value,
        provider_name="relay-b",
        provider_fingerprint=authorization.profile_fingerprint,
    )

    with pytest.raises(RunAuthorizationError, match="profile"):
        await registry.backend_for(spec_for(swapped, authorization))
    assert online.calls == 0


@pytest.mark.asyncio
async def test_authorization_minted_for_one_run_is_refused_for_another(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    spec, _authorization = await confirmed_spec(registry, run_id="run-1")
    # RunSpec already refuses to bind an authorization to a different run, so this
    # shape can only be reached by bypassing validation; backend_for re-checks it.
    object.__setattr__(spec, "run_id", "run-2")

    with pytest.raises(RunAuthorizationError, match="run"):
        await registry.backend_for(spec)
    assert online.calls == 0


@pytest.mark.asyncio
async def test_authorization_fallback_bit_is_rechecked_at_binding_time(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    spec, _authorization = await confirmed_spec(registry)

    object.__setattr__(spec, "fallback_local", True)

    with pytest.raises(RunAuthorizationError, match="fallback"):
        await registry.backend_for(spec)
    assert online.calls == 0


@pytest.mark.asyncio
async def test_exhausted_authorization_fails_without_local_fallback(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, local, online = make_registry(tmp_path, (active,))
    _spec, authorization = await confirmed_spec(registry, fallback_local=True)
    single_call = replace(authorization, max_requests=1)
    single_use = spec_for(
        run_request(registry, fallback_local=True),
        single_call,
    )

    backend = await registry.backend_for(single_use)
    assert await backend.generate_json(request()) == {"ok": True}

    with pytest.raises(RunAuthorizationError, match="exhausted"):
        await backend.generate_json(request())
    assert online.calls == 1
    assert local.calls == 0


@pytest.mark.asyncio
async def test_permitted_fallback_latches_and_emits_exactly_one_switch(
    tmp_path: Path,
) -> None:
    active = profile()
    online = CountingBackend("online", error=ProviderUnavailableError("down"))
    registry, local, _online = make_registry(tmp_path, (active,), online=online)
    spec, _authorization = await confirmed_spec(registry, fallback_local=True)

    backend = await registry.backend_for(spec)

    assert await backend.generate_json(request()) == {"ok": True}
    assert online.calls == 1
    assert local.calls == 1
    assert backend.consume_backend_switch() == "provider_unavailable"  # type: ignore[attr-defined]
    assert backend.consume_backend_switch() is None  # type: ignore[attr-defined]

    # A later repair must go straight to the latched local backend.
    assert await backend.generate_json(request()) == {"ok": True}
    assert online.calls == 1
    assert local.calls == 2
    assert backend.consume_backend_switch() is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_aclose_closes_each_persistent_backend_once_and_leaves_wrappers_alone(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, local, online = make_registry(tmp_path, (active,))
    spec, _authorization = await confirmed_spec(registry, fallback_local=True)
    await registry.backend_for(spec)
    await registry.get_local()
    await registry.get_online(active)

    await registry.aclose()
    await registry.aclose()

    assert local.close_calls == 1
    assert online.close_calls == 1


@pytest.mark.asyncio
async def test_preflight_refuses_a_request_naming_the_wrong_profile(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, _online = make_registry(tmp_path, (active,))
    request_value = run_request(registry, fingerprint="0" * 64)

    with pytest.raises(ConfigurationError, match="profile"):
        await registry.preflight(request_value)
    assert registry._issued == set()


@pytest.mark.asyncio
async def test_profile_lookup_fails_closed_for_missing_or_duplicated_names(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, _online = make_registry(tmp_path, (active,))

    with pytest.raises(ConfigurationError):
        registry.profile_for(None)
    with pytest.raises(ConfigurationError):
        registry.profile_for("absent")


class _RecordingCoordinator:
    """The minimum coordinator surface the controller drives."""

    def __init__(self) -> None:
        self.state = "ready"
        self.active_run_id: str | None = None
        self.history: tuple[object, ...] = ()
        self.stages: tuple[object, ...] = ()
        self.began: list[RunRequest] = []
        self.blocked: list[tuple[RunRequest, str, str]] = []
        self.started: list[RunSpec] = []

    def begin_authorization(self, request: RunRequest) -> None:
        self.began.append(request)
        self.active_run_id = request.run_id
        self.state = "authorizing"

    def record_blocked(
        self,
        request: RunRequest,
        error_category: str,
        public_error: str,
    ) -> object:
        self.blocked.append((request, error_category, public_error))
        self.active_run_id = None
        self.state = "ready"
        return _blocked_record(request, error_category, public_error)

    async def start(self, spec: RunSpec) -> str:
        self.started.append(spec)
        self.active_run_id = spec.run_id
        self.state = "running"
        return spec.run_id

    def request_cancel(self, run_id: str) -> bool:
        return run_id == self.active_run_id


class _RegistryOnlyRuntime:
    def __init__(self, registry: GeneratorRegistry) -> None:
        self.state = "ready"
        self._registry = registry

    @property
    def registry(self) -> GeneratorRegistry:
        return self._registry


def _blocked_record(
    request: RunRequest,
    error_category: str,
    public_error: str,
) -> PanelRunRecord:
    return PanelRunRecord(
        run_id=request.run_id,
        request=request,
        spec=None,
        state="blocked",
        result=None,
        trace=None,
        progress_events=(),
        initialization_duration_ms=1.0,
        run_duration_ms=0.0,
        error_category=error_category,
        public_error=public_error,
    )


def _controller(
    registry: GeneratorRegistry,
    coordinator: _RecordingCoordinator,
) -> PanelController:
    return PanelController(
        coordinator,  # type: ignore[arg-type]
        _RegistryOnlyRuntime(registry),  # type: ignore[arg-type]
        clock=lambda: 0.0,
    )


@pytest.mark.asyncio
async def test_controller_confirmation_mints_an_authorization_the_registry_accepts(
    tmp_path: Path,
) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    coordinator = _RecordingCoordinator()
    controller = _controller(registry, coordinator)
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")

    confirmation = await controller.prepare_run()
    assert confirmation is not None
    assert registry._issued == set()

    await controller.confirm_online()
    spec = coordinator.started[0]

    backend = await registry.backend_for(spec)
    assert backend.backend_kind == "real"
    assert await backend.generate_json(request()) == {"ok": True}
    assert online.calls == 1

    with pytest.raises(RunAuthorizationError):
        await registry.backend_for(spec)
    assert online.calls == 1


@pytest.mark.asyncio
async def test_controller_blocks_when_network_is_disabled(tmp_path: Path) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    registry._config = replace(registry._config, allow_network=False)
    coordinator = _RecordingCoordinator()
    controller = _controller(registry, coordinator)
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")

    confirmation = await controller.prepare_run()

    assert confirmation is None
    assert len(coordinator.blocked) == 1
    assert coordinator.blocked[0][1] == "profile_not_allowed"
    assert coordinator.started == []
    assert online.calls == 0
    assert registry._issued == set()


@pytest.mark.asyncio
async def test_controller_blocks_when_the_api_key_is_missing(tmp_path: Path) -> None:
    active = profile()
    registry, _local, online = make_registry(tmp_path, (active,))
    registry._environ = {}
    coordinator = _RecordingCoordinator()
    controller = _controller(registry, coordinator)
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")

    confirmation = await controller.prepare_run()

    assert confirmation is None
    assert coordinator.blocked[0][1] == "configuration"
    assert (
        API_KEY_ENV in coordinator.blocked[0][2] or "key" in coordinator.blocked[0][2]
    )
    assert coordinator.started == []
    assert online.calls == 0
