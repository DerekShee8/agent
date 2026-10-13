from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from enterprise_rag.application import build_application
from enterprise_rag.cli_rendering import render_command_outcome
from enterprise_rag.config import AppConfig
from enterprise_rag.diagnostics import (
    Diagnostics,
    IndexProbe,
    ModelProbe,
    SystemSnapshot,
)
from enterprise_rag.provider_profiles import ProviderProfile

GIB = 1024**3


def snapshot(
    *,
    available_ram: int = 8 * GIB,
    torch_cuda: bool = True,
    llama_gpu_offload: bool = True,
) -> SystemSnapshot:
    return SystemSnapshot(
        python_version=(3, 12, 14),
        disk_free_bytes=40 * GIB,
        total_ram_bytes=16 * GIB,
        available_ram_bytes=available_ram,
        cuda_available=torch_cuda,
        cuda_name=(
            "NVIDIA GeForce RTX 4060 Laptop GPU" if torch_cuda else None
        ),
        llama_gpu_offload_available=llama_gpu_offload,
    )


def profile() -> ProviderProfile:
    return ProviderProfile(
        name="demo-relay",
        base_url="https://relay.example.com/v1",
        model_id="deepseek-v4.1-flash",
        api_key_env="DEMO_RELAY_KEY",
        privacy_terms_url="https://relay.example.com/privacy",
        input_price_per_million=Decimal("1.0"),
        output_price_per_million=Decimal("2.0"),
        request_limit=5,
        budget_limit_cny=Decimal("1.0"),
        synthetic_data_only=True,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_doctor_reports_all_required_checks_without_secret_values(
    tmp_path: Path,
) -> None:
    secret = "credential-must-never-appear"
    config = AppConfig(
        model_cache_dir=tmp_path / "models",
        index_dir=tmp_path / "index",
        local_model_path=tmp_path / "models" / "model.gguf",
        allow_network=False,
    )
    provider_path = tmp_path / "providers.toml"
    provider_path.write_text("[profiles]\n", encoding="utf-8")
    diagnostics = Diagnostics(
        config,
        provider_profiles_path=provider_path,
        environ={"DEMO_RELAY_KEY": secret},
        system_probe=snapshot,
        model_probe=lambda _config: ModelProbe(
            present=True,
            digest_valid=True,
            verified_artifacts=5,
            total_bytes=4_113_242_274,
        ),
        index_probe=lambda _path: IndexProbe(
            present=True,
            digest_valid=True,
            source_digest="a" * 64,
            row_count=6,
        ),
        profile_loader=lambda _path: (profile(),),
    )

    outcome = await diagnostics.run()
    serialized = render_command_outcome(outcome)

    assert outcome.status == "succeeded"
    assert outcome.data["overall_status"] == "pass"
    checks = outcome.data["checks"]
    assert set(checks) == {
        "python",
        "disk",
        "memory",
        "cuda",
        "local_models",
        "index",
        "provider_profiles",
        "network",
    }
    assert checks["index"]["details"]["source_digest"] == "a" * 64
    provider_details = checks["provider_profiles"]["details"]["profiles"]
    assert provider_details[0]["credential_configured"] is True
    assert secret not in serialized


@pytest.mark.asyncio
async def test_doctor_warns_for_memory_pressure_without_failing(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        model_cache_dir=tmp_path / "models",
        index_dir=tmp_path / "index",
        local_model_path=tmp_path / "models" / "model.gguf",
    )
    diagnostics = Diagnostics(
        config,
        provider_profiles_path=tmp_path / "missing.toml",
        environ={},
        system_probe=lambda: snapshot(available_ram=2 * GIB),
        model_probe=lambda _config: ModelProbe(True, True, 5, 4 * GIB),
        index_probe=lambda _path: IndexProbe(True, True, "b" * 64, 6),
        profile_loader=lambda _path: (),
    )

    outcome = await diagnostics.run()

    assert outcome.status == "succeeded"
    assert outcome.data["overall_status"] == "warn"
    memory = outcome.data["checks"]["memory"]
    assert memory["status"] == "warn"
    assert "close" in memory["remediation"].lower()


@pytest.mark.asyncio
async def test_doctor_distinguishes_torch_cuda_from_llama_gpu_offload(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        model_cache_dir=tmp_path / "models",
        index_dir=tmp_path / "index",
        local_model_path=tmp_path / "models" / "model.gguf",
    )
    diagnostics = Diagnostics(
        config,
        provider_profiles_path=tmp_path / "missing.toml",
        environ={},
        system_probe=lambda: snapshot(torch_cuda=False, llama_gpu_offload=True),
        model_probe=lambda _config: ModelProbe(True, True, 5, 4 * GIB),
        index_probe=lambda _path: IndexProbe(True, True, "b" * 64, 6),
        profile_loader=lambda _path: (),
    )

    outcome = await diagnostics.run()

    cuda = outcome.data["checks"]["cuda"]
    assert cuda["status"] == "warn"
    assert cuda["details"]["torch_cuda_available"] is False
    assert cuda["details"]["llama_gpu_offload_available"] is True


@pytest.mark.asyncio
async def test_doctor_fails_when_required_models_or_index_are_invalid(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        model_cache_dir=tmp_path / "models",
        index_dir=tmp_path / "index",
        local_model_path=tmp_path / "models" / "model.gguf",
    )
    diagnostics = Diagnostics(
        config,
        provider_profiles_path=tmp_path / "missing.toml",
        environ={},
        system_probe=snapshot,
        model_probe=lambda _config: ModelProbe(False, False, 0, 0),
        index_probe=lambda _path: IndexProbe(False, False, None, 0),
        profile_loader=lambda _path: (),
    )

    outcome = await diagnostics.run()

    assert outcome.status == "failed"
    assert outcome.error_category == "diagnostics"
    assert outcome.data["overall_status"] == "fail"
    assert outcome.data["checks"]["local_models"]["status"] == "fail"
    assert outcome.data["checks"]["index"]["status"] == "fail"


def test_build_application_can_compose_doctor_without_loading_models(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        model_cache_dir=tmp_path / "models",
        index_dir=tmp_path / "index",
        local_model_path=tmp_path / "models" / "model.gguf",
    )

    application = build_application(
        config,
        compose_rag=False,
        compose_diagnostics=True,
        provider_profiles_path=tmp_path / "providers.toml",
    )

    assert isinstance(application.diagnostics, Diagnostics)
