"""NiceGUI main file for the page tests' ``user`` fixture.

``nicegui.testing.user_simulation`` executes this file with ``runpy`` under
``__main__``, so the page must be registered at module level and ``ui.run()``
must be called for the app to have a run configuration. Under user simulation
``ui.run`` does not start a server.

The page is built from fakes: the real coordinator and controller run against a
``FakeRuntime``, so a page test exercises the whole read path without loading a
model, opening a socket, or touching a provider. ``RAG_PANEL_TEST_*`` environment
variables let a test choose the runtime's initialization state and the scripted
outcome of each run.
"""

from __future__ import annotations

import os

from nicegui import Client, ui

from enterprise_rag.panel.app import create_panel_app
from tests.integration.panel_fakes import FakeRuntime

_OUTCOMES = ("answered", "refused", "failed", "blocking")


def _scripted_outcomes() -> tuple[str, ...]:
    requested = os.environ.get("RAG_PANEL_TEST_OUTCOMES", "answered")
    outcomes = tuple(item for item in requested.split(",") if item in _OUTCOMES)
    return outcomes or ("answered",)  # type: ignore[return-value]


def _runtime_factory() -> FakeRuntime:
    return FakeRuntime(
        ready=os.environ.get("RAG_PANEL_TEST_READY", "true") == "true",
        outcomes=_scripted_outcomes(),  # type: ignore[arg-type]
        profiles=() if os.environ.get("RAG_PANEL_TEST_NO_PROFILES") else None,
    )


create_panel_app(runtime_factory=_runtime_factory)

# NiceGUI's user plugin evicts the modules that registered pages *and every one
# of their parents* from sys.modules when a simulation ends. The panel page is
# defined in ``enterprise_rag.panel.app``, so leaving it attributed there would
# evict ``enterprise_rag`` itself and break every later test that relies on
# module identity. The page belongs to this harness, so it is re-attributed to
# this module, whose only parent is ``__main__``.
for _page in Client.page_routes:
    _page.__module__ = __name__

ui.run()
