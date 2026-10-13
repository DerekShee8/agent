# 本地工程演示面板设计

日期：2026-10-11
状态：对话设计已逐段批准，等待用户审阅书面版本
适用仓库：`agent项目终版demo`

## 1. 目的

在已经完成真实本地验收的企业知识库 RAG + Agent CLI Core 上增加一个本地浏览器工程演示面板。面板的首要价值不是提供聊天产品皮肤，而是让面试官在一个工作台中看清：

- Query 如何经过 dense、sparse、RRF、Reranker 与 Evidence Gate；
- Gate 为什么放行或拒答；
- Generator 输出如何经过 grounding validation 与最多一次 repair；
- Agent 如何选择知识检索和三个只读业务工具；
- 每个阶段使用了什么 backend、产生了什么公开证据、耗时多久；
- `answered`、`refused`、`blocked`、`failed` 与 `cancelled` 为什么不同。

CLI 仍然是稳定的工程入口；面板是复用同一业务核心的可视化入口，不取代 CLI，也不复制业务规则。

## 2. 已有基线

设计基线为 `master` 分支提交 `28e0687d56eb11b18933171c9794316131fda5ba`。设计阶段工作树保持干净，没有面板生产代码，也没有安装 UI framework。

CLI Core 已有以下历史验收记录：

- 默认离线测试：547 passed，5 live deselected；
- Retriever：Recall@20 = 18/18；
- Reranker：conditional Hit@5 = 18/18；
- RAG：grounded answer 16/18、正确拒答 6/6、逐题引用合法 18/18；
- 正式三幕：cold start 11.101 s、RAG p95 17.323 s、Agent p95 30.134 s、拒答 p95 0.853 s；
- 真实本地 Generator：Qwen3-4B Q5_K_M，经 llama.cpp 结构化生成；
- 当前没有真实 Online Provider 调用证据。

这些记录是面板实施前的基线，不得冒充面板完成后的重新验证。

## 3. 范围

### 3.1 首版包含

- NiceGUI 普通浏览器模式；
- `rag-agent panel` 启动命令；
- RAG / Agent 模式；
- 三个正式预设场景和 Custom 输入；
- Local / Online Generator 选择；
- 已登记 Provider Profile 选择与单次 Online 授权；
- 显式 Online → Local fallback；
- 混合实时的 Pipeline 与 Agent Trace；
- 固定右侧 Inspector；
- 回答、拒答、阻止、失败、取消、来源和业务 Observation；
- 当前会话最近 5 次运行历史；
- 系统初始化与安全状态；
- 默认离线的分层测试和真实本地三幕重验。

### 3.2 首版不包含

- 登录、多用户、多租户或权限后台；
- 公网部署、云托管、独立业务 HTTP API；
- Native window / pywebview；
- Knowledge build、完整评测或模型管理 UI；这些继续使用 CLI；
- 任意 API Key、base URL、model ID、Retriever 或 Reranker 输入；
- 跳过 Evidence Gate、Validator 或降低安全阈值的开关；
- Memory、Multi-Agent、危险工具或写操作；
- hidden chain-of-thought、完整 prompt、secret 或原始 traceback；
- 手机端或移动端布局承诺。

## 4. 总体架构

首版采用同一 Python 进程，不调用 CLI 子进程，也不建立业务 API：

```text
NiceGUI View
    │
    ▼
PanelController
    │  immutable RunSpec
    ▼
RunCoordinator ───── bounded progress queue ─────► UI projection
    │
    ▼
Application
    ├── RagService
    └── AgentService（单 Agent + 多工具）
            ├── search_knowledge → RagService
            ├── get_employee_profile
            ├── get_expense_claim
            └── get_business_trip

completed run: Result + immutable RunTrace ──────► authoritative final view
```

### 4.1 NiceGUI View

只负责展示 ViewModel、收集允许的用户选择并发送 UI intent。它不能根据文本反推来源、判断是否拒答、修改错误类别或伪造 Trace 节点。

### 4.2 PanelController

负责：

- 把页面选择转换成 `RunSpec`；
- 请求 Online 预检与单次授权；
- 把结构化业务结果映射成公开 ViewModel；
- 管理当前选中的运行记录和 Inspector 节点；
- 不包含 Evidence Gate、来源过滤、工具选择或 fallback 判断。

### 4.3 RunCoordinator

进程内只有一个 RunCoordinator，并且同时最多执行一个任务。它负责：

