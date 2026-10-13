# 本地工程演示面板交接

> **状态提示（2026-10-13）**：本文件是**设计阶段**的交接快照，正文中的"截至 2026-10-11"数据、以及"尚未进行真实在线调用"一类的表述，描述的是**写这份文件时**的状态，**已被后续执行阶段取代**。面板实施计划（Task 1–16）已经落地，权威进度记录是 [handoffs/2026-10-11-panel-execution-progress.md](handoffs/2026-10-11-panel-execution-progress.md)；真实在线的实际结果见 [evaluation.md](evaluation.md) 的在线小节。下面第 1 节与第 10 节的两条"没有真实在线调用"结论**已不再成立**。

CLI Core 已完成真实本地验收。本文件把未来“流水线工作台”需要复用的服务、契约、安全边界和仍待选择的 UI framework 交接清楚；它不是面板实施计划，也不授权现在创建前端、HTTP API、部署或发布。

当前使用方法见 [README](../README.md)，架构见 [architecture.md](architecture.md)，三幕验收见 [demo-script.md](demo-script.md)。

## 1. CLI Core 完成状态

截至 2026-10-11：

- 默认离线测试：547 passed，5 live deselected；
- ruff、ty、sdist/wheel build：通过；
- 正式三幕真实本地场景：每幕连续 3 次通过；
- cold start 11.101 s；RAG p95 17.323 s；正式 Agent p95 30.134 s；
- 13 个 allowlisted 模型文件 4,146,440,495 bytes；
- 未进行真实在线 API 调用**（2026-10-12 更新：执行阶段已尝试三次，全部失败——见 [evaluation.md](evaluation.md)）**；
- 未 push、publish 或 deploy。

Git 交付由 `implementation-start` 起的任务级提交组成，Task 19 文档与验收提交为 `6ca0f5a`。

## 2. Acceptance evidence matrix

| 承诺 | 实现证据 | 测试/报告/命令 | 状态 |
|---|---|---|---|
| RAG 可脱离 Agent 独立运行 | `RagService.ask/search_evidence`，Application 可只 compose RAG | `tests/integration/test_rag_service.py`; `rag-agent rag ask ...` | 通过 |
| dense+sparse 双路召回 | 同一次 GTE 编码生成两路表示，独立排名、RRF 融合 | `tests/unit/test_retrieval.py`; `tests/live/test_gte_retriever_live.py` | 通过 |
| 稳定 chunk 与各路 score/rank | immutable `RetrievedChunk` / `RerankedChunk` | `tests/unit/test_models.py`; `tests/integration/test_retriever_fake.py` | 通过 |
| 真实 Retriever | pinned `gte-multilingual-base` + real index | `tests/live/test_gte_retriever_live.py`; eval Recall@20 18/18 | 通过 |
| 真实 Reranker | pinned `gte-multilingual-reranker-base` | `tests/live/test_gte_reranker_live.py`; conditional Hit@5 18/18 | 通过 |
| 生成前 Evidence Gate | score/identity/text programmatic gate，拒答前不调用 Generator | `tests/integration/test_rag_service.py`; 三幕宠物寄养费场景 | 通过 |
| 结构化真实本地 Generator | Qwen3-4B Q5_K_M + llama.cpp strict schema | `tests/live/test_local_generator_live.py` | 通过 |
| 生成后 grounding + 一次 repair | support ID、quote、关键事实、support score 校验 | `tests/unit/test_grounding.py`; `tests/live/test_rag_live.py` | 通过 |
| Agent 不绕过 RAG | `search_knowledge` 调 RAG retrieval/rerank/Gate；finish 必须引用知识证据 | `tests/agent/test_agent_grounding.py`; `test_agent_service.py` | 通过 |
| 三个只读工具 | allowlisted employee/claim/trip registry，无写动作 | `tests/unit/test_agent_tools.py`; `tests/agent/test_tool_failures.py` | 通过 |
| bounded Agent | 最多 6 action、schema、ID、重复 action 与 iteration guard | `tests/agent/test_agent_service.py` | 通过 |
| 只开放 Generator | CLI local/online；禁止单层 Retriever/Reranker env override | `tests/unit/test_config.py`; `tests/integration/test_cli.py` | 通过 |
| online preflight | registered profile、HTTPS、secret env、synthetic-only、请求/成本上界 | `tests/unit/test_provider_profiles.py`; `test_online_generator.py` | 离线契约通过；一次授权调用全部失败（见 [evaluation.md](evaluation.md)） |
| 受控 fallback | 只有显式 online→local；仍经过同一 Gate/Validator | `tests/integration/test_generator_selection.py` | 通过 |
| 默认测试离线确定 | pytest 默认排除 live；Fake 明确 provenance | `rtk uv run pytest` → 547 passed / 5 deselected | 通过 |
| 分层评测 | 24 knowledge + 12 Agent，real/fake-backed provenance | `rag-agent eval run --layer all`; [evaluation.md](evaluation.md) | 通过 |
| no-answer 与引用 | 6/6 拒答；18/18 引用合法 | [evaluation.md](evaluation.md) | 通过 |
| 正式 Demo 稳定性 | 三幕各 3 次，精确工具参数、support、无未授权工具 | `tests/live/test_demo_scenarios_live.py` | 通过 |
| 硬件预算 | 模型 ≤15 GB、cold ≤60 s、三幕 p95 ≤45 s | [task19-demo-acceptance.json](demo-artifacts/task19-demo-acceptance.json) | 通过 |
| 环境诊断 | Python/disk/RAM/CUDA/model/index/profile/network | `tests/integration/test_doctor.py`; `rag-agent doctor` | 通过 |
| secret/model/index 不入 Git | `.gitignore` + tracked file audit | `git ls-files .local '*.gguf' '*.safetensors' config/providers.toml .env` 为空 | 通过 |
| Trace contract | immutable events、状态、耗时、parent ID、递归脱敏 | `tests/unit/test_execution_trace.py`; CLI `--format json --trace` | contract 通过；见已知限制 |

