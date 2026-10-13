from __future__ import annotations

import socket
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from enterprise_rag.errors import (
    BudgetBlockedError,
    ConfigurationError,
    PrivacyBlockedError,
    ProfileNotAllowedError,
)
from enterprise_rag.provider_profiles import (
    OnlineCallPermit,
    OnlineRunPreflight,
    ProviderProfile,
    load_profiles,
    preflight_online_call,
    preflight_online_run,
    profile_fingerprint,
)

EXAMPLE_PATH = Path("config/providers.example.toml")


def profile_toml(name: str = "relay", **overrides: object) -> str:
    values: dict[str, object] = {
        "enabled": False,
        "base_url": "https://relay.example.invalid/v1",
        "model_id": "provider-model",
        "api_key_env": "RAG_AGENT_RELAY_API_KEY",
        "privacy_terms_url": "https://relay.example.invalid/privacy",
        "input_price_per_million": "0",
        "output_price_per_million": "0",
        "request_limit": 0,
        "budget_limit_cny": "0",
        "synthetic_data_only": True,
    }
    values.update(overrides)
    lines = [f"[profiles.{name}]"]
    for key, value in values.items():
        if isinstance(value, bool):
            encoded = str(value).lower()
        elif isinstance(value, int):
            encoded = str(value)
        else:
            encoded = repr(value)
        lines.append(f"{key} = {encoded}")
    return "\n".join(lines) + "\n"


def write_profile(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "providers.toml"
    path.write_text(text, encoding="utf-8")
    return path


def enabled_profile(**overrides: object) -> ProviderProfile:
    values: dict[str, object] = {
        "name": "relay",
        "base_url": "https://relay.example.invalid/v1",
        "model_id": "provider-model",
        "api_key_env": "RAG_AGENT_RELAY_API_KEY",
        "privacy_terms_url": "https://relay.example.invalid/privacy",
        "input_price_per_million": Decimal("2.5"),
        "output_price_per_million": Decimal("5"),
        "request_limit": 3,
        "budget_limit_cny": Decimal("1"),
        "synthetic_data_only": True,
        "enabled": True,
    }
    values.update(overrides)
    return ProviderProfile(**values)  # type: ignore[arg-type]


def test_disabled_example_loads_with_exact_nonsecret_values() -> None:
    profiles = load_profiles(EXAMPLE_PATH)

    assert profiles == (
        ProviderProfile(
            name="relay_example",
            base_url="https://relay.example.invalid/v1",
            model_id="provider-advertised-model-id",
            api_key_env="RAG_AGENT_RELAY_API_KEY",
            privacy_terms_url="https://relay.example.invalid/privacy",
            input_price_per_million=Decimal("0"),
            output_price_per_million=Decimal("0"),
            request_limit=0,
            budget_limit_cny=Decimal("0"),
            synthetic_data_only=True,
            enabled=False,
        ),
    )
    assert "api_key =" not in EXAMPLE_PATH.read_text(encoding="utf-8")


def test_loader_returns_tuple_in_deterministic_name_order(tmp_path: Path) -> None:
    path = write_profile(
        tmp_path,
        profile_toml("zulu") + "\n" + profile_toml("alpha"),
    )

    profiles = load_profiles(path)

    assert isinstance(profiles, tuple)
    assert [profile.name for profile in profiles] == ["alpha", "zulu"]


def test_profile_is_frozen() -> None:
    profile = enabled_profile()

    with pytest.raises((FrozenInstanceError, AttributeError)):
        profile.enabled = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "text",
    [
        "[wrong.relay]\nenabled=false\n",
        profile_toml().replace("model_id = 'provider-model'\n", ""),
        profile_toml() + "unexpected = 'value'\n",
        profile_toml().replace("request_limit = 0", "request_limit = '0'"),
        profile_toml().replace("enabled = false", "enabled = 'false'"),
        profile_toml().replace(
            "input_price_per_million = '0'", "input_price_per_million = 0.0"
        ),
    ],
)
def test_loader_rejects_shape_and_scalar_type_errors(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigurationError):
        load_profiles(write_profile(tmp_path, text))


