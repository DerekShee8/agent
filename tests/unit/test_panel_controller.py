"""The pure panel controller: presets, form intents, and the online confirmation."""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal

import pytest

from enterprise_rag.errors import (
    BudgetBlockedError,
    ConfigurationError,
    PrivacyBlockedError,
    ProfileNotAllowedError,
)
from enterprise_rag.panel.contracts import (
    InitializationStage,
    PanelRunRecord,
    RunRequest,
)
from enterprise_rag.panel.controller import (
    CUSTOM_SCENARIO_ID,
    PRESETS,
    PanelController,
    PanelViewModel,
)
from enterprise_rag.panel.projection import PanelRunViewModel
from enterprise_rag.provider_profiles import (
    OnlineRunPreflight,
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


def preflight(request: RunRequest, active: ProviderProfile) -> OnlineRunPreflight:
    return OnlineRunPreflight(
        run_id=request.run_id,
        profile_name=active.name,
        provider_fingerprint=profile_fingerprint(active),
        base_url=active.base_url,
        model_id=active.model_id,
        api_key_env=active.api_key_env,
        privacy_terms_url=active.privacy_terms_url,
        max_requests=2,
        request_limit=active.request_limit,
        input_token_cap=16_384,
        output_token_cap=512,
        synthetic_data_only=True,
        fallback_local=request.fallback_local,
        max_cost_cny=Decimal("0.09"),
        already_spent_cny=Decimal("0"),
        projected_total_cny=Decimal("0.09"),
        budget_limit_cny=active.budget_limit_cny,
    )


class FakeRegistry:
    def __init__(
        self,
        profiles: tuple[ProviderProfile, ...] = (),
        *,
        preflight_error: Exception | None = None,
    ) -> None:
        self.profiles = profiles
        self.preflight_error = preflight_error
        self.preflights: list[RunRequest] = []
        self.authorized: list[OnlineRunPreflight] = []

    def profile_for(self, name: str | None) -> ProviderProfile:
        if name is None:
            raise ConfigurationError("online generation requires a provider profile")
        matches = tuple(item for item in self.profiles if item.name == name)
        if len(matches) != 1:
            raise ConfigurationError("provider profile is missing or duplicated")
        return matches[0]

    async def preflight(self, request: RunRequest) -> OnlineRunPreflight:
        self.preflights.append(request)
        if self.preflight_error is not None:
            raise self.preflight_error
        return preflight(request, self.profile_for(request.provider_name))

    def authorize(self, preflight_value: OnlineRunPreflight) -> object:
        self.authorized.append(preflight_value)
        from enterprise_rag.panel.contracts import OnlineRunAuthorization

        return OnlineRunAuthorization(
            authorization_id="auth-1",
            run_id=preflight_value.run_id,
            profile_fingerprint=preflight_value.provider_fingerprint,
            max_requests=preflight_value.max_requests,
            input_token_cap=preflight_value.input_token_cap,
            output_token_cap=preflight_value.output_token_cap,
            max_cost_cny=preflight_value.max_cost_cny,
            synthetic_data_only=preflight_value.synthetic_data_only,
            fallback_local=preflight_value.fallback_local,
        )


class FakeRuntime:
    def __init__(self, registry: FakeRegistry, *, state: str = "ready") -> None:
        self._registry = registry
        self._state = state

    @property
    def state(self) -> str:
        return self._state

    @property
    def registry(self) -> FakeRegistry:
        return self._registry


def blocked_record(request: RunRequest) -> PanelRunRecord:
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
        error_category="authorization_cancelled",
        public_error="the operator declined the online confirmation",
    )


