# Enterprise RAG Agent CLI Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interview-ready, offline-first enterprise travel-expense RAG + bounded Agent CLI with real hybrid retrieval, real reranking, a real local Qwen generator, evidence gating, post-generation grounding validation, safe refusal, structured traces, and layered evaluation.

**Architecture:** A small provider-independent Python core exposes `RagService` and `AgentService`; CLI and the future panel call a shared `Application` composition root. Retrieval uses pinned `gte-multilingual-base` dense and sparse representations with deterministic RRF, then pinned `gte-multilingual-reranker-base`; generation is allowed only after an Evidence Gate and accepted only after a structural/support validator, with at most one repair. Real backends live behind Protocol adapters, while default tests use explicitly labelled deterministic fakes.

**Tech Stack:** Python 3.12, Hatchling, uv, frozen dataclasses and Protocols, argparse, NumPy, PyTorch/Transformers/Sentence Transformers for GTE, llama-cpp-python for Qwen3 GGUF, OpenAI Python SDK for registered relay profiles, pytest/pytest-asyncio, Ruff, ty.

---

## Execution boundaries

- Every shell command in this workspace must begin with `rtk`; PowerShell cmdlets run through `rtk proxy powershell -NoProfile -Command '…'`.
- Do not use a worktree, submodule, symlink, editable dependency, or `PYTHONPATH` link to the source repository.
- Read source files only from Git object `f09229248a354d652ac04ef249be0abb1549fbcd`; never copy from its dirty working tree.
- Do not download model files in Tasks 1–9. Task 10 is a mandatory model-download approval pause.
- Do not call a real online API in this plan. The online adapter is tested with a fake client; a live relay profile requires its own cost/privacy approval.
- Default test commands exclude `live`, require no key or network, and may not count fake outputs as real-backend evidence.
- Each task ends in a small commit. If verification fails, fix it before committing; do not carry a red test into the next task.

## Scope split

This plan delivers the urgent interview CLI and the reusable business core. The future engineering panel is deliberately excluded because its technology choice was deferred until CLI acceptance; after this plan passes, write a separate panel implementation plan for the approved “pipeline workbench + fixed right inspector” layout. This split keeps the first deliverable independently runnable and testable.

## File responsibility map

```text
src/enterprise_rag/
  __init__.py             package version only
  __main__.py             python -m entry point
  cli.py                  argparse commands and exit-code mapping
  cli_rendering.py        human and JSON rendering only
  config.py               typed local settings and safety invariants
  provider_profiles.py    allowlisted online profile loading/preflight
  errors.py               stable machine-readable error taxonomy
  models.py               immutable domain/result contracts
  ports.py                provider-independent async Protocols
  ingestion.py            deterministic UTF-8 .md/.txt load and chunk
  execution_trace.py      hierarchical events, timers, redaction
  index_store.py          versioned local index serialization
  retrieval.py            hybrid encoding, dense/sparse ranking, RRF
  reranking.py            deterministic rerank ordering and GTE adapter
  grounding.py            Evidence Gate and Grounding Validator
  generation.py           prompts, schema parsing, fake/local/online adapters
  rag_service.py          fixed RAG control flow
  agent_tools.py          allowlisted read-only fact tools
  agent_service.py        bounded Plan–Act–Observe loop
  application.py          composition root and backend lifecycle
  evaluation.py           layered metric calculation and report output

tests/
  unit/                   pure contracts and algorithms
  integration/            fake-backed service and CLI behavior
  agent/                  tool selection, loop limits, refusal semantics
  live/                   separately selected real-model acceptance
  fixtures/               migrated source fixtures and synthetic test data

data/sample_docs/         fictional policy corpus
data/sample_records/      fictional employee/claim/trip records
evals/                    24 knowledge + 12 Agent cases
docs/                     architecture, demo, evaluation, security, models
```

## Task 1: Establish the independent repository and offline test baseline

**Files:**
- Create: `.python-version`
- Modify: `.gitignore`
- Create: `pyproject.toml`
- Create: `src/enterprise_rag/__init__.py`
- Create: `src/enterprise_rag/__main__.py`
- Create: `src/enterprise_rag/cli.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Verify the target and source boundaries before initialization**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$target=(Resolve-Path -LiteralPath ".").Path; $source=(Resolve-Path -LiteralPath "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo").Path; "$target`n$source"; if ($target -eq $source) { exit 1 }'
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" rev-parse f09229248a354d652ac04ef249be0abb1549fbcd
```

Expected: two different absolute directories, followed by the exact source commit SHA.

- [ ] **Step 2: Initialize only the target repository**

Run:

```powershell
rtk git init
rtk git status --short --branch
```

Expected: a new repository in the current directory; the source repository remains untouched.

- [ ] **Step 3: Write the failing smoke test**

```python
import sys

import pytest

from enterprise_rag import __version__
from enterprise_rag.cli import console_main, main


def test_package_version_and_cli_help(capsys) -> None:
    assert __version__ == "0.1.0"
    exit_code = main(["--help"])
    assert exit_code == 0
    assert "rag-agent" in capsys.readouterr().out


def test_cli_invalid_argument_returns_parser_exit_code() -> None:
    assert main(["--unknown-option"]) == 2


def test_console_main_preserves_process_exit(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["rag-agent"])
    with pytest.raises(SystemExit) as exc_info:
        console_main()
    assert exc_info.value.code == 0
```

- [ ] **Step 4: Run the smoke test and confirm the package does not exist yet**

Run:

```powershell
rtk uv run pytest tests/test_smoke.py -v
```

Expected: FAIL because the package/project environment has not been created.

- [ ] **Step 5: Create the minimal package and project configuration**

Task 1 intentionally declares no `[project.optional-dependencies]` groups. `uv`
creates a universal lock and can fetch optional model-runtime package metadata even
when an extra is not selected. Deferring each optional group until its explicit
approval-stage task prevents model-runtime metadata from being fetched before that
approval.

Use this minimal dependency configuration in `pyproject.toml`:

```toml
[project]
name = "enterprise-rag-agent-demo"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = []

[dependency-groups]
dev = [
  "pytest==9.1.1",
  "pytest-asyncio==1.4.0",
  "ruff==0.16.8",
  "ty==0.0.83",
]

[project.scripts]
rag-agent = "enterprise_rag.cli:console_main"

[build-system]
requires = ["hatchling>=1.28,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/enterprise_rag"]

[tool.pytest.ini_options]
addopts = "-ra --import-mode=importlib -m 'not live'"
asyncio_mode = "auto"
markers = ["live: requires an explicitly approved real model or endpoint"]
testpaths = ["tests"]

[tool.ruff]
line-length = 88
target-version = "py312"
extend-exclude = ["docs"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]

[tool.ty.environment]
python-version = "3.12"
```

Set `.python-version` to `3.12`. Extend `.gitignore` with:

```gitignore
.venv/
.pytest_cache/
.ruff_cache/
.local/
__pycache__/
*.py[cod]
.env
.superpowers/
```

Implement the temporary CLI contract:

```python
# src/enterprise_rag/cli.py
import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="rag-agent")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        build_parser().parse_args(argv)
    except SystemExit as error:
        if not isinstance(error.code, int):
            raise
        return error.code
    return 0


def console_main() -> None:
    raise SystemExit(main())
```

- [ ] **Step 6: Create the environment and run the baseline checks**

Run:

```powershell
rtk uv sync --group dev
rtk uv run pytest tests/test_smoke.py -v
rtk uv run ruff check .
rtk uv run ty check src
```

Expected: smoke test PASS; Ruff and ty exit 0. No optional model dependency or model file is installed.

- [ ] **Step 7: Commit the independent skeleton and approved design artifacts**

```powershell
rtk git add .python-version .gitignore pyproject.toml uv.lock src tests docs
rtk git commit -m "chore: initialize independent interview demo"
rtk git tag implementation-start
```

## Task 2: Selectively migrate the audited core from the pinned source commit

**Files:**
- Create from pinned source: `src/enterprise_rag/models.py`
- Create from pinned source: `src/enterprise_rag/ports.py`
- Create from pinned source: `src/enterprise_rag/errors.py`
- Create from pinned source: `src/enterprise_rag/ingestion.py`
- Create from pinned source: `tests/unit/test_models.py`
- Create from pinned source: `tests/unit/test_ports.py`
- Create from pinned source: `tests/unit/test_errors.py`
- Create from pinned source: `tests/unit/test_ingestion.py`
- Create from pinned source: `tests/fixtures/docs/alpha.md`
- Create from pinned source: `tests/fixtures/docs/beta.txt`
- Create from pinned source: `tests/fixtures/docs/ignored.pdf`
- Create: `docs/migration-ledger.md`

- [ ] **Step 1: Re-read each candidate from the Git object, not the working tree**

Run these exact reads:

```powershell
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:src/enterprise_rag/models.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:src/enterprise_rag/ports.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:src/enterprise_rag/errors.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:src/enterprise_rag/ingestion.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/unit/test_models.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/unit/test_ports.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/unit/test_errors.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/unit/test_ingestion.py"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/fixtures/docs/alpha.md"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/fixtures/docs/beta.txt"
rtk git -C "E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo" show "f09229248a354d652ac04ef249be0abb1549fbcd:tests/fixtures/docs/ignored.pdf"
```

Expected: file content is emitted from the object database. Do not use `Copy-Item` against the source worktree.

- [ ] **Step 2: Add the files with `apply_patch` and record provenance**

The ledger must contain one row per imported file:

```markdown
| Target | Source commit | Source path | Decision |
|---|---|---|---|
| `src/enterprise_rag/models.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `src/enterprise_rag/models.py` | Import, then extend in Task 3 |
```

