"""The localhost-only NiceGUI panel.

This module is the only place that imports NiceGUI, so the rest of the panel
package stays importable without the optional ``panel`` extra. It owns one
process-wide runtime/coordinator and one lightweight controller per browser
client; the page renders view models and submits intents, nothing more.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from nicegui import app, ui

from ..errors import ConfigurationError
from .controller import (
    PanelController,
    PanelViewModel,
    ScenarioPreset,
)
from .coordinator import RunCoordinator
from .projection import PanelRunViewModel, TraceNodeViewModel
from .runtime import PanelRuntime

LOOPBACK_HOST = "127.0.0.1"
_REFRESH_SECONDS = 0.2
_CHOICE_VALUES = {
    "mode-rag": "rag",
    "mode-agent": "agent",
    "generator-local": "local",
    "generator-online": "online",
}


class _RuntimePort(Protocol):
    @property
    def state(self) -> str: ...

    @property
    def registry(self) -> object: ...

    @property
    def runtime_ready_seconds(self) -> float | None: ...

    async def initialize(self, observer: object = None) -> tuple[object, ...]: ...

    async def application_for(self, *args: object, **kwargs: object) -> object: ...

    async def aclose(self) -> None: ...


@dataclass
class _PanelState:
    runtime: _RuntimePort
    coordinator: RunCoordinator
    initialization: asyncio.Task[object] | None = None
    ui_available_at: float | None = None
    clients: list[PanelController] = field(default_factory=list)


def run_panel(
    *,
    host: str,
    port: int,
    open_browser: bool,
    native: bool,
) -> int:
    """Start the panel in browser mode on exactly ``host:port``.

    Every safety-relevant ``ui.run`` default is overridden explicitly: the
    non-native default host is ``0.0.0.0``, and the default ``reload=True``
    spawns a uvicorn reloader that would load every model twice and break the
    single-owner ``aclose`` contract.
    """

    if host != LOOPBACK_HOST:
        raise ConfigurationError("the panel only binds the loopback interface")
    if native is not False:
        raise ConfigurationError("the panel runs in browser mode only")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be a port number between 1 and 65535")
    if not isinstance(open_browser, bool):
        raise TypeError("open_browser must be a boolean")

    print(f"http://{host}:{port}/")
    ui.run(
        host=host,
        port=port,
        show=open_browser,
        native=False,
        reload=False,
        on_air=None,
        fastapi_docs=False,
    )
    return 0


def create_panel_app(
    *,
    runtime_factory: Callable[..., object] = PanelRuntime,
    controller_factory: Callable[..., object] = PanelController,
) -> object:
    """Register the panel route and return the one process-wide runtime."""

    runtime = cast(_RuntimePort, runtime_factory())
    coordinator = RunCoordinator(cast(Any, runtime))
    state = _PanelState(runtime=runtime, coordinator=coordinator)
    # Route and UI-shell availability is recorded before any model load, because
    # the page deliberately appears while initialization is still running.
    state.ui_available_at = time.perf_counter()

    @ui.page("/")
    async def panel_page() -> None:
        controller = cast(
            PanelController,
            controller_factory(coordinator, runtime),
        )
        state.clients.append(controller)
        rendered = {"state": controller.view.state}
        workbench = _workbench(controller)

        def refresh_controls() -> None:
            rendered["state"] = controller.view.state
            controls.refresh()

        controls = _controls(controller, workbench, refresh_controls)
        controls()
        workbench()
        _start_initialization(state)

        async def tick() -> None:
            """Refresh the controller's view model, then re-render what changed."""

            await controller.refresh()
            if controller.view.state != rendered["state"]:
                refresh_controls()
            workbench.refresh()

        ui.timer(_REFRESH_SECONDS, tick)
        ui.timer(0.0, tick, once=True)

    app.on_shutdown(_shutdown(state))
    return runtime


def _start_initialization(state: _PanelState) -> None:
    """Start (or join) the one shared initialization task in the background."""

    if state.initialization is None:
        state.initialization = asyncio.create_task(state.coordinator.initialize())


def _shutdown(state: _PanelState) -> Callable[[], object]:
    async def shutdown() -> None:
        state.clients.clear()
        # Detaches every progress buffer, settles the active run, and closes the
        # runtime exactly once, whatever the clients were doing.
        await state.coordinator.aclose()

    return shutdown