def test_loader_translates_duplicate_tables_without_leaking_content(
    tmp_path: Path,
) -> None:
    marker = "do-not-leak-this-marker"
    text = profile_toml() + f"\n[profiles.relay]\nmodel_id='{marker}'\n"

    with pytest.raises(ConfigurationError) as caught:
        load_profiles(write_profile(tmp_path, text))

    assert marker not in str(caught.value)
    assert "invalid or contains duplicate" in str(caught.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("api_key", "usable-looking-value"),
        ("token", "usable-looking-value"),
        ("password", "usable-looking-value"),
        ("authorization", "Bearer usable-looking-value"),
        ("credential", "usable-looking-value"),
        ("client_secret", "usable-looking-value"),
    ],
)
def test_loader_rejects_credential_bearing_keys_without_leaking_values(
    tmp_path: Path, key: str, value: str
) -> None:
    text = profile_toml() + f"{key} = '{value}'\n"

    with pytest.raises(ConfigurationError) as caught:
        load_profiles(write_profile(tmp_path, text))

    assert value not in str(caught.value)


def test_loader_rejects_credential_bearing_values(tmp_path: Path) -> None:
    marker = "password=do-not-store-me"

    with pytest.raises(ConfigurationError) as caught:
        load_profiles(write_profile(tmp_path, profile_toml(model_id=marker)))

    assert marker not in str(caught.value)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("input_price_per_million", "NaN"),
        ("input_price_per_million", "Infinity"),
        ("input_price_per_million", "-0.01"),
        ("output_price_per_million", "-Infinity"),
        ("budget_limit_cny", "NaN"),
        ("budget_limit_cny", "-1"),
        ("request_limit", -1),
    ],
)
def test_loader_rejects_invalid_prices_budget_and_limits(
    tmp_path: Path, field_name: str, value: object
) -> None:
    with pytest.raises(ConfigurationError):
        load_profiles(write_profile(tmp_path, profile_toml(**{field_name: value})))


@pytest.mark.parametrize(
    "api_key_env", ["", "lowercase_key", "9STARTS_WITH_NUMBER", "HAS-DASH"]
)
def test_profile_rejects_invalid_api_environment_names(api_key_env: str) -> None:
    with pytest.raises(ConfigurationError, match="api_key_env"):
        enabled_profile(api_key_env=api_key_env)


@pytest.mark.parametrize(
    "url",
    [
        "http://relay.example.invalid/v1",
        "https://user:pass@relay.example.invalid/v1",
        "https://relay.example.invalid/v1?key=value",
        "https://relay.example.invalid/v1#fragment",
        "https://localhost/v1",
        "https://api.localhost/v1",
        "https://localhost./v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://0.0.0.0/v1",
        "https://10.1.2.3/v1",
        "https://172.16.1.2/v1",
        "https://192.168.1.2/v1",
        "https://169.254.1.2/v1",
        "https://224.0.0.1/v1",
        "https://2130706433/v1",
        "https://0x7f000001/v1",
        "https://0177.0.0.1/v1",
        "https://127.1/v1",
        "https://relay.example.invalid./v1",
        "https://relay.example.invalid:443:80/v1",
        "https://relay.example.invalid:99999/v1",
        "https://relay.example.invalid\\@127.0.0.1/v1",
    ],
)
def test_loader_rejects_unsafe_or_malformed_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(ConfigurationError):
        load_profiles(write_profile(tmp_path, profile_toml(base_url=url)))


def test_loader_validates_privacy_url_too(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="privacy_terms_url"):
        load_profiles(
            write_profile(
                tmp_path,
                profile_toml(privacy_terms_url="https://127.0.0.1/privacy"),
            )
        )


def test_enabled_non_synthetic_profile_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="synthetic_data_only"):
        load_profiles(
            write_profile(
                tmp_path,
                profile_toml(enabled=True, synthetic_data_only=False),
            )
        )


def test_disabled_non_synthetic_profile_may_load(tmp_path: Path) -> None:
    profiles = load_profiles(
        write_profile(tmp_path, profile_toml(synthetic_data_only=False))
    )

    assert profiles[0].enabled is False
    assert profiles[0].synthetic_data_only is False


def test_preflight_returns_immutable_permit_with_exact_decimal_estimate() -> None:
    permit = preflight_online_call(
        enabled_profile(),
        allow_network=True,
        input_token_cap=1_000,
        output_token_cap=2_000,
        request_number=2,
        already_spent_cny=Decimal("0.1"),
        environ={"RAG_AGENT_RELAY_API_KEY": "resolved-secret"},
    )

    assert permit == OnlineCallPermit(
        profile_name="relay",
        base_url="https://relay.example.invalid/v1",
        model_id="provider-model",
        api_key_env="RAG_AGENT_RELAY_API_KEY",
        api_key="resolved-secret",
        input_token_cap=1_000,
        output_token_cap=2_000,
        request_number=2,
        request_limit=3,
        estimated_cost_cny=Decimal("0.0125"),
        already_spent_cny=Decimal("0.1"),
        projected_total_cny=Decimal("0.1125"),
        budget_limit_cny=Decimal("1"),
    )
    assert "resolved-secret" not in repr(permit)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        permit.request_number = 3  # type: ignore[misc]


