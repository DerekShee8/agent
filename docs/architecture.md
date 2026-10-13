# 架构与数据流

本文解释当前 CLI Core 的实际实现。它补充 [README](../README.md) 的使用说明；安全门槛见 [security-and-cost.md](security-and-cost.md)，指标与验收见 [evaluation.md](evaluation.md)。

## 1. 边界与原则

系统有两个独立业务入口：

- `RagService`：知识问答，可脱离 Agent 独立运行和评测。
- `AgentService`：在 RAG 证据链之上组合只读业务事实，不得绕过 RAG 的拒答或失败。

CLI 只解析参数、调用 Application 和渲染结果。Retriever/Reranker 选择、阈值、来源筛选、工具 allowlist、回答/拒答和错误语义属于业务层，不是 CLI 开关。

```text
CLI / future local panel
          │
          ▼
      Application
     ┌────┴─────┐
     ▼          ▼
RagService   AgentService
     │          │ bounded Plan–Act–Observe
     │          ├─ search_knowledge → RagService.search_evidence
     │          └─ three read-only fact tools
     └──────────┬───────────────────────────────────────────
                ▼
Hybrid Retriever → Reranker → Evidence Gate → Generator
                                              → Grounding Validator
```

## 2. 固定 RAG 主链

```text
Query
  ├─ dense semantic retrieval
  └─ sparse lexical retrieval
          ↓
deterministic Reciprocal Rank Fusion (RRF)
          ↓ retrieve_k = 20
multilingual cross-encoder Reranker
          ↓ rerank_k = 5
Evidence Gate
  ├─ insufficient → programmatic refusal
  └─ sufficient   → structured Generator
                         ↓
                 Grounding Validator
                   ├─ valid → answer
                   └─ invalid → one repair → validate again
                                             ├─ valid → answer
                                             └─ invalid → refusal
```

始终满足 `retrieve_k >= rerank_k > 0`。任何 dense/sparse 编码、融合、Retriever 或 Reranker 故障都会终止本次运行；系统不会偷偷退化成未经独立验收的单路检索，也不会跳过重排后继续生成。

### 2.1 Dense 与 sparse 如何配合

固定 Retriever 为 `Alibaba-NLP/gte-multilingual-base`：

- dense 向量擅长召回措辞不同但语义相近的内容；
- sparse 权重擅长保留 `POLICY-TRAVEL-04`、`P4`、`CNY 600`、业务 ID 等精确词项；
- 两路各自独立排名后取候选并集，通过固定 `rrf_k=60` 做 deterministic RRF；
- 同分时使用稳定 `chunk_id` 排序，因此索引与配置不变时结果可重复。

每个 `RetrievedChunk` 保留 `dense_score/rank`、`sparse_score/rank`、融合后的 `retrieval_score/rank`、原文、文件名和稳定 `chunk_id`。Reranker 不覆盖这些字段，而是在 `RerankedChunk` 上增加 `rerank_score/rank`。

### 2.2 Reranker 与 Evidence Gate

固定 Reranker 为 `Alibaba-NLP/gte-multilingual-reranker-base`，对 Query–chunk 对做更精细的相关性判断。Evidence Gate 是独立的程序化控制点：只保留来源、身份、文本完整且 `rerank_score >= 0.20` 的证据。没有合格证据时返回：

```text
status=refused
refusal_reason=insufficient_evidence
```

此时 Generator 根本不会被调用。这与模型超时、索引损坏等 `failed` 状态不同。

### 2.3 Generator 与 Grounding 闭环

Generator 只能返回结构化 `AnswerDraft`：回答文本，以及每个 claim 的 `support_ids` 和 supporting quotes。业务层随后：

1. 把 quote 重新绑定到本轮允许证据的原文；
2. 检查 support ID 是否存在且属于本轮证据集；
3. 检查 claim 是否出现在答案中、quote 是否真的存在于证据；
4. 检查 claim 中的金额、日期、编号等关键事实是否出现在引用文本；
5. 使用同一 GTE Reranker 作为 claim-support scorer，门槛为 `0.40`。

首次失败只允许一次 repair。repair 后仍有问题、引用为空或引用越界时，程序化返回 `grounding_validation_failed`，不会把失败草稿交给用户。因此 Generator 与 Gate 不是“模型自我反省”的松散闭环，而是生成前、生成后两个独立程序化控制点。

## 3. Agent

Agent 使用最多 6 步的 bounded Plan–Act–Observe 循环。唯一允许的 action 是：

```text
search_knowledge
get_employee_profile
get_expense_claim
get_business_trip
finish
refuse
```

三个业务工具只读、只访问 `data/sample_records` 中的虚构 JSON：