## 3. 面板目标

面板不是聊天产品皮肤，而是让面试官在一屏内看到：

- 输入如何进入检索；
- dense/sparse 如何互补并被 RRF 融合；
- Reranker 如何改变候选顺序；
- Evidence Gate 为什么放行或拒答；
- Generator 输出怎样被 grounding 校验/repair；
- Agent 调用了哪些允许工具、参数是什么、得到了哪些 Observation；
- 每一阶段花了多久、用了哪个 backend、失败类别是什么。

## 4. 已批准布局：流水线工作台

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Question / Task    Generator [local|online]    Run    Safety status │
├──────────────────────────────────────────────┬───────────────────────┤
│ Pipeline                                     │ Fixed right inspector │
│ dense ─ sparse ─ RRF ─ rerank ─ Gate         │ selected stage        │
│                    └─ generate ─ validate     │ input/output summary  │
│ Agent: plan ─ action ─ observation ─ finish  │ score/rank/backend    │
├──────────────────────────────────────────────┤ revision/duration/error│
│ Answer / refusal / failure                   │                       │
│ Sources + business observations              │                       │
└──────────────────────────────────────────────┴───────────────────────┘
```

顶部只允许：问题/任务、RAG/Agent 模式、Generator 选择、已登记 Provider（online 时）、显式 fallback 和运行按钮。不得增加“跳过 Gate”“调低验证”“任意 base URL”“任意工具”等危险开关。

主区应把 `answered`、`refused`、`failed` 用不同视觉语义表达，不能把安全拒答画成红色系统崩溃。

右侧 inspector 固定存在。点击阶段后显示公开字段；不展示 hidden chain-of-thought、完整 system prompt、secret 或未经批准的真实数据。

## 5. 同进程集成边界

首版必须和 CLI 一样，直接在同一 Python 进程调用业务核心，不预先建设 HTTP API。

```python
selected_provider = provider if generator == "online" else None
fallback_enabled = generator == "online" and fallback_local is True
application = build_application(
    compose_rag=True,
    compose_agent=mode == "agent",
    generator_choice=generator,
    provider_name=selected_provider,
    fallback_local=fallback_enabled,
)
request_fallback = True if fallback_enabled else None

if mode == "rag":
    result = await application.ask_rag(
        RagAskRequest(question, generator, selected_provider, request_fallback)
    )
else:
    result = await application.run_agent(
        AgentRunRequest(task, generator, selected_provider, request_fallback)
    )
```

`build_application()` 的 `fallback_local` 是组合开关，必须传 `bool`；单次 Request 的同名字段表示用户是否显式启用回退，只能传 `True` 或 `None`。local 模式下还必须把 `provider` 和 Request 的 `fallback_local` 都设为 `None`。

知识构建、评测和诊断需要各自的轻量组合，不能盲目复用上面的回答 Application：

```python
builder = build_application(compose_rag=False)
build_outcome = await builder.build_knowledge(path)

evaluator = build_application(
    compose_evaluation=True,
    evaluation_layer=layer,
)
evaluation_outcome = await evaluator.run_evaluation(layer)

