from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from enterprise_rag.config import LOCAL_MODEL_FILENAME, AppConfig
from enterprise_rag.generation import AnswerGenerator, LlamaCppGenerator
from enterprise_rag.models import RerankedChunk, RetrievedChunk, ValidationIssue

_EXPECTED_MODEL_BYTES = 2_889_513_184
_EXPECTED_MODEL_SHA256 = (
    "aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f"
)


def test_process_vram_parser_treats_wddm_na_as_unavailable() -> None:
    assert _parse_process_vram_mib("1234, [N/A]\n", pid=1234) is None


def test_peak_working_set_reader_returns_positive_bytes() -> None:
    assert _peak_working_set_bytes() > 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_local_qwen_generator_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def block_network(
        _socket: socket.socket,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise AssertionError("local generation must not open a network socket")

    monkeypatch.setattr(socket.socket, "connect", block_network)
    config = AppConfig()
    assert config.local_model_path.name == LOCAL_MODEL_FILENAME
    assert config.local_model_path.stat().st_size == _EXPECTED_MODEL_BYTES

    load_started = time.perf_counter()
    backend = LlamaCppGenerator.from_config(config)
    load_seconds = time.perf_counter() - load_started
    vram_samples = [_process_vram_mib()]

    generator = AnswerGenerator(backend, max_output_tokens=512)
    evidence = (_hotel_cap_evidence(),)
    question = "P4 员工前往 A 类城市出差时, 每晚酒店住宿上限是多少?"

    answer_started = time.perf_counter()
    draft = await generator.generate_answer(question, evidence)
    answer_seconds = time.perf_counter() - answer_started
    vram_samples.append(_process_vram_mib())

    repair_started = time.perf_counter()
    repaired = await generator.repair_answer(
        question,
        evidence,
        draft,
        (ValidationIssue("repair_probe", 0, "重新输出同一事实与精确引文"),),
    )
    repair_seconds = time.perf_counter() - repair_started
    vram_samples.append(_process_vram_mib())

    for label, result in (("draft", draft), ("repaired", repaired)):
        assert any("\u4e00" <= character <= "\u9fff" for character in result.answer), (
            label,
            result.answer.encode("unicode_escape"),
        )
        assert result.claims
        assert {
            support_id for claim in result.claims for support_id in claim.support_ids
        } <= {"travel-policy.md::0000"}
    assert backend.model_sha256 == _EXPECTED_MODEL_SHA256

    benchmark = {
        "answer_seconds": answer_seconds,
        "cold_load_seconds": load_seconds,
        "model_bytes": config.local_model_path.stat().st_size,
        "model_filename": config.local_model_path.name,
        "model_sha256": backend.model_sha256,
        "observed_peak_process_ram_bytes": _peak_working_set_bytes(),
        "observed_peak_process_vram_mib": max(
            (value for value in vram_samples if value is not None),
            default=None,
        ),
        "repair_seconds": repair_seconds,
    }
    output = Path(".local/benchmarks/task13-local-generator.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(benchmark, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(benchmark, ensure_ascii=False, sort_keys=True))


def _hotel_cap_evidence() -> RerankedChunk:
    return RerankedChunk(
        candidate=RetrievedChunk(
            text=(
                "POLICY-TRAVEL-04 §3.2: P4 员工前往 A 类城市出差时, "
                "每晚酒店住宿可报销上限为 CNY 600。"
            ),
            filename="travel-policy.md",
            chunk_id="travel-policy.md::0000",
            dense_score=0.91,
            dense_rank=1,
            sparse_score=8.2,
            sparse_rank=1,
            retrieval_score=0.032,
            retrieval_rank=1,
        ),
        rerank_score=0.93,
        rerank_rank=1,
    )


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
    process = get_current_process()
    succeeded = get_process_memory_info(
        process,
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
