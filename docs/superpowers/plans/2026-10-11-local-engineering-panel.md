# Local Engineering Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Plan version: v2 (2026-10-11).** v1 was committed as `83e6f94`; Tasks 1–2 of it are already committed as `ac88b1c` and `456766d`, so execution resumes at Task 3. The user approved the following amendments before resuming. (a) The blanket "no real online provider" boundary is replaced by Task 16's single explicitly authorized real-online run. (b) Task 12 gains a NiceGUI test-harness step (Step 0) and replaces the clickable Mermaid diagram with a controlled node chain. (c) `ui.run` must disable `reload`. (d) The `uv` install command documented in Task 14 is settled by measurement rather than assumed. (e) Task 15's cold-start ceiling binds the runtime-ready measurement. (f) Every task now ends by updating `docs/handoffs/2026-10-11-panel-execution-progress.md` in the same commit as its code.

**Goal:** Add a localhost-only NiceGUI engineering panel that reuses the existing enterprise RAG + Agent core, exposes truthful live pipeline/Agent traces, supports safe local or explicitly authorized online generation, and remains independently usable through the existing CLI.

**Architecture:** Keep one Python process and one business core. A panel-only `SharedRuntime` owns the heavy index, encoder, retriever, reranker, validator, tools, cached generators, and keyed `ApplicationTemplate` objects; each accepted Run binds a fresh one-use authorization, trace observer, and cancellation token to a cached template. A pure `PanelController` turns a preflighted `RunRequest` into an immutable `RunSpec` and submits it to a single-task `RunCoordinator`, while service-level trace observers feed a bounded progress buffer and the completed `Result + RunTrace` remains authoritative. NiceGUI renders view models only: it never decides evidence sufficiency, tool choice, source filtering, repair, fallback, or failure semantics.

**Tech Stack:** Python 3.12, existing frozen dataclasses/Protocols/async services, NiceGUI in browser mode after the mandatory exact-version approval gate, asyncio, pytest/pytest-asyncio, Ruff, ty, uv, existing GTE retrieval/reranking and llama.cpp/OpenAI-compatible generator adapters.

---

## Execution boundaries

- Execute in the current independent repository. Do not use a worktree, submodule, symlink, `PYTHONPATH`, or editable dependency on the source archive.
- Every shell command starts with `rtk`; PowerShell commands use `rtk proxy powershell -NoProfile -Command '<literal PowerShell statement>'`.
- The user must approve this plan before any production/test code is changed.
- Task 1 (the dependency gate) is **already complete** as commit `ac88b1c`; Task 2 is complete as `456766d`. Do not repeat them, and do not re-resolve or re-sync dependencies: see the `uv` constraint below.
- Do not download or replace any model. Existing approved local artifacts may be read only during the separately selected live acceptance task.
- **Online boundary (v2):** Tasks 3–15 contract-test online behavior with fake clients only. **Task 16 is the single authorized exception** — one real-online acceptance run against the registered relay profile in `config/providers.toml`, limited to three scenarios × one round, at most 30 provider requests and a CNY 10 ceiling, over synthetic sample data only. The user's API key is never read, echoed, or written by any task. No other real provider call is permitted.
- Default tests remain deterministic, offline, keyless, and free. Fake-backed output must never be labelled real-local evidence.
- Do not format unrelated historical files. Run format checks only on files created or modified by this plan.
- Each implementation task follows red-green-refactor and ends in a small commit. Do not carry a failing test into the next task. Commit messages end with the attribution line `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- **Every task ends by updating `docs/handoffs/2026-10-11-panel-execution-progress.md` sections 2, 3, 4, and 9, in the same commit as that task's code.** That file is the durable progress record; conversation history is not.
- **`uv` constraint:** `uv lock` / `uv sync` stall for hours on this machine whenever they need to re-fetch the `llama-cpp-python` GitHub-release wheel (see `.local/` and `docs/handoffs/2026-10-11-panel-execution-progress.md` §7). Use `uv lock --check --offline` for verification, and never run a bare `uv sync --locked`. Task 1's installation is already done; do not re-run it.

## Approval gates

1. **Plan gate:** satisfied — v1 approved and committed; v2 amendments approved by the user before execution resumed at Task 3.
2. **NiceGUI metadata/install gate:** satisfied — `nicegui==3.18.0` is pinned, locked, installed, and recorded in `docs/dependency-preflight/nicegui.json` (commit `ac88b1c`). Do not re-run it.
3. **Real-local acceptance gate (Task 15):** the user has explicitly chosen to be asked here rather than to pre-authorize. Stop and report the allowlisted model identity/revision/digest, the no-download confirmation, the network hard-block, the nine intended runs, the estimated duration, and the acceptance ceilings; run nothing until the user approves.
4. **Real-online gate (Task 16):** satisfied for exactly one run. The user approved provider (third-party relay), model, endpoint, terms URL, call cap (30 requests), and cost ceiling (CNY 10) on 2026-10-11; the values live in the gitignored `config/providers.toml` and are summarized in `docs/handoffs/2026-10-11-panel-execution-progress.md` §5. Task 16 still requires the user to have set `RAG_AGENT_ONLINE_API_KEY` before it runs. Any second or broader real call needs a fresh authorization.

## File responsibility map

```text
src/enterprise_rag/
  execution_control.py       cooperative cancellation token and RunCancelled
  execution_trace.py         stable event IDs, terminal updates, safe observer hook
  ports.py                   traced Retriever call contract
  retrieval.py               real dense/sparse/RRF child trace events
  rag_service.py             traced standalone/nested RAG chain and stage cancellation
  agent_service.py           traced search_knowledge subtree and stage cancellation
  application.py             staged heavy-component builders + keyed ApplicationTemplate
  generation.py              explicit local-backend close and online usage snapshot
  provider_profiles.py       non-secret profile fingerprint and run-level cost preflight
  cli.py                     `rag-agent panel`, `--port`, and `--no-open`
  panel/
    __init__.py              panel package exports only
    contracts.py             RunSpec, authorization, states, records, initialization data
    public_trace.py          queue-entry whitelist, recursive redaction, and size caps
    progress.py              bounded/coalescing progress buffer
    runtime.py               SharedRuntime, GeneratorRegistry, authorized online wrapper
    coordinator.py           global single-run lifecycle, cancellation, last-five history
    projection.py            public whitelist/redaction and immutable view models
    controller.py            presets, form intents, preflight/confirmation, run selection
    app.py                   NiceGUI page composition and localhost lifecycle

tests/
  unit/test_execution_control.py
  unit/test_execution_trace.py
  unit/test_panel_contracts.py
  unit/test_panel_public_trace.py
  unit/test_panel_progress.py
  unit/test_panel_projection.py
  unit/test_panel_controller.py
  unit/test_panel_runtime.py
  integration/test_panel_services.py
  integration/test_panel_initialization.py
  integration/test_panel_coordinator.py
  integration/test_panel_cli.py
  integration/test_panel_network.py
  integration/test_panel_page.py
  live/test_panel_demo_live.py
  unit/test_collect_panel_acceptance.py
  live/test_panel_online_live.py        (Task 16)
  unit/test_collect_panel_online_acceptance.py  (Task 16)

scripts/
  collect_panel_acceptance.py  write structured real-local acceptance evidence
  collect_panel_online_acceptance.py  write the one authorized real-online evidence (Task 16)

docs/
  panel-guide.md               operator tutorial and troubleshooting
  architecture.md              shared-runtime/coordinator/trace boundaries
  demo-script.md               three-scene panel demo plus CLI fallback
  security-and-cost.md         online one-run authorization and privacy boundaries
  evaluation.md                panel acceptance provenance and metrics
  handoffs/
    2026-10-11-panel-execution-progress.md  durable progress record, updated by every task
    2026-10-11-panel-execution-resume-prompt.md  copy-paste prompt for resuming in a new window
  demo-artifacts/
    panel-demo-acceptance.json generated structured acceptance summary
    panel-online-acceptance.json generated structured real-online summary (Task 16)
```

## Shared contracts locked by this plan

The following names are canonical. Later tasks must not invent aliases for them.

```python
from enterprise_rag.application import GeneratorChoice


PanelMode = Literal["rag", "agent"]
PanelTerminalState = Literal[
    "answered", "refused", "blocked", "failed", "cancelled"
]
PanelLifecycleState = Literal[
    "initializing",
    "initialization_failed",
    "ready",
    "authorizing",
    "running",
    "cancelling",
]

@dataclass(frozen=True, slots=True)
class RunRequest:
    run_id: str
    created_at: float
    mode: PanelMode
    scenario_id: str
    question_or_task: str
    generator: GeneratorChoice
    provider_name: str | None
    provider_fingerprint: str | None
    fallback_local: bool

@dataclass(frozen=True, slots=True)
class OnlineRunAuthorization:
    authorization_id: str
    run_id: str
    profile_fingerprint: str
    max_requests: int
    input_token_cap: int
    output_token_cap: int
    max_cost_cny: Decimal
    synthetic_data_only: bool
    fallback_local: bool

@dataclass(frozen=True, slots=True)
class RunSpec:
    run_id: str
    created_at: float
    mode: PanelMode
    scenario_id: str
    question_or_task: str
    generator: GeneratorChoice
    provider_name: str | None
    provider_fingerprint: str | None
    fallback_local: bool
    online_authorization: OnlineRunAuthorization | None

```

`RunRequest` is created on the Run intent before preflight. `RunSpec` is created once after local validation and, for online runs, after confirmation; it copies and validates every request field plus the authorization and is never mutated. `PanelRunRecord` stores the exact request, an optional exact spec, the terminal state, the real result/trace when they exist, the public progress prefix, initialization duration, run duration, and a sanitized public error. BLOCKED has no spec; CANCELLED preserves its spec but does not fabricate a `RagResult`, `AgentResult`, or completed `RunTrace`, and retains the genuine public event prefix plus one coordinator-owned `cancel_boundary` event. Initialization failure is global panel state represented by `InitializationStage`, not a fake run record.

## Task 1: Resolve and approve the exact NiceGUI dependency

**Files:**
- Modify after approval: `pyproject.toml`
- Modify after approval: `uv.lock`
- Create after approval: `docs/dependency-preflight/nicegui.json`

- [ ] **Step 1: Reconfirm the no-mutation baseline**

Run:

```powershell
rtk git status --short
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -c "import importlib.util; print(importlib.util.find_spec(''nicegui''))"'
rtk uv lock --check --offline --no-python-downloads
```

Expected: clean status, `None` for NiceGUI, and a successful offline lock check.

- [ ] **Step 2: Stop and request metadata-only network approval**

Report that NiceGUI is absent and request permission for one dependency-resolution dry run. State explicitly that it may access the package index and write uv cache entries even though it does not install into `.venv`. Do not run the next command until the user explicitly approves it:

```powershell
rtk uv pip install --dry-run --python ".\.venv\Scripts\python.exe" nicegui
```

Expected after approval: uv reports one exact NiceGUI candidate and its transitive dependency plan without changing `pyproject.toml`, `uv.lock`, or `.venv`; uv cache writes are within the approved scope.

- [ ] **Step 3: Produce the dependency preflight report and obtain exact-version approval**

The report must include the candidate version, index/source, full dependency additions/upgrades/removals, Python 3.12 support, expected artifact size when available, current Transformers/PyTorch/llama.cpp conflicts, and whether `uv.lock` can resolve. Ask the user to approve that exact version and the package installation. If approval is withheld, stop the plan with no repository changes.

- [ ] **Step 4: Record the approved fact before installation**

Create `docs/dependency-preflight/nicegui.json` with exactly these keys: string `package` equal to `nicegui`; literal approved string `version`; resolved package-index URL in `source`; string `python` equal to `3.12`; ISO-8601 `approved_at`; string `approval_scope` equal to `metadata, lockfile update, and panel-extra installation`; boolean `model_downloads` equal to false; boolean `real_api_calls` equal to false. This is an authority-derived record, not a guessed value. The factual values must be written before the following command is run.

- [ ] **Step 5: Pin exactly the recorded version and preserve the panel runtime extras**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$record = Get-Content -LiteralPath "docs/dependency-preflight/nicegui.json" -Raw | ConvertFrom-Json; rtk uv add --optional panel --bounds exact --no-sync ("nicegui==" + $record.version)'
rtk uv lock --check --offline --no-python-downloads
rtk uv sync --locked --no-python-downloads --extra retrieval --extra local-generator --extra online-generator --extra panel
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -c "import importlib.metadata as m, json, pathlib; r=json.loads(pathlib.Path(''docs/dependency-preflight/nicegui.json'').read_text(encoding=''utf-8'')); assert m.version(''nicegui'') == r[''version'']; print(r[''version''])"'
```

