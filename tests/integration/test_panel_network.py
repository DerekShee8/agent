"""Loopback-only tripwires and secret tripwires for the whole panel path.

These tests hard-fail any socket attempt on the local/fake path and prove that a
recognizable secret planted in the environment, a profile, or a fake error can
never reach progress, history, the confirmation view, initialization messages, or
a page view model.
"""

from __future__ import annotations

import socket
from decimal import Decimal
from pathlib import Path

import pytest

from enterprise_rag.cli import PANEL_HOST, main
from enterprise_rag.config import AppConfig
from enterprise_rag.errors import ConfigurationError, ProviderUnavailableError
from enterprise_rag.execution_trace import TraceRecorder
from enterprise_rag.models import RagResult, Source
from enterprise_rag.panel.contracts import (
    InitializationStage,
    PanelRunRecord,
    RunRequest,
    RunSpec,
)
from enterprise_rag.panel.controller import PanelController
from enterprise_rag.panel.coordinator import RunCoordinator
from enterprise_rag.panel.progress import BoundedProgressBuffer
from enterprise_rag.panel.public_trace import reconcile_terminal_events
from enterprise_rag.provider_profiles import ProviderProfile

SECRET = "sk-live-should-never-surface"
API_KEY_ENV = "RAG_AGENT_ONLINE_API_KEY"
FORBIDDEN = (SECRET, API_KEY_ENV + "=", "Traceback (most recent call last)")


class FakeRegistry:
    def __init__(self, profiles: tuple[ProviderProfile, ...] = ()) -> None:
        self.profiles = profiles

    def profile_for(self, name: str | None) -> ProviderProfile:
        matches = tuple(item for item in self.profiles if item.name == name)
        if len(matches) != 1:
            raise ConfigurationError("provider profile is missing")
        return matches[0]

    async def preflight(self, request: object) -> object:
        raise ProviderUnavailableError(f"relay said no: {SECRET}")

    def authorize(self, preflight: object) -> object:
        raise AssertionError("no authorization may be minted here")


class FakeApplication:
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id

    async def ask_rag(self, request: object) -> RagResult:
        recorder = TraceRecorder(self._run_id)
        with recorder.span("retrieval", attributes={"backend_kind": "real"}) as span:
            span.finish("succeeded", attributes={"items": ({"chunk_id": "a"},)})
        with recorder.span("generation", attributes={"backend_kind": "real"}):
            pass
        return RagResult.answered(
            "The P4 hotel cap is CNY 600 per night.",
            (Source(filename="policy.md", chunk_id="policy.md::0000", score=0.9),),
            trace=recorder.finish("succeeded"),
        )

    async def run_agent(self, request: object) -> RagResult:
        return await self.ask_rag(request)


class FakeRuntime:
    def __init__(self) -> None:
        self.registry = FakeRegistry()
        self.runtime_ready_seconds = 1.0
        self.state = "ready"
        self.aclose_calls = 0

    async def initialize(self, observer: object = None) -> tuple[object, ...]:
        del observer
        return ()

    async def application_for(self, spec: RunSpec, *args: object) -> FakeApplication:
        return FakeApplication(spec.run_id)

    async def aclose(self) -> None:
        self.aclose_calls += 1


