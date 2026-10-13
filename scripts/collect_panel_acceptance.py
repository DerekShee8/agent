"""Write structured real-local acceptance evidence for the local engineering panel.

Two modes:

``collect``  drives the real ``PanelController -> RunCoordinator -> PanelRuntime ->
             Application`` path with socket connections hard-failed, runs each of
             the three scenes three times, and writes the machine-local JSON
             atomically under ``.local/``.

``promote``  validates a machine-local artifact against the explicit schema and
             rejects anything that could carry a secret, an absolute path, an
             environment value, a prompt, a response, or a traceback, then writes
             the sanitized artifact that may be committed.

The pure helpers (statistics, schema validation, atomic write, promotion) are
importable without loading a model, opening a socket, or importing the runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EXECUTION_SURFACE = "panel-coordinator"
UI_AVAILABILITY_NOTE = (
    "ui_available_seconds measures route/shell registration, not browser paint"
)
RUNS_PER_SCENE = 3
MAX_RUNTIME_READY_SECONDS = 60.0
MAX_SCENARIO_P95_SECONDS = 45.0
SCENES = ("rag", "agent", "refusal")
_SCENARIO_IDS = {
    "rag": "rag-shanghai-p4",
    "agent": "agent-claim-1024",
    "refusal": "rag-pet-boarding-refusal",
}

_CREDENTIAL_TERM = re.compile(
    r"api[_-]?key|token|password|authorization|secret|credential",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = frozenset(
    {"prompt", "prompts", "system_prompt", "response", "responses", "traceback"}
)
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|root)/)"
)
_ENVIRONMENT_VALUE = re.compile(r"^[A-Z][A-Z0-9_]{3,}=.+")
# An inline credential inside an otherwise innocuous value, e.g.
# "Authorization: Bearer sk-..." or "api_key=...".
_INLINE_CREDENTIAL = re.compile(
    r"(?i)\b(?:authorization|api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*\S+"
)
# A credential-like blob, not a SCREAMING_SNAKE identifier such as a stage name.
_LONG_TOKEN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z0-9])[A-Za-z0-9_\-]{32,}$")
# Content hashes are long hex strings that are required evidence, not secrets.
_DIGEST_KEYS = frozenset(
    {
        "model_digest",
        "digest",
        "sha256",
        "git_commit",
        "source_digest",
        "model_revision",
        "revision",
    }
)


def nearest_rank(values: list[float], percentile: float) -> float:
    """Observed value at ``ceil(p * n) - 1``; p95 of three samples is the max."""

    if not values:
        raise ValueError("nearest_rank needs at least one observation")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def latency_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        raise ValueError("latency_summary needs at least one observation")
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("latency samples must be finite numbers")
    return {
        "samples_seconds": list(values),
        "p50_seconds": nearest_rank(values, 0.50),
        "p95_seconds": nearest_rank(values, 0.95),
        "max_seconds": max(values),
    }


def _reject(reason: str) -> None:
    raise ValueError(f"refusing to promote evidence: {reason}")


def scan_forbidden_content(
    value: object,
    path: str = "$",
    key: str | None = None,
) -> None:
    """Reject anything that could carry a secret, path, env value, or traceback."""

    _scan(value, path, key)


def _scan(value: object, path: str = "$", key: str | None = None) -> None:
    if isinstance(value, dict):
        for nested_key, nested in value.items():
            if not isinstance(nested_key, str):
                _reject(f"non-string key at {path}")
            folded = nested_key.casefold()
            if folded in _FORBIDDEN_KEYS:
                _reject(f"forbidden key {nested_key!r} at {path}")
            # A boolean cannot carry credential material, so a descriptive flag such
            # as ``replayed_authorization_is_refused`` is not a credential key.
            if (
                _CREDENTIAL_TERM.search(nested_key)
                and nested_key != "api_key_env"
                and not isinstance(nested, bool)
            ):
                _reject(f"credential-like key {nested_key!r} at {path}")
            _scan(nested, f"{path}.{nested_key}", nested_key)
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan(nested, f"{path}[{index}]", key)
        return
    if isinstance(value, str):
        if _ABSOLUTE_PATH.search(value):
            _reject(f"absolute path at {path}")
        if _ENVIRONMENT_VALUE.match(value):
            _reject(f"environment value at {path}")
        if _INLINE_CREDENTIAL.search(value):
            _reject(f"inline credential at {path}")
        if _LONG_TOKEN.match(value) and key not in _DIGEST_KEYS and "\\" not in value:
            _reject(f"token-like value at {path}")
        if "Traceback (most recent call last)" in value:
            _reject(f"traceback text at {path}")


_ARTIFACT_SCHEMA: dict[str, object] = {
    "schema_version": int,
    "generated_at_utc": str,
    "git_commit": str,
    "execution_surface": str,
    "ui_availability_note": str,
    "hardware": dict,
    "backend": dict,
    "initialization": dict,
    "scenarios": dict,
    "percentiles": dict,
    "assertions": dict,
    "network_attempts": int,
    "load_counters": dict,
    "peak_ram_bytes": (int, type(None)),
    "peak_vram_mib": (int, type(None)),
    "online": str,
}


def validate_against(
    schema: dict[str, object],
    payload: object,
) -> dict[str, Any]:
    """Reject missing, extra, or mistyped fields; return the payload unchanged.

    This is the one schema check, shared by every artifact shape.
    """

    if not isinstance(payload, dict):
        raise ValueError("artifact must be a JSON object")
    missing = set(schema) - set(payload)
    extra = set(payload) - set(schema)
    if missing or extra:
        raise ValueError(
            f"artifact schema mismatch: missing={sorted(missing)} extra={sorted(extra)}"
        )
    for key, expected in schema.items():
        if not isinstance(payload[key], expected):  # type: ignore[arg-type]
            raise ValueError(f"artifact field {key!r} has the wrong type")
    return payload


def validate_artifact(payload: object) -> dict[str, Any]:
    """Validate a real-local artifact against its explicit schema."""

    validate_against(_ARTIFACT_SCHEMA, payload)
    if not isinstance(payload, dict):
        raise ValueError("artifact must be a JSON object")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unexpected artifact schema version")
    if payload["execution_surface"] != EXECUTION_SURFACE:
        raise ValueError("unexpected execution surface")
    if set(payload["scenarios"]) != set(SCENES):
        raise ValueError("artifact must describe exactly the three scenes")
    return payload


def promote(payload: object) -> dict[str, Any]:
    """Validate and sanitize a machine-local artifact for committing."""

    artifact = validate_artifact(payload)
    _scan(artifact)
    return artifact


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically so a crash can never leave a partial artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=path.name,
        suffix=".tmp",
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_working_set_bytes() -> int | None:
    """Process peak RAM when the platform counter is available, else ``None``.

    Reported as ``null`` rather than guessed when the counter is unavailable.
    """

    if sys.platform != "win32":
        return None
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        get_process_memory_info.restype = wintypes.BOOL
        succeeded = get_process_memory_info(
            get_current_process(),
            ctypes.byref(counters),
            counters.cb,
        )
    except Exception:
        return None
    if not succeeded:
        return None
    return int(counters.PeakWorkingSetSize)


def process_vram_mib() -> int | None:
    """This process's VRAM via nvidia-smi, or ``None`` when it is unavailable.

    PyTorch's own counters would miss llama.cpp's CUDA context, so the platform
    tool is the honest source; a missing tool means ``null``, never a guess.
    """

    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    pid = str(os.getpid())
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) == 2 and fields[0] == pid:
            try:
                return int(fields[1])
            except ValueError:
                return None
    return None


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip()


def hardware_facts() -> dict[str, Any]:
    return {
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
        "platform": platform.platform(),
    }


def _installed_model_bytes() -> int:
    manifest = Path("docs/model-manifest.md").read_text(encoding="utf-8")
    block = manifest.split("<!-- model-manifest:start -->", maxsplit=1)[1]
    block = block.split("<!-- model-manifest:end -->", maxsplit=1)[0]
    rows = json.loads(block.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0])
    total = 0
    for row in rows:
        path = Path(row["cache_path"])
        if not path.is_file() or path.stat().st_size != row["expected_bytes"]:
            raise RuntimeError(
                "an allowlisted model artifact is missing or has the wrong size"
            )
        total += row["expected_bytes"]
    return total


def assert_rag(record: Any) -> None:
    """Scene 1: the P4 Shanghai lodging cap, answered from travel-policy."""

    result = record.result
    assert record.state == "answered", record.state
    assert result is not None and result.answer is not None
    assert "600" in result.answer
    assert "travel-policy.md::0000" in {source.chunk_id for source in result.sources}
    assert all(event.status != "started" for event in record.progress_events)


def assert_agent(record: Any) -> None:
    """Scene 2: the EMP-003 / CLAIM-1024 investigation."""

    result = record.result
    assert record.state == "answered", record.state
    assert result is not None and result.answer is not None
    for required in ("CLAIM-1024", "P4", "680", "600"):
        assert required in result.answer, required
    business = tuple(
        (call.name, dict(call.arguments))
        for call in result.tool_trace
        if call.name != "search_knowledge"
    )
    assert business == (
        ("get_expense_claim", {"claim_id": "CLAIM-1024"}),
        ("get_employee_profile", {"employee_id": "EMP-003"}),
        ("get_business_trip", {"trip_id": "TRIP-2409-017"}),
    ), business
    supported = {
        observation.observation_id
        for observation in result.observations
        if observation.status == "found"
    }
    assert len(supported) >= 3, supported


def assert_refusal(record: Any) -> None:
    """Scene 3: pet boarding is stopped at the Evidence Gate."""

    result = record.result
    assert record.state == "refused", record.state
    assert result is not None
    assert result.refusal_reason == "insufficient_evidence"
    assert result.answer is None
    assert result.sources == ()
    assert all(event.stage != "generation" for event in record.progress_events)
    assert record.progress_events[-1].stage == "evidence_gate"
    assert record.progress_events[-1].status == "refused"


ASSERTIONS = {"rag": assert_rag, "agent": assert_agent, "refusal": assert_refusal}


def _assert_progress_matches_trace(record: Any) -> None:
    from enterprise_rag.panel.public_trace import reconcile_terminal_events

    assert record.trace is not None
    merged = reconcile_terminal_events(record.progress_events, record.trace)
    by_id = {event.event_id: event for event in merged}
    assert len(by_id) == len(record.trace.events)
    for final in record.trace.events:
        live = by_id[final.event_id]
        assert (
            live.parent_id,
            live.stage,
            live.status,
            live.error_category,
        ) == (
            final.parent_id,
            final.stage,
            final.status,
            final.error_category,
        )
    assert record.spec is not None and record.spec.run_id == record.trace.run_id


async def run_acceptance(rounds: int = RUNS_PER_SCENE) -> dict[str, Any]:
    """Drive the real panel path; every real import stays lazy inside this call."""

    import socket

    from enterprise_rag.agent_tools import ToolRegistry
    from enterprise_rag.config import AppConfig
    from enterprise_rag.generation import LlamaCppGenerator
    from enterprise_rag.grounding import GteClaimSupportScorer
    from enterprise_rag.index_store import load_index
    from enterprise_rag.panel.controller import PanelController
    from enterprise_rag.panel.coordinator import RunCoordinator
    from enterprise_rag.panel.runtime import PanelRuntime
    from enterprise_rag.reranking import GteReranker
    from enterprise_rag.retrieval import GteHybridEncoder

    attempts: list[object] = []
    real_connect = socket.socket.connect

    def forbidden(*args: object, **kwargs: object) -> None:
        attempts.append((args, kwargs))
        raise AssertionError("the local panel acceptance must not open a socket")

    socket.socket.connect = forbidden  # type: ignore[method-assign]
    counters = {
        "index": 0,
        "encoder": 0,
        "reranker": 0,
        "support_scorer": 0,
        "tools": 0,
        "local_generator": 0,
    }

    def counting(name: str, factory: Any) -> Any:
        def wrapped(*args: object, **kwargs: object) -> Any:
            counters[name] += 1
            return factory(*args, **kwargs)

        return wrapped

    config = AppConfig()
    runtime: Any = None
    try:
        shell_started = time.perf_counter()
        runtime = PanelRuntime(
            config,
            index_loader=counting("index", load_index),
            encoder_factory=counting("encoder", GteHybridEncoder.from_config),
            reranker_factory=counting("reranker", GteReranker.from_config),
            support_scorer_factory=counting(
                "support_scorer",
                GteClaimSupportScorer,
            ),
            tool_loader=counting("tools", ToolRegistry.from_directory),
            local_factory=counting(
                "local_generator",
                LlamaCppGenerator.from_config,
            ),
        )
        coordinator = RunCoordinator(runtime)
        controller = PanelController(coordinator, runtime)
        ui_available_seconds = time.perf_counter() - shell_started

        started = time.perf_counter()
        stages = await coordinator.initialize()
        runtime_ready_seconds = time.perf_counter() - started

        scenario_seconds: dict[str, list[float]] = {scene: [] for scene in SCENES}
        failures: dict[str, list[str]] = {scene: [] for scene in SCENES}
        backend_kinds: set[str] = set()
        for round_index in range(rounds):
            for scene in SCENES:
                controller.select_scenario(_SCENARIO_IDS[scene])
                run_started = time.perf_counter()
                await controller.prepare_run()
                run_id = coordinator.active_run_id
                assert run_id is not None
                record = await coordinator.wait(run_id)
                scenario_seconds[scene].append(time.perf_counter() - run_started)
                for event in record.progress_events:
                    kind = event.attributes.get("backend_kind")
                    if isinstance(kind, str):
                        backend_kinds.add(kind)
                try:
                    ASSERTIONS[scene](record)
                    _assert_progress_matches_trace(record)
                except AssertionError as error:
                    failures[scene].append(f"round {round_index + 1}: {error}")
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        if runtime is not None:
            await runtime.aclose()

    return {
        "ui_available_seconds": ui_available_seconds,
        "runtime_ready_seconds": runtime_ready_seconds,
        "stages": [
            {
                "name": stage.name,
                "duration_ms": stage.duration_ms,
                "status": stage.status,
            }
            for stage in stages
            if stage.status != "failed"
        ],
        "scenario_seconds": scenario_seconds,
        "failures": failures,
        "network_attempts": len(attempts),
        "load_counters": counters,
        "backend_kinds": sorted(backend_kinds),
    }


def _backend_provenance() -> dict[str, Any]:
    from enterprise_rag.config import LOCAL_MODEL_REPOSITORY_ID, LOCAL_MODEL_REVISION

    digest = None
    manifest = Path("docs/model-manifest.md").read_text(encoding="utf-8")
    for line in manifest.splitlines():
        if "qwen3-4b:q5-k-m" in line:
            digest = json.loads(line.strip().rstrip(","))["sha256"]
            break
    return {
        "kind": "real-local",
        "model_id": LOCAL_MODEL_REPOSITORY_ID,
        "model_revision": LOCAL_MODEL_REVISION,
        "model_digest": digest,
    }


def collect(output: Path, rounds: int = RUNS_PER_SCENE) -> dict[str, Any]:
    """Run the real acceptance and write the machine-local artifact."""

    model_bytes = _installed_model_bytes()
    measurements = asyncio.run(run_acceptance(rounds))
    scenarios = measurements["scenario_seconds"]
    percentiles = {scene: latency_summary(scenarios[scene]) for scene in SCENES}
    assertions = {
        scene: {
            "rounds": rounds,
            "failures": measurements["failures"][scene],
            "passed": not measurements["failures"][scene],
        }
        for scene in SCENES
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "execution_surface": EXECUTION_SURFACE,
        "ui_availability_note": UI_AVAILABILITY_NOTE,
        "hardware": {**hardware_facts(), "model_bytes": model_bytes},
        "backend": _backend_provenance(),
        "initialization": {
            "ui_available_seconds": measurements["ui_available_seconds"],
            "runtime_ready_seconds": measurements["runtime_ready_seconds"],
            "stages": measurements["stages"],
        },
        "scenarios": {scene: list(scenarios[scene]) for scene in SCENES},
        "percentiles": percentiles,
        "assertions": assertions,
        "network_attempts": measurements["network_attempts"],
        "load_counters": measurements["load_counters"],
        "peak_ram_bytes": peak_working_set_bytes(),
        "peak_vram_mib": process_vram_mib(),
        "online": "online-attempted-not-verified",
    }
    atomic_write(output, artifact)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collect-panel-acceptance")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--rounds", type=int, default=RUNS_PER_SCENE)
    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--input", type=Path, required=True)
    promote_parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "collect":
        artifact = collect(arguments.output, arguments.rounds)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True))
        return 0
    payload = json.loads(arguments.input.read_text(encoding="utf-8"))
    atomic_write(arguments.output, promote(payload))
    print(f"promoted {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
