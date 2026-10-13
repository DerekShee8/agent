"""Fakes for the panel page tests.

They are deliberately thin: the page renders real view models produced by the
real coordinator and controller, so a page test exercises the whole read path
without loading a model, opening a socket, or touching a provider.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Literal

from enterprise_rag.execution_control import CancellationToken, RunCancelled
from enterprise_rag.execution_trace import TraceEvent, TraceRecorder
from enterprise_rag.models import RagResult, Source
from enterprise_rag.panel.contracts import (
    InitializationStage,
    OnlineRunAuthorization,
    RunSpec,
)
from enterprise_rag.provider_profiles import (
    OnlineRunPreflight,
    ProviderProfile,
    profile_fingerprint,
)

API_KEY_ENV = "RAG_AGENT_ONLINE_API_KEY"

STAGES = tuple(
    InitializationStage(name, "succeeded", 12.5)
    for name in (
        "CHECKING_CONFIG",
        "LOADING_INDEX",
        "LOADING_RETRIEVER",
        "LOADING_RERANKER",
        "PREPARING_DEFAULT_LOCAL_GENERATOR",
        "READY",
    )
)

Outcome = Literal["answered", "refused", "failed", "blocking"]


def demo_profile() -> ProviderProfile:
    return ProviderProfile(
        name="relay",
        base_url="https://relay.example.invalid/v1",
        model_id="provider-model",
        api_key_env=API_KEY_ENV,
        privacy_terms_url="https://relay.example.invalid/privacy",
        input_price_per_million=Decimal("2.5"),
        output_price_per_million=Decimal("5"),
        request_limit=30,
        budget_limit_cny=Decimal("10"),
        synthetic_data_only=True,
        enabled=True,
    )


class FakeRegistry:
    def __init__(self, profiles: tuple[ProviderProfile, ...] = ()) -> None:
        self.profiles = profiles
        self.authorized = 0

    def profile_for(self, name: str | None) -> ProviderProfile:
        matches = tuple(item for item in self.profiles if item.name == name)
        if len(matches) != 1:
            raise ValueError("provider profile is missing")
        return matches[0]

    async def preflight(self, request: object) -> OnlineRunPreflight:
        profile = self.profile_for(getattr(request, "provider_name", None))
        return OnlineRunPreflight(
            run_id=request.run_id,  # type: ignore[attr-defined]
            profile_name=profile.name,
            provider_fingerprint=profile_fingerprint(profile),
            base_url=profile.base_url,
            model_id=profile.model_id,
            api_key_env=profile.api_key_env,
            privacy_terms_url=profile.privacy_terms_url,
            max_requests=2,
            request_limit=profile.request_limit,
            input_token_cap=16_384,
            output_token_cap=512,
            synthetic_data_only=True,
            fallback_local=request.fallback_local,  # type: ignore[attr-defined]
            max_cost_cny=Decimal("0.09"),
            already_spent_cny=Decimal("0"),
            projected_total_cny=Decimal("0.09"),
            budget_limit_cny=profile.budget_limit_cny,
        )

    def authorize(self, preflight: OnlineRunPreflight) -> OnlineRunAuthorization:
        self.authorized += 1
        return OnlineRunAuthorization(
            authorization_id="auth-1",
            run_id=preflight.run_id,
            profile_fingerprint=preflight.provider_fingerprint,
            max_requests=preflight.max_requests,
            input_token_cap=preflight.input_token_cap,
            output_token_cap=preflight.output_token_cap,
            max_cost_cny=preflight.max_cost_cny,
            synthetic_data_only=preflight.synthetic_data_only,
            fallback_local=preflight.fallback_local,
        )


def _trace(run_id: str, *, status: str) -> object:
    recorder = TraceRecorder(run_id)
    with recorder.span("retrieval", attributes={"backend_kind": "real"}) as span:
        span.finish("succeeded", attributes={"items": ({"chunk_id": "a"},)})
    with recorder.span(
        "generation",
        attributes={"backend_kind": "real", "model_id": "local-model"},
    ):
        pass
    return recorder.finish(status)  # type: ignore[arg-type]


class _FakeApplication:
    def __init__(
        self,
        spec: RunSpec,
        token: CancellationToken,
        outcome: Outcome,
    ) -> None:
        self._spec = spec
        self._token = token
        self._outcome = outcome

    async def ask_rag(self, request: object) -> RagResult:
        return await self._result("rag")

    async def run_agent(self, request: object) -> RagResult:
        return await self._result("agent")

    async def _result(self, mode: str) -> RagResult:
        del mode
        if self._outcome == "blocking":
            for _ in range(400):
                if self._token.cancelled:
                    raise RunCancelled("run cancelled")
                await asyncio.sleep(0.005)
            raise RunCancelled("run cancelled")
        run_id = self._spec.run_id
        if self._outcome == "refused":
            return RagResult.refused(
                "insufficient_evidence",
                trace=_trace(run_id, status="refused"),  # type: ignore[arg-type]
            )
        if self._outcome == "failed":
            return RagResult.failed(
                "retrieval_unavailable",
                trace=_trace(run_id, status="failed"),  # type: ignore[arg-type]
            )
        return RagResult.answered(
            "The P4 hotel cap is CNY 600 per night.",
            (
                Source(
                    filename="travel-policy.md",
                    chunk_id="travel-policy.md::0000",
                    score=0.91,
                ),
            ),
            trace=_trace(run_id, status="succeeded"),  # type: ignore[arg-type]
        )


class FakeRuntime:
    """Satisfies both the coordinator's and the controller's runtime surface."""

    def __init__(
        self,
        *,
        ready: bool = True,
        outcomes: tuple[Outcome, ...] = ("answered",),
        profiles: tuple[ProviderProfile, ...] | None = None,
    ) -> None:
        self._state = "ready" if ready else "initialization_failed"
        self.registry = FakeRegistry(
            (demo_profile(),) if profiles is None else profiles
        )
        self.runtime_ready_seconds = 1.5
        self.initializations = 0
        self.aclose_calls = 0
        self.started: list[RunSpec] = []
        self.observers: list[object] = []
        self._outcomes = list(outcomes) or ["answered"]

    @property
    def state(self) -> str:
        return self._state

    async def initialize(self, observer: object = None) -> tuple[object, ...]:
        self.initializations += 1
        await asyncio.sleep(0)
        for stage in STAGES:
            if observer is not None:
                observer(stage)  # type: ignore[operator]
        return STAGES

    async def application_for(
        self,
        spec: RunSpec,
        token: CancellationToken,
        observer: object = None,
    ) -> _FakeApplication:
        self.started.append(spec)
        if observer is not None:
            self.observers.append(observer)
        outcome = self._outcomes[min(len(self.started) - 1, len(self._outcomes) - 1)]
        if observer is not None:
            observer(  # type: ignore[operator]
                TraceEvent(
                    spec.run_id,
                    f"{spec.run_id}:0001",
                    None,
                    "retrieval",
                    "started",
                    0.0,
                    0.0,
                    {},
                    None,
                )
            )
        return _FakeApplication(spec, token, outcome)

    async def aclose(self) -> None:
        self.aclose_calls += 1
