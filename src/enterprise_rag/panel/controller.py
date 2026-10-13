"""The pure panel controller: form intents, presets, and the online confirmation.

It validates form shape and authorization binding, allocates the run id before
online preflight, and submits immutable specs to the coordinator. It never
inspects answer text, recomputes a Gate decision, filters sources, selects tools,
performs fallback, or invents a trace node.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from ..application import GeneratorChoice
from ..errors import ErrorCategory
from ..provider_profiles import (
    OnlineRunPreflight,
    profile_fingerprint,
)
from .contracts import (
    InitializationStage,
    PanelLifecycleState,
    PanelMode,
    PanelRunRecord,
    RunRequest,
    RunSpec,
)
from .projection import (
    PanelHistoryEntryViewModel,
    PanelRunViewModel,
    project_history,
    project_run,
)
from .runtime import GeneratorRegistry

_AUTHORIZATION_CANCELLED = "authorization_cancelled"


@dataclass(frozen=True, slots=True)
class ScenarioPreset:
    scenario_id: str
    text: str
    mode: PanelMode


PRESETS: tuple[ScenarioPreset, ...] = (
    ScenarioPreset(
        "rag-shanghai-p4",
        "P4 员工去上海出差的每晚住宿上限是多少？",  # noqa: RUF001
        "rag",
    ),
    ScenarioPreset(
        "agent-claim-1024",
        "解释 EMP-003 的 CLAIM-1024 为什么被拒绝。",
        "agent",
    ),
    ScenarioPreset(
        "rag-pet-boarding-refusal",
        "制度是否允许报销宠物寄养费？",  # noqa: RUF001
        "rag",
    ),
)

CUSTOM_SCENARIO_ID = "custom"


@dataclass(frozen=True, slots=True)
class ProviderOptionViewModel:
    name: str
    model_id: str
    base_url: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class OnlineConfirmationViewModel:
    """Display-safe summary shown before any authorization is minted."""

    run_id: str
    profile_name: str
    model_id: str
    base_url: str
    privacy_terms_url: str
    api_key_env: str
    key_configured: bool
    max_requests: int
    request_limit: int
    max_cost_cny: str
    budget_limit_cny: str
    already_spent_cny: str
    fallback_local: bool
    synthetic_data_only: bool


@dataclass(frozen=True, slots=True)
class PanelViewModel:
    state: PanelLifecycleState
    run: PanelRunViewModel | None
    history: tuple[PanelHistoryEntryViewModel, ...]
    initialization: tuple[InitializationStage, ...]
    presets: tuple[ScenarioPreset, ...]
    selected_scenario_id: str | None
    custom_text: str
    question_or_task: str
    text_editable: bool
    mode: PanelMode
    generator: GeneratorChoice
    provider_name: str | None
    providers: tuple[ProviderOptionViewModel, ...]
    fallback_local: bool
    fallback_available: bool
    can_run: bool
    controls_locked: bool
    confirmation: OnlineConfirmationViewModel | None
    blocked_message: str | None


class _RuntimePort(Protocol):
    @property
    def state(self) -> str: ...

    @property
    def registry(self) -> GeneratorRegistry: ...


class _CoordinatorPort(Protocol):
    @property
    def state(self) -> PanelLifecycleState: ...

    @property
    def active_run_id(self) -> str | None: ...

    @property
    def history(self) -> tuple[PanelRunRecord, ...]: ...

    @property
    def stages(self) -> tuple[InitializationStage, ...]: ...

    def begin_authorization(self, request: RunRequest) -> None: ...

    def record_blocked(
        self,
        request: RunRequest,
        error_category: str,
        public_error: str,
    ) -> PanelRunRecord: ...

    async def start(self, spec: RunSpec) -> str: ...

    def request_cancel(self, run_id: str) -> bool: ...

    async def wait(self, run_id: str) -> PanelRunRecord: ...


class PanelController:
    """Per-client form state over one shared coordinator."""

    def __init__(
        self,
        coordinator: _CoordinatorPort,
        runtime: _RuntimePort,
        *,
        presets: tuple[ScenarioPreset, ...] = PRESETS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._coordinator = coordinator
        self._runtime = runtime
        self._presets = tuple(presets)
        self._clock = clock
        self._selected_scenario_id: str | None = None
        self._custom_text = ""
        self._mode: PanelMode = "rag"
        self._generator: GeneratorChoice = "local"
        self._provider_name: str | None = None
        self._provider_fingerprint: str | None = None
        self._fallback_local = False
        self._selected_run_id: str | None = None
        self._selected_event_id: str | None = None
        self._pending_request: RunRequest | None = None
        self._pending_preflight: OnlineRunPreflight | None = None
        self._confirmation: OnlineConfirmationViewModel | None = None
        self._blocked_message: str | None = None
        self._view = self._build_view()

    @property
    def presets(self) -> tuple[ScenarioPreset, ...]:
        return self._presets

    @property
    def view(self) -> PanelViewModel:
        return self._view

    def select_scenario(self, scenario_id: str) -> None:
        """Load a preset's text and mode without starting a run."""

        preset = self._preset_for(scenario_id)
        self._selected_scenario_id = preset.scenario_id
        self._mode = preset.mode
        self._blocked_message = None
        self._refresh_view()

    def update_custom_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("custom text must be a string")
        self._custom_text = text
        self._selected_scenario_id = CUSTOM_SCENARIO_ID
        self._blocked_message = None
        self._refresh_view()

    def select_mode(self, mode: PanelMode) -> None:
        if mode not in {"rag", "agent"}:
            raise ValueError("mode must be rag or agent")
        self._mode = mode
        self._refresh_view()

    def select_generator(self, generator: GeneratorChoice) -> None:
        if generator not in {"local", "online"}:
            raise ValueError("generator must be local or online")
        self._generator = generator
        if generator == "local":
            self._provider_name = None
            self._provider_fingerprint = None
            self._fallback_local = False
        elif self._provider_name is None:
            self._adopt_sole_profile()
        self._blocked_message = None
        self._refresh_view()

    def _adopt_sole_profile(self) -> None:
        """Preselect the profile when exactly one is registered.

        With a single registered profile there is nothing for the operator to
        choose, and the fingerprint is still recorded at this moment so a later
        profile change is detected the same way an explicit pick would be.
        """

        try:
            profiles = self._runtime.registry.profiles
        except Exception:
            return
        if len(profiles) != 1:
            return
        profile = profiles[0]
        self._provider_name = profile.name
        self._provider_fingerprint = profile_fingerprint(profile)

    def select_provider(self, provider_name: str) -> None:
        profile = self._runtime.registry.profile_for(provider_name)
        self._provider_name = profile.name
        self._provider_fingerprint = profile_fingerprint(profile)
        self._blocked_message = None
        self._refresh_view()

    def set_fallback_local(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("fallback must be a boolean")
        self._fallback_local = enabled and self._generator == "online"
        self._refresh_view()

    async def prepare_run(self) -> OnlineConfirmationViewModel | None:
        """Validate the form, then either start a local run or ask for confirmation."""

        question_or_task = self._active_text()
        if not question_or_task.strip():
            raise ValueError("the question or task must not be blank")
        if self._generator == "online" and self._provider_name is None:
            raise ValueError("an online run requires a selected provider profile")
        request = RunRequest(
            run_id=uuid4().hex,
            created_at=self._clock(),
            mode=self._mode,
            scenario_id=self._active_scenario_id(),
            question_or_task=question_or_task,
            generator=self._generator,
            provider_name=self._provider_name if self._generator == "online" else None,
            provider_fingerprint=(
                self._provider_fingerprint if self._generator == "online" else None
            ),
            fallback_local=self._fallback_local,
        )
        if self._generator == "local":
            spec = RunSpec(
                run_id=request.run_id,
                created_at=request.created_at,
                mode=request.mode,
                scenario_id=request.scenario_id,
                question_or_task=request.question_or_task,
                generator=request.generator,
                provider_name=None,
                provider_fingerprint=None,
                fallback_local=False,
                online_authorization=None,
            )
            self._selected_run_id = None
            self._selected_event_id = None
            await self._coordinator.start(spec)
            self._refresh_view()
            return None
        return await self._prepare_online(request)

    async def _prepare_online(
        self,
        request: RunRequest,
    ) -> OnlineConfirmationViewModel | None:
        try:
            profile = self._runtime.registry.profile_for(request.provider_name)
            if profile_fingerprint(profile) != request.provider_fingerprint:
                raise _BlockedError(
                    ErrorCategory.PROFILE_NOT_ALLOWED,
                    "the selected provider profile changed since it was chosen",
                )
        except _BlockedError as error:
            return self._block(request, error)
        except Exception as error:
            return self._block(request, error)
        self._coordinator.begin_authorization(request)
        try:
            preflight = await self._runtime.registry.preflight(request)
        except Exception as error:
            return self._block(request, error)
        self._pending_request = request
        self._pending_preflight = preflight
        self._confirmation = _confirmation_view(preflight)
        self._blocked_message = None
        self._refresh_view()
        return self._confirmation

    async def confirm_online(self) -> str | None:
        """Mint the one-use authorization and start the confirmed run."""

        request = self._pending_request
        preflight = self._pending_preflight
        if request is None or preflight is None:
            raise ValueError("no online confirmation is pending")
        authorization = self._runtime.registry.authorize(preflight)
        spec = RunSpec(
            run_id=request.run_id,
            created_at=request.created_at,
            mode=request.mode,
            scenario_id=request.scenario_id,
            question_or_task=request.question_or_task,
            generator=request.generator,
            provider_name=request.provider_name,
            provider_fingerprint=request.provider_fingerprint,
            fallback_local=request.fallback_local,
            online_authorization=authorization,
        )
        self._clear_confirmation()
        self._selected_run_id = None
        self._selected_event_id = None
        run_id = await self._coordinator.start(spec)
        self._refresh_view()
        return run_id

    def reject_online(self) -> PanelRunRecord:
        """Record one sanitized BLOCKED request without creating anything else."""

        request = self._pending_request
        if request is None:
            raise ValueError("no online confirmation is pending")
        record = self._coordinator.record_blocked(
            request,
            _AUTHORIZATION_CANCELLED,
            "the operator declined the online confirmation",
        )
        self._clear_confirmation()
        self._blocked_message = "the operator declined the online confirmation"
        self._refresh_view()
        return record

    def request_stop(self) -> bool:
        """Ask the coordinator to cancel whatever run is currently active."""

        run_id = self._coordinator.active_run_id
        if run_id is None:
            return False
        return self._coordinator.request_cancel(run_id)

    def select_history(self, run_id: str | None) -> None:
        """Change only the viewed record; the active run stays authoritative."""

        self._selected_run_id = run_id
        self._selected_event_id = None
        self._refresh_view()

    def select_trace_node(self, event_id: str | None) -> None:
        self._selected_event_id = event_id
        self._refresh_view()

    async def refresh(self) -> PanelViewModel:
        self._refresh_view()
        return self._view

    def _block(self, request: RunRequest, error: Exception) -> None:
        category = _error_category(error)
        message = _public_message(error)
        try:
            self._coordinator.record_blocked(request, category, message)
        except ValueError:
            # The coordinator already returned to READY; the preflight failure
            # still must not create a spec, an application, or a client.
            pass
        self._clear_confirmation()
        self._blocked_message = message
        self._refresh_view()
        return None

    def _clear_confirmation(self) -> None:
        self._pending_request = None
        self._pending_preflight = None
        self._confirmation = None

    def _active_text(self) -> str:
        if self._selected_scenario_id == CUSTOM_SCENARIO_ID:
            return self._custom_text
        preset = self._preset_or_none(self._selected_scenario_id)
        return preset.text if preset is not None else ""

    def _active_scenario_id(self) -> str:
        if self._selected_scenario_id in {None, CUSTOM_SCENARIO_ID}:
            return CUSTOM_SCENARIO_ID
        return self._selected_scenario_id

    def _preset_for(self, scenario_id: str) -> ScenarioPreset:
        preset = self._preset_or_none(scenario_id)
        if preset is None:
            raise ValueError(f"unknown scenario preset: {scenario_id}")
        return preset

    def _preset_or_none(self, scenario_id: str | None) -> ScenarioPreset | None:
        return next(
            (item for item in self._presets if item.scenario_id == scenario_id),
            None,
        )

    def _refresh_view(self) -> None:
        self._view = self._build_view()

    def _build_view(self) -> PanelViewModel:
        state = self._coordinator.state
        history = self._coordinator.history
        record = self._viewed_record(history)
        return PanelViewModel(
            state=state,
            run=(
                None
                if record is None
                else project_run(record, selected_event_id=self._selected_event_id)
            ),
            history=project_history(history),
            initialization=self._coordinator.stages,
            presets=self._presets,
            selected_scenario_id=self._selected_scenario_id,
            custom_text=self._custom_text,
            question_or_task=self._active_text(),
            text_editable=self._selected_scenario_id == CUSTOM_SCENARIO_ID,
            mode=self._mode,
            generator=self._generator,
            provider_name=self._provider_name,
            providers=self._provider_options(),
            fallback_local=self._fallback_local,
            fallback_available=self._generator == "online",
            can_run=state == "ready",
            controls_locked=state != "ready",
            confirmation=self._confirmation,
            blocked_message=self._blocked_message,
        )

    def _viewed_record(
        self,
        history: tuple[PanelRunRecord, ...],
    ) -> PanelRunRecord | None:
        if self._selected_run_id is not None:
            selected = next(
                (
                    record
                    for record in history
                    if record.run_id == self._selected_run_id
                ),
                None,
            )
            if selected is not None:
                return selected
        return history[0] if history else None

    def _provider_options(self) -> tuple[ProviderOptionViewModel, ...]:
        try:
            profiles = self._runtime.registry.profiles
        except Exception:
            return ()
        return tuple(
            ProviderOptionViewModel(
                name=profile.name,
                model_id=profile.model_id,
                base_url=profile.base_url,
                enabled=profile.enabled,
            )
            for profile in profiles
        )


class _BlockedError(Exception):
    def __init__(self, category: ErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


def _confirmation_view(
    preflight: OnlineRunPreflight,
) -> OnlineConfirmationViewModel:
    return OnlineConfirmationViewModel(
        run_id=preflight.run_id,
        profile_name=preflight.profile_name,
        model_id=preflight.model_id,
        base_url=preflight.base_url,
        privacy_terms_url=preflight.privacy_terms_url,
        api_key_env=preflight.api_key_env,
        key_configured=True,
        max_requests=preflight.max_requests,
        request_limit=preflight.request_limit,
        max_cost_cny=_decimal_text(preflight.max_cost_cny),
        budget_limit_cny=_decimal_text(preflight.budget_limit_cny),
        already_spent_cny=_decimal_text(preflight.already_spent_cny),
        fallback_local=preflight.fallback_local,
        synthetic_data_only=preflight.synthetic_data_only,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _error_category(error: Exception) -> str:
    category = getattr(error, "category", ErrorCategory.PROTOCOL)
    if isinstance(category, ErrorCategory):
        category = category.value
    if isinstance(category, str) and category:
        return category
    return ErrorCategory.PROTOCOL.value


_PUBLIC_MESSAGES: Mapping[str, str] = {
    ErrorCategory.BUDGET_BLOCKED.value: ("the online budget ceiling would be exceeded"),
    ErrorCategory.PRIVACY_BLOCKED.value: (
        "the online profile is restricted to synthetic data"
    ),
    ErrorCategory.PROFILE_NOT_ALLOWED.value: (
        "the online profile is not allowed for this run"
    ),
    ErrorCategory.CONFIGURATION.value: "the online profile or its key is not usable",
}


def _public_message(error: Exception) -> str:
    category = _error_category(error)
    return _PUBLIC_MESSAGES.get(category, f"the online run was blocked: {category}")


__all__ = [
    "CUSTOM_SCENARIO_ID",
    "PRESETS",
    "OnlineConfirmationViewModel",
    "PanelController",
    "PanelViewModel",
    "ProviderOptionViewModel",
    "ScenarioPreset",
]
