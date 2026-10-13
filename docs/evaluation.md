# 分层评测与验收结果

本文说明评测集、指标、provenance 和截至 2026-10-11 在目标笔记本上的实测结果。运行演示见 [demo-script.md](demo-script.md)，架构语义见 [architecture.md](architecture.md)。

## 1. 为什么分层

端到端“答对了”不能说明故障发生在哪一层。因此项目分别评测：

1. Retriever 是否把相关 chunk 放进 Top 20；
2. Reranker 是否把已经召回的相关 chunk 提升进 Top 5；
3. RAG 是否给出正确、可引用的答案或正确拒答；
4. Agent 是否选择正确工具/参数、保留支持证据并遵守控制流。

每份报告都包含 backend descriptors 和 `provenance`。只要该层混入 scripted/fake control-flow case，整层就标为 `fake-backed`；单个 case 仍独立标注真实或 scripted，防止 Fake 冒充真实后端证据。

## 2. 数据集

### Knowledge：24 例

`evals/knowledge.jsonl` 包含：

- 18 个可回答问题：直接问法、改写、中英混合、制度编号、金额、反例与干扰规则；
- 6 个不可回答问题：语料没有依据，预期 `insufficient_evidence`。

每例声明 expected chunk IDs、required facts、forbidden facts 和预期拒答原因。

### Agent：12 例

`evals/agent.jsonl` 包含：

- A01–A08：使用真实本地 Generator、真实 RAG 和真实只读工具的 live 业务任务；
- A09–A12：用 scripted runner 稳定覆盖非法 ID、工具执行故障、重复 action 和 iteration limit。

每例声明预期工具、精确参数、状态、required support IDs，以及适用的 refusal/error category。

所有数据均为虚构公司、虚构员工与虚构报销记录。

## 3. 指标定义与门槛

| 层 | 指标 | 完成门槛 |
|---|---|---|
| Retriever | Recall@20（18 个可回答例） | 至少 17/18 |
| Reranker | conditional Hit@5，仅统计已成功召回例 | ≥ 90% |
| RAG | grounded correct answer | 至少 16/18 |
| RAG | no-answer accuracy | 6/6 |
| RAG | citation validity | 100% |
| Agent | 正确工具与精确参数 | 至少 11/12 |
| Agent | 预期状态与 required support | 至少 11/12 |
| Agent | forbidden action | 0 |
| 失败语义 | 预设类别匹配 | 100% |

`citation_validity` 按 case 严格计算：回答的全部 source ID 必须属于该 case 的 `expected_chunk_ids`，不能借用其他问题的 gold chunk。运行时还有一层独立安全约束：`RagService` 只会把本次 Evidence Gate 放行证据中的 support ID 转成来源；前者衡量评测相关性，后者防止生成器伪造证据 ID，两者不能混称。

延迟使用每例 wall-clock duration 计算 p50/p95。它用于观察性能，不替代正确性门槛。

## 4. 运行命令

运行单层或全部：

```powershell
rtk uv run rag-agent eval run --layer retriever
rtk uv run rag-agent eval run --layer reranker
rtk uv run rag-agent eval run --layer rag
rtk uv run rag-agent eval run --layer agent
rtk uv run rag-agent eval run --layer all
```

默认单元/集成测试不加载真实模型：

```powershell
rtk uv run pytest
```

显式 live 测试：

```powershell
rtk uv run pytest tests/live -m live -v
```

## 5. 目标机实测快照

环境：Windows 11、Intel i9-13900HX、RTX 4060 Laptop 8 GB、16 GB RAM；固定模型与索引摘要见 [model-manifest.md](model-manifest.md)。Retriever、Reranker 与 Agent 来自 Task 18 真实本地长批次；RAG 指标在 Task 20 修正逐 case 引用口径并收紧最小充分证据指令后，于 2026-10-11 重新完整运行。全程没有网络或在线 API。

| 层 | 结果 |
|---|---|
| Retriever | Recall@20 = **18/18** |
| Reranker | conditional Hit@5 = **18/18** |
| RAG 可回答 | grounded correct = **16/18** |
| RAG 不可回答 | correct refusal = **6/6** |
| RAG 引用 | valid citations = **18/18** |
| RAG 失败类别 | accuracy = **6/6** |
| Agent 工具+参数 | **12/12** |
| Agent 状态 | **11/12** |
| Agent required support | **11/12** |
| Agent 失败类别 | **4/4** |
| Agent forbidden actions | **0** |

