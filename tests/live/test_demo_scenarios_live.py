from __future__ import annotations

import ctypes
import json
import math
import os
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from enterprise_rag.application import (
    AgentRunRequest,
    RagAskRequest,
    build_application,
)
from enterprise_rag.config import AppConfig
from enterprise_rag.models import AgentResult, RagResult

_RUNS_PER_SCENARIO = 3
_MAX_COLD_START_SECONDS = 60.0
_MAX_STANDARD_QUERY_SECONDS = 45.0
_MAX_MODEL_BYTES = 15_000_000_000
_EXPECTED_BUSINESS_CALLS = (
    ("get_expense_claim", {"claim_id": "CLAIM-1024"}),
    ("get_employee_profile", {"employee_id": "EMP-003"}),
    ("get_business_trip", {"trip_id": "TRIP-2409-017"}),
)
_EXPECTED_OBSERVATION_IDS = {
    "obs:get_expense_claim:CLAIM-1024",
    "obs:get_employee_profile:EMP-003",
    "obs:get_business_trip:TRIP-2409-017",
}


@pytest.mark.live
@pytest.mark.asyncio
async def test_official_demo_scenarios_are_stable_and_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def block_network(
        _socket: socket.socket,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise AssertionError("the official local demo must not open a network socket")

    monkeypatch.setattr(socket.socket, "connect", block_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "1")

    model_bytes = _installed_allowlisted_model_bytes()
    assert model_bytes <= _MAX_MODEL_BYTES

    load_started = time.perf_counter()
    application = build_application(AppConfig(), compose_agent=True)
    cold_start_seconds = time.perf_counter() - load_started
    assert application.rag_service is not None
    assert application.agent_service is not None
    assert application.rag_service.backend_kinds == ("real", "real", "real")

    rag_results: list[RagResult] = []
    agent_results: list[AgentResult] = []
    refusal_results: list[RagResult] = []
    rag_seconds: list[float] = []
    agent_seconds: list[float] = []
    refusal_seconds: list[float] = []
    vram_samples = [_process_vram_mib()]

    for _run in range(_RUNS_PER_SCENARIO):
        started = time.perf_counter()
        rag_result = await application.ask_rag(
            RagAskRequest(
                "P4 员工去上海出差，每晚住宿上限是多少？",  # noqa: RUF001
                "local",
            )
        )
        rag_seconds.append(time.perf_counter() - started)
        rag_results.append(rag_result)
        _assert_grounded_hotel_cap(rag_result)
        vram_samples.append(_process_vram_mib())

        started = time.perf_counter()
        agent_result = await application.run_agent(
            AgentRunRequest(
                "解释 EMP-003 的 CLAIM-1024 为什么被拒绝",
                "local",
            )
        )
        agent_seconds.append(time.perf_counter() - started)
        agent_results.append(agent_result)
        _assert_grounded_claim_explanation(agent_result)
        vram_samples.append(_process_vram_mib())

        started = time.perf_counter()
        refusal_result = await application.ask_rag(
            RagAskRequest(
                "制度是否允许员工报销宠物寄养费？",  # noqa: RUF001
                "local",
            )
        )
        refusal_seconds.append(time.perf_counter() - started)
        refusal_results.append(refusal_result)
        _assert_programmatic_refusal(refusal_result)
        vram_samples.append(_process_vram_mib())

    assert len(rag_results) == _RUNS_PER_SCENARIO
    assert len(agent_results) == _RUNS_PER_SCENARIO
    assert len(refusal_results) == _RUNS_PER_SCENARIO
    assert cold_start_seconds <= _MAX_COLD_START_SECONDS
    assert max(rag_seconds) <= _MAX_STANDARD_QUERY_SECONDS
    assert max(agent_seconds) <= _MAX_STANDARD_QUERY_SECONDS
    assert max(refusal_seconds) <= _MAX_STANDARD_QUERY_SECONDS

    benchmark = {
        "agent": _latency_summary(agent_seconds),
        "cold_start_seconds": cold_start_seconds,
        "model_bytes": model_bytes,
        "observed_peak_process_ram_bytes": _peak_working_set_bytes(),
        "observed_peak_process_vram_mib": max(
            (sample for sample in vram_samples if sample is not None),
            default=None,
        ),
        "provenance": "real-local",
        "rag_answer": _latency_summary(rag_seconds),
        "rag_refusal": _latency_summary(refusal_seconds),
        "runs_per_scenario": _RUNS_PER_SCENARIO,
    }
    output = Path(".local/benchmarks/task19-demo-scenarios-live.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(benchmark, ensure_ascii=False, sort_keys=True))


def _top_level_stages(result: RagResult | AgentResult) -> list[str]:
    """Stages that own no parent; nested retrieval children are checked separately."""

    return [event.stage for event in result.trace.events if event.parent_id is None]


def _assert_real_retrieval_children(result: RagResult) -> None:
    events = result.trace.events
    retrieval = next(event for event in events if event.stage == "retrieval")
    assert [
        event.stage for event in events if event.parent_id == retrieval.event_id
    ] == ["dense", "sparse", "rrf_fusion"]


def _assert_grounded_hotel_cap(result: RagResult) -> None:
    assert result.status == "answered"
    assert result.answer is not None and "600" in result.answer
    source_ids = {source.chunk_id for source in result.sources}
    assert "travel-policy.md::0000" in source_ids
    assert source_ids <= {
        "travel-policy.md::0000",
        "travel-policy.md::0001",
    }
    direct_stages = [
        "retrieval",
        "reranking",
        "evidence_gate",
        "generation",
        "grounding_validation",
    ]
    repaired_stages = [
        *direct_stages,
        "repair_generation",
        "repair_validation",
    ]
    stages = _top_level_stages(result)
    assert tuple(stages) in {tuple(direct_stages), tuple(repaired_stages)}
    _assert_real_retrieval_children(result)
    if stages == repaired_stages:
        statuses = {event.stage: event.status for event in result.trace.events}
        assert statuses["grounding_validation"] == "degraded"
        assert statuses["repair_validation"] == "succeeded"


def _assert_grounded_claim_explanation(result: AgentResult) -> None:
    assert result.status == "answered"
    assert result.answer is not None
    for required_fact in ("CLAIM-1024", "P4", "680", "600"):
        assert required_fact in result.answer

    business_calls = tuple(
        (call.name, dict(call.arguments))
        for call in result.tool_trace
        if call.name != "search_knowledge"
    )
    assert business_calls == _EXPECTED_BUSINESS_CALLS
    assert all(
        call.name
        in {
            "search_knowledge",
            "get_employee_profile",
            "get_expense_claim",
            "get_business_trip",
        }
        for call in result.tool_trace
    )

    found_observation_ids = {
        observation.observation_id
        for observation in result.observations
        if observation.status == "found"
    }
    assert _EXPECTED_OBSERVATION_IDS <= found_observation_ids
    support_ids = {
        support_id for claim in result.claims for support_id in claim.support_ids
    }
    assert _EXPECTED_OBSERVATION_IDS <= support_ids
    assert "travel-policy.md::0000" in support_ids
    assert {source.chunk_id for source in result.sources} <= {
        "travel-policy.md::0000",
        "travel-policy.md::0001",
        "expense-policy.md::0000",
        "expense-policy.md::0001",
        "expense-faq.md::0000",
        "expense-faq.md::0001",
    }


def _assert_programmatic_refusal(result: RagResult) -> None:
    assert result.status == "refused"
    assert result.refusal_reason == "insufficient_evidence"
    assert result.answer is None
    assert result.sources == ()
    assert _top_level_stages(result) == [
        "retrieval",
        "reranking",
        "evidence_gate",
    ]
    assert result.trace.events[-1].stage == "evidence_gate"
    assert result.trace.events[-1].status == "refused"
    assert result.trace.events[-1].error_category == "evidence_insufficient"


def _installed_allowlisted_model_bytes() -> int:
    manifest = Path("docs/model-manifest.md").read_text(encoding="utf-8")
    block = manifest.split("<!-- model-manifest:start -->", maxsplit=1)[1]
    block = block.split("<!-- model-manifest:end -->", maxsplit=1)[0]
    rows = json.loads(block.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0])
    total = 0
    for row in rows:
        path = Path(row["cache_path"])
        expected_bytes = row["expected_bytes"]
        assert path.is_file(), path
        assert path.stat().st_size == expected_bytes, path
        total += expected_bytes
    return total


def _latency_summary(values: list[float]) -> dict[str, object]:
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    middle = len(ordered) // 2
    p50 = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "max_seconds": max(ordered),
        "p50_seconds": p50,
        "p95_seconds": ordered[p95_index],
        "samples_seconds": values,
    }


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


def _peak_working_set_bytes() -> int:
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
    if not succeeded:
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize)


def _process_vram_mib() -> int | None:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        return None
    return _parse_process_vram_mib(completed.stdout, pid=os.getpid())


def _parse_process_vram_mib(output: str, *, pid: int) -> int | None:
    total = 0
    matched = False
    for line in output.splitlines():
        fields = tuple(item.strip() for item in line.split(","))
        if len(fields) != 2 or fields[0] != str(pid):
            continue
        matched = True
        try:
            total += int(fields[1])
        except ValueError:
            return None
    return total if matched else 0