Repeat with the exact target/source pairs above. Record that `config.py`, `pyproject.toml`, `uv.lock`, `.env.example`, old plans, learning files, caches and uncommitted handoffs were not imported.

- [ ] **Step 3: Run only the migrated tests**

Run:

```powershell
rtk uv run pytest tests/unit/test_models.py tests/unit/test_ports.py tests/unit/test_errors.py tests/unit/test_ingestion.py -v
```

Expected: all migrated tests PASS in the new repository, including Windows path-boundary behavior or an explicit platform skip already present in the pinned test.

- [ ] **Step 4: Commit the provenance-preserving migration**

```powershell
rtk git add src/enterprise_rag tests/unit tests/fixtures docs/migration-ledger.md
rtk git commit -m "chore: migrate pinned core contracts and ingestion"
```

## Task 3: Expand immutable domain contracts and the hierarchical trace

**Files:**
- Modify: `src/enterprise_rag/models.py`
- Modify: `src/enterprise_rag/ports.py`
- Modify: `src/enterprise_rag/errors.py`
- Create: `src/enterprise_rag/execution_trace.py`
- Modify: `tests/unit/test_models.py`
- Modify: `tests/unit/test_ports.py`
- Modify: `tests/unit/test_errors.py`
- Create: `tests/unit/test_execution_trace.py`

- [ ] **Step 1: Write failing contract tests for hybrid scores, evidence IDs and refusal invariants**

```python
def test_retrieved_chunk_preserves_each_lane_and_fused_rank() -> None:
    chunk = RetrievedChunk(
        text="上海属于一线城市。",
        filename="travel-policy.md",
        chunk_id="travel-policy.md::0001",
        dense_score=0.82,
        dense_rank=2,
        sparse_score=4.1,
        sparse_rank=1,
        retrieval_score=0.0325,
        retrieval_rank=1,
    )
    assert chunk.dense_rank == 2
    assert chunk.sparse_rank == 1
    assert chunk.retrieval_rank == 1


def test_refusal_never_exposes_answer_or_sources() -> None:
    result = RagResult.refused("insufficient_evidence")
    assert result.answer is None
    assert result.sources == ()
    assert result.refusal_reason == "insufficient_evidence"
```

Add tests that reject non-finite scores, non-positive ranks, mutable collections, duplicate evidence IDs and a grounded result without sources.

- [ ] **Step 2: Run the new contract tests and verify failure**

Run:

```powershell
rtk uv run pytest tests/unit/test_models.py tests/unit/test_ports.py tests/unit/test_errors.py -v
```

Expected: FAIL because the migrated contracts do not yet expose hybrid lane fields, structured generation requests or the new error categories.

- [ ] **Step 3: Implement the new immutable contracts**

Use these exact shapes as the public vocabulary:

```python
@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    text: str
    filename: str
    chunk_id: str
    dense_score: float | None
    dense_rank: int | None
    sparse_score: float | None
    sparse_rank: int | None
    retrieval_score: float
    retrieval_rank: int


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    candidate: RetrievedChunk
    rerank_score: float
    rerank_rank: int


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    text: str
    support_ids: tuple[str, ...]
    supporting_quotes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    answer: str
    claims: tuple[EvidenceClaim, ...]


@dataclass(frozen=True, slots=True)
class RagResult:
    status: Literal["answered", "refused", "failed"]
    answer: str | None
    sources: tuple[Source, ...]
    refusal_reason: str | None
    error_category: str | None
    trace: RunTrace
```

Factory constructors `answered`, `refused` and `failed` enforce mutually exclusive states. Add `Observation`, `AgentAction`, `ToolCall`, `AgentResult`, `StructuredGenerationRequest` and `ValidationIssue` with tuple-based collections.

- [ ] **Step 4: Add stable error categories**

Extend the exact enum test and implementation with:

```text
retrieval_unavailable
retrieval_protocol
evidence_insufficient
generation_protocol
grounding_failed
tool_execution
duplicate_action
budget_blocked
privacy_blocked
profile_not_allowed
```

Each exception type has a class-level `category`; no caller parses human strings to branch.

- [ ] **Step 5: Write failing trace tests**

```python
def test_trace_builds_parent_child_tree_and_redacts_secrets() -> None:
    recorder = TraceRecorder(run_id="run-001", clock=FakeClock())
    with recorder.span("retrieval", attributes={"api_key": "secret"}) as parent:
        with recorder.span("dense", parent_id=parent.event_id):
            pass
    trace = recorder.finish("succeeded")
    assert trace.events[1].parent_id == trace.events[0].event_id
    assert "secret" not in trace.to_json()
```

- [ ] **Step 6: Implement `TraceRecorder` with an injectable monotonic clock**

`TraceEvent` contains `event_id`, `parent_id`, `stage`, `status`, `started_at`, `duration_ms`, immutable attributes and optional error category. Permit only `started`, `succeeded`, `refused`, `failed`, `skipped`, `degraded`. Redact keys matching `api_key`, `authorization`, `secret`, `token`, `password`, `system_prompt` recursively before storage.

- [ ] **Step 7: Run contracts, trace, lint and type checks**

```powershell
rtk uv run pytest tests/unit/test_models.py tests/unit/test_ports.py tests/unit/test_errors.py tests/unit/test_execution_trace.py -v
rtk uv run ruff check src tests
rtk uv run ty check src
```

Expected: PASS and zero lint/type errors.

- [ ] **Step 8: Commit the domain and trace foundation**

```powershell
rtk git add src/enterprise_rag tests/unit
rtk git commit -m "feat: define grounded results and execution trace"
```

## Task 4: Implement safe configuration and registered Provider Profiles

**Files:**
- Create: `src/enterprise_rag/config.py`
- Create: `src/enterprise_rag/provider_profiles.py`
- Create: `.env.example`
- Create: `config/providers.example.toml`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_provider_profiles.py`

- [ ] **Step 1: Write failing safe-default tests**

```python
def test_defaults_are_offline_local_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(os, "environ", {})
    config = AppConfig.from_env()
    assert config.generator == "local"
    assert config.retrieve_k == 20
    assert config.rerank_k == 5
    assert config.agent_max_actions == 6
    assert config.allow_model_download is False
    assert config.allow_network is False
```

Also assert `retrieve_k >= rerank_k > 0`, positive chunk settings, finite thresholds/timeouts, model paths inside the configured cache root, and secret values absent from `repr` and logs.

- [ ] **Step 2: Run the configuration tests and confirm failure**

```powershell
rtk uv run pytest tests/unit/test_config.py tests/unit/test_provider_profiles.py -v
```

Expected: FAIL because configuration modules do not exist.

- [ ] **Step 3: Implement typed settings with these defaults**

```python
@dataclass(frozen=True, slots=True)
class AppConfig:
    generator: Literal["local", "online"] = "local"
    retrieve_k: int = 20
    rerank_k: int = 5
    rrf_k: int = 60
    min_rerank_score: float = 0.50
    min_claim_support_score: float = 0.50
    chunk_size: int = 384
    chunk_overlap: int = 48
    agent_max_actions: int = 6
    max_repairs: int = 1
    model_cache_dir: Path = Path(".local/model-cache")
    index_dir: Path = Path(".local/index")
    local_model_path: Path = Path(".local/model-cache/Qwen3-4B-Q5_K_M.gguf")
    allow_model_download: bool = False
    allow_network: bool = False
