from __future__ import annotations

import ctypes
import hashlib
import importlib
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .application import CommandOutcome, ProfileLoader
from .config import (
    EMBEDDING_MODEL_REVISION,
    RERANKER_IMPLEMENTATION_REVISION,
    RERANKER_MODEL_REVISION,
    AppConfig,
)
from .index_store import load_index
from .provider_profiles import ProviderProfile, load_profiles

DiagnosticStatus = Literal["pass", "warn", "fail"]
SystemProbe = Callable[[], "SystemSnapshot"]
ModelProbeCallable = Callable[[AppConfig], "ModelProbe"]
IndexProbeCallable = Callable[[Path], "IndexProbe"]

_GIB = 1024**3
_MIN_DISK_BYTES = 6 * _GIB
_WARN_DISK_BYTES = 15 * _GIB
_MIN_TOTAL_RAM_BYTES = 12 * _GIB
_WARN_AVAILABLE_RAM_BYTES = 4 * _GIB
_MODEL_ARTIFACTS = (
    (
        Path(
            "models--Alibaba-NLP--gte-multilingual-base"
        )
        / "snapshots"
        / EMBEDDING_MODEL_REVISION
        / "model.safetensors",
        610_753_338,
        "f5a35a10faa54da7717870af1517c9b41e9bd8e3880bc5a8e9363d4c3c63e9b0",
    ),
    (
        Path(
            "models--Alibaba-NLP--gte-multilingual-reranker-base"
        )
        / "snapshots"
        / RERANKER_MODEL_REVISION
        / "model.safetensors",
        611_934_706,
        "10ebaa49322dd7e01a13a91c49810939e3f91f231aceaa47fdf0cab3083954f6",
    ),
    (
        Path("models--Alibaba-NLP--new-impl")
        / "snapshots"
        / RERANKER_IMPLEMENTATION_REVISION
        / "configuration.py",
        7_127,
        "3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4",
    ),
    (
        Path("models--Alibaba-NLP--new-impl")
        / "snapshots"
        / RERANKER_IMPLEMENTATION_REVISION
        / "modeling.py",
        59_023,
        "374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0",
    ),
)
_QWEN_BYTES = 2_889_513_184
_QWEN_SHA256 = "aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f"


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    python_version: tuple[int, int, int]
    disk_free_bytes: int
    total_ram_bytes: int
    available_ram_bytes: int
    cuda_available: bool
    cuda_name: str | None
    llama_gpu_offload_available: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.python_version, tuple)
            or len(self.python_version) != 3
            or any(type(item) is not int or item < 0 for item in self.python_version)
        ):
            raise ValueError("python_version must contain three non-negative integers")
        for name in (
            "disk_free_bytes",
            "total_ram_bytes",
            "available_ram_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.cuda_available) is not bool:
            raise TypeError("cuda_available must be a boolean")
        if type(self.llama_gpu_offload_available) is not bool:
            raise TypeError("llama_gpu_offload_available must be a boolean")
        if self.cuda_name is not None and (
            not isinstance(self.cuda_name, str) or not self.cuda_name.strip()
        ):
            raise ValueError("cuda_name must be a non-empty string when provided")


@dataclass(frozen=True, slots=True)
class ModelProbe:
    present: bool
    digest_valid: bool
    verified_artifacts: int
    total_bytes: int

    def __post_init__(self) -> None:
        if type(self.present) is not bool or type(self.digest_valid) is not bool:
            raise TypeError("model probe flags must be booleans")
        if type(self.verified_artifacts) is not int or self.verified_artifacts < 0:
            raise ValueError("verified_artifacts must be non-negative")
        if type(self.total_bytes) is not int or self.total_bytes < 0:
            raise ValueError("total_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class IndexProbe:
    present: bool
    digest_valid: bool
    source_digest: str | None
    row_count: int

    def __post_init__(self) -> None:
        if type(self.present) is not bool or type(self.digest_valid) is not bool:
            raise TypeError("index probe flags must be booleans")
        if self.source_digest is not None and (
            not isinstance(self.source_digest, str)
            or len(self.source_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.source_digest
            )
        ):
            raise ValueError("source_digest must be a lowercase SHA-256 digest")
        if type(self.row_count) is not int or self.row_count < 0:
            raise ValueError("row_count must be non-negative")


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    status: DiagnosticStatus
    message: str
    remediation: str
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.status not in {"pass", "warn", "fail"}:
            raise ValueError("invalid diagnostic status")
        if not self.message.strip() or not self.remediation.strip():
            raise ValueError("diagnostic text must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "details": dict(self.details),
            "message": self.message,
            "remediation": self.remediation,
            "status": self.status,
        }


class Diagnostics:
    def __init__(
        self,
        config: AppConfig,
        *,
        provider_profiles_path: Path,
        environ: Mapping[str, str] | None = None,
        system_probe: SystemProbe | None = None,
        model_probe: ModelProbeCallable | None = None,
        index_probe: IndexProbeCallable | None = None,
        profile_loader: ProfileLoader = load_profiles,
    ) -> None:
        if not isinstance(config, AppConfig):
            raise TypeError("config must be an AppConfig")
        if not isinstance(provider_profiles_path, Path):
            raise TypeError("provider_profiles_path must be a Path")
        if environ is not None and not isinstance(environ, Mapping):
            raise TypeError("environ must be a mapping")
        self._config = config
        self._provider_profiles_path = provider_profiles_path
        self._environ = os.environ if environ is None else environ
        self._system_probe = system_probe or (
            lambda: collect_system_snapshot(config.model_cache_dir)
        )
        self._model_probe = model_probe or probe_local_models
        self._index_probe = index_probe or probe_index
        self._profile_loader = profile_loader

    async def run(self) -> CommandOutcome:
        checks: dict[str, DiagnosticCheck] = {}
        try:
            snapshot = self._system_probe()
        except Exception:
            snapshot = None
        if snapshot is None:
            unavailable = DiagnosticCheck(
                "fail",
                "System resources could not be inspected.",
                "Run doctor from a supported local Python environment.",
                {},
            )
            for name in ("python", "disk", "memory", "cuda"):
                checks[name] = unavailable
        else:
            checks.update(_system_checks(snapshot))

        try:
            models = self._model_probe(self._config)
        except Exception:
            models = ModelProbe(False, False, 0, 0)
        checks["local_models"] = _model_check(models)

        try:
            index = self._index_probe(self._config.index_dir)
        except Exception:
            index = IndexProbe(False, False, None, 0)
        checks["index"] = _index_check(index)
        checks["provider_profiles"] = self._provider_check()
        checks["network"] = _network_check(self._config.allow_network)

        statuses = {check.status for check in checks.values()}
        overall: DiagnosticStatus = (
            "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
        )
        data = {
            "checks": {name: check.to_dict() for name, check in checks.items()},
            "overall_status": overall,
        }
        if overall == "fail":
            return CommandOutcome.failed("diagnostics", data)
        return CommandOutcome.succeeded(data)

    def _provider_check(self) -> DiagnosticCheck:
        if not self._provider_profiles_path.is_file():
            return DiagnosticCheck(
                "warn",
                "No online provider profile file is configured.",
                "Copy providers.example.toml only if online generation is needed.",
                {"profiles": ()},
            )
        try:
            profiles = self._profile_loader(self._provider_profiles_path)
        except Exception:
            return DiagnosticCheck(
                "fail",
                "Provider profiles are invalid or unreadable.",
                "Validate the provider TOML against the documented schema.",
                {"profiles": ()},
            )
        public_profiles = tuple(
            _public_profile(profile, self._environ) for profile in profiles
        )
        missing_enabled_key = any(
            profile.enabled and profile.api_key_env not in self._environ
            for profile in profiles
        )
        status: DiagnosticStatus = "warn" if missing_enabled_key else "pass"
        return DiagnosticCheck(
            status,
            (
                "An enabled provider is missing its credential environment variable."
                if missing_enabled_key
                else "Provider profiles are valid; credential values were not read out."
            ),
            (
                "Set the named environment variable outside chat and Git."
                if missing_enabled_key
                else "Keep credentials in environment variables or a secret manager."
            ),
            {"profiles": public_profiles},
        )


def collect_system_snapshot(disk_target: Path) -> SystemSnapshot:
    disk_root = _nearest_existing_path(disk_target)
    disk_free = shutil.disk_usage(disk_root).free
    total_ram, available_ram = _memory_bytes()
    cuda_available, cuda_name, llama_gpu_offload = _cuda_status()
    return SystemSnapshot(
        python_version=(
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        ),
        disk_free_bytes=disk_free,
        total_ram_bytes=total_ram,
        available_ram_bytes=available_ram,
        cuda_available=cuda_available,
        cuda_name=cuda_name,
        llama_gpu_offload_available=llama_gpu_offload,
    )


def probe_local_models(config: AppConfig) -> ModelProbe:
    paths = (
        *(
            (config.model_cache_dir / relative_path, expected_bytes, digest)
            for relative_path, expected_bytes, digest in _MODEL_ARTIFACTS
        ),
        (config.local_model_path, _QWEN_BYTES, _QWEN_SHA256),
    )
    if any(not path.is_file() for path, _size, _digest in paths):
        return ModelProbe(False, False, 0, 0)
    verified = 0
    total_bytes = 0
    for path, expected_bytes, expected_digest in paths:
        try:
            actual_bytes = path.stat().st_size
            if actual_bytes != expected_bytes or _sha256_file(path) != expected_digest:
                return ModelProbe(True, False, verified, total_bytes + actual_bytes)
        except OSError:
            return ModelProbe(False, False, verified, total_bytes)
        verified += 1
        total_bytes += actual_bytes
    return ModelProbe(True, True, verified, total_bytes)


def probe_index(path: Path) -> IndexProbe:
    if not path.is_dir():
        return IndexProbe(False, False, None, 0)
    try:
        index = load_index(path)
    except Exception:
        return IndexProbe(True, False, None, 0)
    return IndexProbe(
        True,
        True,
        index.manifest.source_digest,
        index.manifest.row_count,
    )


def _system_checks(snapshot: SystemSnapshot) -> dict[str, DiagnosticCheck]:
    python_ok = snapshot.python_version[:2] == (3, 12)
    python = DiagnosticCheck(
        "pass" if python_ok else "fail",
        (
            "Python 3.12 is active."
            if python_ok
            else "The active Python version is unsupported."
        ),
        "Use Python >=3.12,<3.13 for this repository.",
        {"version": ".".join(str(item) for item in snapshot.python_version)},
    )
    if snapshot.disk_free_bytes < _MIN_DISK_BYTES:
        disk_status: DiagnosticStatus = "fail"
    elif snapshot.disk_free_bytes < _WARN_DISK_BYTES:
        disk_status = "warn"
    else:
        disk_status = "pass"
    disk = DiagnosticCheck(
        disk_status,
        "Model-cache disk capacity was inspected.",
        "Keep at least 15 GiB free; 6 GiB is the hard minimum.",
        {"free_bytes": snapshot.disk_free_bytes},
    )
    if snapshot.total_ram_bytes < _MIN_TOTAL_RAM_BYTES:
        memory_status: DiagnosticStatus = "fail"
    elif snapshot.available_ram_bytes < _WARN_AVAILABLE_RAM_BYTES:
        memory_status = "warn"
    else:
        memory_status = "pass"
    memory = DiagnosticCheck(
        memory_status,
        "Physical memory capacity and current availability were inspected.",
        (
            "Close browsers and virtual-display or streaming software before the demo."
        ),
        {
            "available_bytes": snapshot.available_ram_bytes,
            "total_bytes": snapshot.total_ram_bytes,
        },
    )
    all_gpu_backends = (
        snapshot.cuda_available and snapshot.llama_gpu_offload_available
    )
    if all_gpu_backends:
        cuda_message = "CUDA is visible to PyTorch and llama.cpp."
    elif snapshot.llama_gpu_offload_available:
        cuda_message = (
            "llama.cpp GPU offload is available, but PyTorch CUDA is unavailable."
        )
    elif snapshot.cuda_available:
        cuda_message = (
            "PyTorch CUDA is available, but llama.cpp GPU offload is unavailable."
        )
    else:
        cuda_message = "CUDA is unavailable to both local inference runtimes."
    cuda = DiagnosticCheck(
        "pass" if all_gpu_backends else "warn",
        cuda_message,
        "Install the approved CUDA-compatible wheels and verify the NVIDIA driver.",
        {
            "device_name": snapshot.cuda_name,
            "llama_gpu_offload_available": (
                snapshot.llama_gpu_offload_available
            ),
            "torch_cuda_available": snapshot.cuda_available,
        },
    )
    return {"python": python, "disk": disk, "memory": memory, "cuda": cuda}


def _model_check(probe: ModelProbe) -> DiagnosticCheck:
    valid = probe.present and probe.digest_valid
    return DiagnosticCheck(
        "pass" if valid else "fail",
        (
            "Pinned local model artifacts are present and digest-valid."
            if valid
            else "Required local model artifacts are missing or invalid."
        ),
        "Restore only the approved revisions from docs/model-manifest.md.",
        {
            "digest_valid": probe.digest_valid,
            "present": probe.present,
            "total_bytes": probe.total_bytes,
            "verified_artifacts": probe.verified_artifacts,
        },
    )


def _index_check(probe: IndexProbe) -> DiagnosticCheck:
    valid = probe.present and probe.digest_valid
    return DiagnosticCheck(
        "pass" if valid else "fail",
        (
            "The local index is present and its artifact digests are valid."
            if valid
            else "The local index is missing or failed digest validation."
        ),
        "Rebuild the index from the fictional demo corpus with `rag-agent kb build`.",
        {
            "digest_valid": probe.digest_valid,
            "present": probe.present,
            "row_count": probe.row_count,
            "source_digest": probe.source_digest,
        },
    )


def _network_check(allow_network: bool) -> DiagnosticCheck:
    return DiagnosticCheck(
        "warn" if allow_network else "pass",
        (
            "Online calls are explicitly enabled."
            if allow_network
            else "Network access is disabled by default."
        ),
        (
            "Require the documented preflight and explicit approval before any call."
            if allow_network
            else "Keep network disabled for the offline interview demo."
        ),
        {"enabled": allow_network},
    )


def _public_profile(
    profile: ProviderProfile,
    environ: Mapping[str, str],
) -> dict[str, object]:
    return {
        "credential_configured": profile.api_key_env in environ,
        "enabled": profile.enabled,
        "model_id": profile.model_id,
        "name": profile.name,
        "synthetic_data_only": profile.synthetic_data_only,
    }


def _nearest_existing_path(path: Path) -> Path:
    candidate = path.resolve(strict=False)
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def _memory_bytes() -> tuple[int, int]:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("memory status is unavailable")
        return int(status.total_physical), int(status.available_physical)
    page_size = os.sysconf("SC_PAGE_SIZE")
    total_pages = os.sysconf("SC_PHYS_PAGES")
    available_pages = os.sysconf("SC_AVPHYS_PAGES")
    return page_size * total_pages, page_size * available_pages


def _cuda_status() -> tuple[bool, str | None, bool]:
    torch_available = False
    name: str | None = None
    try:
        torch = importlib.import_module("torch")
        cuda = torch.cuda
        torch_available = bool(cuda.is_available())
        name = str(cuda.get_device_name(0)) if torch_available else None
    except Exception:
        pass
    llama_gpu_offload = False
    try:
        llama_cpp = importlib.import_module("llama_cpp")
        supports_offload = llama_cpp.llama_supports_gpu_offload
        if callable(supports_offload):
            llama_gpu_offload = bool(supports_offload())
    except Exception:
        pass
    return torch_available, name, llama_gpu_offload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