| 工具 | 唯一参数 | 返回事实 |
|---|---|---|
| `get_employee_profile` | `employee_id` | 部门、职级、差旅等级 |
| `get_expense_claim` | `claim_id` | 金额、票据、状态、拒绝码和关联 ID |
| `get_business_trip` | `trip_id` | 目的地、城市等级、日期、审批和预算 |

action 先经过 exact-schema 和 allowlist 校验；业务 ID 还要匹配固定格式。重复的工具+参数 fingerprint 被拒绝，超过 6 步返回 `iteration_limit`。不存在的记录形成 `not_found` Observation；工具异常则 `failed`，Agent 不得猜值。

Agent 的 `finish` 还有额外不变量：最终答案必须引用至少一条知识证据；如果使用了业务事实，相应 claim 必须引用稳定的 `obs:<tool>:<id>`。最终合成与 RAG 一样经过 Grounding Validator，最多 repair 一次。

## 4. Generator 选择与受控降级

Retriever、Reranker、Gate 和 Validator 固定。普通用户只能选择 Generator：

- `local`：`Qwen3-4B-Q5_K_M.gguf`，默认离线后端；
- `online`：OpenAI-compatible adapter，但只能选择 `config/providers.toml` 中已登记且启用的 Profile；
- `--fallback-local`：只有显式指定时，在线 Generator 的受控错误才可切回本地，并在 trace 中产生 `backend_switch/degraded`。

本地 fallback 仍复用同一个 Gate、结构化 schema 和 Grounding Validator。任何其他层都没有“跳过后继续”的 fallback。

| 阶段/情况 | 业务结果 |
|---|---|
| Retriever/Reranker/index 失败 | `failed`，不生成 |
| Gate 无合格证据 | `refused: insufficient_evidence` |
| 本地 Generator 失败 | `failed` |
| 在线 Generator 失败且未启用 fallback | `failed` |
| 在线 Generator 受控失败且显式 fallback | 切到本地并记 `degraded` |
| 初次 grounding 失败 | repair 一次 |
| repair 后仍失败 | `refused: grounding_validation_failed` |
| Agent 重复 action / 超步数 | 程序化拒答 |
| 工具异常 | `failed`，不猜测 |

## 5. 结果契约

`RagResult` 与 `AgentResult` 都只有三种互斥状态：

- `answered`：有答案和至少一个合法来源；
- `refused`：无答案、无来源，有稳定 `refusal_reason`；
- `failed`：无答案、无来源，有稳定 `error_category`。

这种状态机保证“没有制度依据”和“系统坏了”不会混成同一种用户体验。CLI 对 `refused` 返回 0，对 `failed` 返回 1。

## 6. Trace contract

`RunTrace` 是一次运行的不可变记录。Run 是隐式根；当前业务服务按执行顺序产生顶层阶段事件：

```text
RAG Run
├─ retrieval
├─ reranking
├─ evidence_gate
├─ generation
├─ grounding_validation
├─ repair_generation? / repair_validation?
└─ backend_switch?              # 只在显式 online→local 时

Agent Run
├─ agent_plan                   # 每一步
├─ agent_action
├─ agent_observation?
├─ grounding_validation
├─ grounding_issue?
└─ repair_generation? / repair_validation?
```

每个事件包含 `event_id`、`parent_id`、`stage`、`status`、单调时钟起点、`duration_ms`、公开 attributes 与可选 `error_category`。contract 支持显式 parent/child；当前服务事件主要是隐式 Run 下的顶层节点。dense/sparse/RRF 细节保存在检索对象和 Retriever 评测中，当前 CLI 不把三者拆成独立 trace 子事件。

允许状态为 `started | succeeded | refused | failed | skipped | degraded`。存储前递归脱敏 `api_key`、`authorization`、`secret`、`token`、`password`、`system_prompt`。Trace 不保存 hidden chain-of-thought。

## 7. 索引与供应链

索引由 UTF-8 `.md` / `.txt` 文档确定性切分并生成，默认目录 `.local/index`。Manifest 保存 source digest、chunk rows、embedding 维度、backend kind 和各产物 digest；加载时文件缺失、多余、摘要错误或 source/index 不匹配都会失败。

运行时固定 `local_files_only=True`，不会静默下载 GTE remote code 或权重。精确模型、revision、SHA-256、许可证与路径见 [model-manifest.md](model-manifest.md)。

## 8. 代码地图