```

Add pinned identifiers as ordinary non-secret fields:

```text
Alibaba-NLP/gte-multilingual-base @ 087a024525fd6e2fe749cb4679d218d8bcc95bdd
Alibaba-NLP/gte-multilingual-reranker-base @ 0acb790751daef0c1a7c5191122056bd16ef9f09
Alibaba-NLP/new-impl @ 40ced75 (resolve and record the full SHA before download)
Qwen/Qwen3-4B-GGUF / Qwen3-4B-Q5_K_M.gguf
```

- [ ] **Step 4: Implement allowlisted Provider Profiles and preflight**

```python
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
```

`load_profiles(path)` rejects credentials in TOML, non-HTTPS remote URLs, loopback confusion, missing price/privacy fields, duplicate names and enabled profiles with `synthetic_data_only=false`. `preflight_online_call` resolves the API key from `api_key_env`, estimates the upper-bound cost from configured token caps, and raises before constructing a network client if any policy fails.

- [ ] **Step 5: Create a disabled, non-secret example profile**

```toml
[profiles.relay_example]
enabled = false
base_url = "https://relay.example.invalid/v1"
model_id = "provider-advertised-model-id"
api_key_env = "RAG_AGENT_RELAY_API_KEY"
privacy_terms_url = "https://relay.example.invalid/privacy"
input_price_per_million = "0"
output_price_per_million = "0"
request_limit = 0
budget_limit_cny = "0"
synthetic_data_only = true
```

The `.invalid` profile cannot accidentally make a real call and is not represented as a verified vendor.

- [ ] **Step 6: Run configuration checks**

```powershell
rtk uv run pytest tests/unit/test_config.py tests/unit/test_provider_profiles.py -v
rtk uv run ruff check src tests
rtk uv run ty check src
```

Expected: PASS; no key appears in captured logs or dataclass representations.

- [ ] **Step 7: Commit safe configuration**

```powershell
rtk git add src/enterprise_rag/config.py src/enterprise_rag/provider_profiles.py tests/unit .env.example config
rtk git commit -m "feat: add fail-closed runtime configuration"
```

## Task 5: Add the fictional travel-expense corpus and read-only records

**Files:**
- Create: `data/sample_docs/travel-policy.md`
- Create: `data/sample_docs/expense-policy.md`
- Create: `data/sample_docs/expense-faq.md`
- Create: `data/sample_records/employees.json`
- Create: `data/sample_records/claims.json`
- Create: `data/sample_records/trips.json`
- Create: `tests/fixtures/sample_expected.json`
- Create: `tests/unit/test_sample_data.py`

- [ ] **Step 1: Write failing consistency tests for the canonical demo**

```python
def test_claim_1024_matches_employee_trip_and_policy(sample_data) -> None:
    employee = sample_data.employee("EMP-003")
    claim = sample_data.claim("CLAIM-1024")
    trip = sample_data.trip("TRIP-2409-017")
    assert employee.grade == "P4"
    assert claim.employee_id == employee.employee_id
    assert claim.trip_id == trip.trip_id
    assert claim.hotel_nightly_amount == 680
    assert claim.status == "rejected"
    assert claim.rejection_code == "HOTEL_OVER_LIMIT"
```

- [ ] **Step 2: Run the consistency test and verify missing fixtures**

```powershell
rtk uv run pytest tests/unit/test_sample_data.py -v
```

Expected: FAIL because no sample corpus or record loader exists.

- [ ] **Step 3: Create exact policy facts**

The normative corpus must state, in separate identifiable sections:

```text
POLICY-TRAVEL-04 §3.2: P4 employees visiting tier-A cities may claim at most CNY 600 per hotel night.
POLICY-TRAVEL-04 §3.3: Amounts above the limit require VP preapproval recorded before the trip.
POLICY-EXPENSE-02 §2.1: Transport expenses above CNY 50 require a valid receipt.
POLICY-EXPENSE-02 §4.1: A claim is rejected when a required receipt or required preapproval is absent.
POLICY-EXPENSE-02 §5.2: A rejected claim may be corrected and resubmitted within 10 calendar days.
```

Write natural Chinese paragraphs around those facts, include the English IDs verbatim, and add distractor rules for P5 employees and non-tier-A cities so reranking is meaningful.

- [ ] **Step 4: Create exact canonical records**

```json
{
  "employee_id": "EMP-003",
  "name": "林晓雨",
  "department": "解决方案工程部",
  "grade": "P4",
  "travel_tier": "standard"
}
```

```json
{
  "claim_id": "CLAIM-1024",
  "employee_id": "EMP-003",
  "trip_id": "TRIP-2409-017",
  "hotel_nightly_amount": 680,
  "hotel_nights": 2,
  "transport_amount": 120,
  "receipt_present": true,
  "status": "rejected",
  "rejection_code": "HOTEL_OVER_LIMIT"
}
```

```json
{
  "trip_id": "TRIP-2409-017",
  "destination": "上海",
  "city_tier": "A",
  "start_date": "2026-09-12",
  "end_date": "2026-09-14",
  "approved": true,
  "vp_exception_approved": false,
  "budget": 2600
}
```

Add at least two unrelated employees, claims and trips to prevent the demo from succeeding through single-record assumptions.

- [ ] **Step 5: Make the fixture test load raw JSON and assert referential integrity**

Use only `json` and `pathlib` in this test; the business tool loader is implemented later. Assert unique IDs, existing foreign keys, non-negative amounts, ISO dates and absence of real-looking email, phone or government ID fields.

- [ ] **Step 6: Run and commit the fictional dataset**

```powershell
rtk uv run pytest tests/unit/test_sample_data.py -v
rtk git add data tests/fixtures tests/unit/test_sample_data.py
rtk git commit -m "data: add fictional travel expense demo corpus"
```

## Task 6: Build a versioned local index contract without loading a model

**Files:**
- Modify: `src/enterprise_rag/ingestion.py`
- Create: `src/enterprise_rag/index_store.py`
- Modify: `src/enterprise_rag/ports.py`
- Create: `tests/unit/test_index_store.py`
- Create: `tests/integration/test_index_build_fake.py`

- [ ] **Step 1: Write failing round-trip and corruption tests**

```python
def test_index_round_trip_preserves_chunks_dense_and_sparse(tmp_path) -> None:
    index = HybridIndex(
        manifest=IndexManifest(schema_version=1, model_revision="rev", dimension=2),
        chunks=(DocumentChunk("规则", "policy.md", "policy.md::0000"),),
        dense=((0.6, 0.8),),
        sparse=(((101, 1.25),),),
    )
    save_index(index, tmp_path)
    assert load_index(tmp_path) == index
```

Also test mismatched row counts, duplicate chunk IDs, non-finite vectors, path traversal, partial writes and an unknown schema version.

- [ ] **Step 2: Run the index tests and confirm failure**

```powershell
rtk uv run pytest tests/unit/test_index_store.py tests/integration/test_index_build_fake.py -v
```

Expected: FAIL because the store and encoder port do not exist.

- [ ] **Step 3: Add the encoder port and immutable encoded batch**

```python
@runtime_checkable
class HybridEncoderPort(Protocol):
    async def encode(
        self, texts: tuple[str, ...]
    ) -> EncodedBatch:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class EncodedBatch:
    dense: tuple[tuple[float, ...], ...]
    sparse: tuple[tuple[tuple[int, float], ...], ...]