Expected: lock check succeeds and the printed installed version exactly equals the approved JSON record.

- [ ] **Step 6: Verify that existing offline behavior did not regress**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest -q'
```

Expected: exit code 0, no live test selected, and no reduction caused by missing dependencies or collection errors. The historical `547 passed, 5 deselected` result remains a baseline, not a future fixed count after adding tests.

- [ ] **Step 7: Commit the approved dependency gate**

Run:

```powershell
rtk git add pyproject.toml uv.lock docs/dependency-preflight/nicegui.json
rtk git commit -m "build: pin approved NiceGUI panel dependency"
```

## Task 2: Add immutable panel contracts and cooperative cancellation

**Files:**
- Create: `src/enterprise_rag/execution_control.py`
- Create: `src/enterprise_rag/panel/__init__.py`
- Create: `src/enterprise_rag/panel/contracts.py`
- Create: `tests/unit/test_execution_control.py`
- Create: `tests/unit/test_panel_contracts.py`

- [ ] **Step 1: Write failing cancellation tests**

```python
import pytest

from enterprise_rag.execution_control import CancellationToken, RunCancelled


def test_token_starts_active_and_raises_only_after_cancel() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()


def test_cancel_is_idempotent() -> None:
    token = CancellationToken()
    token.cancel()
    token.cancel()
    assert token.cancelled is True
```

- [ ] **Step 2: Write failing contract tests**

Cover these concrete test functions in `tests/unit/test_panel_contracts.py`: `test_local_run_rejects_provider_and_authorization`, `test_online_run_requires_matching_authorization`, `test_authorization_must_bind_run_profile_and_fallback`, `test_blocked_record_has_request_category_but_no_spec_result_or_trace`, `test_answered_record_requires_matching_spec_result_and_trace`, `test_business_failed_record_requires_matching_failed_result_and_trace`, `test_pre_service_failed_record_requires_public_error_and_no_result`, `test_cancelled_record_preserves_spec_and_public_prefix_without_result`, `test_failed_initialization_stage_requires_category_and_message`, and `test_run_request_and_spec_are_frozen`.

Use `RunRequest`, `RunSpec`, `OnlineRunAuthorization`, and `PanelRunRecord` with literal run/profile IDs; assert each invalid combination raises `ValueError` and assignment raises `FrozenInstanceError`.

- [ ] **Step 3: Run the tests and verify the modules are missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_execution_control.py tests/unit/test_panel_contracts.py -v'
```

Expected: collection fails because the new modules do not exist.

- [ ] **Step 4: Implement the minimal cancellation contract**

Create `src/enterprise_rag/execution_control.py` with this complete public surface:

```python
from __future__ import annotations

import threading


class RunCancelled(Exception):
    """Cooperative cancellation observed at a safe stage boundary."""


class CancellationToken:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelled("run cancelled")
```

- [ ] **Step 5: Implement the exact panel dataclasses**

In `src/enterprise_rag/panel/contracts.py`, define the canonical aliases from “Shared contracts”, plus:

```python
@dataclass(frozen=True, slots=True)
class InitializationStage:
    name: str
    status: Literal["pending", "running", "succeeded", "failed"]
    duration_ms: float
    error_category: str | None = None
    public_error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PanelRunRecord:
    run_id: str
    request: RunRequest
    spec: RunSpec | None
    state: PanelTerminalState
    result: RagResult | AgentResult | None
    trace: RunTrace | None
    progress_events: tuple[TraceEvent, ...]
    initialization_duration_ms: float
    run_duration_ms: float
    error_category: str | None
    public_error: str | None
```

Validate non-empty IDs/text, finite non-negative timings/cost, positive limits, request/spec field equality, exact online binding including `fallback_local`, legal local/online combinations, and the five terminal-state invariants described above. ANSWERED/REFUSED require a matching spec, business Result, and completed trace. FAILED with a returned business Result requires the matching failed Result and completed failed trace, and the record category must equal the Result category; an unexpected failure before service invocation has a matching spec, no Result/Trace, and a stable `error_category` plus sanitized `public_error` instead of a fabricated business object. BLOCKED requires no spec/Result/Trace and requires a stable category/message; CANCELLED requires a spec but no Result/completed trace and preserves only actually observed public events plus its cancel boundary. Successful/refused/cancelled records cannot carry an unrelated panel error. A failed `InitializationStage` likewise requires category/message, while non-failed stages cannot carry them. Export these types from `src/enterprise_rag/panel/__init__.py`; export no NiceGUI symbols there so importing the core does not require the panel extra.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_execution_control.py tests/unit/test_panel_contracts.py -v'
```

Expected: all new contract tests pass.

- [ ] **Step 7: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/execution_control.py src/enterprise_rag/panel tests/unit/test_execution_control.py tests/unit/test_panel_contracts.py
rtk git commit -m "feat: add panel run contracts and cancellation"
```

## Task 3: Extend traces with live observers and real dense/sparse/RRF children

**Files:**
- Modify: `src/enterprise_rag/execution_trace.py`
- Modify: `src/enterprise_rag/ports.py`
- Modify: `src/enterprise_rag/retrieval.py`
- Modify: `src/enterprise_rag/evaluation.py`
- Modify: `tests/fakes.py`
- Modify: `tests/unit/test_ports.py`
- Modify: `tests/unit/test_execution_trace.py`
- Modify: `tests/unit/test_retrieval.py`

- [ ] **Step 1: Add failing observer identity tests**

Append tests that construct `TraceRecorder("run-1", observer=events.append, clock=FakeClock())`, open one span, finish it with result attributes, and assert:

```python
assert [event.status for event in events] == ["started", "succeeded"]
assert events[0].event_id == events[1].event_id
assert recorder.finish("succeeded").events == (events[1],)
```

Add a second observer that raises `RuntimeError`; assert the span and final `RunTrace` still succeed. Add a third test asserting `finish("succeeded", attributes={"token": "secret"})` stores `"<redacted>"`. Update every direct `TraceEvent` fixture to include `run_id`, and add negative tests for an event whose run ID differs from its `RunTrace`.

- [ ] **Step 2: Add failing hybrid-child tests**

In `tests/unit/test_retrieval.py`, call the real `HybridRetriever.retrieve("policy question", 5, trace_scope=TraceScope(recorder, parent.event_id))` over the existing deterministic in-memory index. Assert the final stages and parent links are exactly:

```python
assert [(event.stage, event.parent_id) for event in trace.events] == [
    ("retrieval", None),
    ("dense", trace.events[0].event_id),
    ("sparse", trace.events[0].event_id),
    ("rrf_fusion", trace.events[0].event_id),
]
```

Assert dense/sparse attributes contain ranked chunk IDs/scores and RRF contains the fused Top-K; never assert model reasoning or prompts.

- [ ] **Step 3: Run the focused tests and see signature failures**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_execution_trace.py tests/unit/test_retrieval.py tests/unit/test_ports.py -v'
```

Expected: failures for the missing `observer`, `attributes` terminal update, `TraceScope`, and traced Retriever signature.

- [ ] **Step 4: Implement observer-safe terminal updates**

Add these public contracts to `execution_trace.py`:

```python
TraceObserver = Callable[[TraceEvent], None]


@dataclass(frozen=True, slots=True)
class TraceScope:
    recorder: TraceRecorder
    parent_id: str
```

Extend `TraceRecorder.__init__` with `observer: TraceObserver | None = None`. Notify once after `_start_span` creates the started event and once after `_close_span` replaces it with the terminal event. Catch `Exception` from the observer and do not alter the business trace. Extend `_TraceSpan.finish` and `_close_span` with `attributes: Mapping[str, object] | None`; merge terminal attributes through `_freeze_mapping` so sensitive keys remain redacted. Do not catch `BaseException`.

Add `"cancelled"` to `TraceStatus` for the coordinator-owned `cancel_boundary` progress event, but do not add it to `RunStatus`; a cancelled panel run has no fabricated completed business trace. Existing service spans continue to accept only `RunStatus` terminal values.

Add an explicit `run_id: str` field to `TraceEvent`, populate it from `TraceRecorder.run_id`, serialize it in `RunTrace.to_json`, and validate in `RunTrace.__post_init__` that every event's run ID equals the trace run ID. Update all direct `TraceEvent` constructors and tests. This makes live run binding structural instead of relying on an event-ID prefix.

- [ ] **Step 5: Add the traced Retriever call without a second retrieval implementation**

Change the `RetrieverPort.retrieve` contract and all four implementations/test doubles found by `rg -n "async def retrieve" src tests` to accept:

```python
*,
trace_scope: TraceScope | None = None,
```

In `HybridRetriever.retrieve`, keep one encode/rank/fuse path. When a scope exists, wrap `rank_dense`, `rank_sparse`, and `reciprocal_rank_fusion` in child spans and finish them with immutable public rows:

```python
{
    "items": tuple(
        {
            "chunk_id": chunk_id,
            "source": chunks_by_id[chunk_id].filename,
            "text_preview": chunks_by_id[chunk_id].text[:300],
            "score": score,
            "rank": rank,
        }
        for rank, (chunk_id, score) in enumerate(ranked[:top_k], start=1)
    )
}
```

Build `chunks_by_id` once before tracing. For RRF iterate `FusedItem` objects and include `chunk_id`, `source`, a maximum-300-character `text_preview`, `dense_rank`, `sparse_rank`, `retrieval_score`, and `retrieval_rank`. Trace lists are limited to the requested `top_k`; the later public projection limits display to five. The returned `RetrievedChunk` tuple remains byte-for-byte equivalent to the untraced call.

- [ ] **Step 6: Run retrieval and trace regression tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_execution_trace.py tests/unit/test_retrieval.py tests/unit/test_ports.py tests/integration/test_retriever_fake.py -v'
```