def _controls(
    controller: PanelController,
    workbench: ui.refreshable,
    refresh_controls: Callable[[], None],
) -> ui.refreshable:
    @ui.refreshable
    def controls() -> None:
        view = controller.view
        with ui.card().classes("w-full").mark("panel-controls"):
            ui.label("Local engineering panel").classes("text-h6")
            with ui.row().classes("w-full items-start"):
                for preset in view.presets:
                    _preset_card(controller, refresh_controls, preset)
            ui.input(
                "Custom question or task",
                value=view.custom_text,
                on_change=lambda event: controller.update_custom_text(
                    "" if event.value is None else str(event.value)
                ),
            ).classes("w-full").mark("custom-input")
            with ui.row().classes("items-center").mark("mode-controls"):
                ui.label("Mode:").classes("text-caption")
                _choice_button(
                    refresh_controls,
                    controller.select_mode,
                    "mode-rag",
                    "RAG",
                    view.mode == "rag",
                )
                _choice_button(
                    refresh_controls,
                    controller.select_mode,
                    "mode-agent",
                    "Agent",
                    view.mode == "agent",
                )
            with ui.row().classes("items-center").mark("generator-controls"):
                ui.label("Generator:").classes("text-caption")
                _choice_button(
                    refresh_controls,
                    controller.select_generator,
                    "generator-local",
                    "Local",
                    view.generator == "local",
                )
                _choice_button(
                    refresh_controls,
                    controller.select_generator,
                    "generator-online",
                    "Online",
                    view.generator == "online",
                )
            with ui.row().classes("items-center"):
                ui.select(
                    [option.name for option in view.providers],
                    label="Provider profile",
                    value=view.provider_name,
                    on_change=lambda event: _select_provider(
                        refresh_controls, controller, event.value
                    ),
                ).mark("provider-select")
                if view.fallback_available:
                    ui.switch(
                        "Fall back to local after an online failure",
                        value=view.fallback_local,
                        on_change=lambda event: _after(
                            refresh_controls,
                            controller.set_fallback_local,
                            bool(event.value),
                        ),
                    ).mark("fallback-switch")
            with ui.row().classes("items-center"):

                async def run_click() -> None:
                    await _run(controller, refresh_controls, workbench)

                def stop_click() -> None:
                    controller.request_stop()
                    refresh_controls()

                ui.button("Run", on_click=run_click).mark("run-button").set_enabled(
                    view.can_run
                )
                ui.button("Stop", on_click=stop_click).mark("stop-button").set_enabled(
                    view.state in {"running", "cancelling"}
                )

    return controls


def _preset_card(
    controller: PanelController,
    refresh_controls: Callable[[], None],
    preset: ScenarioPreset,
) -> None:
    with (
        ui.card()
        .classes("cursor-pointer")
        .mark(f"scenario-card-{preset.scenario_id}")
        .on(
            "click",
            lambda: _after(
                refresh_controls, controller.select_scenario, preset.scenario_id
            ),
        )
    ):
        ui.label(preset.scenario_id).classes("text-caption")
        ui.label(preset.text)


def _choice_button(
    refresh_controls: Callable[[], None],
    intent: Callable[..., object],
    marker: str,
    label: str,
    selected: bool,
) -> None:
    """A two-state choice rendered as an explicit, individually clickable button."""

    def choose() -> None:
        _after(refresh_controls, intent, _CHOICE_VALUES[marker])

    ui.button(label, on_click=choose).mark(marker).props(
        "outline" if not selected else "unelevated"
    ).set_enabled(not selected)


def _after(
    refresh_controls: Callable[[], None],
    intent: Callable[..., object],
    value: object,
) -> None:
    intent(value)
    refresh_controls()


def _select_provider(
    refresh_controls: Callable[[], None],
    controller: PanelController,
    name: object,
) -> None:
    if name:
        controller.select_provider(str(name))
    refresh_controls()


async def _run(
    controller: PanelController,
    refresh_controls: Callable[[], None],
    workbench: ui.refreshable,
) -> None:
    try:
        await controller.prepare_run()
    except ValueError:
        # A blank question or a missing provider is a form error: the controller
        # keeps the previous view and no run is created.
        pass
    refresh_controls()
    workbench.refresh()