```

- [ ] **Step 4: Implement atomic versioned storage**

Store `manifest.json`, `chunks.jsonl`, `dense.npy` and `sparse.jsonl` in a temporary sibling directory, fsync files, then rename the completed directory into place. Manifest fields include schema version, source digest, chunk settings, model ID/revision/code revision, dimension, row count and build timestamp. `load_index` verifies every count, digest and finite value before returning.

- [ ] **Step 5: Implement fake-backed index build**

```python
async def build_index(
    docs_dir: Path,
    index_dir: Path,
    encoder: HybridEncoderPort,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> IndexManifest:
    documents = load_documents(docs_dir)
    chunks = chunk_documents(documents, chunk_size, chunk_overlap)
    encoded = await encoder.encode(tuple(chunk.text for chunk in chunks))
    manifest = IndexManifest(
        schema_version=1,
        backend_kind=encoder.backend_kind,
        source_digest=corpus_digest(chunks),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        model_id=encoder.model_id,
        model_revision=encoder.model_revision,
        code_revision=encoder.code_revision,
        dimension=len(encoded.dense[0]),
        row_count=len(chunks),
    )
    index = HybridIndex(
        manifest=manifest,
        chunks=chunks,
        dense=encoded.dense,
        sparse=encoded.sparse,
    )
    save_index(index, index_dir)
    return index.manifest
```

The fake encoder must set `backend_kind="fake"` in its manifest so it cannot be presented as a real model index.

- [ ] **Step 6: Verify and commit the store**

```powershell
rtk uv run pytest tests/unit/test_ingestion.py tests/unit/test_index_store.py tests/integration/test_index_build_fake.py -v
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag tests
rtk git commit -m "feat: add deterministic hybrid index storage"
```

## Task 7: Implement deterministic dense/sparse ranking and RRF

**Files:**
- Create: `src/enterprise_rag/retrieval.py`
- Create: `tests/unit/test_retrieval.py`
- Create: `tests/integration/test_retriever_fake.py`

- [ ] **Step 1: Write failing ranking tests including stable ties**

```python
def test_rrf_unions_lanes_and_uses_chunk_id_for_final_ties() -> None:
    dense = (("b", 0.9), ("a", 0.8))
    sparse = (("a", 4.0), ("b", 3.0))
    fused = reciprocal_rank_fusion(dense, sparse, rrf_k=60, top_k=2)
    assert [item.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].retrieval_score == fused[1].retrieval_score
```

Add cases for a chunk present in only one lane, invalid `top_k`, zero dense norm, non-finite scores and `top_k` larger than the corpus.

- [ ] **Step 2: Confirm tests fail**

```powershell
rtk uv run pytest tests/unit/test_retrieval.py tests/integration/test_retriever_fake.py -v
```

Expected: FAIL because retrieval functions are absent.

- [ ] **Step 3: Implement pure lane ranking and RRF**

```python
def reciprocal_rank_fusion(
    dense: tuple[RankedLaneItem, ...],
    sparse: tuple[RankedLaneItem, ...],
    *,
    rrf_k: int,
    top_k: int,
) -> tuple[FusedItem, ...]:
    by_id: dict[str, dict[str, float | int | None]] = {}
    for lane_name, items in (("dense", dense), ("sparse", sparse)):
        for rank, item in enumerate(items, start=1):
            row = by_id.setdefault(item.chunk_id, {})
            row[f"{lane_name}_score"] = item.score
            row[f"{lane_name}_rank"] = rank
            row["rrf"] = float(row.get("rrf", 0.0)) + 1.0 / (rrf_k + rank)
    ordered = sorted(by_id.items(), key=lambda pair: (-float(pair[1]["rrf"]), pair[0]))
    return tuple(FusedItem.from_row(chunk_id, row, rank) for rank, (chunk_id, row) in enumerate(ordered[:top_k], 1))
```

Dense ranks use cosine similarity; sparse ranks use dot product across token-weight mappings. Each lane sorts by descending score then stable `chunk_id`.

- [ ] **Step 4: Implement `HybridRetriever` against `HybridIndex` and `HybridEncoderPort`**

Encode the query once, independently rank the full corpus in both lanes, keep the top `retrieve_k` from each lane, union/fuse them, and return the final `retrieve_k` candidates with all six score/rank fields. A failure in either lane raises a retrieval error; it never silently falls back to one lane.

- [ ] **Step 5: Verify deterministic retrieval and commit**

```powershell
rtk uv run pytest tests/unit/test_retrieval.py tests/integration/test_retriever_fake.py -v
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag/retrieval.py tests
rtk git commit -m "feat: add deterministic hybrid retrieval and rrf"
```

## Task 8: Build the fake-backed RAG safety loop before real models

**Files:**
- Create: `src/enterprise_rag/reranking.py`
- Create: `src/enterprise_rag/grounding.py`
- Create: `src/enterprise_rag/generation.py`
- Create: `src/enterprise_rag/rag_service.py`
- Modify: `src/enterprise_rag/ports.py`
- Create: `tests/fakes.py`
- Create: `tests/unit/test_grounding.py`
- Create: `tests/unit/test_generation.py`
- Create: `tests/integration/test_rag_service.py`

- [ ] **Step 1: Write failing tests for the fixed stage order**

```python
@pytest.mark.asyncio
async def test_rag_runs_retrieve_rerank_gate_generate_validate_in_order() -> None:
    calls: list[str] = []
    service = make_fake_rag(calls=calls, gate="pass", validation="valid")
    result = await service.ask("P4 上海住宿标准是多少？")
    assert result.status == "answered"
    assert calls == ["retrieve", "rerank", "gate", "generate", "validate"]
```

Add tests proving: gate refusal never calls Generator; retrieval/rerank failures never call later stages; invalid draft triggers exactly one repair; invalid repair becomes a programmatic refusal; returned source IDs are a subset of reranked evidence.

- [ ] **Step 2: Run the RAG tests and confirm failure**

```powershell
rtk uv run pytest tests/unit/test_grounding.py tests/unit/test_generation.py tests/integration/test_rag_service.py -v
```

Expected: FAIL because the safety loop is absent.

- [ ] **Step 3: Implement an explicit Evidence Gate**

```python
@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    evidence: tuple[RerankedChunk, ...]
    reason: str | None


class EvidenceGate:
    def decide(self, evidence: tuple[RerankedChunk, ...]) -> GateDecision:
        eligible = tuple(
            item
            for item in evidence
            if item.rerank_score >= self._min_score
            and item.candidate.text.strip()
            and item.candidate.chunk_id
            and item.candidate.filename
        )
        if not eligible:
            return GateDecision(False, (), "insufficient_evidence")
        return GateDecision(True, eligible, None)
```

The gate belongs to `RagService`; CLI cannot pass a threshold or override the decision.

- [ ] **Step 4: Define the answer JSON Schema and strict parser**

The schema requires exactly `answer` and `claims`; each claim requires non-empty `text`, `support_ids` and `supporting_quotes`, with no additional properties. Parse JSON into immutable `AnswerDraft`; reject duplicate support IDs, empty claims, a claim absent from the final answer, invalid UTF-8/control characters and unknown keys.

```python
ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "claims"],
    "properties": {
        "answer": {"type": "string", "minLength": 1},
        "claims": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "support_ids", "supporting_quotes"],
            },
        },
    },
}
```

Keep the provider boundary generic and put answer/repair prompting in `AnswerGenerator`:

```python
@runtime_checkable
class StructuredGeneratorPort(Protocol):
    async def generate_json(
        self, request: StructuredGenerationRequest
    ) -> Mapping[str, object]:
        raise NotImplementedError


class AnswerGenerator:
    def __init__(self, backend: StructuredGeneratorPort) -> None:
        self._backend = backend

    async def generate_answer(self, question, evidence) -> AnswerDraft:
        request = answer_request(question, evidence, ANSWER_SCHEMA)
        return parse_answer_draft(await self._backend.generate_json(request))

    async def repair_answer(self, question, evidence, draft, issues) -> AnswerDraft:
        request = repair_request(question, evidence, draft, issues, ANSWER_SCHEMA)
        return parse_answer_draft(await self._backend.generate_json(request))
```

- [ ] **Step 5: Implement deterministic structural/support validation**

For every claim:

1. every `support_id` exists in the allowed chunk/Observation map;
2. each `supporting_quote` is an exact normalized substring of one cited support;
3. every number, currency amount, date and uppercase business ID in the claim appears in a cited quote;
4. a `ClaimSupportScorerPort` reranks the claim against its cited texts and meets `min_claim_support_score`;
5. no claim or source survives an upstream refused/failed state.

Return a tuple of `ValidationIssue(code, claim_index, detail)`; do not raise for an ordinary invalid draft.

Define the scoring boundary in `ports.py` so the validator does not depend on a concrete GTE class:

```python
@runtime_checkable
class ClaimSupportScorerPort(Protocol):
    async def score(
        self, claim: str, support_texts: tuple[str, ...]
    ) -> tuple[float, ...]:
        raise NotImplementedError
```

- [ ] **Step 6: Implement one-repair `RagService.ask`**

```python
async def ask(self, question: str) -> RagResult:
    candidates = await self._retriever.retrieve(question, self._retrieve_k)
    evidence = await self._reranker.rerank(question, candidates, self._rerank_k)
    decision = self._gate.decide(evidence)
    if not decision.allowed:
        return self._refusal(decision.reason or "insufficient_evidence")

    draft = await self._generator.generate_answer(question, decision.evidence)
    issues = await self._validator.validate(draft, decision.evidence, ())
    if not issues:
        return self._answered(draft, decision.evidence)

    repaired = await self._generator.repair_answer(
        question, decision.evidence, draft, issues
    )
    repaired_issues = await self._validator.validate(repaired, decision.evidence, ())
    if repaired_issues:
        return self._refusal("grounding_validation_failed")
    return self._answered(repaired, decision.evidence)