class FakeCoordinator:
    def __init__(
        self,
        *,
        state: str = "ready",
        history: tuple[PanelRunRecord, ...] = (),
        stages: tuple[InitializationStage, ...] = (),
    ) -> None:
        self._state = state
        self._history = history
        self._stages = stages
        self._active_run_id: str | None = None
        self.began: list[RunRequest] = []
        self.started: list[object] = []
        self.blocked: list[tuple[RunRequest, str, str]] = []
        self.cancelled: list[str] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    @property
    def history(self) -> tuple[PanelRunRecord, ...]:
        return self._history

    @property
    def stages(self) -> tuple[InitializationStage, ...]:
        return self._stages

    def begin_authorization(self, request: RunRequest) -> None:
        self.began.append(request)
        self._active_run_id = request.run_id
        self._state = "authorizing"

    def record_blocked(
        self,
        request: RunRequest,
        error_category: str,
        public_error: str,
    ) -> PanelRunRecord:
        self.blocked.append((request, error_category, public_error))
        self._active_run_id = None
        self._state = "ready"
        return blocked_record(request)

    async def start(self, spec: object) -> str:
        self.started.append(spec)
        self._state = "running"
        self._active_run_id = spec.run_id  # type: ignore[attr-defined]
        return spec.run_id  # type: ignore[attr-defined]

    def request_cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


def make_controller(
    *,
    profiles: tuple[ProviderProfile, ...] = (),
    preflight_error: Exception | None = None,
    coordinator: FakeCoordinator | None = None,
) -> tuple[PanelController, FakeCoordinator, FakeRegistry]:
    registry = FakeRegistry(profiles, preflight_error=preflight_error)
    runtime = FakeRuntime(registry)
    active_coordinator = coordinator or FakeCoordinator()
    controller = PanelController(
        active_coordinator,  # type: ignore[arg-type]
        runtime,  # type: ignore[arg-type]
        clock=lambda: 0.0,
    )
    return controller, active_coordinator, registry


def test_presets_are_the_three_documented_scenarios() -> None:
    assert [(preset.scenario_id, preset.mode) for preset in PRESETS] == [
        ("rag-shanghai-p4", "rag"),
        ("agent-claim-1024", "agent"),
        ("rag-pet-boarding-refusal", "rag"),
    ]
    assert PRESETS[0].text.startswith("P4 员工去上海出差")
    assert "CLAIM-1024" in PRESETS[1].text
    assert "宠物寄养" in PRESETS[2].text


def test_selecting_a_preset_loads_text_and_mode_without_starting() -> None:
    controller, coordinator, _registry = make_controller()

    controller.select_scenario("agent-claim-1024")

    view = controller.view
    assert view.selected_scenario_id == "agent-claim-1024"
    assert view.mode == "agent"
    assert view.question_or_task == PRESETS[1].text
    assert view.text_editable is False
    assert coordinator.started == []


def test_preset_text_is_read_only_and_custom_text_is_editable() -> None:
    controller, _coordinator, _registry = make_controller()
    controller.select_scenario("rag-shanghai-p4")

    controller.update_custom_text("a custom question")

    view = controller.view
    assert view.selected_scenario_id == CUSTOM_SCENARIO_ID
    assert view.custom_text == "a custom question"
    assert view.question_or_task == "a custom question"
    assert view.text_editable is True
    # The preset itself is untouched.
    assert PRESETS[0].text.startswith("P4 员工去上海出差")


def test_retriever_and_reranker_are_absent_from_the_controller_surface() -> None:
    controller, _coordinator, _registry = make_controller()

    public = {name for name in dir(controller) if not name.startswith("_")}
    assert not any(
        "retriev" in name.lower() or "rerank" in name.lower() for name in public
    )
    assert not any(
        "retriev" in field.name.lower() or "rerank" in field.name.lower()
        for field in fields(PanelViewModel)
    )


@pytest.mark.asyncio
async def test_local_run_creates_one_immutable_spec_and_starts_once() -> None:
    controller, coordinator, registry = make_controller()
    controller.select_scenario("rag-shanghai-p4")

    confirmation = await controller.prepare_run()

    assert confirmation is None
    assert len(coordinator.started) == 1
    spec = coordinator.started[0]
    assert spec.generator == "local"
    assert spec.provider_name is None
    assert spec.online_authorization is None
    assert spec.question_or_task == PRESETS[0].text
    assert registry.preflights == []
    assert registry.authorized == []


@pytest.mark.asyncio
async def test_blank_custom_text_cannot_start_a_run() -> None:
    controller, coordinator, _registry = make_controller()
    controller.update_custom_text("   ")

    with pytest.raises(ValueError, match="blank"):
        await controller.prepare_run()

    assert coordinator.started == []