Expected: all selected tests pass, including identical ranking with and without tracing.

- [ ] **Step 7: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/execution_trace.py src/enterprise_rag/ports.py src/enterprise_rag/retrieval.py src/enterprise_rag/evaluation.py tests/fakes.py tests/unit/test_ports.py tests/unit/test_execution_trace.py tests/unit/test_retrieval.py
rtk git commit -m "feat: emit live hierarchical retrieval traces"
```

## Task 4: Make RAG and Agent traces nestable and cancellation-aware

**Files:**
- Modify: `src/enterprise_rag/rag_service.py`
- Modify: `src/enterprise_rag/agent_service.py`
- Modify: `src/enterprise_rag/application.py`
- Modify: `tests/integration/test_rag_service.py`
- Modify: `tests/agent/test_agent_service.py`
- Create: `tests/integration/test_panel_services.py`

- [ ] **Step 1: Write failing standalone RAG trace tests**

Use deterministic fakes and an observed `TraceRecorder`. Assert the completed standalone chain is:

```text
retrieval -> dense/sparse/rrf_fusion children
reranking
evidence_gate
generation
grounding_validation
optional repair_generation -> repair_validation
```

Assert a gate refusal has no generation node and retains `status="refused"` on both the gate and run.

- [ ] **Step 2: Write failing Agent nested-chain tests**

Build a scripted Agent action sequence `search_knowledge -> finish`. Assert `search_knowledge` is a child of `agent_action`; the real RAG retrieval/reranking/gate events are descendants of `search_knowledge`; `agent_observation` and `agent_finish` use the same run recorder. Assert the nested search never emits generation.

- [ ] **Step 3: Write failing cancellation-boundary tests**

Use blocking fakes controlled by `asyncio.Event`:

```python
token.cancel()
release_backend.set()
with pytest.raises(RunCancelled):
    await task
assert generator.calls == 1
assert validator.calls == 0
```

Cover cancellation after retrieval, after reranking, after a blocking generator returns, and after an Agent tool returns. Assert `RunCancelled` is re-raised rather than converted to a failed business result.

- [ ] **Step 4: Run tests and verify nesting/cancellation fail**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_services.py -v'
```

Expected: failures because services cannot yet share a recorder/parent or cancellation token.

- [ ] **Step 5: Implement one private RAG pipeline used by both entry points**

Retain the existing `trace_factory: Callable[[], TraceRecorder] | None` constructor parameter and add `cancellation: CancellationToken | None = None`. These are bound by a fresh per-run Application in Task 5; they are never mutated on a cached service.

Keep `ask(question)` public. Extend `search_evidence` with keyword-only `recorder: TraceRecorder | None = None` and `parent_id: str | None = None`. Both call one `_search_pipeline` that checks the token before and after retrieval, reranking, and gate; passes `TraceScope(recorder, retrieval_span.event_id)` to the Retriever; and creates the same stage events in standalone and nested operation. `ask` then performs generation/validation/one repair with cancellation checks between stages. Add `except RunCancelled: raise` before the existing broad exception handler.

Populate terminal attributes from actual structured outputs, never from UI reconstruction: reranking rows include chunk/source/text preview plus retrieval and rerank score/rank; the Gate includes threshold, observed score, support IDs, decision, and refusal reason; generation/repair include public backend/model/revision/digest and available usage/cost snapshots; validations include issue count and support IDs. Cap previews at 300 characters at trace creation and let projection enforce the same boundary again.

- [ ] **Step 6: Implement the Agent subtree and finish stage**

Retain the existing per-instance trace factory and add the same cancellation constructor parameter to `AgentService`. Check it before/after plan, each action/tool, observation, finish validation, repair, and loop transition. For `search_knowledge`, create:

```python
with recorder.span("agent_action", attributes=action_attributes) as action_span:
    with recorder.span(
        "search_knowledge",
        parent_id=action_span.event_id,
        attributes={"step": step},
    ) as search_span:
        result = await self._rag.search_evidence(
            question,
            recorder=recorder,
            parent_id=search_span.event_id,
        )
```

Record a terminal `agent_finish` span around `_finish`. Re-raise `RunCancelled` before the broad handler. Keep duplicate-action, iteration-limit, Evidence Gate, validation, source, and tool semantics unchanged.

- [ ] **Step 7: Run service regressions**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_services.py tests/integration/test_rag_service.py tests/agent/test_agent_service.py -v'
```

Expected: all selected tests pass; existing result statuses and sources are unchanged.

- [ ] **Step 8: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/rag_service.py src/enterprise_rag/agent_service.py src/enterprise_rag/application.py tests/integration/test_rag_service.py tests/agent/test_agent_service.py tests/integration/test_panel_services.py
rtk git commit -m "feat: add nested cancellable service execution"
```

## Task 5: Separate real initialization stages from per-run Application binding

**Files:**
- Modify: `src/enterprise_rag/application.py`
- Modify: `tests/unit/test_application.py`
- Modify: `tests/integration/test_real_composition.py`
- Create: `tests/unit/test_panel_runtime.py`

- [ ] **Step 1: Write failing staged-ownership tests**

Inject counting index, encoder, reranker, support-scorer, and tool factories. Call the new stage builders in order and assert each factory runs exactly once. Assemble two bound Applications from one `ApplicationTemplate`; assert Retriever, Reranker, Gate, Validator, ToolRegistry, encoder, and index keep object identity while the two Applications receive distinct generator bindings, trace factories, and cancellation tokens. Assert each bound Application contains both standalone RAG and Agent capabilities.

- [ ] **Step 2: Write failing template and legacy-facade regression tests**

Assert `ApplicationTemplate` rejects a fake production generator, invalid local/provider/fallback combinations, and a profile fingerprint inconsistent with its key. Assert the existing public `build_application` signature and request matching remain unchanged, fake production backends are still rejected, and CLI evaluation/doctor composition never imports or constructs panel objects.

- [ ] **Step 3: Run focused tests and verify the staged APIs are missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_application.py tests/unit/test_panel_runtime.py tests/integration/test_real_composition.py -v'
```

Expected: failures only for the missing staged builders and `ApplicationTemplate`.

- [ ] **Step 4: Add independently timed stage products and builders**

Define `RetrievalRuntimeComponents`, `RerankingRuntimeComponents`, and `SharedRuntimeComponents` dataclasses in `application.py`. The shared container owns `config`, `index`, `encoder`, `retriever`, `reranker`, `support_scorer`, `gate`, `validator`, `tools`, and `index_builder`, but no generator or per-run state.

Expose synchronous, independently callable builders with these responsibilities:

```text
load_runtime_index(
    config: AppConfig,
    *,
    index_loader: IndexLoader = load_index,
) -> HybridIndex

build_runtime_retrieval(
    config: AppConfig,
    index: HybridIndex,
    *,
    encoder_factory: EncoderFactory | None = None,
) -> RetrievalRuntimeComponents

build_runtime_reranking(
    config: AppConfig,
    *,
    reranker_factory: RerankerFactory | None = None,
    support_scorer_factory: SupportScorerFactory | None = None,
) -> RerankingRuntimeComponents

load_runtime_tools(
    records_root: Path = Path("data/sample_records"),
) -> ToolRegistry
```

The `assemble_shared_runtime_components` function only validates and combines already-created stage products; it performs no I/O and loads nothing. Each builder retains the current real-backend checks. Add `GteIndexBuilder.with_encoder(config, encoder)` so no caller mutates the private `_encoder` field.

Add idempotent async `aclose_runtime_components(shared)` that walks unique owned encoder/reranker/support-scorer/index/tool resources and invokes a supported `aclose`/`close` at most once; synchronous close hooks run through `asyncio.to_thread`, and objects without a close hook are ignored. The runtime, not a bound Application or template, is the only owner allowed to call it. Partial initialization tracks each successfully created stage product and closes those products in reverse creation order.

- [ ] **Step 5: Add a cacheable template with fresh per-run binding**

Define:

```text
@dataclass(frozen=True, slots=True)
class ApplicationTemplate:
    shared: SharedRuntimeComponents
    generator_kind: GeneratorChoice
    provider_name: str | None
    provider_fingerprint: str | None
    fallback_local: bool

    bind(
        self,
        *,
        generator_backend: StructuredGeneratorPort,
        trace_factory: Callable[[], TraceRecorder],
        cancellation: CancellationToken,
    ) -> Application
```

The template is safe to cache by `(generator_kind, provider_fingerprint, fallback_local)` because it contains no one-use authorization, run ID, recorder, or token. `bind` creates only `AnswerGenerator`, `RagService`, `AgentPlanner`, `AgentService`, and the lightweight `Application`; it reuses every heavy/shared field and performs `_require_real_backend` on the selected generator. A bound Application is single-run and never cached. It has both RAG and Agent, no evaluator/diagnostics, and preserves the existing `Application` request-matching behavior.

Keep `build_application` as the public legacy façade. Reuse stage helpers only where doing so cannot change CLI evaluation, doctor, or generator-selection semantics.

- [ ] **Step 6: Run composition tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_application.py tests/unit/test_panel_runtime.py tests/integration/test_real_composition.py tests/integration/test_generator_selection.py -v'
```

Expected: all pass; counters prove one heavy load, and distinct run bindings cannot share authorization/trace/cancellation state.

- [ ] **Step 7: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/application.py tests/unit/test_application.py tests/unit/test_panel_runtime.py tests/integration/test_real_composition.py
rtk git commit -m "refactor: stage and share RAG runtime components"
```

## Task 6: Build the generator registry and one-run online authorization

**Files:**
- Modify: `src/enterprise_rag/generation.py`
- Modify: `src/enterprise_rag/provider_profiles.py`
- Create: `src/enterprise_rag/panel/runtime.py`
- Modify: `tests/unit/test_online_generator.py`
- Modify: `tests/unit/test_provider_profiles.py`
- Modify: `tests/unit/test_panel_runtime.py`
- Create: `tests/integration/test_panel_authorization.py`
- Create: `tests/integration/test_panel_initialization.py`

- [ ] **Step 1: Write failing profile fingerprint and run-preflight tests**

Use literal fake profiles and assert:

```python
assert profile_fingerprint(profile) == profile_fingerprint(profile)
assert profile_fingerprint(replace(profile, model_id="changed")) != fingerprint
assert "api_key" not in profile_fingerprint(profile)
```

Test `preflight_online_run` with RAG maximum 2 calls and Agent maximum `agent_max_actions + 2` calls (one generator attempt per action, one possible final-answer protocol retry, and one grounding repair). It must check enabled/synthetic-only/network/key/request limit and calculate the conservative total cost from the current request/spend snapshot without creating a client. Lock this formula with tests against the current `AgentPlanner` control flow; if that flow changes later, the bound test must fail. Budget/request overflow must raise the existing public `BudgetBlockedError` or `ProfileNotAllowedError`.

- [ ] **Step 2: Write failing registry reuse and authorization tests**

Inject counting local/online factories. Assert:

- local loads lazily once and is reused by local and fallback runs;
- one complete profile fingerprint maps to one persistent online generator;
- normal online and online+fallback share that online object;
- one key `(generator_kind, profile_fingerprint, fallback_local)` maps to one persistent `ApplicationTemplate`, while every Run gets a new bound Application;
- switching RAG/Agent does not reset online request count or cumulative spend;
- an authorization cannot be replayed, cross a run ID, profile fingerprint, fallback bit, or limit;
- unauthorized/expired/mismatched runs never call the underlying generator;
- authorization exhaustion never triggers local fallback;
- permitted fallback latches to the shared Local backend after the first eligible online failure, emits exactly one online-to-local switch for that Run, and does not retry Online during a later repair;
- every created online SDK client is closed exactly once after its call;
- `aclose()` closes each unique persistent owned backend once, including partial initialization failure, while per-run fallback wrappers remain non-owning.

In `tests/integration/test_panel_initialization.py`, use blocking counting factories to prove that the six stage updates occur in order, each blocking loader executes through `asyncio.to_thread`, two concurrent page initializers share one load, warnings remain warnings, a failing stage leaves Run disabled, and shutdown after every partial-failure boundary closes each created resource exactly once.

- [ ] **Step 3: Run the tests and verify the registry/preflight APIs are absent**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_provider_profiles.py tests/unit/test_online_generator.py tests/unit/test_panel_runtime.py tests/integration/test_panel_authorization.py tests/integration/test_panel_initialization.py -v'
```