延迟：

| 路径 | p50 | p95 | 解释 |
|---|---:|---:|---|
| RAG 长批次 | 8.648 s | 14.404 s | 修正引用口径后的完整 24-case 重跑；满足标准单问 ≤45 s 目标 |
| Agent 长批次 | 14.780 s | 76.126 s | 多步任务，不满足 45 s；不得包装成标准单问结果 |

Agent A07 在该长批次中经过一次有界协议重试后仍返回 `generation_protocol`，因此状态与 required support 为 11/12。单独运行曾达到 12/12，但完成报告采用更严格的长批次结果，不隐藏 nondeterminism。

`agent` 整层报告为 `fake-backed`，因为 A09–A12 是 scripted control-flow cases；A01–A08 的 case provenance 仍为 real。`all` 汇总也因此是 `fake-backed`，这不否定其真实子层，而是诚实表示混合来源。

### 正式三幕连续验收

命令：

```powershell
rtk uv run pytest tests/live/test_demo_scenarios_live.py -m live -v -s
```

2026-10-11 在同一真实本地 Application 实例中，把“RAG 回答 → Agent 解释 → RAG 拒答”连续执行 3 轮。测试硬阻断 socket network，并断言每轮的结论、知识来源、三个业务 Observation、精确工具参数、无未授权工具和拒答语义。RAG trace 允许设计内的零次或一次 grounding repair，但禁止其他未批准路径。

| 项目 | 实测 | 预算/结论 |
|---|---:|---|
| 13 个 allowlisted 模型文件 | 4,146,440,495 bytes（约 3.862 GiB） | ≤15 GB，通过 |
| pipeline cold start | 11.101 s | ≤60 s，通过 |
| RAG 回答 p50 / p95 | 13.487 / 17.323 s | p95 ≤45 s，通过 |
| 正式 Agent p50 / p95 | 28.976 / 30.134 s | p95 ≤45 s，通过 |
| RAG 拒答 p50 / p95 | 0.746 / 0.853 s | p95 ≤45 s，通过 |
| 进程 peak working set | 6,458,970,112 bytes（约 6.02 GiB） | 已记录 |
| 按进程 VRAM | unavailable | WDDM / `nvidia-smi` 返回 N/A，不猜测 |

三幕测试共 146.02 秒并通过。正式 Agent 场景明显快于 12-case 长批次的 p95；两者口径不同，不能互相替代：三幕是固定面试路径，长批次包含更复杂真实任务和 4 个 scripted control-flow cases。

可离线展示的验收摘要位于 [demo-artifacts/task19-demo-acceptance.json](demo-artifacts/task19-demo-acceptance.json)。它是经过断言的摘要，不冒充原始 CLI transcript。

## 6. 如何读报告

报告中的关键字段：

- `provenance`：`real` 或 `fake-backed`；
- `backends`：组件名、backend kind、revision/digest 等可用信息；
- `metrics.<name>.numerator/denominator`：不要只看格式化百分比；
- `latency.p50_ms/p95_ms`：该次批次分布；
- `cases`：每例命中、状态、引用、support 和 scenario 类型。

评测通过不等于“模型不会幻觉”。它证明的是：在固定虚构数据、固定模型与固定配置上，检索、拒答、引用和受约束 Agent 达到声明门槛。

## 7. 默认测试与真实验收的区别

- 默认 `pytest`：Fake/contract 为主，验证确定性、边界、错误类别与控制流；无需模型、GPU、网络或密钥。
- `tests/live`：真实本地 Retriever/Reranker/Generator；需要批准过的模型缓存。
- CLI `eval`：真实已组合业务主链；运行较慢。
- online live eval：**已尝试、未通过**——计划授权的那一次真实在线验收共尝试三次（2026-10-12），全部失败；在线路径目前只有 fake 契约测试覆盖。任何真实中转站调用都必须先完成 [独立成本与隐私审批](security-and-cost.md)。

正式面试当天仍应先运行 `doctor`；只有在机器状态发生变化时才需要重新生成性能证据。演示步骤见 [Demo 脚本](demo-script.md)。

## 8. 面板验收（panel acceptance）

**本节的值在真实本地验收（计划 Task 15）跑完之前一律留空**——不要在这里编造数字，也不要把面板的 fake 契约测试标成 real-local。

面板验收与上面的 CLI 基线是**两种不同的执行面（execution surface）**，必须分开记录：