- 接受不可变 `RunSpec`；
- 管理运行状态、进度队列和 cooperative cancellation；
- 绑定并核验 `run_id`；
- 在完成后生成 `PanelRunRecord`；
- 维护最多 5 条、仅内存存在的历史；
- 拒绝并发 Run，而不是隐式排队。

### 4.4 业务核心

`Application`、`RagService`、`AgentService` 和现有结果契约继续拥有业务语义。RAG 必须可脱离 Agent 独立运行；Agent 的 `search_knowledge` 必须调用同一条受保护 RAG 链，不能绕过 Gate 或抹掉来源与失败语义。

## 5. Framework 与启动契约

Framework 选择 NiceGUI，使用普通浏览器模式：

- 只绑定 `127.0.0.1`；
- 默认端口为 `8080`，可用 `--port` 显式修改；
- 端口占用时明确失败并提示 `--port`，不静默跳到未知端口；
- 默认打开浏览器；`--no-open` 只启动服务并打印 URL；
- 不使用 `native=True`；
- 不启用公网监听、登录、遥测或云功能。

CLI 入口：

```powershell
rag-agent panel
rag-agent panel --port 8081
rag-agent panel --no-open
```

NiceGUI 的精确版本必须在任何生产代码创建前锁定。实施计划的第一个任务将选择一个精确版本并进行依赖解析预检；只有该版本能与 Python 3.12、现有锁文件、Transformers、PyTorch 和 llama.cpp 共存时才写入独立的 `panel` optional dependency。若预检需要联网获取包元数据或安装工件，必须先报告并取得确认。没有解析证据时不猜版本，也不使用宽松或未固定版本。

## 6. 资源所有权与模型生命周期

不能为每个 Generator 组合重新加载整套 RAG。面板引入显式的共享运行时边界：

### 6.1 SharedRuntime

进程内单例拥有：

- HybridIndex；
- GTE multilingual encoder；
- GTE Reranker；
- Retriever；
- claim support scorer；
- Evidence Gate 与 Validator 所需的固定组件；
- 三个只读业务工具的数据仓库。

这些组件只加载一次。它们的失败属于初始化失败，Run 必须保持禁用。

### 6.2 GeneratorRegistry

- Local Generator 在 Local 第一次成为选中默认项时异步准备，随后保留到进程退出；
- 当前首版只有一个已验收本地模型，因此 Model 字段只读；
- 每个完整 Provider Profile fingerprint 对应一个长期存在的 Online Generator；
- Online 普通模式与 Online + fallback 共用同一个 Online Generator；
- fallback 复用同一个 Local Generator；
- 不进行热卸载、LRU 驱逐或运行中模型更换；
- Provider Profile 在面板启动时读取，文件变化需要重启面板；
- Online Generator 的请求数和累计费用状态不能通过切换 UI 或重新组合 Application 重置。

### 6.3 Application 组合

轻量 Application 组合按 `(generator_kind, profile_fingerprint, fallback_local)` 识别，并复用 SharedRuntime 与 GeneratorRegistry 中的实例。组合同时具备 RAG 和 Agent 能力，因此切换 RAG/Agent 模式不重新加载模型。

长期资源必须有明确的 `close/aclose` 所有权，由面板关闭流程统一释放；不得依赖任意对象被垃圾回收来关闭网络 client。

## 7. 初始化流程

页面先出现，再显示可见的后台初始化：

```text
STARTING_UI
  → CHECKING_CONFIG
  → LOADING_INDEX
  → LOADING_RETRIEVER
  → LOADING_RERANKER
  → PREPARING_DEFAULT_LOCAL_GENERATOR
  → READY
```

规则：

- READY 前 Run 禁用；
- 每个阶段显示状态与耗时；
- 初始化不下载模型、不联网；
- 缺少批准模型、索引不匹配或资源不足时进入 initialization failure；
- 页面保留诊断和修复建议，但不能绕过失败继续 Run；
- 初始化耗时与正式回答耗时分开报告。

## 8. 不可变 RunSpec

点击 Run 并完成必要的 Online 确认后，PanelController 创建不可变 `RunSpec`：

```text
run_id
created_at
mode: rag | agent
scenario_id: preset id | custom
question_or_task
generator: local | online
provider_name: optional
provider_fingerprint: optional
fallback_local: bool
online_authorization: optional one-use permit
```