```

Wrap each call in trace spans and map typed exceptions to `failed`, while insufficient evidence and invalid final grounding map to `refused`.

- [ ] **Step 7: Mark every fake result and trace explicitly**

`tests/fakes.py` provides scripted Retriever, Reranker, Generator, support scorer and clock. Every fake exposes `backend_kind="fake"`, and the service trace carries that field so a fake test report cannot be mistaken for live acceptance.

- [ ] **Step 8: Run the full offline RAG safety suite and commit**

```powershell
rtk uv run pytest tests/unit/test_grounding.py tests/unit/test_generation.py tests/integration/test_rag_service.py -v
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag tests
rtk git commit -m "feat: enforce evidence gate and grounding repair loop"
```

## Task 9: Deliver the first complete fake-backed CLI slice

**Files:**
- Modify: `src/enterprise_rag/cli.py`
- Create: `src/enterprise_rag/cli_rendering.py`
- Create: `src/enterprise_rag/application.py`
- Modify: `src/enterprise_rag/__main__.py`
- Create: `tests/integration/test_cli.py`

- [ ] **Step 1: Write failing CLI tests for command shape and exit codes**

```python
def test_rag_ask_json_contains_result_and_trace(capsys) -> None:
    exit_code = main([
        "rag", "ask", "P4 上海住宿标准是多少？",
        "--generator", "local", "--format", "json", "--trace",
    ], application=FakeApplication())
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "answered"
    assert payload["trace"][0]["stage"] == "retrieval"
```

Cover `kb build`, `rag ask`, `agent run`, `eval run`, `doctor`; invalid arguments exit 2, operational failure exits 1, normal refusal exits 0 with `status=refused`.

- [ ] **Step 2: Confirm the CLI tests fail**

```powershell
rtk uv run pytest tests/integration/test_cli.py -v
```

Expected: FAIL because only the temporary help parser exists.

- [ ] **Step 3: Build nested argparse commands without business decisions**

```text
rag-agent kb build PATH
rag-agent rag ask QUESTION --generator local|online [--provider NAME]
rag-agent agent run TASK --generator local|online [--provider NAME]
rag-agent eval run --layer retriever|reranker|rag|agent|all
rag-agent doctor
```

All answer commands accept `--trace` and `--format text|json`. Add `--fallback-local` only to online generation; its absence must remain distinguishable from `False` in the application request.

- [ ] **Step 4: Implement rendering as a pure adapter**

`cli_rendering.py` accepts only domain results and returns strings. Human output includes status, answer/refusal/error, sources and total duration; detailed trace is opt-in. JSON output uses stable keys and serializes all trace events. Neither renderer calls a model, filters sources, chooses a tool or changes a result status.

- [ ] **Step 5: Add an `Application` shell with injected factories**

```python
@dataclass(slots=True)
class Application:
    rag_service: RagService
    agent_service: AgentService | None
    index_builder: IndexBuilder
    evaluator: Evaluator | None
    diagnostics: Diagnostics
```

Use fake composition only in tests. Production composition raises a clear configuration error until the real adapters are completed; it must not silently use fakes.

- [ ] **Step 6: Verify the CLI slice and commit**

```powershell
rtk uv run pytest tests/integration/test_cli.py -v
rtk uv run python -m enterprise_rag --help
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag tests/integration
rtk git commit -m "feat: add cli command surface and structured rendering"
```

## Task 10: Prepare the model manifest and pause for explicit download approval

**Files:**
- Create: `docs/model-manifest.md`
- Create: `scripts/check_model_manifest.py`
- Create: `tests/unit/test_model_manifest.py`
- Modify: `src/enterprise_rag/config.py`

- [ ] **Step 1: Write a failing manifest completeness test**

```python
def test_every_model_entry_has_supply_chain_fields() -> None:
    entries = parse_manifest(Path("docs/model-manifest.md"))
    for entry in entries:
        assert len(entry.revision) == 40
        assert len(entry.sha256) == 64
        assert entry.license
        assert entry.expected_bytes > 0
        assert entry.cache_path.is_relative_to(Path(".local/model-cache"))
```

- [ ] **Step 2: Resolve metadata only; do not fetch binary weights**

Use Hugging Face metadata/API calls to resolve full 40-character revisions and file metadata. Record these already verified design inputs:

| Artifact | Pinned starting point | Approx. size | License / known digest |
|---|---|---:|---|
| `Alibaba-NLP/gte-multilingual-base` | `087a024525fd6e2fe749cb4679d218d8bcc95bdd` | repo ~629 MB | Apache-2.0; `model.safetensors` SHA-256 `f5a35a10faa54da7717870af1517c9b41e9bd8e3880bc5a8e9363d4c3c63e9b0` |
| `Alibaba-NLP/gte-multilingual-reranker-base` | `0acb790751daef0c1a7c5191122056bd16ef9f09` | repo ~629 MB | Apache-2.0; weight SHA-256 `10ebaa49322dd7e01a13a91c49810939e3f91f231aceaa47fdf0cab3083954f6` |
| `Alibaba-NLP/new-impl` remote code | current verified head begins `40ced75` | small code repo | Apache-2.0; resolve full SHA and audit files |
| `Qwen/Qwen3-4B-GGUF/Qwen3-4B-Q5_K_M.gguf` | repository head begins `bc64014` | 2.89 GB | Apache-2.0; SHA-256 `aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f` |

Expected combined model data is about 4.2 GB before library caches, under the 12 GB target and 15 GB hard cap.

- [ ] **Step 3: Audit pinned remote code before authorizing `trust_remote_code`**

Review every Python file referenced by both model `auto_map` entries at the pinned `new-impl` revision. Record file SHA-256 values, imports, filesystem/network behavior and license. The acceptance condition is: inference-only PyTorch/Transformers code, no subprocess, no network calls, no file writes outside normal model loading, no dynamic package installation.

- [ ] **Step 4: Verify manifest consistency without downloading models**

Implement `parse_manifest(path)` in `scripts/check_model_manifest.py`; it parses a fenced JSON manifest block embedded in the Markdown and returns typed entries. The CLI checks 40-character revisions, 64-character digests, unique cache paths, total expected bytes and metadata-only reachability without writing model files.

After resolving metadata, replace the short audited heads in `AppConfig` with the same full 40-character model, code and GGUF repository revisions recorded in the manifest; tests assert exact equality between config and manifest.

```powershell
rtk uv run pytest tests/unit/test_model_manifest.py -v
rtk uv run python scripts/check_model_manifest.py --metadata-only
```

Expected: PASS, full revisions and expected files printed, zero weight files under `.local/model-cache`.

- [ ] **Step 5: Commit the audited manifest and stop execution**

```powershell
rtk git add docs/model-manifest.md scripts/check_model_manifest.py tests/unit/test_model_manifest.py
rtk git commit -m "docs: pin and audit local model artifacts"
```

Then report to the user, in one message, the exact model names, revisions, file digests, licenses, source URLs, expected download bytes, total disk impact and cache path. Wait for explicit approval. Approval of this implementation plan alone is not download approval.

## Task 11: Implement and accept the real GTE hybrid Retriever

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/enterprise_rag/retrieval.py`
- Modify: `src/enterprise_rag/application.py`
- Create: `tests/unit/test_gte_encoder_adapter.py`
- Create: `tests/live/test_gte_retriever_live.py`

- [ ] **Step 1: After model approval, declare and install the retrieval extra**

Add the first optional dependency group to `pyproject.toml` immediately before the
approved sync:

```toml
[project.optional-dependencies]
retrieval = [
  "numpy==2.3.3",
  "sentence-transformers==6.1.0",
]
```

Then install it and download only the pinned snapshots:

```powershell
rtk uv sync --group dev --extra retrieval
```

Download the two approved GTE snapshots and the audited remote-code snapshot into the manifest cache directory using exact revisions and include-pattern allowlists. Verify every digest immediately; delete/quarantine only the mismatching artifact, not the cache root.

- [ ] **Step 2: Write adapter tests with injected tokenizer/model doubles**

```python
@pytest.mark.asyncio
async def test_gte_encoder_returns_dense_and_sparse_from_one_forward_pass() -> None:
    encoder = GteHybridEncoder(tokenizer=FakeTokenizer(), model=FakeTokenModel())
    batch = await encoder.encode(("政策文本",))
    assert batch.dense == ((0.6, 0.8),)
    assert batch.sparse == (((101, 1.5), (202, 0.5)),)
```