- `execution_surface = "panel-coordinator"`：走 `PanelController → RunCoordinator → PanelRuntime → Application` 的真实路径，但**不**包含浏览器绘制；`ui_available_seconds` 度量的是路由/UI 外壳注册，不是浏览器首屏。
- 语义断言（三幕 × 三轮）与性能上限（冷启动 `runtime_ready_seconds <= 60 s`、每个正式场景 p95 `<= 45 s`）由 `scripts/collect_panel_acceptance.py` 收集到机器本地文件，再经严格 promotion 写成 `docs/demo-artifacts/panel-demo-acceptance.json`。
- 该 artifact 只包含结构化测量与通过/失败断言，**不含**原始 prompt/response 文本。

面板的**默认测试**（离线、无密钥、无网络、免费）证明的是另一件事：契约、边界、取消、脱敏、有界队列与 UI 只渲染视图模型。它**不**证明真实模型在真实硬件上的答案质量——那由第 5 节的 CLI 基线和 Task 15 的面板验收分别负责。

### 未做的验证：浏览器端到端

**面板从来没有在真实浏览器里被人打开过并作为验收的一部分。** 这一点必须明确，否则上面那些勾会被误读成"UI 已验证"：

- Task 15 的面板验收走的是 `PanelController → RunCoordinator → PanelRuntime → Application`，**按设计就不包含浏览器**；它证明的是编排、语义断言与性能上限。
- `ui_available_seconds`（实测 0.0000332 s）度量的是**路由/UI 外壳注册**，**不是**浏览器首屏，也不是绘制完成。
- 页面行为（预设卡片、Custom 输入、Local/Online 切换、确认视图、画布节点、Inspector、Stop、五终态渲染、最近五条历史）只由 **NiceGUI 的 user-simulation 测试**覆盖（`tests/integration/test_panel_page.py`，14 个测试，跑在 `panel_main.py` 这个 harness 上）。这是**进程内的页面模拟**，不是真实浏览器。
- 因此**未经验证**的包括：真实浏览器里的首屏渲染、CSS/布局是否可用、Quasar 组件在真实点击下的行为、`--port` / `--no-open` 在真实浏览器会话里的体验。
- 计划最终 checklist 里与页面行为有关的两条（"页面早于初始化出现"、"三预设/Custom/Stop/Inspector 等按设计工作"）依据的正是这套模拟测试，**不是**浏览器验收。

这不影响 real-local 验收的结论（那本来就不含浏览器），但它是一个**真实存在的验证缺口**，不得用"面板验收通过"来覆盖。

### 实测快照（2026-10-12，目标笔记本）

来源：`docs/demo-artifacts/panel-demo-acceptance.json`（由 `scripts/collect_panel_acceptance.py` 生成并经严格 promotion 脱敏）。**这是结构化摘要，不是浏览器 E2E 结果，也不是原始 transcript。**

| 项目 | 值 | 上限 / 要求 |
|---|---|---|
| execution surface | `panel-coordinator` | — |
| git commit | `39d8f6216f9f718a2281c9d23bfb086a4bd013e9` | — |
| backend provenance | `real-local`（`Qwen/Qwen3-4B-GGUF`，revision `bc640142c66e1fdd12af0bd68f40445458f3869b`，digest `aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f`） | 必须 real-local |
| 冷启动 `runtime_ready_seconds` | **9.179 s** | ≤ 60 s ✅ |
| `ui_available_seconds` | **0.0000332 s** | 无上限（只度量路由/外壳注册） |
| RAG 场景 p95 | **15.239 s**（样本 15.239 / 7.025 / 7.765） | ≤ 45 s ✅ |
| Agent 场景 p95 | **24.878 s**（样本 23.770 / 24.878 / 24.780） | ≤ 45 s ✅ |
| 拒答场景 p95 | **0.680 s**（样本 0.585 / 0.660 / 0.680） | ≤ 45 s ✅ |
| 三幕 × 三轮语义断言 | 全部通过（`failures` 均为空） | 3/3 ✅ |
| 网络尝试次数 | **0** | 必须为 0 ✅ |
| 加载计数 | index / encoder / reranker / local_generator 各 **1** | 各恰好 1 ✅ |
| 进程峰值 RAM | 7,009,529,856 bytes | 记录实际值 |
| 进程峰值 VRAM | `null` | 无可靠计数器时如实为 null |
| Online | `online-attempted-not-verified` | 见下节 |
### 真实在线：三次尝试，**均未通过**（2026-10-12）