Expected: new tests fail on missing fingerprint, usage snapshot, run preflight, and registry.

- [ ] **Step 4: Add non-secret online usage and deterministic fingerprints**

In `generation.py`, add:

```python
@dataclass(frozen=True, slots=True)
class OnlineUsageSnapshot:
    request_number: int
    spent_cny: Decimal


async def usage_snapshot(self) -> OnlineUsageSnapshot:
    async with self._call_lock:
        return OnlineUsageSnapshot(self._request_number, self._spent_cny)
```

The method belongs to `OnlineGenerator` and exposes no key, prompt, response, or client. Add an idempotent `close()` to `LlamaCppGenerator`; it calls the underlying llama object's callable `close` at most once and is safe when no close method exists.

Each `OnlineGenerator.generate_json` call creates its SDK client only after the existing core preflight succeeds, then closes that per-call client in `finally` (using callable `aclose`/`close` as supported by the locked SDK/fake), including success, mapped provider failure, and cancellation. Blocked preflight therefore creates zero clients. This makes network ownership explicit without caching a client that could retain a rotated key; the generator's request/spend counters remain persistent.

Rename the private fallback tuple to the public constant `ONLINE_FALLBACK_FAILURES` and keep its exact existing provider/core-preflight members. Both the legacy explicit fallback and the panel's per-run wrapper use this one definition; panel authorization errors are deliberately outside it.

In `provider_profiles.py`, compute `profile_fingerprint` as SHA-256 of canonical JSON containing `name`, `base_url`, `model_id`, `api_key_env`, `privacy_terms_url`, prices, request/budget limits, and synthetic/enabled flags. Do not include the environment value. Add a frozen `OnlineRunPreflight` containing only display-safe fields and the maximum request/cost numbers.

- [ ] **Step 5: Implement `GeneratorRegistry` and the authorization wrapper**

`src/enterprise_rag/panel/runtime.py` must expose `GeneratorRegistry.get_local`, `get_online(profile)`, `preflight(request)`, `authorize(preflight)`, `backend_for(spec)`, and `aclose`. Generator getters, `preflight`, `backend_for`, and `aclose` are async because creation/usage/close operations may await locks or clients; `get_local` single-flights the blocking local factory through `asyncio.to_thread`. `authorize` is synchronous and returns `OnlineRunAuthorization`; `backend_for` resolves a per-run `StructuredGeneratorPort` binding.

`authorize` creates a random `authorization_id` only after UI confirmation. `backend_for` atomically consumes that ID for one matching `RunSpec`, including the exact fallback bit, wraps the persistent `OnlineGenerator` in `AuthorizedOnlineGenerator`, and optionally wraps it with a per-run, non-owning `AuthorizedLocalFallbackGenerator`. The fallback wrapper catches only `ONLINE_FALLBACK_FAILURES`, latches permanently to the shared Local backend after the first eligible failure, and exposes one consumable backend-switch category. The authorized wrapper checks the run-level call count before every online generation; the underlying `OnlineGenerator` still executes its per-request core preflight. Define a stable `RunAuthorizationError` in `panel.runtime` that is not a member/subclass of any exception in `ONLINE_FALLBACK_FAILURES`; exhausted, replayed, or mismatched authorization therefore becomes a FAILED business result and can never switch to Local. No authorization object stores a key, and neither per-run wrapper owns/closes the shared raw backends.

- [ ] **Step 6: Implement `PanelRuntime` initialization and composition**

`PanelRuntime.initialize(observer)` exposes one shared initialization task to all connected pages and runs these exact stages through `asyncio.to_thread` where loading is blocking:

```text
CHECKING_CONFIG
LOADING_INDEX
LOADING_RETRIEVER
LOADING_RERANKER
PREPARING_DEFAULT_LOCAL_GENERATOR
READY
```

`CHECKING_CONFIG` loads/validates `AppConfig`, provider profiles, and read-only tools without loading a model. `LOADING_INDEX`, `LOADING_RETRIEVER`, and `LOADING_RERANKER` call the separate Task 5 builders, so each displayed duration measures real work rather than slices of one monolithic function. `PREPARING_DEFAULT_LOCAL_GENERATOR` awaits the registry's one lazy local load. `READY` assembles `SharedRuntimeComponents` without I/O. Expected diagnostic warnings are stored in `InitializationStage.warnings` and do not become hard failures; missing artifacts, identity mismatches, or resource/configuration exceptions do.

The runtime loads profiles once, constructs the shared components once, prepares the default local generator once, records per-stage and total ready duration separately, and remains `initialization_failed`/Run-disabled after any stage error. Concurrent calls from two pages await the same initializer and do not duplicate a load. Task 12 records page-route/UI availability separately before initialization begins. Async `application_for(spec, token, observer)` obtains a fresh authorized generator binding, reuses a cached `ApplicationTemplate` keyed by `(generator_kind, provider_fingerprint, fallback_local)`, and binds a run-specific `TraceRecorder(spec.run_id, observer=observer)` plus the token. It never stores the authorization in the template. `aclose()` settles initialization, closes the registry and `aclose_runtime_components` exactly once, then clears templates.

- [ ] **Step 7: Run runtime and authorization tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_provider_profiles.py tests/unit/test_online_generator.py tests/unit/test_panel_runtime.py tests/integration/test_panel_authorization.py tests/integration/test_panel_initialization.py -v'
```

Expected: all pass; fake client creation count is zero for every blocked case.

- [ ] **Step 8: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/generation.py src/enterprise_rag/provider_profiles.py src/enterprise_rag/panel/runtime.py tests/unit/test_online_generator.py tests/unit/test_provider_profiles.py tests/unit/test_panel_runtime.py tests/integration/test_panel_authorization.py tests/integration/test_panel_initialization.py
rtk git commit -m "feat: add cached generators and one-run authorization"
```

## Task 7: Add the bounded progress buffer with proven event capacity

**Files:**
- Create: `src/enterprise_rag/panel/public_trace.py`
- Create: `src/enterprise_rag/panel/progress.py`
- Create: `tests/unit/test_panel_public_trace.py`
- Create: `tests/unit/test_panel_progress.py`
- Modify: `tests/integration/test_panel_services.py`

- [ ] **Step 1: Write failing queue-entry sanitization tests**

Construct `TraceEvent` objects for every approved stage with nested attributes containing allowed public fields plus `api_key`, `authorization`, `secret`, `token`, `password`, prompt/system-prompt variants, environment values, traceback text, six ranked rows, and 500-character evidence. Assert `sanitize_public_trace_event` preserves structural IDs/status/timing, drops all non-whitelisted keys recursively, caps ranked rows at five and strings at 300 Unicode characters, and represents `Decimal` costs as normalized strings. Unknown stages retain only structural fields and empty attributes. The input event remains unchanged.

- [ ] **Step 2: Write failing capacity and critical-event tests**

Build a `BoundedProgressBuffer(capacity=256, sanitizer=sanitize_public_trace_event)` and publish 1,000 repeated non-terminal `status="started"` snapshots over a fixed set of event IDs, followed by terminal, error, repair, backend-switch, cancellation-boundary, and completed-trace events. Assert:

```python
assert buffer.pending_count <= 256
assert critical_event_ids <= {event.event_id for event in buffer.drain()}
```

Test deterministic ordering, duplicate subscription rejection, consumer detach, sanitizer/observer exceptions, and a closed buffer. None may alter the fake business result, and no forbidden value may appear in `drain()`.

- [ ] **Step 3: Write the worst-case Agent cardinality test**

Generate (a) the maximum valid six-action completed Agent trace with nested RAG on every permitted search, one repair, one fallback, and all terminal events, and (b) the maximum valid cancelled prefix with one cancellation boundary. Assert the unique critical-event count of each valid path is below 256. If either count reaches the capacity, the implementation must raise the capacity or redesign delivery before merge; this proof is required before relying on “evict/coalesce only non-terminal updates”.

- [ ] **Step 4: Run the tests and verify the modules are missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_panel_public_trace.py tests/unit/test_panel_progress.py tests/integration/test_panel_services.py -v'
```

Expected: import/behavior failures for the missing sanitizer and buffer.

- [ ] **Step 5: Implement the public trace boundary and exact bounded policy**

`public_trace.py` owns the literal `PUBLIC_STAGE_FIELDS` mapping (spelled out in Task 9), the recursive denylist, five-row limit, 300-character cap, and `sanitize_public_trace_event`. It has no NiceGUI import. Trace production remains UI-neutral; the buffer invokes this sanitizer synchronously before retaining an event. Sanitization failure detaches live delivery and is swallowed at the observer boundary; it never changes business execution or the final authoritative trace.

`BoundedProgressBuffer` owns one `collections.deque[TraceEvent]` and one lock. Its `publish(event)` follows this order:

1. ignore updates after `detach()`/`close()`;
2. when an event ID already has a queued `status="started"` snapshot, replace that snapshot in place with the newest snapshot for the same ID (especially its terminal form);
3. append while below capacity;
4. when full, evict the oldest `started` update, never a terminal event;
5. if a full buffer contains only terminal events, mark the subscriber detached and rely on the separately returned authoritative final `RunTrace`; never block or raise into business execution.

Expose `subscribe()`, `detach()`, `publish()`, `drain()`, `pending_count`, and `detached`. There is no unbounded side queue. `publish` is synchronous so `TraceRecorder` can call it; UI drains on a timer.

- [ ] **Step 6: Reconcile drained progress with the final trace**

Add `reconcile_terminal_events(progress_prefix, trace)` that sanitizes terminal snapshots from `RunTrace`, then returns a bounded immutable display sequence by replacing/adding them. This is projection support only; it must not synthesize a business conclusion or modify the trace. Assert every final trace event appears exactly once with matching `event_id`, `parent_id`, `stage`, `status`, and `error_category`, while its displayed attributes remain public-safe.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_panel_public_trace.py tests/unit/test_panel_progress.py tests/integration/test_panel_services.py -v'
```

Expected: all pass; maximum pending count never exceeds 256.