def spec_for(run_id: str = "run-1") -> RunSpec:
    return RunSpec(
        run_id=run_id,
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


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def forbidden(*args: object, **kwargs: object) -> object:
        attempts.append((args, kwargs))
        raise AssertionError("network access attempted on the local path")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    return attempts


@pytest.mark.asyncio
async def test_a_local_panel_run_never_opens_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = forbid_network(monkeypatch)
    runtime = FakeRuntime()
    coordinator = RunCoordinator(runtime)  # type: ignore[arg-type]
    await coordinator.initialize()

    run_id = await coordinator.start(spec_for())
    record = await coordinator.wait(run_id)

    assert record.state == "answered"
    assert attempts == []


@pytest.mark.asyncio
async def test_the_panel_only_ever_binds_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((PANEL_HOST, 0))
        free_port = probe.getsockname()[1]

    bound: list[object] = []
    real_bind = socket.socket.bind

    def recording_bind(self: socket.socket, address: object) -> None:
        bound.append(address)
        real_bind(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "bind", recording_bind)
    calls: list[dict[str, object]] = []

    exit_code = main(
        ["panel", "--port", str(free_port), "--no-open"],
        panel_runner=lambda **kwargs: calls.append(dict(kwargs)) or 0,
    )

    assert exit_code == 0
    assert bound == [(PANEL_HOST, free_port)]
    assert calls[0]["host"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_no_secret_reaches_progress_history_or_the_confirmation_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, SECRET)
    profile = ProviderProfile(
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
    runtime = FakeRuntime()
    runtime.registry = FakeRegistry((profile,))
    coordinator = RunCoordinator(runtime)  # type: ignore[arg-type]
    await coordinator.initialize()
    controller = PanelController(coordinator, runtime, clock=lambda: 0.0)  # type: ignore[arg-type]
    controller.select_scenario("rag-shanghai-p4")
    controller.select_generator("online")

    confirmation = await controller.prepare_run()

    assert confirmation is None  # the preflight fails with a secret-bearing error
    rendered = repr(controller.view) + repr(controller.view.blocked_message)
    for forbidden in FORBIDDEN:
        assert forbidden not in rendered


def test_no_secret_reaches_a_page_view_model_or_an_initialization_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, SECRET)
    stage = InitializationStage(
        name="CHECKING_CONFIG",
        status="failed",
        duration_ms=1.0,
        error_category="configuration",
        public_error="panel initialization failed at CHECKING_CONFIG: configuration",
    )
    recorder = TraceRecorder("run-1")
    with recorder.span(
        "generation",
        attributes={
            "backend_kind": "real",
            "api_key": SECRET,
            "system_prompt": SECRET,
        },
    ):
        pass
    trace = recorder.finish("succeeded")
    record = PanelRunRecord(
        run_id="run-1",
        request=RunRequest(
            run_id="run-1",
            created_at=0.0,
            mode="rag",
            scenario_id="scenario",
            question_or_task="question",
            generator="local",
            provider_name=None,
            provider_fingerprint=None,
            fallback_local=False,
        ),
        spec=spec_for(),
        state="answered",
        result=RagResult.answered(
            "answer",
            (Source(filename="policy.md", chunk_id="policy.md::0000", score=0.9),),
            trace=trace,
        ),
        trace=trace,
        progress_events=trace.events,
        initialization_duration_ms=1.0,
        run_duration_ms=2.0,
        error_category=None,
        public_error=None,
    )

    from enterprise_rag.panel.projection import project_history, project_run

    rendered = (
        repr(project_run(record))
        + repr(project_history((record,)))
        + repr(stage)
        + repr(record.progress_events)
    )
    for forbidden in FORBIDDEN:
        assert forbidden not in rendered
    assert SECRET not in rendered


def test_progress_and_the_final_trace_agree_on_every_terminal_event() -> None:
    recorder = TraceRecorder("run-1")
    buffer = BoundedProgressBuffer()
    buffer.subscribe("browser")
    recorder = TraceRecorder("run-1", observer=buffer.publish)
    with recorder.span("retrieval", attributes={"backend_kind": "real"}) as span:
        span.finish("succeeded", attributes={"items": ({"chunk_id": "a"},)})
    with recorder.span("generation", attributes={"backend_kind": "real"}):
        pass
    trace = recorder.finish("succeeded")

    merged = reconcile_terminal_events(buffer.drain(), trace)
    by_id = {event.event_id: event for event in merged}

    assert len(by_id) == len(trace.events)
    for final in trace.events:
        live = by_id[final.event_id]
        assert (
            live.parent_id,
            live.stage,
            live.status,
            live.error_category,
        ) == (
            final.parent_id,
            final.stage,
            final.status,
            final.error_category,
        )


def test_the_panel_config_never_enables_network_by_default(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    config = AppConfig(
        model_cache_dir=cache,
        index_dir=tmp_path / "index",
        local_model_path=cache / "model.gguf",
    )

    assert config.allow_network is False