这是计划授权的**唯一一次**真实在线验收，为定位问题共尝试了**三次**。**本节不给出任何在线验收结论。**

| 场景 | 第 1 次（`model_id` 带 `[1M]` 后缀） | 第 2 次（`model_id = deepseek-v4.1-flash`） | 第 3 次（2026-10-12，同一 `model_id`） |
|---|---|---|---|
| RAG | `failed` · `invalid_request` · 16.5 s | `failed` · **`timeout`** · 52.6 s | `failed` · **`provider_unavailable`** · 39.5 s |
| Agent | `failed` · `invalid_request` · 3.9 s | `failed` · **`timeout`** · 48.3 s | `failed` · **`timeout`** · 48.7 s |
| 拒答 | `refused` ✅ · 0.6 s | `refused` ✅ · 0.5 s | `refused` ✅ · 0.5 s |
| 真实请求 | 2 次 | 2 次 | 2 次 |
| 成本上界 | CNY 0.686 | CNY 0.686 | CNY 0.686 |
| 负例检查 | 指纹项失败（已修复） | **三项全通过** | **三项全通过** |

**第 3 次尝试（2026-10-12，`RAG_AGENT_ALLOW_NETWORK=true`，`RAG_AGENT_ALLOW_MODEL_DOWNLOAD=false`）带来的唯一新信息：**

- 这是第一次由**我们自己的代码路径**观察到 5xx。RAG 幕的 `error_category = provider_unavailable`，而按 `_mapped_online_error`（`generation.py:619-622`）该类别**只在** `status_code >= 500`（或 `APIConnectionError` / `InternalServerError`）时产生。此前两次验收运行只见到 400 与超时，5xx 只出现在绕过项目代码的直连探测里；现在**两条相互独立的路径**（我们的编排路径 + 直连 `Invoke-RestMethod`）都观察到了 5xx。
- Agent 幕仍是 `timeout`（48.7 s ≈ 45 s `online_timeout_seconds` 上限 + 编排开销），与第 2 次同形。
- 拒答幕仍然通过（0.5 s，Gate 停住、不调生成器）。
- 2 次请求、成本上界 CNY 0.686（**基于假设单价的上界**，非实测）；负例检查三项全通过；`network_allowed_for_this_run = true`。
- **未**重试、**未**提高请求上限、**未**换模型、**未**改代码绕过。
- 该失败运行**未**提升为验收 artifact——`docs/demo-artifacts/` 下**没有** `panel-online-acceptance.json`。原始证据留在机器本地 `.local/benchmarks/panel-online-live.json`（未跟踪、被 `.gitignore` 忽略）。

**能确定的：**

- 三次的**拒答场景都通过**——它在 Evidence Gate 停住、**不调用生成器**。这独立证明面板的检索 / 重排 / Gate / 拒答链路是好的，失败只在"在线生成"这一腿。
- 第 1 次的 `invalid_request`（HTTP 400）在去掉 `[1M]` 后缀后**消失**，说明那个后缀确实是 400 的原因，`deepseek-v4.1-flash` 已通过**请求校验层**。
- 第 2 次变成 `timeout`：**不是** HTTP 错误码，而是**在 45 s 的 `online_timeout_seconds` 内没有收到响应**。这属于新的失败模式，不是"请求被拒"。
- 第 3 次由**我方代码路径**观察到 `provider_unavailable`（即 5xx），与绕过项目代码的直连探测所见的 502 指向同一件事：失败发生在中转站**与它的上游之间**，与我们请求的形状无关。
- `effective_base_url` 记录为 `https://ai.yangwang.name/v1/`——这是**代码实际构造出来的 SDK 客户端 URL**（本地构造、零网络），比只读配置里的 host 更强。

**不能确定的：**

- 中转站为何不在 45 s 内响应：可能是它自身排队/缓慢，可能是它不支持 strict `json_schema` 响应格式而挂住，也可能是模型 id 仍不对而它以"不响应"而非报错的方式表现。**尚未区分**。
- 中转站上游究竟是谁（caveat 4）。

### 第 3 步：一次直连探测（绕过本项目代码），结论已收窄

用 `Invoke-RestMethod` 直接 POST `https://ai.yangwang.name/v1/chat/completions`，**不带任何 `response_format`**、`max_tokens = 8`：

```text
9.6s  ERROR -> (502) Bad Gateway
```

这一条排除了整类猜测：