- [ ] **Step 8: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/panel/public_trace.py src/enterprise_rag/panel/progress.py tests/unit/test_panel_public_trace.py tests/unit/test_panel_progress.py tests/integration/test_panel_services.py
rtk git commit -m "feat: add bounded panel progress delivery"
```

## Task 8: Implement the global single-run coordinator and cancellation lifecycle

**Files:**
- Create: `src/enterprise_rag/panel/coordinator.py`
- Create: `tests/integration/test_panel_coordinator.py`

- [ ] **Step 1: Write failing lifecycle tests with controlled Futures**

Use `asyncio.Event`, never timing sleeps, to assert these transitions:

```text
INITIALIZING -> READY
INITIALIZING -> INITIALIZATION_FAILED
READY -> RUNNING -> ANSWERED/REFUSED/FAILED -> READY
READY -> AUTHORIZING -> BLOCKED -> READY
RUNNING -> CANCELLING -> CANCELLED -> READY
```

Also assert a second Run during RUNNING/CANCELLING is rejected immediately with no hidden queue, repeated Stop is idempotent, and completing versus cancelling produces exactly one terminal record.

- [ ] **Step 2: Write failing late-update and history tests**

Complete six runs and assert history stores the newest five in stable order. Start a seventh run, publish a delayed event/result carrying the sixth run ID, and assert it cannot change the active view or seventh record. Re-running identical input must produce a different run ID.

- [ ] **Step 3: Run tests and verify the coordinator is missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_coordinator.py -v'
```

Expected: collection fails for the missing module.

- [ ] **Step 4: Implement the single-task coordinator**

Expose these exact members on `RunCoordinator`: read-only `state`, `active_run_id`, and `history`; async `initialize`, `start(spec)`, `wait(run_id)`, and `aclose`; synchronous `begin_authorization(request)`, `record_blocked(request, error_category, public_error)`, `request_cancel(run_id)`, and `drain_progress(run_id)`. `initialize` returns `tuple[InitializationStage, ...]`, `record_blocked` and `wait` return `PanelRunRecord`, `request_cancel` returns whether the matching active run was newly marked, and `drain_progress` returns `tuple[TraceEvent, ...]`.

`begin_authorization` reserves exactly that request/run ID and moves READY to AUTHORIZING without creating a service or client. `record_blocked` is legal only for the matching pending request; it creates a BLOCKED record with `spec=None`, zero business calls, and then returns to READY. `start` validates an online spec against that pending request (or reconstructs the value-equivalent request for a local spec), creates exactly one task and one `CancellationToken`/buffer, obtains a fresh bound Application from `PanelRuntime`, invokes `ask_rag` or `run_agent`, maps only structured result statuses to ANSWERED/REFUSED/FAILED, and reconciles final trace events. An unexpected template/binding failure after a spec exists but before service invocation maps to the explicit pre-service FAILED shape (spec + sanitized public error, no fabricated Result/Trace), never BLOCKED.

When `RunCancelled` reaches the coordinator, append one direct `TraceEvent` with a deterministic unique ID, `run_id` equal to the spec, `stage="cancel_boundary"`, `status="cancelled"`, no parent, and no sensitive attributes. Create CANCELLED with no Result/completed trace and the genuine progress prefix. The coordinator stays busy until a blocking backend returns and the token is observed. A stale `request_cancel(old_run_id)` returns false and cannot affect the active token.

- [ ] **Step 5: Implement deterministic terminal arbitration**

Guard terminal record creation with one lock and an `active_run_id` comparison. After a terminal record is stored, late progress/results are ignored. Store records in `deque(maxlen=5)`, then return lifecycle state to READY without mutating the terminal record's state. `aclose()` detaches progress, requests/settles any active run, closes the runtime once, and clears the memory-only history.

- [ ] **Step 6: Run coordinator tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_coordinator.py tests/unit/test_panel_progress.py -v'
```

Expected: all pass with no sleep-based flakiness.

- [ ] **Step 7: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/panel/coordinator.py tests/integration/test_panel_coordinator.py
rtk git commit -m "feat: coordinate one cancellable panel run"
```

## Task 9: Project only safe structured data into panel view models

**Files:**
- Create: `src/enterprise_rag/panel/projection.py`
- Create: `tests/unit/test_panel_projection.py`

- [ ] **Step 1: Write failing five-state projection tests**

Construct real `RagResult`/`AgentResult` fixtures for ANSWERED, REFUSED, and business FAILED, plus pre-service FAILED, BLOCKED, and CANCELLED records. Assert state label, icon, answer/refusal/error, sources, tool calls, observations, durations, and trace nodes come only from structured fields. Put misleading words such as “refused” and fake citations inside answer text and prove the projector does not infer status/source from them.

- [ ] **Step 2: Write failing whitelist/redaction/limit tests**

Inject nested attributes containing `api_key`, `authorization`, `secret`, `token`, `password`, `system_prompt`, raw traceback, prompt, environment values, six ranked items, and 500-character evidence. Assert forbidden keys/values are absent recursively, rank lists stop at 5, and previews stop at 300 Unicode characters.

- [ ] **Step 3: Run tests and verify projection is missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_panel_projection.py -v'
```

Expected: collection fails for the missing module.

- [ ] **Step 4: Reuse the public trace whitelist and implement view models**

Import the canonical `PUBLIC_STAGE_FIELDS`/sanitizer from `panel.public_trace`; do not define a second whitelist. Verify the Task 7 mapping contains at minimum:

```python
PUBLIC_STAGE_FIELDS = {
    "retrieval": {"backend_kind"},
    "dense": {"items", "backend_kind"},
    "sparse": {"items", "backend_kind"},
    "rrf_fusion": {"items"},
    "reranking": {"items", "backend_kind"},
    "evidence_gate": {"threshold", "score", "support_ids", "decision", "reason"},
    "generation": {"backend_kind", "model_id", "model_revision", "model_digest", "usage", "cost_cny"},
    "grounding_validation": {"issue_count", "support_ids", "backend_kind"},
    "grounding_issue": {"attempt", "claim_index", "code"},
    "repair_generation": {"backend_kind", "model_id", "model_revision", "model_digest", "usage", "cost_cny"},
    "repair_validation": {"issue_count", "support_ids", "backend_kind"},
    "backend_switch": {"from_backend", "to_backend"},
    "agent_plan": {"step", "backend_kind"},
    "agent_action": {"step", "action", "arguments"},
    "search_knowledge": {"step"},
    "agent_observation": {"step", "observation_id", "source", "status"},
    "agent_finish": {"step"},
    "cancel_boundary": set(),
}
```

Unknown keys are dropped, not passed through. Apply the same sanitizer again when projecting a completed `RunTrace` (defense in depth), then return frozen `TraceNodeViewModel`, `InspectorViewModel`, and `PanelRunViewModel` dataclasses. Each state has both text and icon; color is supplemental.

- [ ] **Step 5: Run projection tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_panel_projection.py tests/unit/test_execution_trace.py -v'
```

Expected: all pass and no secret marker value appears in serialized view models.

- [ ] **Step 6: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/panel/projection.py tests/unit/test_panel_projection.py
rtk git commit -m "feat: add safe panel view projections"
```

## Task 10: Add the pure panel controller, presets, and online confirmation flow

**Files:**
- Create: `src/enterprise_rag/panel/controller.py`
- Create: `tests/unit/test_panel_controller.py`
- Modify: `tests/integration/test_panel_authorization.py`

- [ ] **Step 1: Write failing preset and input tests**

Define assertions for these exact immutable presets:

```python
PRESETS = (
    ScenarioPreset(
        "rag-shanghai-p4",
        "P4 员工去上海出差的每晚住宿上限是多少？",
        "rag",
    ),
    ScenarioPreset(
        "agent-claim-1024",
        "解释 EMP-003 的 CLAIM-1024 为什么被拒绝。",
        "agent",
    ),
    ScenarioPreset(
        "rag-pet-boarding-refusal",
        "制度是否允许报销宠物寄养费？",
        "rag",
    ),
)
```

Selecting a card loads text/mode but does not start a run. Preset text is read-only; Custom text is editable and must be non-blank. Retriever/Reranker are absent from user-selectable controller fields.

- [ ] **Step 2: Write failing local and online flow tests**

Assert:

- local Run creates a fresh immutable `RunSpec` and starts once;
- online selection returns a display-safe confirmation view before creating a RunSpec;
- rejecting the confirmation records one sanitized BLOCKED request with category `authorization_cancelled` and creates no Application/business/client call;
- confirming creates one authorization bound to run/profile/fallback and starts once;
- network-off, missing/disabled/changed profile, fingerprint mismatch, privacy, budget, request limit, and missing key produce BLOCKED and zero client/application calls;
- a request already sent that later hits auth/quota/timeout maps to FAILED, not BLOCKED;
- controls lock during RUNNING/CANCELLING and unlock only on READY;
- selected history changes only the viewed record; a new Run remains the active view.

- [ ] **Step 3: Run tests and verify the controller is missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_panel_controller.py tests/integration/test_panel_authorization.py -v'
```

Expected: collection fails for the missing controller.

- [ ] **Step 4: Implement controller state without NiceGUI imports**

Expose these exact members on `PanelController`: read-only `presets` and `view`; synchronous `select_scenario`, `update_custom_text`, `select_mode`, `select_generator`, `select_provider`, `set_fallback_local`, `reject_online`, `request_stop`, `select_history`, and `select_trace_node`; async `prepare_run`, `confirm_online`, and `refresh`. `prepare_run` returns `OnlineConfirmationViewModel | None`; `refresh` returns `PanelViewModel`.

The controller may validate form shape and authorization binding. It must not inspect answer text, recompute Gate decisions, filter sources, select tools, perform fallback, or invent trace nodes. `prepare_run` first creates one immutable `RunRequest`. Local mode immediately creates its matching `RunSpec` and calls `coordinator.start`. Online mode calls `coordinator.begin_authorization(request)` before preflight and returns a display-safe confirmation without creating a spec. `confirm_online` creates the one-use authorization, copies the request into its matching immutable spec, and starts it. `reject_online` calls `record_blocked`; `request_stop` always passes the currently active run ID. The run ID is therefore allocated before online preflight and remains identical through confirmation, authorization, progress, result, and history.

- [ ] **Step 5: Map preflight failures precisely**

Only failures before application/service invocation become BLOCKED. Use stable public categories and sanitized messages. Once an authorized backend call starts, provider/config/protocol failures are returned through the business result as FAILED. Store no key, environment value, prompt, response, or traceback in the controller.

- [ ] **Step 6: Run controller/authorization tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_panel_controller.py tests/integration/test_panel_authorization.py tests/integration/test_panel_coordinator.py -v'
```

Expected: all pass.

- [ ] **Step 7: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/panel/controller.py tests/unit/test_panel_controller.py tests/integration/test_panel_authorization.py
rtk git commit -m "feat: add panel presets and safe run controller"
```

## Task 11: Add the lazy `rag-agent panel` CLI entry

**Files:**
- Modify: `src/enterprise_rag/cli.py`
- Create: `tests/integration/test_panel_cli.py`
- Modify: `tests/integration/test_cli.py`

- [ ] **Step 1: Write failing parser and launcher tests**

Assert:

```python
assert parse(["panel"]).port == 8080
assert parse(["panel"]).no_open is False
assert parse(["panel", "--port", "8081", "--no-open"]).port == 8081
```

Inject a fake `panel_runner` into `main`; assert it receives `host="127.0.0.1"`, the requested port, `open_browser=False/True`, and `native=False`. Assert invalid ports return parser code 2. Assert all existing CLI parser/dispatch tests remain unchanged.

- [ ] **Step 2: Write the occupied-port failure test**

Use a local socket bound to a selected test port, invoke the fake/real entry preflight, and assert exit code 1 plus a sanitized message containing the occupied port and `--port`. It must not silently choose another port.

- [ ] **Step 3: Run tests and observe the missing command**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_cli.py tests/integration/test_cli.py tests/integration/test_agent_cli.py -v'
```

Expected: panel parser/launcher tests fail; legacy tests remain green.

- [ ] **Step 4: Add a synchronous lazy launcher branch**

Add a `panel` parser with integer `--port` default 8080 and `--no-open`. Before `build_application` and the existing asynchronous dispatch call, handle:

```python
if arguments.command == "panel":
    runner = panel_runner or _load_panel_runner()
    return runner(
        host="127.0.0.1",
        port=arguments.port,
        open_browser=not arguments.no_open,
        native=False,
    )
```

`_load_panel_runner` is the only place in `cli.py` that imports `enterprise_rag.panel.app`. This keeps all non-panel CLI commands importable without the panel extra and avoids nesting NiceGUI inside the current `asyncio.run` path.

- [ ] **Step 5: Add deterministic port preflight**

The panel entry probes only the requested `127.0.0.1:port`, closes the probe socket, then starts NiceGUI. Convert `OSError`/bind failure to one public error suggesting `--port`; never bind `0.0.0.0`, never use port 0, and never auto-increment.

- [ ] **Step 6: Run CLI regressions**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_cli.py tests/integration/test_cli.py tests/integration/test_agent_cli.py tests/integration/test_eval_cli.py tests/integration/test_doctor.py tests/test_smoke.py -v'
```

Expected: all pass; no model or network is touched.

- [ ] **Step 7: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/cli.py tests/integration/test_panel_cli.py tests/integration/test_cli.py
rtk git commit -m "feat: add localhost panel CLI command"
```

## Task 12: Implement the NiceGUI engineering workbench

**Files:**
- Create: `src/enterprise_rag/panel/app.py`
- Create: `tests/integration/test_panel_page.py`
- Create: `conftest.py` (repository root)
- Create: `tests/integration/panel_main.py`

- [ ] **Step 0: Wire the NiceGUI test harness before writing any page test**

The repository currently contains **no `conftest.py` at all**, so the harness this task depends on does not exist yet. Establish it in isolation first, and prove it does not disturb the existing suite:

- Add a **repository-root** `conftest.py` containing `pytest_plugins = ["nicegui.testing.user_plugin"]`. It must be at the root: pytest 9 rejects `pytest_plugins` in a non-top-level conftest, so `tests/conftest.py` is not an option. Use `user_plugin`, never `plugin` — the latter pulls in the selenium-backed screen fixtures, which are not installed.
- Give the `user` fixture a main file. `nicegui.testing.general_fixtures.get_path_to_main_file` falls back to the ini value `main_file` (default `main.py` next to the ini file) and raises `FileNotFoundError` when it is missing. Create `tests/integration/panel_main.py` that builds the page from a fake runtime and fake controller, and select it per test with the `nicegui_main_file` marker (or set `main_file` in `[tool.pytest.ini_options]`).
- Confirm the harness coexists with the existing `--import-mode=importlib` and `asyncio_mode = "auto"`, and that the plugin's `pytest_configure` (which repoints NiceGUI's `Storage.path` to a temp directory) changes no existing test's behavior.

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest -q'
```

Expected: the entire existing suite still passes with the harness loaded, and no new collection errors appear. Commit this wiring on its own (`test: wire the NiceGUI page test harness`) before adding page tests, so a harness regression can never be mistaken for a page regression.

- [ ] **Step 1: Write failing page tests using the locked NiceGUI test harness**

Register a fake `PanelController` and verify these visible behaviors by stable labels/test IDs:

- title and initialization stage list appear immediately;
- Run is disabled before READY and enabled after successful initialization;
- three preset cards populate without auto-run; Custom is editable;
- RAG/Agent and Local/Online controls reflect the controller;
- local model is read-only; Online exposes only registered profiles and read-only model/endpoint;
- fallback appears only for Online and defaults off;
- Online confirmation shows provider/model/endpoint/privacy/call cap/cost/fallback and has Confirm/Cancel;
- controls lock while running; Stop remains available;
- horizontal Pipeline, fixed right Inspector, answer/refusal/error, sources, observations, and recent-five history render from the ViewModel;
- ANSWERED/REFUSED/BLOCKED/FAILED/CANCELLED each have text and icon, not color alone;
- selecting a node changes only Inspector; selecting history never mutates the record;
- two browser clients share one initializer/coordinator/history while retaining independent UI selections;
- a late old-run refresh cannot overwrite the active run.

Do not use `pytest.importorskip`; a missing approved panel extra is an explicit test failure.

- [ ] **Step 2: Run the page tests and verify the page is missing**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_page.py -v'
```

Expected: collection or route failure because `panel.app` does not exist.

- [ ] **Step 3: Create the page shell and background initialization**

`create_panel_app(runtime_factory=PanelRuntime, controller_factory=PanelController)` creates exactly one process-wide runtime/coordinator and one lightweight controller per browser client. It registers `/` and records route/UI-shell availability before any model load, renders the initialization shell immediately, then starts/joins the shared initialization task in the background and refreshes each controller view with a bounded timer. UI callbacks submit intents only. A second browser client observes the same active run/history and cannot trigger a second initializer. Shutdown detaches every client, waits for/settles the active task, and awaits `PanelRuntime.aclose()` once.

- [ ] **Step 4: Build the approved desktop layout**

Use NiceGUI rows/cards/splitters with these stable regions:

```text
panel-controls
  scenario-cards | custom-input | mode | generator | profile/model | run/stop | safety
panel-workbench
  pipeline-canvas (wide horizontal main chain)
  inspector-panel (fixed right column)
panel-result
  state | answer/refusal/failure | sources | observations
recent-runs
```

Render the pipeline from `TraceNodeViewModel` only, as a **controlled node chain**: a horizontal row of NiceGUI cards, one per stage, each showing the stage label, its status as text plus an icon, and its elapsed time. Clicking a card selects that event ID for the Inspector and does nothing else — it cannot execute code or change business state. Do **not** use `ui.mermaid`: it renders client-side only and never reports a node click back to Python, so it cannot drive the Inspector. Do not display token streaming, prompts, hidden reasoning, keys, environment values, or traceback.

- [ ] **Step 5: Wire Run, confirmation, Stop, Inspector, and history intents**

Each callback captures the current UI values, calls one controller method, and immediately renders the returned immutable ViewModel. Run never calls a service directly. Stop only requests cooperative cancellation. Refresh ignores a view whose `run_id` differs from the active/selected ID.

- [ ] **Step 6: Implement the browser-mode runner**

Expose `run_panel(*, host: str, port: int, open_browser: bool, native: bool) -> int`. Validate `host == "127.0.0.1"` and `native is False`, print `http://127.0.0.1:{port}/`, and call `nicegui.ui.run` with **every safety-relevant default overridden explicitly**:

```python
ui.run(
    host=host,              # never rely on the default: non-native mode defaults to "0.0.0.0"
    port=port,
    show=open_browser,      # ui.run's parameter is named `show`, not `open_browser`
    native=False,
    reload=False,           # the default True spawns a uvicorn reloader subprocess that loads
                            # every model twice and breaks the single-owner aclose() contract
    on_air=None,            # never expose the panel through NiceGUI's remote-access feature
    fastapi_docs=False,     # do not publish OpenAPI docs for the panel's internal routes
)
```

Verify against the locked version's actual signature before relying on it. No independent business HTTP endpoints are added.

- [ ] **Step 7: Run page and CLI tests**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_panel_page.py tests/integration/test_panel_cli.py tests/unit/test_panel_controller.py -v'
```

Expected: all pass with fake runtime/controller; no real model is loaded.

- [ ] **Step 8: Commit**

Run:

```powershell
rtk git add src/enterprise_rag/panel/app.py tests/integration/test_panel_page.py
rtk git commit -m "feat: add NiceGUI engineering workbench"
```

## Task 13: Close offline safety, failure, and regression gaps

**Files:**
- Modify: `tests/integration/test_panel_services.py`
- Modify: `tests/integration/test_panel_authorization.py`
- Modify: `tests/integration/test_panel_coordinator.py`
- Modify: `tests/integration/test_panel_page.py`
- Create: `tests/integration/test_panel_network.py`
- Modify as required by failures: panel/core files changed in Tasks 2–12

- [ ] **Step 1: Add the final trace/progress consistency matrix**

For answered RAG, refused RAG, answered Agent, repair success, repair refusal, authorized fallback, and failed provider cases, assert:

```python
assert record.spec.run_id == record.trace.run_id
for final in record.trace.events:
    live = terminal_by_id[final.event_id]
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
```

Observer absent, detached, or raising must yield the same business result and final trace as the baseline.

- [ ] **Step 2: Add the five-terminal-state invariant matrix**

Verify ANSWERED requires answered Result/Trace/sources; REFUSED requires refused Result/Trace/reason and no answer; BLOCKED has no spec/Result/Trace and zero business/client calls; business FAILED preserves a failed Result/Trace; pre-service FAILED preserves its spec and sanitized public error but has no fabricated Result/Trace; CANCELLED has no authoritative Result or fabricated final trace and stores only the real public prefix plus one cancelled boundary. Returning coordinator lifecycle to READY must not alter terminal record state. Initialization failure is asserted through the initialization view and never mislabelled as a run.

- [ ] **Step 3: Add loopback/network and secret tripwires**

In `tests/integration/test_panel_network.py`, hard-fail every `socket.connect` during the local/fake controller-to-service path and assert zero attempts. For page/runner tests, instrument bind/connect and allow only `127.0.0.1` or `::1`; fail `0.0.0.0`, LAN, public, DNS, or package/model endpoints. Assert the CLI and `run_panel` reject every non-loopback host before NiceGUI starts.

Seed environment/profile/fake errors with recognizable secret strings; serialize progress, history, confirmation, initialization warnings/errors, and page ViewModels; assert none contain those strings, full prompts, system prompts, arbitrary environment values, or traceback text.

- [ ] **Step 4: Run the complete default suite**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; & ".\.venv\Scripts\python.exe" -m pytest -q'
```

Expected: all default tests pass, all live tests remain deselected, and the pre-panel baseline of 547 passing tests is not reduced.