运行开始后，问题、模式、Generator、Provider 和 fallback 控件全部锁定。任何进度事件、结果或错误都必须带相同 `run_id`；旧运行或迟到的更新不能覆盖当前运行。

## 9. Online 单次授权

Online 模式每个 Run 必须重新确认。授权流程在网络请求之前展示：

- Provider 名称；
- model ID；
- endpoint；
- 数据条款与 synthetic-only 限制；
- 本次最大模型调用数；
- 单价或额度信息；
- 预计成本与硬上限；
- 是否允许 Online → Local fallback。

最大调用数与成本估算必须由业务层的预检契约产生，UI 不自行复制 Agent action、repair 或预算算法。

确认后生成一次性 `OnlineRunAuthorization`，绑定：

- `run_id`；
- Provider fingerprint；
- 最大请求数；
- 输入/输出上限；
- 费用上限；
- synthetic-only 声明。

授权不能复用到下一次 Run。Profile、模型、endpoint 或预算发生变化时旧授权失效。面板不得通过修改全局环境变量来模拟逐次授权；Generator 在每次真实调用前仍必须执行核心 preflight。

密钥只从环境变量或密钥管理读取，页面只显示“已配置/未配置”，不显示长度、前后缀或真实值。

## 10. Trace 与实时进度

### 10.1 最终事实

对于正常完成的业务运行，结构化 `Result + RunTrace` 是唯一最终事实。UI 只做 projection，不能使用运行中事件拼出一个与最终结果矛盾的结论。

对于尚未调用业务服务就被预检阻止的运行，记录 `BLOCKED` PanelRunRecord，但不伪造业务 Result 或 RunTrace。对于 cooperative cancellation，记录 `CANCELLED` PanelRunRecord 与真实产生的公开事件前缀，不伪造一个成功或失败的业务结果。

### 10.2 服务级嵌套事件

RAG Trace 必须真实表达：

```text
retrieval
  ├── dense
  ├── sparse
  └── rrf_fusion
reranking
evidence_gate
generation
grounding_validation
optional_repair
```

Agent Trace 必须表达：

```text
agent_plan
agent_action
  └── search_knowledge
        └── nested RAG chain
agent_observation
...
agent_finish
```

运行中 progress event 与最终 RunTrace 使用相同 event ID、parent ID、stage 和终态。UI 不得根据代码预期提前制造子节点。

### 10.3 Observer 与背压

- 业务服务接受 UI-neutral、只读 progress observer；
- 每个 Run 使用容量 256 的 bounded queue；
- 非关键的重复 running 更新可以合并；
- stage terminal、error、backend switch、repair、cancel boundary 和最终结果不得丢失；
- observer 或 queue consumer 异常不得改变业务结果；
- UI 断开后业务运行可安全收尾，但不得继续积累无界事件；
- queue overflow、重复订阅、consumer 断开和 observer 抛错必须测试。

### 10.4 公开字段

所有 attributes 在进入队列前完成字段白名单、递归脱敏和长度限制。Inspector 只展示：

- Dense/Sparse：chunk ID、source、score、rank、最多 300 字符文本预览；
- RRF/Reranker：Top 5 及前后 rank；
- Gate：阈值、实际分数、support IDs、放行或拒答原因；
- Generator：backend、model ID、revision/digest、duration、可获得的 usage/cost；
- Agent：action 名称、允许公开的参数、结构化 Observation；
- Error：category、stage 与脱敏说明。

不允许进入公开 Trace 的内容包括 secret、Authorization header、任意环境变量、完整 prompt、system prompt、hidden chain-of-thought 和原始 traceback。

## 11. RunCoordinator 与取消语义

主要生命周期：

```text
INITIALIZING → READY
READY → AUTHORIZING → BLOCKED | RUNNING
READY → RUNNING
RUNNING → ANSWERED | REFUSED | FAILED
RUNNING → CANCELLING → CANCELLED
terminal → READY
```

取消是 cooperative cancellation：

- Stop 只设置 cancellation token；
- 服务在阶段边界检查 token；
- 已进入 `asyncio.to_thread` 的 llama.cpp 推理不会被假装立即停止；
- 已发送的 Online 请求也不会被描述成“从未发生”；
- 底层阶段结束前 Coordinator 继续保持 busy，不能启动下一次 Run；
- cancellation 被观察后不进入后续业务阶段，也不发布答案；
- 取消请求之后返回的迟到 UI 更新不会覆盖 CANCELLED；
- 用户重新运行会创建全新 `run_id`。

