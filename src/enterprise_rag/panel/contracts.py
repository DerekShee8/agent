from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Literal

from ..application import GeneratorChoice
from ..execution_trace import RunTrace, TraceEvent
from ..models import AgentResult, RagResult

PanelMode = Literal["rag", "agent"]
PanelTerminalState = Literal[
    "answered",
    "refused",
    "blocked",
    "failed",
    "cancelled",
]
PanelLifecycleState = Literal[
    "initializing",
    "initialization_failed",
    "ready",
    "authorizing",
    "running",
    "cancelling",
]

_PANEL_MODES = frozenset({"rag", "agent"})
_TERMINAL_STATES = frozenset({"answered", "refused", "blocked", "failed", "cancelled"})
_GENERATOR_CHOICES = frozenset({"local", "online"})
_INITIALIZATION_STATUSES = frozenset({"pending", "running", "succeeded", "failed"})
_BUSINESS_RESULTS = (RagResult, AgentResult)
_REQUEST_SPEC_FIELDS = (
    "run_id",
    "created_at",
    "mode",
    "scenario_id",
    "question_or_task",
    "generator",
    "provider_name",
    "provider_fingerprint",
    "fallback_local",
)


def _require_nonempty(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_literal(value: object, allowed: frozenset[str], name: str) -> None:
    _require_nonempty(value, name)
    if value not in allowed:
        raise ValueError(f"invalid {name}: {value}")


def _require_bool(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")


def _require_positive_integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _normalize_timing(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be an int or float")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be non-negative and finite") from exc
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be non-negative and finite")
    return normalized


def _normalize_cost(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be non-negative and finite")
    return value


def _require_optional_nonempty(value: object, name: str) -> None:
    if value is None:
        return
    _require_nonempty(value, name)


@dataclass(frozen=True, slots=True)
class RunRequest:
    run_id: str
    created_at: float
    mode: PanelMode
    scenario_id: str
    question_or_task: str
    generator: GeneratorChoice
    provider_name: str | None
    provider_fingerprint: str | None
    fallback_local: bool

    def __post_init__(self) -> None:
        _require_nonempty(self.run_id, "run_id")
        object.__setattr__(
            self,
            "created_at",
            _normalize_timing(self.created_at, "created_at"),
        )
        _require_literal(self.mode, _PANEL_MODES, "mode")
        _require_nonempty(self.scenario_id, "scenario_id")
        _require_nonempty(self.question_or_task, "question_or_task")
        _require_literal(self.generator, _GENERATOR_CHOICES, "generator")
        _require_bool(self.fallback_local, "fallback_local")
        _require_optional_nonempty(self.provider_name, "provider_name")
        _require_optional_nonempty(self.provider_fingerprint, "provider_fingerprint")
        self._validate_provider_binding()

    def _validate_provider_binding(self) -> None:
        if self.generator == "local":
            if self.provider_name is not None:
                raise ValueError("local run cannot select a provider")
            if self.provider_fingerprint is not None:
                raise ValueError("local run cannot select a provider profile")
            if self.fallback_local:
                raise ValueError("local run cannot enable fallback")
            return
        if self.provider_name is None or self.provider_fingerprint is None:
            raise ValueError("online run requires a provider")


@dataclass(frozen=True, slots=True)
class OnlineRunAuthorization:
    authorization_id: str
    run_id: str
    profile_fingerprint: str
    max_requests: int
    input_token_cap: int
    output_token_cap: int
    max_cost_cny: Decimal
    synthetic_data_only: bool
    fallback_local: bool

    def __post_init__(self) -> None:
        _require_nonempty(self.authorization_id, "authorization_id")
        _require_nonempty(self.run_id, "run_id")
        _require_nonempty(self.profile_fingerprint, "profile_fingerprint")
        _require_positive_integer(self.max_requests, "max_requests")
        _require_positive_integer(self.input_token_cap, "input_token_cap")
        _require_positive_integer(self.output_token_cap, "output_token_cap")
        object.__setattr__(
            self,
            "max_cost_cny",
            _normalize_cost(self.max_cost_cny, "max_cost_cny"),
        )
        _require_bool(self.synthetic_data_only, "synthetic_data_only")
        _require_bool(self.fallback_local, "fallback_local")


@dataclass(frozen=True, slots=True)
class RunSpec:
    run_id: str
    created_at: float
    mode: PanelMode
    scenario_id: str
    question_or_task: str
    generator: GeneratorChoice
    provider_name: str | None
    provider_fingerprint: str | None
    fallback_local: bool
    online_authorization: OnlineRunAuthorization | None

    def __post_init__(self) -> None:
        _require_nonempty(self.run_id, "run_id")
        object.__setattr__(
            self,
            "created_at",
            _normalize_timing(self.created_at, "created_at"),
        )
        _require_literal(self.mode, _PANEL_MODES, "mode")
        _require_nonempty(self.scenario_id, "scenario_id")
        _require_nonempty(self.question_or_task, "question_or_task")
        _require_literal(self.generator, _GENERATOR_CHOICES, "generator")
        _require_bool(self.fallback_local, "fallback_local")
        _require_optional_nonempty(self.provider_name, "provider_name")
        _require_optional_nonempty(self.provider_fingerprint, "provider_fingerprint")
        if self.online_authorization is not None and not isinstance(
            self.online_authorization, OnlineRunAuthorization
        ):
            raise TypeError("online_authorization must be an OnlineRunAuthorization")
        self._validate_provider_binding()
        self._validate_authorization_binding()

    def _validate_provider_binding(self) -> None:
        if self.generator == "local":
            if self.provider_name is not None:
                raise ValueError("local run cannot select a provider")
            if self.provider_fingerprint is not None:
                raise ValueError("local run cannot select a provider profile")
            if self.fallback_local:
                raise ValueError("local run cannot enable fallback")
            return
        if self.provider_name is None or self.provider_fingerprint is None:
            raise ValueError("online run requires a provider")

    def _validate_authorization_binding(self) -> None:
        if self.generator == "local":
            if self.online_authorization is not None:
                raise ValueError("local run cannot carry an authorization")
            return
        authorization = self.online_authorization
        if authorization is None:
            raise ValueError("online run requires an authorization")
        if authorization.run_id != self.run_id:
            raise ValueError("authorization must bind the run id")
        if authorization.profile_fingerprint != self.provider_fingerprint:
            raise ValueError("authorization must bind the profile")
        if authorization.fallback_local != self.fallback_local:
            raise ValueError("authorization must bind the fallback flag")


@dataclass(frozen=True, slots=True)
class InitializationStage:
    name: str
    status: Literal["pending", "running", "succeeded", "failed"]
    duration_ms: float
    error_category: str | None = None
    public_error: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "name")
        _require_literal(self.status, _INITIALIZATION_STATUSES, "initialization status")
        object.__setattr__(
            self,
            "duration_ms",
            _normalize_timing(self.duration_ms, "duration_ms"),
        )
        if not isinstance(self.warnings, tuple):
            raise TypeError("warnings must be a tuple")
        for warning in self.warnings:
            _require_nonempty(warning, "warning")
        _require_optional_nonempty(self.error_category, "error_category")
        _require_optional_nonempty(self.public_error, "public_error")
        if self.status == "failed":
            if self.error_category is None:
                raise ValueError("failed stage requires an error_category")
            if self.public_error is None:
                raise ValueError("failed stage requires a public_error")
            return
        if self.error_category is not None:
            raise ValueError("non-failed stage cannot carry an error_category")
        if self.public_error is not None:
            raise ValueError("non-failed stage cannot carry a public_error")


@dataclass(frozen=True, slots=True)
class PanelRunRecord:
    run_id: str
    request: RunRequest
    spec: RunSpec | None
    state: PanelTerminalState
    result: RagResult | AgentResult | None
    trace: RunTrace | None
    progress_events: tuple[TraceEvent, ...]
    initialization_duration_ms: float
    run_duration_ms: float
    error_category: str | None
    public_error: str | None

    def __post_init__(self) -> None:
        _require_nonempty(self.run_id, "run_id")
        if not isinstance(self.request, RunRequest):
            raise TypeError("request must be a RunRequest")
        if self.spec is not None and not isinstance(self.spec, RunSpec):
            raise TypeError("spec must be a RunSpec")
        _require_literal(self.state, _TERMINAL_STATES, "state")
        if self.result is not None and not isinstance(self.result, _BUSINESS_RESULTS):
            raise TypeError("result must be a RagResult or AgentResult")
        if self.trace is not None and not isinstance(self.trace, RunTrace):
            raise TypeError("trace must be a RunTrace")
        if not isinstance(self.progress_events, tuple):
            raise TypeError("progress_events must be a tuple")
        if any(not isinstance(event, TraceEvent) for event in self.progress_events):
            raise TypeError("progress_events must contain only TraceEvent")
        object.__setattr__(
            self,
            "initialization_duration_ms",
            _normalize_timing(
                self.initialization_duration_ms, "initialization_duration_ms"
            ),
        )
        object.__setattr__(
            self,
            "run_duration_ms",
            _normalize_timing(self.run_duration_ms, "run_duration_ms"),
        )
        _require_optional_nonempty(self.error_category, "error_category")
        _require_optional_nonempty(self.public_error, "public_error")

        if self.request.run_id != self.run_id:
            raise ValueError("record run_id must match the request")
        if self.spec is not None:
            if self.spec.run_id != self.run_id:
                raise ValueError("record run_id must match the spec")
            self._validate_request_spec_equality()

        if self.state == "blocked":
            self._validate_blocked()
        elif self.state == "cancelled":
            self._validate_cancelled()
        elif self.state == "failed":
            self._validate_failed()
        else:
            self._validate_completed_business_state()

    def _validate_request_spec_equality(self) -> None:
        spec = self.spec
        assert spec is not None
        for field_name in _REQUEST_SPEC_FIELDS:
            if getattr(spec, field_name) != getattr(self.request, field_name):
                raise ValueError(f"spec must copy the request field: {field_name}")

    def _validate_blocked(self) -> None:
        if self.spec is not None:
            raise ValueError("blocked record cannot carry a spec")
        if self.result is not None:
            raise ValueError("blocked record cannot carry a result")
        if self.trace is not None:
            raise ValueError("blocked record cannot carry a trace")
        if self.progress_events:
            raise ValueError("blocked record cannot carry progress events")
        if self.error_category is None:
            raise ValueError("blocked record requires an error_category")
        if self.public_error is None:
            raise ValueError("blocked record requires a public_error")

    def _validate_cancelled(self) -> None:
        if self.spec is None:
            raise ValueError("cancelled record requires a spec")
        if self.result is not None:
            raise ValueError("cancelled record cannot carry a result")
        if self.trace is not None:
            raise ValueError("cancelled record cannot carry a completed trace")
        if self.error_category is not None:
            raise ValueError("cancelled record cannot carry an error_category")
        if self.public_error is not None:
            raise ValueError("cancelled record cannot carry a public_error")

    def _validate_failed(self) -> None:
        if self.spec is None:
            raise ValueError("failed record requires a spec")
        if self.error_category is None:
            raise ValueError("failed record requires an error_category")
        if self.result is None:
            if self.trace is not None:
                raise ValueError("pre-service failure cannot carry a trace")
            if self.public_error is None:
                raise ValueError("failed record requires a public_error")
            return
        if self.result.status != "failed":
            raise ValueError("failed record requires a failed result")
        if self.trace is None:
            raise ValueError("failed record requires a completed trace")
        if self.trace != self.result.trace:
            raise ValueError("record trace must match the result trace")
        if self.trace.run_id != self.spec.run_id:
            raise ValueError("record trace must match the spec run id")
        if self.result.error_category != self.error_category:
            raise ValueError("record category must match the result category")

    def _validate_completed_business_state(self) -> None:
        if self.spec is None:
            raise ValueError(f"{self.state} record requires a spec")
        if self.result is None:
            raise ValueError(f"{self.state} record requires a business result")
        if self.trace is None:
            raise ValueError(f"{self.state} record requires a completed trace")
        if self.result.status != self.state:
            raise ValueError(f"{self.state} record requires a {self.state} result")
        if self.trace != self.result.trace:
            raise ValueError("record trace must match the result trace")
        if self.trace.run_id != self.spec.run_id:
            raise ValueError("record trace must match the spec run id")
        if self.error_category is not None:
            raise ValueError(f"{self.state} record cannot carry an error_category")
        if self.public_error is not None:
            raise ValueError(f"{self.state} record cannot carry a public_error")