Assert special/padding token IDs are removed, repeated token IDs keep their maximum positive ReLU weight, dense rows are L2-normalized, and CPU float16 is never selected.

- [ ] **Step 3: Confirm adapter tests fail**

```powershell
rtk uv run pytest tests/unit/test_gte_encoder_adapter.py -v
```

Expected: FAIL because `GteHybridEncoder` is absent.

- [ ] **Step 4: Implement the audited hybrid encoder**

Use `AutoTokenizer.from_pretrained` and `AutoModelForTokenClassification.from_pretrained` with `revision=config.embedding_revision`, `code_revision=config.gte_code_revision`, `trust_remote_code=True`, `local_files_only=True`, and the configured cache. One forward pass yields:

```python
dense = normalize(output.last_hidden_state[:, 0, :], p=2, dim=1)
weights = relu(output.logits).squeeze(-1)
```

Convert positive non-special token weights into sorted `(token_id, max_weight)` tuples. Never call `from_pretrained` without `local_files_only=True` at runtime.

- [ ] **Step 5: Build the real index and run live Retriever acceptance**

```powershell
rtk uv run rag-agent kb build data/sample_docs
rtk uv run pytest tests/live/test_gte_retriever_live.py -m live -v
```

Expected: index manifest says `backend_kind=real`; the canonical exact-ID, paraphrase and Chinese/English questions retrieve the expected chunk within Top 20; live tests report model revisions.

- [ ] **Step 6: Re-run offline tests and commit**

```powershell
rtk uv run pytest
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add pyproject.toml uv.lock src tests/live tests/unit
rtk git commit -m "feat: add real multilingual hybrid retriever"
```

## Task 12: Implement and accept the real multilingual Reranker

**Files:**
- Modify: `src/enterprise_rag/reranking.py`
- Modify: `src/enterprise_rag/grounding.py`
- Modify: `src/enterprise_rag/application.py`
- Create: `tests/unit/test_gte_reranker_adapter.py`
- Create: `tests/live/test_gte_reranker_live.py`

- [ ] **Step 1: Write failing adapter tests for ordering, truncation and stable ties**

```python
@pytest.mark.asyncio
async def test_reranker_orders_by_score_then_chunk_id() -> None:
    reranker = GteReranker(tokenizer=FakePairTokenizer(), model=FakeSequenceModel([0.5, 0.5]))
    result = await reranker.rerank("问题", candidates_b_then_a(), top_n=2)
    assert [item.candidate.chunk_id for item in result] == ["a", "b"]
    assert [item.rerank_rank for item in result] == [1, 2]
```

Also test `top_n > len(candidates)`, invalid bounds, non-finite logits, configured 512-token truncation and typed exception mapping.

- [ ] **Step 2: Confirm failure, then implement the adapter**

Load `AutoModelForSequenceClassification` and tokenizer with the pinned model/code revisions, `trust_remote_code=True`, `local_files_only=True`, and `model.eval()`. Batch pairs, use `max_length=512`, preserve original candidate fields, and sort by `(-score, chunk_id)`.

Expose the same loaded model through `GteClaimSupportScorer`, which implements `ClaimSupportScorerPort.score` for `(claim, cited support)` pairs. It returns one finite score per support and shares the model instance; it does not load a second Reranker.

- [ ] **Step 3: Run live Reranker acceptance**

```powershell
rtk uv run pytest tests/live/test_gte_reranker_live.py -m live -v
```

Expected: relevant policy chunks are in Top 5 for the acceptance queries and scores/ranks are finite, stable and traceable.

- [ ] **Step 4: Verify no bypass and commit**

```powershell
rtk uv run pytest tests/integration/test_rag_service.py tests/unit/test_gte_reranker_adapter.py -v
rtk uv run pytest
rtk git add src/enterprise_rag tests
rtk git commit -m "feat: add real multilingual reranking"
```

## Task 13: Implement and accept the local Qwen3 structured Generator

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/enterprise_rag/generation.py`
- Modify: `src/enterprise_rag/application.py`
- Create: `tests/unit/test_llama_cpp_generator.py`
- Create: `tests/live/test_local_generator_live.py`

- [ ] **Step 1: Declare and install the approved runtime without downloading a model implicitly**

Add this key to the existing `[project.optional-dependencies]` table immediately
before the approved sync:

```toml
local-generator = ["llama-cpp-python==0.3.36"]
```

For the RTX 4060 and Python 3.12, prefer the official prebuilt CUDA 12.4 wheel index; verify installed version `0.3.36` and import before touching the GGUF:

```powershell
rtk uv sync --group dev --extra local-generator --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
rtk uv run python -c "import llama_cpp; print(llama_cpp.__version__)"
```

Expected: `0.3.36`. If the wheel is incompatible with the installed driver/runtime, stop and report diagnostics; do not silently build another backend or download a different model.

- [ ] **Step 2: Write failing unit tests using a fake `Llama` object**

Assert the adapter sends `temperature=0`, `seed` fixed, `response_format={"type":"json_object","schema":ANSWER_SCHEMA}`, non-thinking prompt instructions, maximum output tokens, and returns a typed protocol error when content is missing or invalid JSON.

- [ ] **Step 3: Implement `LlamaCppGenerator`**

```python
self._llm = Llama(
    model_path=str(config.local_model_path),
    n_ctx=8192,
    n_gpu_layers=-1,
    seed=42,
    verbose=False,
)

response = await asyncio.to_thread(
    self._llm.create_chat_completion,
    messages=list(request.messages),
    response_format={"type": "json_object", "schema": request.schema},
    temperature=0.0,
    max_tokens=request.max_output_tokens,
)
```

All answer and action prompts append Qwen's `/no_think` control and instruct the model to return only the schema object; hidden reasoning is neither requested nor persisted.

The adapter never calls `from_pretrained`; the approved GGUF must already exist at the exact configured path and match the manifest digest. Missing/mismatched files raise typed local-model errors before loading.

- [ ] **Step 4: Run the local live test**

```powershell
rtk uv run pytest tests/live/test_local_generator_live.py -m live -v
```

Expected: Qwen returns schema-valid Chinese output for one evidence set and one repair request; reported model SHA matches the manifest; no network socket is opened.

- [ ] **Step 5: Measure load and one generation, then commit**

Record cold load, peak RAM/VRAM and generation time in a machine-local benchmark artifact ignored by Git. Fail the acceptance test if the model file is not `Qwen3-4B-Q5_K_M.gguf` or its SHA differs.

```powershell
rtk uv run pytest
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add pyproject.toml uv.lock src/enterprise_rag tests
rtk git commit -m "feat: add local qwen structured generator"
```

## Task 14: Compose and accept the complete real RAG pipeline

**Files:**
- Modify: `src/enterprise_rag/application.py`
- Modify: `src/enterprise_rag/cli.py`
- Create: `tests/integration/test_real_composition.py`
- Create: `tests/live/test_rag_live.py`

- [ ] **Step 1: Write a failing composition test**

```python
def test_production_composition_never_constructs_a_fake(tmp_path) -> None:
    app = build_application(real_config(tmp_path))
    assert app.rag_service.backend_kinds == ("real", "real", "real")
```

Use injected adapter factories in the unit test; assert missing index/model paths fail closed.

- [ ] **Step 2: Implement one-load lifecycle composition**

`build_application` loads the index, GTE encoder, GTE reranker and Qwen runtime once, then injects them into `RagService`. Cache these resources inside the application instance, not module globals. CLI constructs one application per process and reuses it for the command.

- [ ] **Step 3: Run the three core live scenarios**

```powershell
rtk uv run rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --trace
rtk uv run rag-agent rag ask "没有任何制度依据的问题" --generator local --trace
rtk uv run pytest tests/live/test_rag_live.py -m live -v
```

Expected: first query answers with valid policy chunk IDs; second query is refused before generation; live test includes one deliberately invalid first draft and proves exactly one repair or safe refusal.

- [ ] **Step 4: Verify all offline paths and commit**

```powershell
rtk uv run pytest
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag tests
rtk git commit -m "feat: compose end-to-end grounded rag"
```

## Task 15: Implement the three read-only business tools

**Files:**
- Create: `src/enterprise_rag/agent_tools.py`
- Create: `tests/unit/test_agent_tools.py`
- Create: `tests/agent/test_tool_failures.py`

- [ ] **Step 1: Write failing tests for allowlist, validation and immutable Observations**

```python
@pytest.mark.asyncio
async def test_claim_tool_returns_stable_observation_id(registry) -> None:
    observation = await registry.call(
        "get_expense_claim", {"claim_id": "CLAIM-1024"}
    )
    assert observation.observation_id == "obs:get_expense_claim:CLAIM-1024"
    assert observation.payload["rejection_code"] == "HOTEL_OVER_LIMIT"
