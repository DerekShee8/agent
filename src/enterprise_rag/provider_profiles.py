from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    localcontext,
)
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from enterprise_rag.errors import (
    BudgetBlockedError,
    ConfigurationError,
    PrivacyBlockedError,
    ProfileNotAllowedError,
)

_PROFILE_FIELDS = frozenset(
    {
        "enabled",
        "base_url",
        "model_id",
        "api_key_env",
        "privacy_terms_url",
        "input_price_per_million",
        "output_price_per_million",
        "request_limit",
        "budget_limit_cny",
        "synthetic_data_only",
    }
)
_DECIMAL_FIELDS = (
    "input_price_per_million",
    "output_price_per_million",
    "budget_limit_cny",
)
_CREDENTIAL_TERM = re.compile(
    r"api[_-]?key|token|password|authorization|secret|credential",
    re.IGNORECASE,
)
_API_KEY_ENV = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_NUMERIC_HOST_LABEL = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)\Z")
_ONE_MILLION = Decimal(1_000_000)
_MAX_COST_PRECISION = 10_000


def _require_nonempty_string(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ConfigurationError(f"{name} must be a nonempty string")
    return value


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ConfigurationError(f"{name} must be a finite nonnegative Decimal")
    return value


def _validate_remote_https_url(name: str, value: object) -> str:
    url = _require_nonempty_string(name, value)
    if (
        url != url.strip()
        or "\\" in url
        or any(ord(character) < 32 for character in url)
    ):
        raise ConfigurationError(f"{name} must be a well-formed HTTPS remote URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ConfigurationError(
            f"{name} must be a well-formed HTTPS remote URL"
        ) from None

    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
        or port == 0
    ):
        raise ConfigurationError(
            f"{name} must be HTTPS without userinfo, query, or fragment"
        )

    host = parsed.hostname
    lowered_host = host.lower()
    if (
        "%" in host
        or lowered_host == "localhost"
        or lowered_host.endswith(".localhost")
        or host.endswith(".")
    ):
        raise ConfigurationError(f"{name} must identify a public remote host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2 or not all(_DNS_LABEL.fullmatch(label) for label in labels):
            raise ConfigurationError(f"{name} contains an invalid hostname") from None
        if all(_NUMERIC_HOST_LABEL.fullmatch(label) for label in labels):
            raise ConfigurationError(
                f"{name} contains an ambiguous numeric hostname"
            ) from None
    else:
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_private
        ):
            raise ConfigurationError(f"{name} must not use a non-public IP address")
    return url


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    name: str
    base_url: str
    model_id: str
    api_key_env: str
    privacy_terms_url: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    request_limit: int
    budget_limit_cny: Decimal
    synthetic_data_only: bool
    enabled: bool

    def __post_init__(self) -> None:
        _require_nonempty_string("name", self.name)
        _validate_remote_https_url("base_url", self.base_url)
        _require_nonempty_string("model_id", self.model_id)
        api_key_env = _require_nonempty_string("api_key_env", self.api_key_env)
        if not _API_KEY_ENV.fullmatch(api_key_env):
            raise ConfigurationError(
                "api_key_env must be an uppercase environment-variable name"
            )
        _validate_remote_https_url("privacy_terms_url", self.privacy_terms_url)
        _require_decimal("input_price_per_million", self.input_price_per_million)
        _require_decimal("output_price_per_million", self.output_price_per_million)
        if type(self.request_limit) is not int or self.request_limit < 0:
            raise ConfigurationError(
                "request_limit must be an actual nonnegative integer"
            )
        _require_decimal("budget_limit_cny", self.budget_limit_cny)
        if type(self.synthetic_data_only) is not bool:
            raise ConfigurationError("synthetic_data_only must be a boolean")
        if type(self.enabled) is not bool:
            raise ConfigurationError("enabled must be a boolean")
        if self.enabled and not self.synthetic_data_only:
            raise ConfigurationError(
                "enabled profiles must set synthetic_data_only to true"
            )