- [ ] **Step 5: Run static checks**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ruff.exe" check .'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ty.exe" check src'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ruff.exe" format --check src/enterprise_rag/execution_control.py src/enterprise_rag/execution_trace.py src/enterprise_rag/ports.py src/enterprise_rag/retrieval.py src/enterprise_rag/rag_service.py src/enterprise_rag/agent_service.py src/enterprise_rag/application.py src/enterprise_rag/generation.py src/enterprise_rag/provider_profiles.py src/enterprise_rag/cli.py src/enterprise_rag/panel tests/unit/test_execution_control.py tests/unit/test_panel_contracts.py tests/unit/test_panel_public_trace.py tests/unit/test_panel_progress.py tests/unit/test_panel_projection.py tests/unit/test_panel_controller.py tests/unit/test_panel_runtime.py tests/integration/test_panel_services.py tests/integration/test_panel_authorization.py tests/integration/test_panel_initialization.py tests/integration/test_panel_coordinator.py tests/integration/test_panel_cli.py tests/integration/test_panel_network.py tests/integration/test_panel_page.py'
rtk uv build --offline --no-python-downloads
```

Expected: Ruff lint and ty report success; every task-touched file passes format check; wheel/sdist build succeeds. Do not run whole-repository format because historical unrelated files are not all formatted.

If `uv build --offline` fails because build isolation cannot obtain `hatchling` without network, do not treat it as a product failure: report the exact error, then either build with a local/cached backend (`--no-build-isolation`, using the `hatchling` already present in `.venv`) or state in the handoff that the offline build could not be verified on this machine. Never silently drop the build check, and never "fix" it by enabling network.

- [ ] **Step 6: Commit any test-driven corrections**

Run:

```powershell
rtk git add src tests
rtk git commit -m "test: harden panel safety and lifecycle"
```

If no corrections were required, do not create an empty commit.

## Task 14: Update operator, architecture, security, and demo documentation

**Files:**
- Modify: `README.md`
- Create: `docs/panel-guide.md`
- Modify: `docs/architecture.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/security-and-cost.md`
- Modify: `docs/evaluation.md`

- [ ] **Step 1: Document exact install/start commands**

**First measure, then write.** Do not document an install command that has not been run on this machine. `uv sync --locked` re-verifies the `llama-cpp-python` GitHub-release wheel whenever it has to re-materialize it, which stalls for hours here (~40 KB/s; see `docs/handoffs/2026-10-11-panel-execution-progress.md` §7). Before writing any install line, run the candidates **with a timeout** and record what actually happens:

```powershell
rtk uv sync --locked --offline --no-python-downloads --extra retrieval --extra local-generator --extra online-generator --extra panel
rtk uv lock --check --offline --no-python-downloads
```

If the offline sync succeeds, document that form. If it does not, document the path that does — for example installing only the missing extras with `uv pip install --python ".\.venv\Scripts\python.exe"` — and state plainly in the guide which command is known-good on this machine and why the plain `uv sync --locked` form is not recommended. Whichever form you document, it must be a command you actually executed successfully.

README and `docs/panel-guide.md` must show the verified install command followed by:

```powershell
$env:PYTHONUTF8 = "1"
$env:RAG_AGENT_ALLOW_NETWORK = "false"
$env:RAG_AGENT_GENERATOR = "local"
rtk uv run --locked --no-sync rag-agent panel
rtk uv run --locked --no-sync rag-agent panel --port 8081 --no-open
```

(`uv run --no-sync` never triggers a re-sync, so those two lines are safe as written.)

Explain initialization stages, three presets, Custom, RAG versus Agent, local versus online, Inspector, Stop semantics, five terminal states, recent-five memory-only history, occupied-port recovery, and the existing CLI fallback.

- [ ] **Step 2: Document architecture and truth boundaries**

Update `docs/architecture.md` with the one-process diagram, real staged initialization, SharedRuntime/GeneratorRegistry ownership, keyed authorization-free `ApplicationTemplate` cache, fresh per-run binding, single RunCoordinator, service-level nested traces, bounded queue, cooperative cancellation, and `Result + RunTrace` authority. State explicitly that UI does not own Gate, source, tool, repair, or fallback decisions.

- [ ] **Step 3: Document security/cost and evaluation provenance**

Update `docs/security-and-cost.md` with one-run authorization, profile fingerprint, synthetic-only policy, key-status-only display, request/token/cost caps, no arbitrary endpoint/model entry, and the separate real-online approval requirement. In `docs/evaluation.md`, preserve old CLI metrics as historical baseline and create a separate panel acceptance section whose values are populated only after Task 15; do not label fake contract tests real-local.

- [ ] **Step 4: Document the three-scene interview story**

Update `docs/demo-script.md` with:

1. P4 Shanghai lodging RAG answer and citation;
2. EMP-003 / CLAIM-1024 Agent investigation with three read-only tools;
3. pet-boarding unsupported question stopped at Evidence Gate.

For each scene list the pipeline nodes to click, expected state/source/Observation, and a CLI fallback command.

- [ ] **Step 5: Verify documentation commands and links offline**

Run:

```powershell
rtk proxy powershell -NoProfile -Command 'Test-Path README.md; Test-Path docs/panel-guide.md; Test-Path docs/architecture.md; Test-Path docs/demo-script.md; Test-Path docs/security-and-cost.md; Test-Path docs/evaluation.md'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m enterprise_rag.cli --help'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m enterprise_rag.cli panel --help'
```

Expected: all paths are `True`; both help commands exit 0 and the panel help lists `--port`/`--no-open`.

- [ ] **Step 6: Commit**

Run:

```powershell
rtk git add README.md docs/panel-guide.md docs/architecture.md docs/demo-script.md docs/security-and-cost.md docs/evaluation.md
rtk git commit -m "docs: add local engineering panel guide"
```

## Task 15: Re-run real-local panel acceptance and write evidence

**Files:**
- Create: `tests/live/test_panel_demo_live.py`
- Create: `tests/unit/test_collect_panel_acceptance.py`
- Create: `scripts/collect_panel_acceptance.py`
- Generate but do not track: `.local/benchmarks/panel-demo-live.json`
- Create: `docs/demo-artifacts/panel-demo-acceptance.json`
- Modify: `docs/demo-artifacts/README.md`
- Modify: `docs/evaluation.md`

- [ ] **Step 1: Stop for the real-local acceptance gate**

Report the already-installed local model identity/revision/digest, existing artifact path, expected no-download status, network hard block, nine intended runs, estimated duration from the historical baseline, and acceptance ceilings. Obtain explicit approval before loading the real model. Do not request or expose a key.

- [ ] **Step 2: Write the live test before running it**

Drive the real route `PanelController -> RunCoordinator -> PanelRuntime -> Application` with socket connect monkeypatched to fail. Mark the file `live`. Run each scene three times and assert:

```text
RAG: ANSWERED; answer contains CNY 600; source includes travel-policy.md::0000
Agent: ANSWERED; answer includes CLAIM-1024, P4, 680, 600;
       exact business tool order/arguments and three supported Observations
Refusal: REFUSED; reason insufficient_evidence; no answer/source/generation event
All: progress terminal events match final Trace; backend provenance is real-local
```

- [ ] **Step 3: Run the new and existing real-local live tests**

After approval, run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; $env:RAG_AGENT_ALLOW_MODEL_DOWNLOAD="false"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; & ".\.venv\Scripts\python.exe" -m pytest tests/live/test_demo_scenarios_live.py tests/live/test_panel_demo_live.py -m live -v -s'
```

Expected: both legacy CLI-core and new panel-path live suites pass with zero socket connection.

- [ ] **Step 4: Test the collector schema, statistics, and promotion boundary offline**

Before collecting real measurements, write deterministic unit tests for three-sample nearest-rank p50/p95/max, atomic local output, schema rejection of missing/extra fields, and promotion rejection of secret-like keys/values, absolute paths, environment values, prompts, responses, and traceback. Use a synthetic fixture only; do not load a model or open a socket. Run the test once and expect import/behavior failures because the collector does not exist. Then implement the pure schema/statistics/atomic-write/promotion helpers and CLI argument parsing in `scripts/collect_panel_acceptance.py`; keep real runtime imports lazy inside `collect` so the unit tests remain lightweight.

Run:

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_collect_panel_acceptance.py -v'
```

Expected after implementation: all collector/promotion tests pass offline.

- [ ] **Step 5: Collect structured acceptance evidence**

`scripts/collect_panel_acceptance.py collect` must invoke the same controller/coordinator path, instrument socket connection attempts and fail on any attempt, run three rounds per scene, preserve the exact result/trace assertions, and write the machine-local JSON atomically to `.local/benchmarks/panel-demo-live.json`. It must not store or claim to store a raw prompt/response transcript.

Record these exact evidence groups: schema version and UTC timestamp; Git commit; `execution_surface="panel-coordinator"`; a note that UI availability measures route/shell registration rather than browser paint; CPU/GPU/RAM facts; backend/model IDs, revisions, and digests without absolute paths; `ui_available_seconds`; `runtime_ready_seconds`; every initialization-stage duration; all nine raw scenario durations; nearest-rank p50/p95 plus max for each three-sample scene; semantic assertion outcomes; zero network attempts; and load counters proving index, encoder, reranker, and local generator each loaded once. Record process peak RAM/VRAM only when a reliable platform counter is available, otherwise use `null`. Record Online as `online-unverified`.

For the three samples, define percentile index as `ceil(p * n) - 1` on the sorted observations, so p95 is the observed maximum rather than an interpolated or invented value.

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; $env:RAG_AGENT_ALLOW_MODEL_DOWNLOAD="false"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; & ".\.venv\Scripts\python.exe" scripts/collect_panel_acceptance.py collect --output ".local/benchmarks/panel-demo-live.json"'
```

Expected JSON acceptance:

- cold initialization `<= 60 s` — this ceiling binds **`runtime_ready_seconds`** (index, encoder, reranker, and the default local generator all loaded, Run enabled). `ui_available_seconds` measures route/shell registration only and is reported separately with no ceiling, because the design deliberately shows the page before initialization finishes;
- each formal scenario p95 `<= 45 s`;
- three of three rounds per scene meet the semantic assertions;
- local mode records zero network attempts;
- model/backend provenance is `real-local`;
- load counters for index/encoder/reranker/local generator are all exactly one;
- process peak RAM or VRAM remains `null` when it cannot be measured safely rather than guessed.

- [ ] **Step 6: Verify and promote a sanitized evidence artifact**

First inspect the machine-local artifact and run the script's strict promotion mode:

```powershell
rtk git check-ignore .local/benchmarks/panel-demo-live.json
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" scripts/collect_panel_acceptance.py promote --input ".local/benchmarks/panel-demo-live.json" --output "docs/demo-artifacts/panel-demo-acceptance.json"'
```

Expected: `.local/` is ignored; promotion rejects secret-like keys/values, absolute paths, environment values, prompts, responses, traceback, and any field outside an explicit artifact schema; the promoted JSON preserves only the sanitized measurements and pass/fail assertions. Copy exact values from the promoted artifact into the panel section of `docs/evaluation.md`. Label it a structured summary from the panel-coordinator execution surface, not a browser E2E result or raw transcript. If any ceiling/assertion fails, preserve the failed local evidence, diagnose, fix with a new failing test, and repeat the entire nine-run acceptance; never edit numbers manually.

- [ ] **Step 7: Run final offline verification again**