doctor = build_application(
    compose_rag=False,
    compose_diagnostics=True,
)
diagnostic_outcome = await doctor.run_diagnostics()
```

fresh knowledge build 必须先使用 `compose_rag=False`，否则组合阶段会尝试加载尚未生成的 index。评测必须显式 `compose_evaluation=True`，诊断必须显式 `compose_diagnostics=True`；否则对应调用按契约返回 `configuration` failure。

不要调用 CLI 子进程并解析 text。UI adapter 读取 `RagResult`、`AgentResult`、`RunTrace`、`CommandOutcome` 的结构化字段，渲染层不复制业务决策。

## 6. View-model 映射

| UI 区域 | 结构化来源 |
|---|---|
| 最终状态 | `result.status` |
| 回答/拒答/错误 | `answer`, `refusal_reason`, `error_category` |
| 知识来源 | `sources` |
| Agent claims | `claims[].text/support_ids/supporting_quotes` |
| 工具轨迹 | `tool_trace[].name/arguments/status/error_category` |
| 业务证据 | `observations[].observation_id/source/status/facts/support_text` |
| 阶段节点 | `trace.events[].stage/status/duration_ms/error_category` |
| Inspector attributes | `trace.events[].attributes` |
| 父子关系 | `event_id/parent_id` |

面板不得从 answer 文本反向推断引用、状态或错误；这些字段已有正式契约。

## 7. Trace 的当前现实与面板前置设计点

`TraceRecorder` contract 支持 parent/child；当前 RAG 与 Agent 生产事件主要是隐式 Run 根下的顶层、有序阶段流。Agent 的 `search_knowledge` 也不会把内部 retrieval/reranking/Gate 子 trace 合并进 Agent trace。dense/sparse/RRF score/rank 存在检索对象与评测报告中，但当前 `RagResult` 不直接暴露候选列表。

因此面板实施前必须先做一个短设计决定：

1. 首版只忠实展示现有顶层事件，并在 Retriever inspector 中使用专门的只读 debug/view-model contract；或
2. 先扩展服务级 trace，让 retrieval 包含 dense/sparse/fusion 子事件、Agent action 包含知识子链，再开发 UI。

推荐第 2 种，因为它最符合“流水线工作台”的价值；但这需要独立设计、TDD 和性能/隐私评审，不能由 UI 自己伪造层级。

## 8. Framework 选择仍待确认

本交接不预选 Streamlit、Gradio、NiceGUI、Textual 或其他框架。下一次 brainstorming 只需比较 2–3 个候选，并围绕这些已固定约束：

- 本地运行、同一 Python 进程；
- 支持 async 或安全封装；
- 固定右侧 inspector 和可点击 pipeline 节点；
- 能渲染较大的结构化 trace，不阻塞 UI；
- 可明确显示 local/online 安全状态；
- 不为了框架引入独立 API、登录、云部署或遥测；
- 在 16 GB RAM 机器上不能显著挤压模型预算。

Framework 选定、书面设计和实施计划再次批准前，不创建面板生产代码。

## 9. 面板必须继承的安全规则

- 默认 network off；online 必须选择 registered Profile；
- secret 只从环境变量/secret manager 读取，UI 不回显；
- online 前显示供应商、模型 ID、endpoint、条款、价格、请求数、预算和 synthetic-only；
- 不允许真实企业/个人文档外发；
- 只读工具 allowlist 不扩张；
- fallback 必须显式，且只发生在 Generator；
- refused 与 failed 分开展示；
- 不显示 hidden chain-of-thought；
- 不自动下载新模型、调用 API、push、publish 或 deploy。

## 10. 已知限制与诚实表述

- 生产 trace 目前主要是平面阶段流，不应宣称已经提供完整嵌套事件树。
- Evidence Gate 是 relevance/source 完整性门，不是通用逻辑可回答性证明。
- Grounding Validator 是 exact ID、quote、有限关键事实正则与 support scorer 的组合，不是数学上的无幻觉证明。
- PyTorch 当前是 CPU build；llama.cpp GPU offload 可用。两者不要混称“全链 GPU”。
- Agent 12-case 长批次 p95 为 76.126 s，虽然正式面试场景 p95 为 30.134 s；必须分别报告。
- A07 长批次仍有一次 `generation_protocol` 波动。
- Agent 与 `all` eval 因 4 个 scripted control-flow cases 标记为 `fake-backed`；A01–A08 才是 real cases。
- Windows WDDM 没有提供按进程 VRAM 数值；只报告 unavailable，不猜测。
- online adapter 的**离线**完成证据仅为契约/preflight 测试。**（2026-10-12 更新：执行阶段已做过一次被授权的真实在线验收，三次尝试全部失败，未通过、未验证；见 [evaluation.md](evaluation.md)。）**

## 11. 下一检查点

用户审阅并接受 CLI 完成证据后，单独启动面板 brainstorming：先确认 framework 和 trace 扩展策略，再写书面设计与实施计划。此检查点之前保持仓库本地，不 push、publish 或 deploy。
