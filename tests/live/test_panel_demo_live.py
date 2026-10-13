"""Real-local acceptance for the panel path, three scenes x three rounds.

This drives the real ``PanelController -> RunCoordinator -> PanelRuntime ->
Application`` route with socket connections hard-failed, and asserts the same
semantic and latency ceilings the CLI baseline uses. It shares its runner and
its assertions with ``scripts/collect_panel_acceptance.py`` so the two can never
drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.collect_panel_acceptance import (
    MAX_RUNTIME_READY_SECONDS,
    MAX_SCENARIO_P95_SECONDS,
    RUNS_PER_SCENE,
    SCENES,
    latency_summary,
    peak_working_set_bytes,
    run_acceptance,
)

_BENCHMARK = Path(".local/benchmarks/panel-demo-live.json")


@pytest.mark.live
@pytest.mark.asyncio
async def test_panel_coordinator_real_local_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "1")

    measurements = await run_acceptance(RUNS_PER_SCENE)

    # Zero network attempts: run_acceptance hard-fails every socket connect.
    assert measurements["network_attempts"] == 0
    # Backend provenance is real-local, never fake-backed. The Evidence Gate is
    # a programmatic component by design, so "programmatic" is expected too.
    assert "real" in measurements["backend_kinds"]
    assert "fake" not in measurements["backend_kinds"]
    # Heavy components each load exactly once.
    for name in ("index", "encoder", "reranker", "local_generator"):
        assert measurements["load_counters"][name] == 1, name

    assert measurements["runtime_ready_seconds"] <= MAX_RUNTIME_READY_SECONDS
    percentiles = {}
    for scene in SCENES:
        assert measurements["failures"][scene] == [], measurements["failures"][scene]
        summary = latency_summary(measurements["scenario_seconds"][scene])
        assert summary["p95_seconds"] <= MAX_SCENARIO_P95_SECONDS, scene
        percentiles[scene] = summary

    benchmark = {
        "backend_kinds": measurements["backend_kinds"],
        "execution_surface": "panel-coordinator",
        "load_counters": measurements["load_counters"],
        "network_attempts": measurements["network_attempts"],
        "observed_peak_process_ram_bytes": peak_working_set_bytes(),
        "percentiles": percentiles,
        "provenance": "real-local",
        "runs_per_scene": RUNS_PER_SCENE,
        "runtime_ready_seconds": measurements["runtime_ready_seconds"],
        "ui_available_seconds": measurements["ui_available_seconds"],
    }
    _BENCHMARK.parent.mkdir(parents=True, exist_ok=True)
    _BENCHMARK.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(benchmark, ensure_ascii=False, sort_keys=True))