def _workbench(controller: PanelController) -> ui.refreshable:
    @ui.refreshable
    def workbench() -> None:
        view = controller.view
        _initialization(view)
        _pipeline(view, controller)
        _result(view)
        _history(view, controller)
        _confirmation(view, controller)

    return workbench


def _initialization(view: PanelViewModel) -> None:
    with ui.row().classes("w-full items-center").mark("initialization"):
        ui.label(f"Panel state: {view.state}").mark("panel-state")
        for stage in view.initialization:
            ui.badge(f"{stage.name}: {stage.status}").mark(
                f"initialization-stage-{stage.name}"
            )


def _pipeline(view: PanelViewModel, controller: PanelController) -> None:
    with ui.row().classes("w-full items-start").mark("pipeline-canvas"):
        nodes = view.run.trace_nodes if view.run is not None else ()
        if not nodes:
            ui.label("No run yet.").mark("pipeline-empty")
        for node in nodes:
            _node(node, controller)
    with ui.column().classes("w-full").mark("inspector-panel"):
        if view.run is not None:
            _inspector(view.run)


def _node(node: TraceNodeViewModel, controller: PanelController) -> None:
    with (
        ui.card()
        .classes("cursor-pointer")
        .mark(f"pipeline-node-{node.stage}")
        .on("click", lambda: controller.select_trace_node(node.event_id))
    ):
        ui.label(node.label)
        ui.label(f"{node.status_text} ({node.status_icon})").classes("text-caption")
        ui.label(f"{node.duration_ms:.1f} ms").classes("text-caption")


def _inspector(run: PanelRunViewModel) -> None:
    inspector = run.inspector
    if inspector.empty_message is not None:
        ui.label(inspector.empty_message).mark("inspector-empty")
        return
    ui.label(f"Inspector: {inspector.title}").classes("text-subtitle2")
    ui.label(f"{inspector.status_text} ({inspector.status_icon})")
    for label, value in inspector.rows:
        ui.label(f"{label}: {value}").mark("inspector-row")


def _result(view: PanelViewModel) -> None:
    with ui.card().classes("w-full").mark("panel-result"):
        if view.run is None:
            return
        ui.label(f"{view.run.state_text} ({view.run.state_icon})").mark("run-state")
        if view.run.answer is not None:
            ui.label(view.run.answer).mark("run-answer")
        if view.run.refusal_reason is not None:
            ui.label(f"refusal: {view.run.refusal_reason}").mark("run-refusal")
        if view.run.public_error is not None:
            ui.label(f"error: {view.run.public_error}").mark("run-error")
        for source in view.run.sources:
            ui.label(f"source: {source.chunk_id}").mark("run-source")
        for observation in view.run.observations:
            ui.label(f"observation: {observation.label}").mark("run-observation")


def _history(view: PanelViewModel, controller: PanelController) -> None:
    with ui.row().classes("w-full items-center").mark("recent-runs"):
        for entry in view.history:
            ui.button(
                f"{entry.state_text}: {entry.scenario_id}",
                on_click=lambda run_id=entry.run_id: controller.select_history(run_id),
            ).mark(f"history-{entry.run_id}")


def _confirmation(view: PanelViewModel, controller: PanelController) -> None:
    confirmation = view.confirmation
    if confirmation is None:
        if view.blocked_message is not None:
            ui.label(view.blocked_message).mark("blocked-message")
        return
    with ui.card().classes("w-full").mark("online-confirmation"):
        ui.label(f"Provider: {confirmation.profile_name}")
        ui.label(f"Model: {confirmation.model_id}")
        ui.label(f"Endpoint: {confirmation.base_url}")
        ui.label(f"Privacy terms: {confirmation.privacy_terms_url}")
        ui.label(
            f"Request cap: {confirmation.max_requests} of {confirmation.request_limit}"
        )
        ui.label(f"Cost ceiling: CNY {confirmation.max_cost_cny}")
        ui.label(f"Fallback to local: {confirmation.fallback_local}")
        with ui.row():

            async def confirm_click() -> None:
                await controller.confirm_online()

            def cancel_click() -> None:
                controller.reject_online()

            ui.button("Confirm", on_click=confirm_click).mark("confirm-online")
            ui.button("Cancel", on_click=cancel_click).mark("cancel-online")


__all__ = [
    "LOOPBACK_HOST",
    "create_panel_app",
    "run_panel",
]