```

Also test unknown tools, unexpected arguments, malformed IDs, not-found records, corrupted JSON, duplicate record IDs and attempts to pass paths, URLs or nested objects.

- [ ] **Step 2: Confirm failures**

```powershell
rtk uv run pytest tests/unit/test_agent_tools.py tests/agent/test_tool_failures.py -v
```

Expected: FAIL because the registry and tools are absent.

- [ ] **Step 3: Implement strict argument schemas and record repositories**

Accept only these regex-constrained payloads:

```text
get_employee_profile: {"employee_id": "EMP-[0-9]{3}"}
get_expense_claim:    {"claim_id": "CLAIM-[0-9]{4}"}
get_business_trip:    {"trip_id": "TRIP-[0-9]{4}-[0-9]{3}"}
```

Load each JSON file once, verify unique IDs and foreign keys, and expose immutable mappings. No tool receives a filesystem path from the model.

- [ ] **Step 4: Implement an allowlisted registry**

```python
class ToolRegistry:
    async def call(self, name: str, arguments: Mapping[str, object]) -> Observation:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        validated = tool.validate(arguments)
        return await tool.execute(validated)
```

Observation payloads contain only the requested fictional record and `status="found"|"not_found"`; tool exceptions map to typed categories and sanitized trace attributes.

- [ ] **Step 5: Verify no side effects and commit**

```powershell
rtk uv run pytest tests/unit/test_agent_tools.py tests/agent/test_tool_failures.py -v
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag/agent_tools.py tests
rtk git commit -m "feat: add allowlisted read-only business tools"
```

## Task 16: Implement the bounded Plan–Act–Observe Agent

**Files:**
- Create: `src/enterprise_rag/agent_service.py`
- Modify: `src/enterprise_rag/generation.py`
- Modify: `src/enterprise_rag/application.py`
- Create: `tests/agent/test_agent_service.py`
- Create: `tests/agent/test_agent_grounding.py`
- Create: `tests/integration/test_agent_cli.py`

- [ ] **Step 1: Write failing loop-control tests**

```python
@pytest.mark.asyncio
async def test_agent_uses_knowledge_and_two_fact_tools_then_finishes() -> None:
    generator = ScriptedGenerator([
        action("search_knowledge", question="P4 上海住宿上限"),
        action("get_expense_claim", claim_id="CLAIM-1024"),
        action("get_business_trip", trip_id="TRIP-2409-017"),
        finish(valid_grounded_draft()),
    ])
    result = await make_agent(generator=generator).run("解释 CLAIM-1024")
    assert result.status == "answered"
    assert [call.name for call in result.tool_trace] == [
        "search_knowledge", "get_expense_claim", "get_business_trip"
    ]
```

Add tests for zero business tools, all three business tools, duplicate action, malformed arguments, unknown action, not-found observation, tool crash, RAG refusal, finish without knowledge evidence, and the 6-action limit.

- [ ] **Step 2: Confirm the Agent tests fail**

```powershell
rtk uv run pytest tests/agent/test_agent_service.py tests/agent/test_agent_grounding.py -v
```

Expected: FAIL because the loop is absent.

- [ ] **Step 3: Define the exact action schema**

```json
{
  "oneOf": [
    {"type":"object","required":["action","question"],"properties":{"action":{"const":"search_knowledge"},"question":{"type":"string"}},"additionalProperties":false},
    {"type":"object","required":["action","employee_id"],"properties":{"action":{"const":"get_employee_profile"},"employee_id":{"type":"string"}},"additionalProperties":false},
    {"type":"object","required":["action","claim_id"],"properties":{"action":{"const":"get_expense_claim"},"claim_id":{"type":"string"}},"additionalProperties":false},
    {"type":"object","required":["action","trip_id"],"properties":{"action":{"const":"get_business_trip"},"trip_id":{"type":"string"}},"additionalProperties":false},
    {"type":"object","required":["action","draft"],"properties":{"action":{"const":"finish"},"draft":{"type":"object"}},"additionalProperties":false},
    {"type":"object","required":["action","reason"],"properties":{"action":{"const":"refuse"},"reason":{"type":"string"}},"additionalProperties":false}
  ]
}
```

- [ ] **Step 4: Implement the bounded loop**

```python
for step in range(1, self._max_actions + 1):
    action = await self._planner.next_action(task, observations)
    fingerprint = action.fingerprint()
    if fingerprint in seen:
        return self._refused("duplicate_action")
    seen.add(fingerprint)

    if action.name == "search_knowledge":
        rag_result = await self._rag.ask(action.arguments["question"])
        observations.append(observation_from_rag(rag_result))
    elif action.name in BUSINESS_TOOLS:
        observations.append(await self._tools.call(action.name, action.arguments))
    elif action.name == "refuse":
        return self._refused("agent_requested_refusal")
    elif action.name == "finish":
        return await self._finish(action.draft, observations)
return self._refused("iteration_limit")
```

Do not expose planner prose or chain-of-thought. Trace only the action name, sanitized validated arguments, Observation ID/status, duration and typed failure.

- [ ] **Step 5: Enforce final grounding across knowledge and tool facts**

`_finish` requires at least one successful `search_knowledge` result before any policy conclusion. Build an allowed support map from RAG chunk IDs and tool Observation IDs, then run the same Grounding Validator and at most one repair. If RAG refused, its refusal remains visible and tools alone cannot invent a policy rule.

- [ ] **Step 6: Wire `agent run` and verify JSON/text output**

```powershell
rtk uv run pytest tests/agent tests/integration/test_agent_cli.py -v
rtk uv run rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --trace
```

Expected under fake composition in automated tests: correct action sequence and cited Observations. Production command is exercised in the live task after real composition.

- [ ] **Step 7: Run all offline checks and commit**

```powershell
rtk uv run pytest
rtk uv run ruff check src tests
rtk uv run ty check src
rtk git add src/enterprise_rag tests
rtk git commit -m "feat: add bounded grounded agent loop"
```

## Task 17: Add the online Generator adapter without making a real call

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/enterprise_rag/generation.py`
- Modify: `src/enterprise_rag/application.py`
- Modify: `src/enterprise_rag/cli.py`
- Create: `tests/unit/test_online_generator.py`
- Create: `tests/integration/test_generator_selection.py`

- [ ] **Step 1: Declare and install only the SDK extra after plan approval**

Add this key to the existing `[project.optional-dependencies]` table immediately
before the approved sync:

```toml
online-generator = ["openai==2.54.0"]
```

```powershell
rtk uv sync --group dev --extra online-generator
```

This installs code only and does not authorize or perform an API call.

- [ ] **Step 2: Write failing tests with an injected fake OpenAI-compatible client**

Test that the adapter uses only a loaded Provider Profile, reads the key from the named environment variable, sends the configured model ID and JSON schema, sets `max_retries=0`, enforces timeout/request/budget caps, and redacts the key/base URL credentials from trace and exceptions.

- [ ] **Step 3: Implement the adapter and exception mapping**

```python
client = AsyncOpenAI(
    api_key=api_key,
    base_url=profile.base_url,
    timeout=config.online_timeout_seconds,
    max_retries=0,
)
response = await client.chat.completions.create(
    model=profile.model_id,
    messages=list(request.messages),
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "enterprise_rag_output",
            "strict": True,
            "schema": request.schema,
        },
    },
    temperature=0,
)
```

Map authentication, permission, balance/quota, rate-limit, timeout, unavailable, invalid-request and protocol failures separately. If a relay does not implement `json_schema`, treat that as a protocol failure; do not silently accept unconstrained text.

- [ ] **Step 4: Implement explicit-only local fallback**

`--generator online` without `--fallback-local` returns the online failure. With the flag, the application may invoke the local Generator once, emit a `degraded` backend-switch trace event, and continue through the same Grounding Validator. No other stage can be skipped.

- [ ] **Step 5: Verify no network calls and commit**

```powershell
rtk uv run pytest tests/unit/test_online_generator.py tests/integration/test_generator_selection.py -v
rtk uv run pytest
rtk git add pyproject.toml uv.lock src/enterprise_rag tests
rtk git commit -m "feat: add audited online generator profiles"
```