首版不提供队列、不提供强杀线程、不提供并行模型推理。

## 12. UI 信息架构

目标布局为桌面浏览器中的“横向主链 + 固定右侧 Inspector”：

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Scenario / Question | Mode | Generator | Model/Profile | Run | Safety │
├──────────────────────────────────────────────┬──────────────────────────┤
│ Pipeline                                     │ Fixed Inspector          │
│ Dense + Sparse → RRF → Rerank → Gate         │ selected stage           │
│                          → Generate → Validate│ public input/output       │
│ Agent: Plan → Action → Observation → Finish │ rank/score/backend/time   │
├──────────────────────────────────────────────┤ revision/error            │
│ Answer / Refusal / Failure                   │                          │
│ Sources + Business Observations              │                          │
│ Recent runs: last 5                          │                          │
└──────────────────────────────────────────────┴──────────────────────────┘
```

Pipeline 节点使用 NiceGUI 支持的可点击 Mermaid 图或等价的受控组件；点击只改变 Inspector 选择，不改变业务运行。

### 12.1 场景入口

三个正式卡片：

1. RAG：P4 员工去上海出差的住宿上限；
2. Agent：解释 EMP-003 的 CLAIM-1024 为什么被拒绝；
3. Refusal：制度是否允许报销宠物寄养费。

点击卡片只加载固定输入，不自动运行，预设问题只读。`Custom` 模式才允许自由输入。

### 12.2 Generator 控件

- Local：显示 `Qwen3-4B Q5_K_M · 已验证`，首版只读；
- Online：显示已登记 Provider Profile 下拉框；
- model ID 与 endpoint 由 Profile 自动带出并只读；
- fallback 只在 Online 出现，默认关闭；
- Retriever 与 Reranker 不出现在可替换控件中。

### 12.3 混合实时反馈

- 阶段开始：节点立即变为 running 并开始计时；
- 阶段结束：显示 score、rank、证据和终态；
- 主画布保持摘要；Inspector 提供细节；
- `search_knowledge` 可展开为内部 RAG 子链；
- UI 不显示 token streaming，首版只显示阶段级进度与最终结构化答案。

### 12.4 最近运行

- 仅内存保存最近 5 条 `PanelRunRecord`；
- 第 6 条完成后淘汰最旧记录；
- 可以回看对应结果、来源和 Inspector；
- 新 Run 始终是当前活动视图；
- 关闭面板后全部清除；
- 不保存密钥、完整 prompt、原始异常或未经脱敏的数据。

## 13. 结果与错误语义

UI 使用五种终态，但不强行扩张现有业务 Result 的枚举：

| Panel 状态 | 含义 | 业务映射 |
|---|---|---|
| `ANSWERED` | 答案通过业务核心的证据与 grounding 路径 | `RagResult/AgentResult.status == answered` |
| `REFUSED` | 系统正常，证据不足而拒答 | `status == refused` |
| `BLOCKED` | 运行前的网络、授权、隐私、预算或 Profile 预检未通过 | Panel lifecycle；无伪造业务 Result |
| `FAILED` | 配置、模型、Provider、协议或内部执行失败 | `status == failed` 或初始化失败 |
| `CANCELLED` | 用户请求停止并在安全阶段边界结束 | Coordinator lifecycle；无伪造成功结果 |

颜色只是辅助：ANSWERED 为绿色、REFUSED 为琥珀色、BLOCKED 为橙色、FAILED 为红色、CANCELLED 为灰色，同时必须显示文字和图标。

## 14. 重试与受控降级

面板不实现通用自动重试：

- Grounding Validator 失败时，只允许现有 RagService 最多一次 repair；
- Online Generator 失败时，只在该 Run 明确授权 fallback 后切换 Local 一次；
- Retriever、Reranker、Gate 或其他关键阶段失败时不得跳过；
- 每次 repair 与 backend switch 必须成为 Trace 事件；
- 用户点击重新运行属于新 Run，Online 需要重新确认；
- UI toast、渲染失败或浏览器重连不得触发模型重试。

## 15. 测试策略

### 15.1 默认离线层

新增测试继续满足：确定、无密钥、无网络、无费用、不加载真实模型。

1. **ViewModel 单测**
   - 五种状态映射；
   - 字段白名单；
   - 递归脱敏与 300 字符截断；
   - Top 5 与来源映射；
   - 不从答案文本反推结构化字段。

2. **RunCoordinator 单测**
   - 全局单任务；
   - RunSpec 与 run ID 锁定；
   - bounded queue 与关键事件不丢失；
   - observer failure；
   - cooperative cancellation；
   - 迟到更新隔离；
   - 最近 5 条历史和淘汰顺序。

3. **Service / Trace 集成测试**
   - dense/sparse/RRF 嵌套事件；
   - Agent action 下嵌套 RAG；
   - progress event 与最终 Trace ID/终态一致；
   - repair 与显式 fallback；
   - blocked 不调用外部 client；
   - UI observer 失败不改变业务结果。

4. **NiceGUI 页面测试**
   - 三个预设与 Custom；
   - Run 启用/禁用；
   - Local/Online 控件；
   - Online 确认；
   - Pipeline 节点与 Inspector；
   - Stop；
   - 历史回看；
   - `rag-agent panel --no-open` 与 localhost 绑定。

### 15.2 回归与真实本地验收

- 重新运行全部现有默认测试和新增测试；
- 重新运行 `ruff check .`；
- 重新运行 `ty check src`；
- 对新增和修改文件运行格式检查；
- 不借本任务批量格式化当前仓库中无关的历史文件；
- 在新面板环境重新执行真实本地三幕，每幕连续 3 次；
- 验证结论、来源、三个业务 Observation、精确工具参数、拒答语义和 Trace；
- Local 模式测试硬阻断网络；
- Online 只做 Fake/contract 测试，除非用户另行批准真实调用。

## 16. 性能与完成标准

面板只有同时满足以下条件才称为“可演示、可使用”：

### 16.1 功能

- `rag-agent panel` 可以稳定启动与关闭；
- RAG、Agent、三个预设和 Custom 全部可用；
- Local/Online 选择和 Online 单次授权完整；
- Pipeline、Inspector、来源、Observation 与五种状态正确；
- 单任务、取消、历史、repair 与 fallback 符合本设计；
- CLI 原有命令无行为回归。

### 16.2 正确性与安全

- UI 不复制 Gate、来源过滤、工具选择或 fallback 语义；
- RAG 可独立运行；
- Agent 不绕过 RAG；
- local 模式没有网络；
- Online 未经单次授权不会创建真实请求；
- secret 与禁止字段不进入页面、事件或历史；
- Fake 不冒充 real backend 证据。

### 16.3 质量

- 所有默认离线测试通过；
- 所有新增 Panel 测试通过；
- 现有真实本地三幕在新环境连续 3 轮通过；
- `ruff check .` 与 `ty check src` 通过；
- 新增和修改文件格式通过；
- cold start ≤60 s；
- 三个正式场景各自 p95 ≤45 s；
- 页面在模型运行时仍能更新计时并接受 Stop intent；
- 初始化时间与业务运行时间分别报告。

### 16.4 证据表述

- real-local、fake-backed 与 online-unverified 明确区分；
- 旧 CLI 数据不冒充新面板重验；
- 未经授权和实测的 Online Provider 不标记为“已验收”；
- 不把 Evidence Gate 或 Validator 描述成数学上的无幻觉证明。

## 17. 文档交付

实施完成时同步更新：

- `README.md`：安装、`rag-agent panel`、Local/Online 使用；
- 面板使用指南：三幕 Demo、Custom 与常见故障；
- `docs/architecture.md`：同进程边界、SharedRuntime、RunCoordinator、Trace；
- `docs/demo-script.md`：面板演示步骤与 CLI 备用路径；
- `docs/security-and-cost.md`：单次授权、Profile、预算、fallback 与数据限制；
- `docs/evaluation.md`：面板重验结果与 provenance；
- 结构化验收摘要：保存断言后的数据，不冒充原始 transcript。

## 18. 实施顺序约束

本设计批准后仍不能直接实施。顺序必须是：

1. 用户审阅并批准本书面设计；
2. 使用 `writing-plans` 生成可执行实施计划；
3. 用户批准实施计划；
4. 锁定 NiceGUI 精确版本并完成依赖冲突预检；
5. 以 TDD 扩展 Trace、observer、运行时生命周期和 Panel adapter；
6. 实现 NiceGUI 页面；
7. 完成离线、回归与真实本地验收；
8. 更新文档与验收证据。

任何真实 API、模型下载、外部数据发送、推送、发布或部署仍需独立明确授权。