def test_preflight_cost_is_exact_beyond_default_decimal_precision() -> None:
    price = Decimal("0.123456789012345678901234567890123456789")
    profile = enabled_profile(
        input_price_per_million=price,
        output_price_per_million=Decimal("0"),
    )

    permit = preflight_online_call(
        profile,
        allow_network=True,
        input_token_cap=1,
        output_token_cap=1,
        request_number=1,
        environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
    )
    with localcontext() as context:
        context.prec = 80
        expected = price / Decimal(1_000_000)

    assert permit.estimated_cost_cny == expected


def test_preflight_requires_a_validated_provider_profile() -> None:
    with pytest.raises(ConfigurationError, match="ProviderProfile"):
        preflight_online_call(
            object(),  # type: ignore[arg-type]
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_preflight_blocks_when_network_is_disabled() -> None:
    with pytest.raises(ProfileNotAllowedError, match="network"):
        preflight_online_call(
            enabled_profile(),
            allow_network=False,
            input_token_cap=1,
            output_token_cap=1,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_preflight_blocks_disabled_profile() -> None:
    profile = enabled_profile(enabled=False)

    with pytest.raises(ProfileNotAllowedError, match="disabled"):
        preflight_online_call(
            profile,
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_preflight_blocks_non_synthetic_profile_for_privacy() -> None:
    profile = enabled_profile(enabled=False, synthetic_data_only=False)

    with pytest.raises(PrivacyBlockedError, match="synthetic"):
        preflight_online_call(
            profile,
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


@pytest.mark.parametrize("request_number", [0, -1, True, 1.5])
def test_preflight_rejects_malformed_request_numbers(request_number: object) -> None:
    with pytest.raises(ConfigurationError, match="request_number"):
        preflight_online_call(
            enabled_profile(),
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=request_number,  # type: ignore[arg-type]
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_preflight_blocks_request_limit_exceeded() -> None:
    with pytest.raises(ProfileNotAllowedError, match="request limit"):
        preflight_online_call(
            enabled_profile(),
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=4,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"RAG_AGENT_RELAY_API_KEY": "  "},
        {"RAG_AGENT_RELAY_API_KEY": " secret-with-spaces "},
    ],
)
def test_preflight_rejects_missing_or_blank_key_without_leaking(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ConfigurationError, match="RAG_AGENT_RELAY_API_KEY") as caught:
        preflight_online_call(
            enabled_profile(),
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=1,
            environ=environment,
        )

    assert "resolved-secret" not in str(caught.value)


def test_preflight_maps_decimal_cost_overflow_to_configuration_error() -> None:
    profile = enabled_profile(
        input_price_per_million=Decimal("1e999999"),
        budget_limit_cny=Decimal("1e999999"),
    )

    with pytest.raises(ConfigurationError, match="cost"):
        preflight_online_call(
            profile,
            allow_network=True,
            input_token_cap=10**30,
            output_token_cap=1,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_preflight_blocks_projected_cost_above_budget() -> None:
    profile = enabled_profile(budget_limit_cny=Decimal("0.01"))

    with pytest.raises(BudgetBlockedError, match="budget"):
        preflight_online_call(
            profile,
            allow_network=True,
            input_token_cap=1_000,
            output_token_cap=2_000,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("input_token_cap", 0),
        ("input_token_cap", -1),
        ("input_token_cap", True),
        ("input_token_cap", 1.5),
        ("output_token_cap", 0),
        ("output_token_cap", False),
        ("already_spent_cny", Decimal("-0.01")),
        ("already_spent_cny", Decimal("NaN")),
        ("already_spent_cny", Decimal("Infinity")),
        ("already_spent_cny", 0.1),
        ("allow_network", "true"),
    ],
)
def test_preflight_rejects_malformed_caps_spend_and_flag(
    field_name: str, value: object
) -> None:
    kwargs: dict[str, object] = {
        "allow_network": True,
        "input_token_cap": 1,
        "output_token_cap": 1,
        "request_number": 1,
        "already_spent_cny": Decimal("0"),
        "environ": {"RAG_AGENT_RELAY_API_KEY": "secret"},
    }
    kwargs[field_name] = value

    with pytest.raises(ConfigurationError):
        preflight_online_call(enabled_profile(), **kwargs)  # type: ignore[arg-type]


def test_loader_and_preflight_never_resolve_dns_or_open_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def network_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", network_forbidden)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    profile = load_profiles(write_profile(tmp_path, profile_toml()))[0]

    with pytest.raises(ProfileNotAllowedError):
        preflight_online_call(
            profile,
            allow_network=True,
            input_token_cap=1,
            output_token_cap=1,
            request_number=1,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_profile_fingerprint_is_stable_and_bound_to_every_public_field() -> None:
    profile = enabled_profile()
    fingerprint = profile_fingerprint(profile)

    assert fingerprint == profile_fingerprint(profile)
    assert len(fingerprint) == 64
    assert all(character in "0123456789abcdef" for character in fingerprint)
    assert profile_fingerprint(replace(profile, model_id="changed")) != fingerprint
    assert profile_fingerprint(replace(profile, enabled=False)) != fingerprint
    assert profile_fingerprint(replace(profile, request_limit=9)) != fingerprint
    assert profile_fingerprint(replace(profile, api_key_env="OTHER_KEY")) != fingerprint
    assert "api_key" not in fingerprint


def _run_preflight(**overrides: object) -> OnlineRunPreflight:
    arguments: dict[str, object] = {
        "run_id": "run-1",
        "allow_network": True,
        "max_requests": 2,
        "input_token_cap": 1_000,
        "output_token_cap": 100,
        "fallback_local": False,
        "environ": {"RAG_AGENT_RELAY_API_KEY": "secret"},
    }
    arguments.update(overrides)
    return preflight_online_run(enabled_profile(), **arguments)  # type: ignore[arg-type]


def test_run_preflight_sums_every_planned_call_conservatively() -> None:
    preflight = _run_preflight()

    assert preflight.run_id == "run-1"
    assert preflight.max_requests == 2
    assert preflight.profile_name == "relay"
    assert preflight.model_id == "provider-model"
    assert preflight.privacy_terms_url == "https://relay.example.invalid/privacy"
    assert preflight.synthetic_data_only is True
    assert preflight.fallback_local is False
    assert preflight.already_spent_cny == Decimal(0)
    # 2 x (1000 x 2.5 / 1e6 + 100 x 5 / 1e6) = 0.006
    assert preflight.max_cost_cny == Decimal("0.006")
    assert preflight.projected_total_cny == Decimal("0.006")
    assert preflight.budget_limit_cny == Decimal("1")
    assert preflight.provider_fingerprint == profile_fingerprint(enabled_profile())


def test_run_preflight_continues_from_the_current_spend_snapshot() -> None:
    preflight = _run_preflight(
        run_id="run-2",
        max_requests=1,
        fallback_local=True,
        request_number=1,
        already_spent_cny=Decimal("0.003"),
    )

    assert preflight.already_spent_cny == Decimal("0.003")
    assert preflight.max_cost_cny == Decimal("0.003")
    assert preflight.projected_total_cny == Decimal("0.006")
    assert preflight.fallback_local is True


@pytest.mark.parametrize(
    ("profile_overrides", "expected"),
    [
        ({"request_limit": 1}, ProfileNotAllowedError),
        ({"budget_limit_cny": Decimal("0.004")}, BudgetBlockedError),
    ],
)
def test_run_preflight_rejects_request_or_budget_overflow(
    profile_overrides: dict[str, object],
    expected: type[Exception],
) -> None:
    with pytest.raises(expected):
        preflight_online_run(
            enabled_profile(**profile_overrides),  # type: ignore[arg-type]
            run_id="run-3",
            allow_network=True,
            max_requests=2,
            input_token_cap=1_000,
            output_token_cap=100,
            fallback_local=False,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


@pytest.mark.parametrize(
    ("profile_overrides", "allow_network"),
    [
        ({}, False),
        ({"enabled": False}, True),
    ],
)
def test_run_preflight_requires_network_and_an_enabled_profile(
    profile_overrides: dict[str, object],
    allow_network: bool,
) -> None:
    with pytest.raises(ProfileNotAllowedError):
        preflight_online_run(
            enabled_profile(**profile_overrides),  # type: ignore[arg-type]
            run_id="run-4",
            allow_network=allow_network,
            max_requests=1,
            input_token_cap=1_000,
            output_token_cap=100,
            fallback_local=False,
            environ={"RAG_AGENT_RELAY_API_KEY": "secret"},
        )


def test_run_preflight_requires_the_key_without_echoing_it() -> None:
    with pytest.raises(ConfigurationError, match="RAG_AGENT_RELAY_API_KEY"):
        _run_preflight(environ={})

    preflight = _run_preflight(
        environ={"RAG_AGENT_RELAY_API_KEY": "relay-secret-value"},
    )

    assert "relay-secret-value" not in repr(preflight)
    assert preflight.api_key_env == "RAG_AGENT_RELAY_API_KEY"
