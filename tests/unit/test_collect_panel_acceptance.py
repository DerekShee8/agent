"""Pure schema, statistics, atomic-write, and promotion tests for the collector.

These never load a model, open a socket, or import the panel runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.collect_panel_acceptance import (
    SCHEMA_VERSION,
    atomic_write,
    latency_summary,
    nearest_rank,
    promote,
    validate_artifact,
)


def artifact() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": "2026-10-12T00:00:00+00:00",
        "git_commit": "0123456789abcdef",
        "execution_surface": "panel-coordinator",
        "ui_availability_note": "route/shell registration, not browser paint",
        "hardware": {"cpu_count": 8, "platform": "Windows-11"},
        "backend": {"kind": "real-local", "model_id": "Qwen/Qwen3-4B-GGUF"},
        "initialization": {"runtime_ready_seconds": 11.5, "stages": []},
        "scenarios": {"rag": [1.0, 2.0, 3.0], "agent": [4.0], "refusal": [0.5]},
        "percentiles": {"rag": {"p95_seconds": 3.0}},
        "assertions": {"rag": {"passed": True, "failures": [], "rounds": 3}},
        "network_attempts": 0,
        "load_counters": {"index": 1, "encoder": 1, "reranker": 1},
        "peak_ram_bytes": 1024,
        "peak_vram_mib": None,
        "online": "online-attempted-not-verified",
    }


def test_nearest_rank_uses_the_observed_ceiling_index() -> None:
    values = [3.0, 1.0, 2.0]

    assert nearest_rank(values, 0.50) == 2.0
    assert nearest_rank(values, 0.95) == 3.0  # p95 of three samples is the max
    assert nearest_rank(values, 1.0) == 3.0


def test_nearest_rank_rejects_empty_or_out_of_range_input() -> None:
    with pytest.raises(ValueError):
        nearest_rank([], 0.5)
    with pytest.raises(ValueError):
        nearest_rank([1.0], 0.0)


def test_latency_summary_reports_samples_and_percentiles() -> None:
    summary = latency_summary([10.0, 30.0, 20.0])

    assert summary["samples_seconds"] == [10.0, 30.0, 20.0]
    assert summary["p50_seconds"] == 20.0
    assert summary["p95_seconds"] == 30.0
    assert summary["max_seconds"] == 30.0


def test_latency_summary_rejects_non_finite_samples() -> None:
    with pytest.raises(ValueError):
        latency_summary([1.0, float("nan")])


def test_atomic_write_replaces_the_target_and_leaves_no_temp_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "nested" / "artifact.json"

    atomic_write(target, {"value": 1})
    atomic_write(target, {"value": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 2}
    assert [item.name for item in target.parent.iterdir()] == ["artifact.json"]


def test_validate_rejects_missing_and_extra_fields() -> None:
    incomplete = artifact()
    incomplete.pop("network_attempts")
    with pytest.raises(ValueError, match="missing"):
        validate_artifact(incomplete)

    extended = artifact()
    extended["raw_transcript"] = "hello"
    with pytest.raises(ValueError, match="extra"):
        validate_artifact(extended)

    mistyped = artifact()
    mistyped["network_attempts"] = "0"
    with pytest.raises(ValueError, match="wrong type"):
        validate_artifact(mistyped)


def test_validate_requires_exactly_the_three_scenes() -> None:
    payload = artifact()
    payload["scenarios"] = {"rag": [1.0]}

    with pytest.raises(ValueError, match="three scenes"):
        validate_artifact(payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key", "anything"),
        ("authorization", "anything"),
        ("secret_token", "anything"),
        ("prompt", "P4 员工去上海出差"),
        ("system_prompt", "you are a helpful assistant"),
        ("response", "the model said this"),
        ("traceback", "Traceback (most recent call last):"),
    ],
)
def test_promote_rejects_secret_like_keys(key: str, value: str) -> None:
    payload = artifact()
    payload["hardware"] = {key: value}

    with pytest.raises(ValueError, match="refusing to promote"):
        promote(payload)


@pytest.mark.parametrize(
    "value",
    [
        "E:\\onedrive文件\\OneDrive\\secret.json",
        "C:/Users/EASON/model.gguf",
        "/home/runner/model.gguf",
        "RAG_AGENT_ONLINE_API_KEY=sk-live-value",
        "Traceback (most recent call last): boom",
        "sk-live-0123456789abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_promote_rejects_absolute_paths_env_values_and_secret_values(
    value: str,
) -> None:
    payload = artifact()
    payload["hardware"] = {"note": value}

    with pytest.raises(ValueError, match="refusing to promote"):
        promote(payload)


def test_promote_accepts_a_valid_artifact() -> None:
    payload = artifact()

    assert promote(payload) == payload


def test_promote_accepts_identifiers_and_content_hashes() -> None:
    payload = artifact()
    payload["initialization"] = {
        "stages": [{"name": "PREPARING_DEFAULT_LOCAL_GENERATOR", "status": "succeeded"}]
    }
    payload["backend"] = {
        "model_digest": "a" * 64,
        "model_revision": "b" * 40,
        "git_commit": "c" * 40,
    }

    assert promote(payload) == payload


def test_promote_rejects_a_payload_that_is_not_an_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        promote([artifact()])