Do not add a real profile or live test until the user supplies a relay and separately confirms provider, endpoint, model claim, privacy terms, prices, request count, estimated cost and hard cap.

## Task 18: Add the 36-case layered evaluation harness

**Files:**
- Create: `evals/knowledge.jsonl`
- Create: `evals/agent.jsonl`
- Create: `src/enterprise_rag/evaluation.py`
- Modify: `src/enterprise_rag/cli.py`
- Create: `tests/unit/test_evaluation.py`
- Create: `tests/integration/test_eval_cli.py`

- [ ] **Step 1: Write failing metric tests**

```python
def test_recall_at_20_counts_answerable_cases_only() -> None:
    cases = (
        knowledge_case("k1", answerable=True, expected=("c1",)),
        knowledge_case("k2", answerable=False, expected=()),
    )
    report = score_retriever(cases, {"k1": ("c1",), "k2": ()}, k=20)
    assert report.numerator == 1
    assert report.denominator == 1
```

Test conditional Reranker Hit@5, grounded-answer correctness, no-answer rejection, citation validity, Agent tool/argument accuracy, forbidden-action count, failure-category accuracy and p50/p95 latency.

- [ ] **Step 2: Create 24 exact knowledge cases**

Use IDs `K01`–`K24` with this fixed distribution:

```text
K01-K04 direct Chinese policy questions
K05-K08 paraphrases
K09-K12 Chinese questions containing English policy/expense terms
K13-K16 exact policy IDs, amounts or grade/city terms
K17-K18 distractor-heavy answerable questions
K19-K24 unanswerable or missing-evidence questions
```

Each JSONL row contains `case_id`, `question`, `answerable`, `expected_chunk_ids`, `required_facts`, `forbidden_facts`. The six no-answer rows have empty expected chunks and explicit refusal reason `insufficient_evidence`.

- [ ] **Step 3: Create 12 exact Agent cases**

Use IDs `A01`–`A12`:

```text
A01-A02 knowledge search with zero business tools
A03-A04 one business tool
A05-A08 two or three business tools, including CLAIM-1024
A09 invalid employee/claim/trip ID
A10 tool not found/corrupt fixture
A11 scripted duplicate action
A12 scripted iteration limit
```

Each row contains expected ordered business tool names, exact validated arguments, expected final status, required support IDs and expected error/refusal category.

- [ ] **Step 4: Implement layer runners and an honest report**

`Evaluator` runs `retriever`, `reranker`, `rag`, `agent` or `all`. Every report includes backend kind, model IDs/revisions, corpus digest, case count, raw numerator/denominator, latency distribution and timestamp. Refuse to label a report `real` if any measured layer uses a fake.

- [ ] **Step 5: Verify the fake harness, then run real acceptance**

```powershell
rtk uv run pytest tests/unit/test_evaluation.py tests/integration/test_eval_cli.py -v
rtk uv run rag-agent eval run --layer all --format json
```

After real models are approved and installed:

```powershell
rtk uv run rag-agent eval run --layer retriever
rtk uv run rag-agent eval run --layer reranker
rtk uv run rag-agent eval run --layer rag
rtk uv run rag-agent eval run --layer agent
```

Expected acceptance: Retriever at least 17/18 Recall@20; conditional Reranker Hit@5 at least 90%; RAG at least 16/18 grounded correct; no-answer 6/6 refused; valid citations 100%; Agent at least 11/12 correct tools/arguments; unauthorized action/write/loop/secret leak 0; planned failure categories 100% correct.

- [ ] **Step 6: Commit evaluation data and runner**

```powershell
rtk git add evals src/enterprise_rag/evaluation.py src/enterprise_rag/cli.py tests
rtk git commit -m "feat: add layered rag and agent evaluation"
```

## Task 19: Finish diagnostics, documentation and the interview demo

**Files:**
- Create: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/demo-script.md`
- Create: `docs/evaluation.md`
- Create: `docs/security-and-cost.md`
- Modify: `docs/model-manifest.md`
- Create: `docs/troubleshooting.md`
- Modify: `src/enterprise_rag/application.py`
- Modify: `src/enterprise_rag/cli.py`
- Create: `tests/integration/test_doctor.py`
- Create: `tests/live/test_demo_scenarios_live.py`

- [ ] **Step 1: Write failing `doctor` tests**

Assert `doctor` reports Python, disk, RAM, CUDA visibility, model/index presence and digest, provider-profile status, network disabled/enabled state, but never prints environment values for key variables.

- [ ] **Step 2: Implement deterministic diagnostics**

Each check returns `pass`, `warn` or `fail`, a public message and remediation. Missing real models are `fail` for production composition but do not break default unit tests. High memory pressure is a warning that instructs the presenter to close browsers/virtual-display software before the demo.

- [ ] **Step 3: Write the documentation against actual commands and results**

`README.md` contains a 5-minute offline quick start, explicit model-approval step, command examples and project scope. Architecture documents the fixed chain and event tree. Security/cost documents the relay preflight and synthetic-data-only rule. Troubleshooting includes Windows CUDA wheel, cache, index digest, memory pressure and refusal/error distinctions.

- [ ] **Step 4: Write the three-act demo script**

Use these commands:

```powershell
rtk uv run rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --trace
rtk uv run rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --trace
rtk uv run rag-agent rag ask "制度是否允许员工报销宠物寄养费？" --generator local --trace
rtk uv run rag-agent eval run --layer all
```

Provide an 8–10 minute narration, a 5-minute compressed sequence, and fallback screenshots/JSON artifacts that contain only fictional data.

- [ ] **Step 5: Run each official scenario three times**

```powershell
rtk uv run pytest tests/live/test_demo_scenarios_live.py -m live -v
```

Expected: three consecutive runs per scenario have semantically consistent conclusions, valid citations, no unauthorized action and within-budget latency.

- [ ] **Step 6: Measure the completion budget on the target laptop**

Record model bytes, cold start, peak RAM/VRAM and standard-query p50/p95. Acceptance: model files no more than 15 GB, cold start no more than 60 seconds, standard-query p95 no more than 45 seconds. If a metric fails, report it honestly and optimize within the approved model set; do not substitute an unapproved model.

- [ ] **Step 7: Run final verification**

```powershell
rtk uv run pytest
rtk uv run ruff check .
rtk uv run ty check src
rtk uv build
rtk git status --short
```

Expected: default offline suite PASS with live tests deselected; lint/type/build exit 0; only intentional documentation/benchmark result changes are present.

- [ ] **Step 8: Commit the interview-ready CLI**

```powershell
rtk git add README.md docs src tests evals data pyproject.toml uv.lock
rtk git commit -m "docs: complete interview demo and acceptance evidence"
```

## Task 20: Final review and panel-plan handoff

**Files:**
- Modify if verification proves necessary: `docs/evaluation.md`
- Create: `docs/panel-handoff.md`

- [ ] **Step 1: Review the branch diff against the initial commit**

```powershell
rtk git log --oneline --decorate
rtk git diff --stat implementation-start..HEAD
rtk git status --short
```

Expected: small task-oriented commits, clean working tree, no model/cache/index/secret files tracked.

- [ ] **Step 2: Verify every design acceptance item has evidence**

Create a table mapping: independent RAG, hybrid retrieval, reranking, Gate, one-repair validation, Agent constraints, offline tests, real backends, metrics, performance and demo stability to the exact test/report/command that proves it.

- [ ] **Step 3: Write the panel handoff without choosing an unapproved framework**

`docs/panel-handoff.md` records the approved pipeline-workbench layout, fixed right inspector, shared `Application` API, trace contract and the requirement for same-process service calls. It states that the panel framework must be selected in a short follow-up design decision after CLI acceptance.

- [ ] **Step 4: Request user review before any panel implementation, push, publish or deployment**

Report the CLI completion evidence, remaining live-relay decision and panel design checkpoint. Do not push, publish, deploy or contact an external service without separate authorization.

## Implementation checkpoints

Execution must pause for user input at exactly these material boundaries:

1. **After Task 10:** approve exact model downloads and cache location.
2. **Before any real online call:** approve relay vendor, claimed model, endpoint, terms, prices, request count, estimated cost and hard cap.
3. **After Task 19:** review measured acceptance evidence and decide whether to tune within the approved models.
4. **After Task 20:** select the future panel framework, then write and approve its separate implementation plan.

Approving this plan authorizes repository/code/test/document work and ordinary dependency installation during execution. It does not authorize model downloads, live API calls, pushing, publishing, deployment or destructive operations.