- **中转站可达、鉴权通过**（否则是 401 而非 502），请求确实到达了它。
- **502 是它自己拿不到上游有效响应**——失败发生在**它与它的上游之间**。
- **9.6 s 就返回**，远早于 180 s 上限 ⇒ 不是"我们等太久"。
- 该探测**完全没有 `response_format`**，却照样失败 ⇒ **strict `json_schema` 假设被排除**，我们请求的形状不是原因。

**仍然未区分的三种可能**（都属中转站一侧）：①它当下故障/上游宕机；②它并不真的提供 `deepseek-v4.1-flash`（有些中转站对未知模型返回 502 而非 404）；③它账户/额度层面的问题。

另注：验收运行拿到的是 **45 s 超时**（第 3 次还拿到一次 5xx），直连探测拿到的是 **9.6 s 的 502**——同一中转站多种失败方式，说明**它自身行为不稳定**。

### 第 4 步：模型列表 —— **模型 id 是对的，问题在中转站**

`GET /v1/models`（带鉴权）**正常返回 200**，列出该中转站提供：

```text
deepseek-v4.1-flash      ← 与本项目 profile 中的 model_id 完全一致
glm-5.3
glm-5.3-flash
mimo-v2.6-flash
mimo-v2.6-pro
minimax-m3
muse-spark-1.3-contributor
```

据此可以把可能性收干净：

| 假设 | 判定 |
|---|---|
| 模型 id 不对 | **排除** —— `/v1/models` 里就有 `deepseek-v4.1-flash` |
| 不支持 strict `json_schema` | **排除** —— 不带 `response_format` 的直连探测同样失败 |
| 我们的超时设得太短 | **排除** —— 它 9.6 s 就返回 502，不是慢而是坏 |
| **中转站完成路径（其上游）故障** | **最可能** |

关键对比：**`/v1/models` 返回 200，而 `/v1/chat/completions` 返回 502** —— 该中转站的**控制面（鉴权、模型列表）正常，数据面（实际生成）故障**。

**caveat 1 据此修正为更准确的表述**：`model_id` 确实出现在该中转站自己的模型列表里（已核对），但**这不证明**它真的在服务该厂商的模型——它只证明中转站**声称**提供。

**尚未做的事**：没有盲目重试、没有提高请求上限、没有改代码逻辑去"绕过"它、没有为了"通过"而换模型。

四条 caveat 依旧成立且**原样**适用于任何后续在线结果；**不得**把这三次尝试描述为"已验证 / 已校验 / 可生产"。

**Task 16 的收尾结论（2026-10-12）：真实在线未通过、未验证。** 三次真实在线尝试（外加一次绕过本项目代码的直连探测）**全部失败**，且没有任何一次成功提升为验收 artifact：`docs/demo-artifacts/` 下**不存在** `panel-online-acceptance.json`。因此：

- 面板的**在线路径只有 fake 契约测试覆盖**（`tests/unit/test_collect_panel_online_acceptance.py`、`tests/integration/test_panel_authorization.py`、`tests/live/test_panel_online_live.py` 的缺-key 路径）；这些测试证明的是**授权、上限、指纹绑定、一次性消费与脱敏**，**不是**真实模型的输出质量。
- **这家第三方中转站的实际服务未经验证**：它的生成端点在我们这一侧观察到的全部行为是 400 / 超时 / 5xx，从未返回过一次可用的补全。
- 真正跑通的在线语义验收**尚未发生**；本节与第 8 节的 `real-local` 值是**两种不同 provenance，不得互相替代**。

各初始化阶段实测耗时：

| 阶段 | 耗时 |
|---|---|
| `CHECKING_CONFIG` | 4.2 ms |
| `LOADING_INDEX` | 5.0 ms |
| `LOADING_RETRIEVER` | 2,838.0 ms |
| `LOADING_RERANKER` | 989.4 ms |
| `PREPARING_DEFAULT_LOCAL_GENERATOR` | 5,341.9 ms |
| `READY` | 0.03 ms |

同时重跑了**既有 CLI 真实本地套件**（`tests/live/test_demo_scenarios_live.py` 与 `tests/live/test_rag_live.py`，2 passed，173 s），证明面板改造没有破坏 CLI 基线：CLI 冷启动 8.19 s、RAG 答案 p95 15.36 s、Agent p95 27.99 s、拒答 p95 0.71 s。

**这一节只包含 `real-local` 证据。** 面板的默认离线测试（777 passed）证明的是契约、边界、取消、脱敏与有界队列，**不是**真实模型质量。
