"""Panel contracts.

This package exports the panel's pure data contracts only. Importing it must
never require the optional ``panel`` extra, so no NiceGUI symbol is re-exported
here; the browser application lives in :mod:`enterprise_rag.panel.app` and is
imported lazily by the CLI.
"""

from .contracts import (
    InitializationStage,
    OnlineRunAuthorization,
    PanelLifecycleState,
    PanelMode,
    PanelRunRecord,
    PanelTerminalState,
    RunRequest,
    RunSpec,
)

__all__ = [
    "InitializationStage",
    "OnlineRunAuthorization",
    "PanelLifecycleState",
    "PanelMode",
    "PanelRunRecord",
    "PanelTerminalState",
    "RunRequest",
    "RunSpec",
]