Run:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest -q'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ruff.exe" check .'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ty.exe" check src'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ruff.exe" format --check tests/live/test_panel_demo_live.py tests/unit/test_collect_panel_acceptance.py scripts/collect_panel_acceptance.py'
rtk git status --short
```

Expected: default suite green with live deselected; Ruff/ty green; status contains only Task 15 evidence/test/doc changes.

- [ ] **Step 8: Commit the verified acceptance evidence**

Run:

```powershell
rtk git add tests/live/test_panel_demo_live.py tests/unit/test_collect_panel_acceptance.py scripts/collect_panel_acceptance.py docs/demo-artifacts/panel-demo-acceptance.json docs/demo-artifacts/README.md docs/evaluation.md
rtk git commit -m "test: verify real-local panel demo"
```

## Task 16: Run the one authorized real-online acceptance

This is the only task in the plan permitted to reach a real provider. It runs **three scenarios × one round** (RAG, Agent, refusal) through the same panel path, against the registered relay profile in the gitignored `config/providers.toml`, under a 30-request cap and a CNY 10 ceiling, over synthetic sample data only.

**Files:**
- Create: `tests/live/test_panel_online_live.py`
- Create: `tests/unit/test_collect_panel_online_acceptance.py`
- Create: `scripts/collect_panel_online_acceptance.py`
- Generate but do not track: `.local/benchmarks/panel-online-live.json`
- Create: `docs/demo-artifacts/panel-online-acceptance.json`
- Modify: `docs/evaluation.md`
- Modify: `docs/security-and-cost.md`
- Modify: `docs/panel-guide.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/handoffs/2026-10-11-panel-execution-progress.md`

- [ ] **Step 1: Stop for the real-online gate**

Report, and then wait: the registered profile name; `base_url`; `model_id` and the fact that it is **unverified**; `privacy_terms_url` and the fact that the page currently renders **no body text**; the request cap (30) and cost ceiling (CNY 10); that the per-token prices are a **conservative assumption, not this relay's advertised pricing**, so any cost figure is an upper bound; that only `data/sample_docs` and `data/sample_records` may leave the machine; and that this task makes about 10–12 provider calls across three scenarios.

Confirm the key is present **by name only**: report `RAG_AGENT_ONLINE_API_KEY` as configured or not configured. Never read, print, log, or store its value, and never let it enter a trace, an evidence file, or a view model. If it is not configured, stop with no repository changes.

- [ ] **Step 2: Write the live test before running it**

Mark the file `live`. Drive the real route `PanelController -> RunCoordinator -> PanelRuntime -> Application` with `generator="online"` and the registered profile, completing the one-use authorization exactly as the UI would. One round each:

```text
RAG:      ANSWERED; answer contains CNY 600; source includes travel-policy.md::0000
Agent:    ANSWERED; answer includes CLAIM-1024, P4, 680, 600; three supported Observations
Refusal:  REFUSED; reason insufficient_evidence; no answer/source/generation event
All:      progress terminal events match the final Trace;
          generation provenance is real-online, not fake-backed or real-local;
          the run never exceeds its authorized request count
```

Also assert the negative that matters most: a second run reusing the same authorization, or a run whose profile fingerprint/fallback bit does not match, must be refused **before** any provider client is created.

- [ ] **Step 3: Run the online live test once**

Requires the user's key to be set and network enabled **for this command only**:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="true"; $env:RAG_AGENT_ALLOW_MODEL_DOWNLOAD="false"; & ".\.venv\Scripts\python.exe" -m pytest tests/live/test_panel_online_live.py -m live -v -s'
```

Expected: all three scenarios behave as asserted. If the provider returns an authentication, quota, model-not-found, or protocol error, **do not retry blindly and do not raise the cap**: stop, report the sanitized error category, and treat the run as a finding — a wrong `model_id` is the single most likely cause and is a one-line profile fix followed by a fresh authorization. If the key or network is unavailable, report that plainly instead of substituting a fake run.

- [ ] **Step 4: Test the collector schema, statistics, and promotion boundary offline**

Reuse the pure helpers from `scripts/collect_panel_acceptance.py` (schema validation, nearest-rank statistics, atomic write, promotion rejection) rather than writing a second implementation; import them, and only add what the online evidence needs. Keep real runtime imports lazy inside `collect`.

Write `tests/unit/test_collect_panel_online_acceptance.py` against a synthetic fixture and run it offline before any real call:

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_collect_panel_online_acceptance.py -v'
```

Expected: schema rejection of missing/extra fields; promotion rejection of secret-like keys/values, absolute paths, environment values, prompts, responses, and traceback; and a test proving the collector's output schema cannot carry a raw API key.

- [ ] **Step 5: Collect the real-online evidence**

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="true"; $env:RAG_AGENT_ALLOW_MODEL_DOWNLOAD="false"; & ".\.venv\Scripts\python.exe" scripts/collect_panel_online_acceptance.py collect --output ".local/benchmarks/panel-online-live.json"'
```

Record exactly: schema version and UTC timestamp; git commit; `execution_surface="panel-coordinator"`; `backend_provenance="real-online"`; profile name, profile fingerprint, base_url host, and model_id; total provider requests and the configured cap; per-scenario durations and semantic outcomes; and the cost figures **labelled as upper bounds derived from assumed prices**. Record these caveats verbatim in the artifact:

- the model identity is the string configured in the profile and was **not** verified against the relay's own model list;
- the privacy/terms URL resolves to a real route whose body is **empty**;
- per-token prices are an **assumed conservative upper bound**, so costs are estimates, not measurements;
- the provider is a **third-party relay**, not the model vendor.

Never write the key, an Authorization header, a prompt, a response body, or a traceback into the artifact.

- [ ] **Step 6: Promote a sanitized artifact and correct the provenance labels**

```powershell
rtk git check-ignore .local/benchmarks/panel-online-live.json
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\python.exe" scripts/collect_panel_online_acceptance.py promote --input ".local/benchmarks/panel-online-live.json" --output "docs/demo-artifacts/panel-online-acceptance.json"'
```

Then update the documentation so nothing overclaims:

- `docs/evaluation.md`: replace the `online-unverified` placeholder with the real-online results **and** the four caveats above. Keep the real-local section separate; do not blend the two provenances.
- `docs/security-and-cost.md`: state what the one-run authorization actually enforced (request cap, budget ceiling, fingerprint binding, one-use consumption) and that the relay's pricing and model identity remain unverified.
- `docs/panel-guide.md` and `docs/demo-script.md`: add the online path and the exact command, and state that online requires the user's own key and an explicit per-run confirmation.

Do **not** describe the provider as verified, validated, or production-ready anywhere.

- [ ] **Step 7: Run final offline verification**

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest -q'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ruff.exe" check .'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ty.exe" check src'
rtk git status --short
```

Expected: the default suite is still green **with network disabled** — Task 13's loopback tripwires and the offline-by-default guarantee must survive this task unchanged. Confirm `config/providers.toml` and `.local/` are still untracked.

- [ ] **Step 8: Commit and update the progress record**

```powershell
rtk git add tests/live/test_panel_online_live.py tests/unit/test_collect_panel_online_acceptance.py scripts/collect_panel_online_acceptance.py docs/demo-artifacts/panel-online-acceptance.json docs/evaluation.md docs/security-and-cost.md docs/panel-guide.md docs/demo-script.md docs/handoffs/2026-10-11-panel-execution-progress.md
rtk git commit -m "test: verify authorized real-online panel run"
```

## Final completion checklist

> **Status 2026-10-13:** 13 of 14 items are satisfied and ticked below. The one unticked item is the real-online acceptance run, which is **closed out as not passed / not verified** — see the note under that item. Every tick corresponds to a commit and a recorded verification; nothing here is ticked on the strength of a plan rather than a measurement. **Verification method matters for the two page-behaviour items:** they rest on NiceGUI's in-process user-simulation tests, **not** a real browser — no browser end-to-end acceptance was ever performed. See `docs/evaluation.md` §8, "未做的验证：浏览器端到端".

- [x] `rag-agent panel`, `--port`, and `--no-open` work on `127.0.0.1` without native mode or a separate business API. — `857f16d`; `ui.run` overrides checked parameter-by-parameter against NiceGUI 3.18.0's signature.
- [x] Page appears before initialization; Run stays disabled until READY; initialization and run duration are separate. — `c8526ca`; `ui_available_seconds` recorded before any model load.
- [x] RAG, Agent, three presets, Custom, Local/Online, confirmation, explicit fallback, Stop, Inspector, sources, Observations, and recent-five history behave as designed. — `c8526ca`, `4ed72d1`.
- [x] Heavy components load once; cached online counters/cost persist; per-run authorization is never reused. — `d6543d3` measured each load counter at exactly 1; the real-online run's negative checks confirmed one-use consumption.
- [x] Standalone RAG and Agent-nested RAG preserve Gate/source/failure semantics and real hierarchical trace IDs. — `402a13b`.
- [x] Queue stays bounded at 256; live observer failure/disconnect never changes business results; final `Result + RunTrace` remains authoritative. — `ef5dc05`; worst case measured at 95 (96 with a cancel prefix) events.
- [x] Five terminal states satisfy their structural invariants; no late update overwrites a newer run. — `639d8ef`, `892c949`.
- [x] Secrets, prompts, hidden reasoning, raw traceback, and arbitrary environment values never enter progress, history, Inspector, or HTML. — `892c949` tripwires; `518131d` and `2dbdd9f` added inline-credential scanning.
- [x] Default suite is offline/keyless/free and fully green; Ruff, ty, touched-file format, and package build pass. — 824 passed / 7 deselected; `uv build --offline` succeeded. The format check is scoped to files this plan created, per the measured deviation recorded in the progress handoff §9.
- [x] Existing CLI behavior and historical acceptance remain green. — `d6543d3` re-ran the CLI live suite (2 passed, 173 s).
- [x] Real-local panel acceptance passes three scenes × three rounds within cold/p95 ceilings. — `d6543d3`; cold start 9.179 s, RAG/Agent/refusal p95 15.239 / 24.878 / 0.680 s.
- [ ] **The one authorized real-online run completes three scenarios × one round within the 30-request cap and CNY 10 ceiling, and its evidence carries the four caveats (unverified model identity, empty terms page, assumed prices, third-party relay).** — **NOT MET, closed out 2026-10-13 by user decision.** The run was attempted **three times** (`00f4902`, `518131d`, `b44b840`); all three failed, and the refusal scene was the only scene that ever passed. The *caps* were honoured (2 requests against a cap of 30; CNY 0.686 upper bound against a ceiling of CNY 10) and the four caveats were recorded verbatim — but no scenario ever produced an online answer, so **no acceptance artifact was promoted** and the online path remains covered only by fake contract tests. The failure was localized to the relay: `/v1/models` returns 200 while `/v1/chat/completions` returns 502, and our own code path independently observed a 5xx (`provider_unavailable`). This is an external provider outage, not a defect in this repository, and it is recorded as unverified rather than worked around. See `docs/evaluation.md`'s online section.
- [x] The default suite remains offline, keyless, and free even after Task 16, with the loopback tripwires intact. — re-verified with `RAG_AGENT_ALLOW_NETWORK=false` after the close-out: 824 passed, 7 deselected.
- [x] `docs/handoffs/2026-10-11-panel-execution-progress.md` reflects the final state: every completed task, its commit, and the current breakpoint. — `aa01e58` and the close-out commit.