@pytest.mark.asyncio
async def test_online_selection_returns_a_confirmation_without_a_spec() -> None:
    active = profile()
    controller, coordinator, registry = make_controller(profiles=(active,))
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")
    controller.set_fallback_local(True)

    confirmation = await controller.prepare_run()

    assert confirmation is not None
    assert confirmation.profile_name == "relay"
    assert confirmation.model_id == "provider-model"
    assert confirmation.base_url == "https://relay.example.invalid/v1"
    assert confirmation.privacy_terms_url == "https://relay.example.invalid/privacy"
    assert confirmation.api_key_env == API_KEY_ENV
    assert confirmation.max_requests == 2
    assert confirmation.max_cost_cny == "0.09"
    assert confirmation.fallback_local is True
    assert confirmation.synthetic_data_only is True
    assert coordinator.began and coordinator.started == []
    assert registry.authorized == []


@pytest.mark.asyncio
async def test_rejecting_the_confirmation_records_one_blocked_request() -> None:
    active = profile()
    controller, coordinator, registry = make_controller(profiles=(active,))
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")
    await controller.prepare_run()

    record = controller.reject_online()

    assert record.state == "blocked"
    assert len(coordinator.blocked) == 1
    _request, category, message = coordinator.blocked[0]
    assert category == "authorization_cancelled"
    assert message
    assert coordinator.started == []
    assert registry.authorized == []
    assert controller.view.confirmation is None


@pytest.mark.asyncio
async def test_confirming_creates_one_authorization_and_starts_once() -> None:
    active = profile()
    controller, coordinator, registry = make_controller(profiles=(active,))
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")
    controller.set_fallback_local(True)
    confirmation = await controller.prepare_run()
    assert confirmation is not None

    run_id = await controller.confirm_online()

    assert run_id == confirmation.run_id
    assert len(registry.authorized) == 1
    assert len(coordinator.started) == 1
    spec = coordinator.started[0]
    assert spec.run_id == confirmation.run_id
    assert spec.online_authorization is not None
    assert spec.fallback_local is True
    assert spec.provider_fingerprint == profile_fingerprint(active)
    assert controller.view.confirmation is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProfileNotAllowedError("network is disabled"), "profile_not_allowed"),
        (PrivacyBlockedError("synthetic only"), "privacy_blocked"),
        (BudgetBlockedError("budget would be exceeded"), "budget_blocked"),
        (ConfigurationError("key is missing"), "configuration"),
    ],
)
async def test_preflight_failures_block_without_starting_anything(
    error: Exception,
    expected: str,
) -> None:
    active = profile()
    controller, coordinator, registry = make_controller(
        profiles=(active,),
        preflight_error=error,
    )
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")

    confirmation = await controller.prepare_run()

    assert confirmation is None
    assert len(coordinator.blocked) == 1
    _request, category, message = coordinator.blocked[0]
    assert category == expected
    assert message
    assert str(error) not in message
    assert coordinator.started == []
    assert registry.authorized == []
    assert controller.view.blocked_message == message


@pytest.mark.asyncio
async def test_a_profile_that_changed_after_selection_is_blocked() -> None:
    active = profile()
    controller, coordinator, registry = make_controller(profiles=(active,))
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")
    # The profile file changed between selection and the Run intent.
    registry.profiles = (profile("relay", model_id="different-model"),)

    confirmation = await controller.prepare_run()

    assert confirmation is None
    assert len(coordinator.blocked) == 1
    assert coordinator.blocked[0][1] == "profile_not_allowed"
    assert coordinator.started == []
    assert registry.authorized == []


@pytest.mark.asyncio
async def test_a_profile_that_disappeared_is_blocked_before_authorization() -> None:
    active = profile()
    controller, coordinator, registry = make_controller(profiles=(active,))
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")
    controller.select_provider("relay")
    registry.profiles = ()

    confirmation = await controller.prepare_run()

    assert confirmation is None
    assert len(coordinator.blocked) == 1
    assert coordinator.blocked[0][1] == "configuration"
    assert coordinator.started == []
    assert registry.authorized == []


@pytest.mark.asyncio
async def test_an_online_run_without_a_provider_is_a_form_error() -> None:
    controller, coordinator, _registry = make_controller(profiles=())
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")

    with pytest.raises(ValueError, match="provider profile"):
        await controller.prepare_run()

    assert coordinator.started == []
    assert coordinator.blocked == []


