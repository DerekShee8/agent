"""Schema and promotion tests for the one authorized real-online artifact.

These run offline, without a key, and without touching a provider.
"""

from __future__ import annotations

import pytest

from scripts.collect_panel_online_acceptance import (
    CAVEATS,
    SCHEMA_VERSION,
    promote,
    validate_online_artifact,
)


def artifact() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": "2026-10-12T00:00:00+00:00",
        "git_commit": "0123456789abcdef",
        "execution_surface": "panel-coordinator",
        "backend_provenance": "real-online",
        "profile_name": "relay_example",
        "profile_fingerprint": "b" * 64,
        "base_url_host": "relay.example.invalid",
        "effective_base_url": "https://relay.example.invalid/v1/",
        "model_id": "provider-advertised-model-id",
        "model_identity_verified": False,
        "privacy_terms_body_empty": True,
        "pricing_is_assumed_upper_bound": True,
        "provider_is_third_party_relay": True,
        "caveats": list(CAVEATS),
        "total_provider_requests": 11,
        "configured_request_cap": 30,
        "cost_upper_bound_cny": "0.42",
        "cost_basis": "assumed conservative per-token upper bound",
        "scenarios": {
            "rag": {"outcomes": ["answered"], "seconds": [12.5], "summary": None},
            "agent": {"outcomes": ["answered"], "seconds": [25.0], "summary": None},
            "refusal": {"outcomes": ["refused"], "seconds": [0.8], "summary": None},
        },
        "assertions": {
            "rag": {"rounds": 1, "failures": [], "passed": True},
            "agent": {"rounds": 1, "failures": [], "passed": True},
            "refusal": {"rounds": 1, "failures": [], "passed": True},
        },
        "negative_checks": {
            "replayed_authorization_is_refused": True,
            "mismatched_fingerprint_is_refused": True,
        },
        "network_allowed_for_this_run": True,
        "peak_ram_bytes": 1024,
        "peak_vram_mib": None,
    }


def test_promote_accepts_a_valid_online_artifact() -> None:
    payload = artifact()

    assert promote(payload) == payload


def test_validate_rejects_missing_and_extra_fields() -> None:
    incomplete = artifact()
    incomplete.pop("caveats")
    with pytest.raises(ValueError, match="missing"):
        validate_online_artifact(incomplete)

    extended = artifact()
    extended["raw_response"] = "the provider said this"
    with pytest.raises(ValueError, match="extra"):
        validate_online_artifact(extended)


def test_validate_rejects_a_verified_model_identity_claim() -> None:
    payload = artifact()
    payload["model_identity_verified"] = True

    with pytest.raises(ValueError, match="never verified"):
        validate_online_artifact(payload)


def test_validate_requires_the_four_caveats_verbatim() -> None:
    payload = artifact()
    payload["caveats"] = list(CAVEATS)[:3]

    with pytest.raises(ValueError, match="verbatim"):
        validate_online_artifact(payload)


def test_validate_rejects_wrong_provenance_or_surface() -> None:
    for key, value in (
        ("backend_provenance", "real-local"),
        ("execution_surface", "cli"),
    ):
        payload = artifact()
        payload[key] = value
        with pytest.raises(ValueError):
            validate_online_artifact(payload)


def test_validate_requires_exactly_the_three_scenes() -> None:
    payload = artifact()
    payload["scenarios"] = {"rag": {}}

    with pytest.raises(ValueError, match="three scenes"):
        validate_online_artifact(payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key", "anything"),
        ("authorization", "Bearer something"),
        ("secret", "anything"),
        ("access_token", "anything"),
        ("prompt", "P4 员工去上海出差"),
        ("system_prompt", "you are a helpful assistant"),
        ("response", "the provider said this"),
        ("traceback", "Traceback (most recent call last):"),
    ],
)
def test_promote_rejects_secret_like_keys(key: str, value: str) -> None:
    payload = artifact()
    payload["scenarios"] = {"rag": {key: value}, "agent": {}, "refusal": {}}

    with pytest.raises(ValueError, match="refusing to promote"):
        promote(payload)


@pytest.mark.parametrize(
    "value",
    [
        "E:\\onedrive文件\\OneDrive\\providers.toml",
        "C:/Users/EASON/model.gguf",
        "/home/runner/model.gguf",
        "RAG_AGENT_ONLINE_API_KEY=sk-live-value",
        "Authorization: Bearer sk-live-0123456789abcdefghijklmnopqrstuvwxyz",
        "Traceback (most recent call last): boom",
    ],
)
def test_promote_rejects_paths_env_values_and_secret_values(value: str) -> None:
    payload = artifact()
    payload["scenarios"] = {"rag": {"note": value}, "agent": {}, "refusal": {}}

    with pytest.raises(ValueError, match="refusing to promote"):
        promote(payload)


def test_the_output_schema_cannot_carry_a_raw_api_key() -> None:
    """The artifact has no key field, and a key-shaped value is refused."""

    from scripts.collect_panel_online_acceptance import ONLINE_ARTIFACT_SCHEMA

    assert not any(
        "key" in name and "api_key_env" not in name for name in ONLINE_ARTIFACT_SCHEMA
    )
    payload = artifact()
    payload["profile_name"] = "sk-live-0123456789abcdefghijklmnopqrstuvwxyz"

    with pytest.raises(ValueError, match="refusing to promote"):
        promote(payload)