def profile_fingerprint(profile: ProviderProfile) -> str:
    """Deterministic SHA-256 over every non-secret profile field.

    The fingerprint identifies a profile configuration without exposing the
    environment value the key lives in, so it is safe to cache, log, and show.
    """

    if not isinstance(profile, ProviderProfile):
        raise ConfigurationError("profile must be a validated ProviderProfile")
    canonical = json.dumps(
        {
            "name": profile.name,
            "base_url": profile.base_url,
            "model_id": profile.model_id,
            "api_key_env": profile.api_key_env,
            "privacy_terms_url": profile.privacy_terms_url,
            "input_price_per_million": str(profile.input_price_per_million),
            "output_price_per_million": str(profile.output_price_per_million),
            "request_limit": profile.request_limit,
            "budget_limit_cny": str(profile.budget_limit_cny),
            "synthetic_data_only": profile.synthetic_data_only,
            "enabled": profile.enabled,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OnlineRunPreflight:
    """Display-safe summary of what one whole run would be allowed to spend.

    It carries no key, no environment value, and no prompt; every field here can
    be rendered in a confirmation view or written to an evidence file.
    """

    run_id: str
    profile_name: str
    provider_fingerprint: str
    base_url: str
    model_id: str
    api_key_env: str
    privacy_terms_url: str
    max_requests: int
    request_limit: int
    input_token_cap: int
    output_token_cap: int
    synthetic_data_only: bool
    fallback_local: bool
    max_cost_cny: Decimal
    already_spent_cny: Decimal
    projected_total_cny: Decimal
    budget_limit_cny: Decimal

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("profile_name", self.profile_name),
            ("provider_fingerprint", self.provider_fingerprint),
            ("base_url", self.base_url),
            ("model_id", self.model_id),
            ("api_key_env", self.api_key_env),
            ("privacy_terms_url", self.privacy_terms_url),
        ):
            _require_nonempty_string(name, value)
        for name, value in (
            ("max_requests", self.max_requests),
            ("request_limit", self.request_limit),
            ("input_token_cap", self.input_token_cap),
            ("output_token_cap", self.output_token_cap),
        ):
            _require_positive_actual_int(name, value)
        for name, value in (
            ("synthetic_data_only", self.synthetic_data_only),
            ("fallback_local", self.fallback_local),
        ):
            if type(value) is not bool:
                raise ConfigurationError(f"{name} must be a boolean")
        for name, value in (
            ("max_cost_cny", self.max_cost_cny),
            ("already_spent_cny", self.already_spent_cny),
            ("projected_total_cny", self.projected_total_cny),
            ("budget_limit_cny", self.budget_limit_cny),
        ):
            _require_decimal(name, value)


@dataclass(frozen=True, slots=True)
class OnlineCallPermit:
    profile_name: str
    base_url: str
    model_id: str
    api_key_env: str
    api_key: str = field(repr=False)
    input_token_cap: int
    output_token_cap: int
    request_number: int
    request_limit: int
    estimated_cost_cny: Decimal
    already_spent_cny: Decimal
    projected_total_cny: Decimal
    budget_limit_cny: Decimal


def _contains_credential_material(key: str, value: object) -> bool:
    if key != "api_key_env" and _CREDENTIAL_TERM.search(key):
        return True
    if isinstance(value, str):
        return key != "api_key_env" and _CREDENTIAL_TERM.search(value) is not None
    if isinstance(value, Mapping):
        return any(
            _contains_credential_material(str(child_key), child_value)
            for child_key, child_value in value.items()
        )
    if isinstance(value, list):
        return any(_contains_credential_material(key, item) for item in value)
    return False


def _profile_from_mapping(name: str, values: object) -> ProviderProfile:
    if not isinstance(values, dict):
        raise ConfigurationError("Each provider profile must be a TOML table")
    if _contains_credential_material(name, values):
        raise ConfigurationError(
            "Provider profile contains forbidden credential-bearing material"
        )
    fields = set(values)
    missing = _PROFILE_FIELDS - fields
    unknown = fields - _PROFILE_FIELDS
    if missing or unknown:
        raise ConfigurationError(
            "Provider profile fields must exactly match the documented schema"
        )

    converted: dict[str, Any] = dict(values)
    for field_name in _DECIMAL_FIELDS:
        raw_value = converted[field_name]
        if type(raw_value) is not str:
            raise ConfigurationError(f"{field_name} must be a decimal string")
        try:
            converted[field_name] = Decimal(raw_value)
        except InvalidOperation:
            raise ConfigurationError(
                f"{field_name} must be a valid decimal string"
            ) from None

    for field_name in (
        "base_url",
        "model_id",
        "api_key_env",
        "privacy_terms_url",
    ):
        if type(converted[field_name]) is not str:
            raise ConfigurationError(f"{field_name} must be a string")
    if type(converted["request_limit"]) is not int:
        raise ConfigurationError("request_limit must be an integer")
    for field_name in ("enabled", "synthetic_data_only"):
        if type(converted[field_name]) is not bool:
            raise ConfigurationError(f"{field_name} must be a boolean")

    return ProviderProfile(name=name, **converted)


def load_profiles(path: str | Path) -> tuple[ProviderProfile, ...]:
    profile_path = Path(path)
    try:
        with profile_path.open("rb") as profile_file:
            document = tomllib.load(profile_file)
    except tomllib.TOMLDecodeError:
        raise ConfigurationError(
            "Provider profile TOML is invalid or contains duplicate definitions"
        ) from None
    except OSError:
        raise ConfigurationError("Provider profile file could not be read") from None

    if set(document) != {"profiles"} or not isinstance(document["profiles"], dict):
        raise ConfigurationError("Provider profile TOML must contain [profiles.<name>]")
    profiles = document["profiles"]
    return tuple(
        _profile_from_mapping(name, profiles[name]) for name in sorted(profiles)
    )


def _require_positive_actual_int(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ConfigurationError(f"{name} must be an actual positive integer")
    return value


def _required_cost_precision(
    profile: ProviderProfile,
    input_token_cap: int,
    output_token_cap: int,
    already_spent_cny: Decimal,
) -> int:
    bounds: list[tuple[int, int]] = []
    for price, token_cap in (
        (profile.input_price_per_million, input_token_cap),
        (profile.output_price_per_million, output_token_cap),
    ):
        if price.is_zero():
            continue
        price_tuple = price.as_tuple()
        if type(price_tuple.exponent) is not int:
            raise ConfigurationError("Online call cost inputs must be finite")
        exponent = price_tuple.exponent - 6
        digit_bound = len(price_tuple.digits) + len(str(token_cap))
        bounds.append((exponent, exponent + digit_bound - 1))

    if not already_spent_cny.is_zero():
        spent_tuple = already_spent_cny.as_tuple()
        if type(spent_tuple.exponent) is not int:
            raise ConfigurationError("Online call cost inputs must be finite")
        bounds.append(
            (
                spent_tuple.exponent,
                spent_tuple.exponent + len(spent_tuple.digits) - 1,
            )
        )

    if not bounds:
        return 28
    precision = max(adjusted for _exponent, adjusted in bounds) - min(
        exponent for exponent, _adjusted in bounds
    ) + 2
    if precision > _MAX_COST_PRECISION:
        raise ConfigurationError(
            "Online call cost requires an unsupported numeric precision"
        )
    return max(28, precision)


def preflight_online_call(
    profile: ProviderProfile,
    *,
    allow_network: bool,
    input_token_cap: int,
    output_token_cap: int,
    request_number: int,
    already_spent_cny: Decimal = Decimal(0),
    environ: Mapping[str, str] | None = None,
) -> OnlineCallPermit:
    if not isinstance(profile, ProviderProfile):
        raise ConfigurationError("profile must be a validated ProviderProfile")
    if type(allow_network) is not bool:
        raise ConfigurationError("allow_network must be a boolean")
    if not allow_network:
        raise ProfileNotAllowedError(
            "Online profile blocked because network is disabled"
        )
    if not profile.synthetic_data_only:
        raise PrivacyBlockedError("Online profile is restricted to synthetic data")
    if not profile.enabled:
        raise ProfileNotAllowedError(f"Provider profile {profile.name!r} is disabled")

    request_number = _require_positive_actual_int("request_number", request_number)
    if request_number > profile.request_limit:
        raise ProfileNotAllowedError(
            f"Provider profile {profile.name!r} request limit would be exceeded"
        )
    input_token_cap = _require_positive_actual_int(
        "input_token_cap", input_token_cap
    )
    output_token_cap = _require_positive_actual_int(
        "output_token_cap", output_token_cap
    )
    if (
        not isinstance(already_spent_cny, Decimal)
        or not already_spent_cny.is_finite()
        or already_spent_cny < 0
    ):
        raise ConfigurationError(
            "already_spent_cny must be a finite nonnegative Decimal"
        )

    environment = os.environ if environ is None else environ
    api_key = environment.get(profile.api_key_env)
    if (
        not isinstance(api_key, str)
        or not api_key.strip()
        or api_key != api_key.strip()
    ):
        raise ConfigurationError(
            "Required API key environment variable "
            f"{profile.api_key_env} is missing, blank, or malformed"
        )

    try:
        required_precision = _required_cost_precision(
            profile,
            input_token_cap,
            output_token_cap,
            already_spent_cny,
        )
        with localcontext() as context:
            context.prec = required_precision
            context.Emax = MAX_EMAX
            context.Emin = MIN_EMIN
            context.traps[Inexact] = True
            estimated_cost_cny = (
                Decimal(input_token_cap)
                * profile.input_price_per_million
                / _ONE_MILLION
                + Decimal(output_token_cap)
                * profile.output_price_per_million
                / _ONE_MILLION
            )
            projected_total_cny = already_spent_cny + estimated_cost_cny
    except DecimalException:
        raise ConfigurationError(
            "Online call cost could not be represented safely"
        ) from None
    if not estimated_cost_cny.is_finite() or not projected_total_cny.is_finite():
        raise ConfigurationError("Online call cost must remain finite")
    if projected_total_cny > profile.budget_limit_cny:
        raise BudgetBlockedError(
            f"Provider profile {profile.name!r} budget limit would be exceeded"
        )

    return OnlineCallPermit(
        profile_name=profile.name,
        base_url=profile.base_url,
        model_id=profile.model_id,
        api_key_env=profile.api_key_env,
        api_key=api_key,
        input_token_cap=input_token_cap,
        output_token_cap=output_token_cap,
        request_number=request_number,
        request_limit=profile.request_limit,
        estimated_cost_cny=estimated_cost_cny,
        already_spent_cny=already_spent_cny,
        projected_total_cny=projected_total_cny,
        budget_limit_cny=profile.budget_limit_cny,
    )


def preflight_online_run(
    profile: ProviderProfile,
    *,
    run_id: str,
    allow_network: bool,
    max_requests: int,
    input_token_cap: int,
    output_token_cap: int,
    fallback_local: bool,
    request_number: int = 0,
    already_spent_cny: Decimal = Decimal(0),
    environ: Mapping[str, str] | None = None,
) -> OnlineRunPreflight:
    """Bound a whole run before any client exists.

    Every planned request is put through the same single-call preflight, so the
    run-level request limit, key check, and conservative cost projection can never
    drift from what the persistent generator enforces later. No client is created
    here, and the returned summary carries no key or environment value.
    """

    if not isinstance(profile, ProviderProfile):
        raise ConfigurationError("profile must be a validated ProviderProfile")
    _require_nonempty_string("run_id", run_id)
    max_requests = _require_positive_actual_int("max_requests", max_requests)
    if type(fallback_local) is not bool:
        raise ConfigurationError("fallback_local must be a boolean")

    projected_total_cny = already_spent_cny
    for offset in range(1, max_requests + 1):
        permit = preflight_online_call(
            profile,
            allow_network=allow_network,
            input_token_cap=input_token_cap,
            output_token_cap=output_token_cap,
            request_number=request_number + offset,
            already_spent_cny=projected_total_cny,
            environ=environ,
        )
        projected_total_cny = permit.projected_total_cny

    return OnlineRunPreflight(
        run_id=run_id,
        profile_name=profile.name,
        provider_fingerprint=profile_fingerprint(profile),
        base_url=profile.base_url,
        model_id=profile.model_id,
        api_key_env=profile.api_key_env,
        privacy_terms_url=profile.privacy_terms_url,
        max_requests=max_requests,
        request_limit=profile.request_limit,
        input_token_cap=input_token_cap,
        output_token_cap=output_token_cap,
        synthetic_data_only=profile.synthetic_data_only,
        fallback_local=fallback_local,
        max_cost_cny=projected_total_cny - already_spent_cny,
        already_spent_cny=already_spent_cny,
        projected_total_cny=projected_total_cny,
        budget_limit_cny=profile.budget_limit_cny,
    )