| 责任 | 模块 |
|---|---|
| CLI 与渲染 | `src/enterprise_rag/cli.py`, `cli_rendering.py` |
| 应用组合 | `application.py` |
| 索引与摄取 | `ingestion.py`, `index_store.py` |
| 双路召回与 RRF | `retrieval.py` |
| Reranker | `reranking.py` |
| Gate 与 Validator | `grounding.py` |
| Generator adapters / schemas | `generation.py` |
| 独立 RAG | `rag_service.py` |
| Agent 与只读工具 | `agent_service.py`, `agent_tools.py` |
| Trace 与错误类别 | `execution_trace.py`, `errors.py` |
| 分层评测 | `evaluation.py` |
| 环境诊断 | `diagnostics.py` |

下一步从 [Demo 脚本](demo-script.md) 体验完整数据流，或从 [评测说明](evaluation.md) 查看每个结论的验证证据。

## 9. 本地工程面板（同进程、同核心）

面板是**第二个入口**，不是第二个业务实现。一个 Python 进程、一份业务核心：

```text
PanelRuntime（进程级，唯一所有者）
  ├─ config / index / encoder / retriever / reranker / support_scorer / gate / validator / tools / index_builder
  ├─ GeneratorRegistry
  │    ├─ 懒加载一次的本地生成器（单飞，走 asyncio.to_thread）
  │    └─ 按完整 profile 指纹持久复用的在线生成器（请求数与花费累计在它身上）
  └─ ApplicationTemplate 缓存（键 = generator_kind + provider_fingerprint + fallback_local）
         ↑ 模板里**没有** run id、recorder、取消令牌或一次性授权，所以可以安全缓存

RunCoordinator（全局单 Run）
  └─ 每次接受的 Run：一个新的 CancellationToken + 一个新的 BoundedProgressBuffer
       └─ ApplicationTemplate.bind(...) → 一次性 Application
            ├─ RagService / AgentService（复用全部重组件）
            ├─ TraceRecorder(run_id, observer=buffer.publish)
            └─ 权威产物 = 完成后的 Result + RunTrace
```

要点：

- **真实的分阶段初始化**：`CHECKING_CONFIG` → `LOADING_INDEX` → `LOADING_RETRIEVER` → `LOADING_RERANKER` → `PREPARING_DEFAULT_LOCAL_GENERATOR` → `READY`，每个阶段的耗时是真实工作而不是同一函数的切片；阻塞加载器走 `asyncio.to_thread`。页面可用时刻（路由/外壳注册）在任何模型加载之前记录，因此它和 `runtime_ready_seconds` 是两个不同的指标。
- **密钥与授权**：`config/providers.toml` 里的 key 只以环境变量名存在；`profile_fingerprint` 是覆盖全部非机密字段的 SHA-256，用于缓存键与"选中后被改动"的检测。`GeneratorRegistry.authorize()` 在**用户确认之后**才铸造一次性授权，`backend_for(spec)` 原子消费它，并再次校验 run id、profile 指纹与 fallback 位。
- **有界进度**：服务层通过 `TraceObserver` 同步推事件，缓冲区容量 256，同一 event id 的 `started` 快照会被终态**原地替换**，满时只淘汰最老的 `started`、**永不**淘汰终态事件；全为终态时直接放弃并依赖权威 `RunTrace`。观察者异常被吞，永远不影响业务执行。
- **投影只读白名单**：`panel/public_trace.py` 是唯一一份 `PUBLIC_STAGE_FIELDS` 白名单与递归脱敏实现，`panel/projection.py` 复用它。未知阶段只保留结构字段。
- **UI 不拥有任何业务决定**：Evidence Gate、来源过滤、工具选择、repair、fallback、失败语义全部在服务层；面板只渲染 `PanelRunRecord` 投影出来的不可变视图模型，并提交意图。
- **取消**：`RunCancelled` 在服务阶段边界被观察后**原样向上抛**，不会被转成 `failed` 业务结果；协调器写入一条 `cancel_boundary` 事件，CANCELLED 记录不伪造 result/trace。
- **离线保证**：默认测试与面板的 local 路径都不打开 socket（有 tripwire 测试硬失败每一次 `socket.connect`），`run_panel` 只 bind `127.0.0.1`，且显式覆盖 `reload=False`、`on_air=None`、`fastapi_docs=False`。

| 责任 | 模块 |
|---|---|
| 面板页面与浏览器模式 runner | `src/enterprise_rag/panel/app.py` |
| 每客户端表单状态与 online 确认 | `panel/controller.py` |
| 全局单 Run 生命周期与历史 | `panel/coordinator.py` |
| 共享运行时、生成器注册表、一次性授权 | `panel/runtime.py` |
| 公开 trace 白名单与脱敏 | `panel/public_trace.py` |
| 有界/合并的进度缓冲 | `panel/progress.py` |
| 视图模型投影 | `panel/projection.py` |
| 不可变契约与五态不变量 | `panel/contracts.py` |