@pytest.mark.asyncio
async def test_switching_to_online_adopts_a_sole_registered_profile() -> None:
    active = profile()
    controller, _coordinator, _registry = make_controller(profiles=(active,))
    controller.select_scenario("rag-shanghai-p4")

    controller.select_generator("online")

    view = controller.view
    assert view.provider_name == "relay"
    assert [option.name for option in view.providers] == ["relay"]


def test_switching_to_online_leaves_multiple_profiles_unchosen() -> None:
    controller, _coordinator, _registry = make_controller(
        profiles=(profile("relay"), profile("relay-b")),
    )

    controller.select_generator("online")

    assert controller.view.provider_name is None


@pytest.mark.asyncio
async def test_a_started_run_that_fails_later_is_failed_not_blocked() -> None:
    from enterprise_rag.panel.contracts import RunSpec

    run_request = RunRequest(
        run_id="run-1",
        created_at=0.0,
        mode="rag",
        scenario_id="scenario",
        question_or_task="question",
        generator="local",
        provider_name=None,
        provider_fingerprint=None,
        fallback_local=False,
    )
    run_spec = RunSpec(
        run_id="run-1",
        created_at=0.0,
        mode="rag",
        scenario_id="scenario",
        question_or_task="question",
        generator="local",
        provider_name=None,
        provider_fingerprint=None,
        fallback_local=False,
        online_authorization=None,
    )
    failed = PanelRunRecord(
        run_id="run-1",
        request=run_request,
        spec=run_spec,
        state="failed",
        result=None,
        trace=None,
        progress_events=(),
        initialization_duration_ms=1.0,
        run_duration_ms=2.0,
        error_category="authentication",
        public_error="panel run failed before returning a result: authentication",
    )
    coordinator = FakeCoordinator(history=(failed,))
    controller, _coordinator, _registry = make_controller(coordinator=coordinator)

    view = controller.view

    assert view.run is not None
    assert isinstance(view.run, PanelRunViewModel)
    assert view.run.state == "failed"
    assert view.run.error_category == "authentication"
    assert view.blocked_message is None


def test_controls_lock_while_running_and_unlock_on_ready() -> None:
    for state, locked in (
        ("initializing", True),
        ("ready", False),
        ("authorizing", True),
        ("running", True),
        ("cancelling", True),
    ):
        coordinator = FakeCoordinator(state=state)
        controller, _coordinator, _registry = make_controller(coordinator=coordinator)
        assert controller.view.controls_locked is locked, state
        assert controller.view.can_run is (state == "ready")


def test_request_stop_always_targets_the_active_run() -> None:
    coordinator = FakeCoordinator(state="running")
    coordinator._active_run_id = "run-42"
    controller, _coordinator, _registry = make_controller(coordinator=coordinator)

    assert controller.request_stop() is True
    assert coordinator.cancelled == ["run-42"]

    coordinator._active_run_id = None
    assert controller.request_stop() is False


@pytest.mark.asyncio
async def test_selecting_history_changes_only_the_viewed_record() -> None:
    older = blocked_record(
        RunRequest(
            run_id="run-old",
            created_at=0.0,
            mode="rag",
            scenario_id="scenario",
            question_or_task="older question",
            generator="local",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
        )
    )
    newer = blocked_record(
        RunRequest(
            run_id="run-new",
            created_at=0.0,
            mode="rag",
            scenario_id="scenario",
            question_or_task="newer question",
            generator="local",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
        )
    )
    coordinator = FakeCoordinator(history=(newer, older))
    controller, _coordinator, _registry = make_controller(coordinator=coordinator)

    assert controller.view.run is not None
    assert controller.view.run.run_id == "run-new"

    controller.select_history("run-old")

    assert controller.view.run is not None
    assert controller.view.run.run_id == "run-old"
    assert [entry.run_id for entry in controller.view.history] == [
        "run-new",
        "run-old",
    ]

    await controller.refresh()
    assert controller.view.run is not None
    assert controller.view.run.run_id == "run-old"


@pytest.mark.asyncio
async def test_a_new_run_remains_the_active_view() -> None:
    controller, coordinator, _registry = make_controller()
    controller.select_history("some-old-run")
    controller.select_scenario("rag-shanghai-p4")

    await controller.prepare_run()

    assert controller.view.run is None
    assert coordinator.started
