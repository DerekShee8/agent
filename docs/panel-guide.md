# 本地工程面板使用指南

本地工程面板是一个**只绑定 `127.0.0.1`** 的 NiceGUI 工作台，复用与 CLI 完全相同的 `Application` / `RagService` / `AgentService` 核心。它不修改任何业务结论：证据是否充分、选哪个工具、过滤哪些来源、是否 repair、是否回退、失败语义，全部由核心决定，面板只负责渲染视图模型和提交意图。

面板不会打开 `0.0.0.0`、不会启用 NiceGUI 的 `on_air` 远程访问、不会发布内部 OpenAPI 文档、不会开启 uvicorn reloader。

## 1. 安装（本机实测）

以下命令在目标机上**实际执行通过**（离线、约 1 秒；只会重新安装本仓库自身，不会重新获取任何模型或 wheel）：

```powershell
rtk uv sync --locked --offline --no-python-downloads --extra retrieval --extra local-generator --extra online-generator --extra panel
rtk uv lock --check --offline --no-python-downloads
```

**不要**使用裸 `uv sync --locked`：本机的 `llama-cpp-python` 来自 `[tool.uv.sources]` 里指向 GitHub release 的 487 MiB wheel，一旦需要重新获取会以约 40 KB/s 的速度卡数小时（详见 `docs/handoffs/2026-10-11-panel-execution-progress.md` 第 7 节）。`--offline` 让它只使用本地缓存，因此不会触发下载。

## 2. 启动

```powershell
$env:PYTHONUTF8 = "1"
$env:RAG_AGENT_ALLOW_NETWORK = "false"
$env:RAG_AGENT_GENERATOR = "local"
rtk uv run --locked --no-sync rag-agent panel
rtk uv run --locked --no-sync rag-agent panel --port 8081 --no-open
```

- `uv run --no-sync` 永远不会触发重新同步，所以上面两行是安全的。
- 默认端口 `8080`，默认会打开浏览器；`--no-open` 只打印地址。
- 端口被占用时**直接失败并返回退出码 1**，提示里包含端口号和 `--port`，**不会**静默换端口、不会绑定 `0.0.0.0`、不会自动递增。换一个端口即可：`rag-agent panel --port 8081`。
- 页面地址始终打印为 `http://127.0.0.1:<port>/`。

> **已验证范围（请如实转述）**：面板**没有做过真实浏览器的端到端验收**。页面行为只由 NiceGUI 的 user-simulation 测试覆盖（进程内页面模拟）；real-local 验收按设计走的是 controller/coordinator/runtime，不含浏览器。所以"面板验收通过"指的是**编排、语义断言与性能上限**，不包含真实浏览器里的首屏渲染、布局与点击体验。详见 [evaluation.md](evaluation.md) 第 8 节的"未做的验证"。

## 3. 初始化阶段

页面**先出现**，初始化在后台跑，所以打开页面时可能看到尚未 READY：

```text
CHECKING_CONFIG                     校验配置、provider profile（缺失只告警）与只读工具
LOADING_INDEX                       加载并校验 hybrid index
LOADING_RETRIEVER                   加载 query encoder 并构建 retriever
LOADING_RERANKER                    加载 reranker 并派生 Evidence Gate 与 validator
PREPARING_DEFAULT_LOCAL_GENERATOR   懒加载默认本地生成器
READY                               组合共享组件（无 I/O）
```

- 每个阶段有独立耗时；`runtime_ready_seconds`（READY 为止的总时长）与页面可用时刻（`ui_available_seconds`）是**两个不同**的指标。
- 页面可用的时刻在任何模型加载**之前**记录，这是有意为之。
- 任一阶段失败：面板进入 `initialization_failed`，**Run 保持禁用**，并且已经创建成功的阶段产物会按**逆创建顺序**关闭一次。
- provider profile 文件缺失或不可读只是**警告**（`online provider profiles are unavailable`），本地模式仍然可用。

## 4. 三个预设与 Custom

| scenario_id | 文本 | 模式 |
|---|---|---|
| `rag-shanghai-p4` | P4 员工去上海出差的每晚住宿上限是多少？ | rag |
| `agent-claim-1024` | 解释 EMP-003 的 CLAIM-1024 为什么被拒绝。 | agent |
| `rag-pet-boarding-refusal` | 制度是否允许报销宠物寄养费？ | rag |

- 点卡片只会**填入文本和模式**，不会启动 Run。
- 预设文本是只读的；切到 Custom 后输入框可编辑，且**必须非空**才能 Run（空文本会被拒绝，不会创建任何 Run）。
- Retriever 和 Reranker **不在**用户可选字段里——面板只允许在 `local` / 已登记的 `online` profile 之间选择生成后端。

## 5. Local 与 Online

- **Local**：直接用已批准的本地模型，离线、免费、无 key。
- **Online**：只列出 `config/providers.toml` 中已登记的 profile（模型名与 endpoint 只读展示，不能手填任意地址）。选择 Online 后会出现「失败后回退到本地」开关，**默认关闭**；只有一个已登记 profile 时会自动选中它。
- 点 Run（Online）**不会**立刻发请求：先弹出确认视图，展示 provider、model、endpoint、隐私条款地址、本次请求上限、成本上限、已花费金额与 fallback 设置，然后由你点 Confirm 或 Cancel。
- 点 Cancel 会记下一条 **BLOCKED**（`authorization_cancelled`）记录，且**不会**创建授权、不会创建 Application、不会发出任何请求。
- 以下情况一律在**发出请求之前**变成 BLOCKED，且零客户端调用：网络被禁用、profile 被禁用、profile 在选中之后被改动（指纹不符）、profile 从文件里消失、隐私策略不允许、预算上限、请求上限、缺少 API key。错误信息经过脱敏，只带稳定类别，不回显密钥或 provider 原文。
- 已经**发出**的请求之后遇到鉴权/配额/超时等错误，是 **FAILED**，不是 BLOCKED。

## 6. 流水线画布与 Inspector

- 主画布是**受控节点卡片链**：每个阶段一张卡片，显示阶段名、状态（文本 + 图标，颜色只是补充）与耗时。点击卡片只做一件事——把该事件选进右侧 Inspector，**不能**执行代码或改变业务状态。
- 为什么不用 Mermaid：NiceGUI 的 `ui.mermaid` 只在浏览器端渲染，**不会把节点点击回传 Python**，无法驱动 Inspector。
- 嵌套结构是真实的：RAG 的 `retrieval` 下挂着真实的 `dense` / `sparse` / `rrf_fusion` 子事件；Agent 的 `search_knowledge` 下挂着完整的检索/重排/Gate 子树，并且**永不**在嵌套搜索里产生 `generation`。
- Inspector 只显示白名单字段：reranking 的排名行（最多 5 行、文本预览最多 300 字符）、Gate 的阈值/观测分/支持 ID/决策/拒答原因、generation 的公开后端与模型标识（含可用 usage/cost）、validation 的问题数与支持 ID。
- 密钥、系统提示词、完整 prompt、隐藏推理、原始 traceback、任意环境变量值**不会**进入进度、历史、Inspector 或 HTML。

## 7. Stop 语义

- Stop 是**协作式取消**：它只设置取消令牌，服务在下一个安全阶段边界观察到取消后才停下。
- 处于阻塞中的后端（例如正在推理的本地模型）不会被强杀；面板保持 `cancelling` 直到它返回并观察到令牌。
- 取消产生的记录状态是 **CANCELLED**：保留真实的进度前缀，外加**一条**协调器自己写入的 `cancel_boundary` 事件；**不会**伪造 `RagResult` / `AgentResult`，也不会伪造完成的 trace。
- RUNNING / CANCELLING 期间控件锁定，只有回到 READY 才解锁。
- 同一时刻只允许一个 Run：运行中再次点 Run 会被立即拒绝，**没有隐藏队列**；重复点 Stop 是幂等的。

## 8. 五个终态

| 状态 | 文本 / 图标 | 含义 |
|---|---|---|
| `answered` | Answered / check_circle | 有依据的答案，带来源 |
| `refused` | Refused / blocked | 程序化拒答（证据不足、grounding 失败等），是**安全成功路径** |
| `blocked` | Blocked / lock | 请求发出前就被拦下（无 spec、无 result、无 trace） |
| `failed` | Failed / error | 已发出请求后失败，或服务调用前的绑定失败 |
| `cancelled` | Cancelled / cancel | 协作式取消 |

每个状态都同时有**文本和图标**，颜色只是补充（不能只靠颜色区分）。

## 9. 最近五条历史

- 历史是**内存内**的，最多保留最近 5 条，进程退出即消失。
- 点历史条目只会切换**查看的记录**，不会改动记录本身，也不会改动正在运行的活动 Run。
- 启动一个新的 Run 会把视图切回活动 Run。

## 10. CLI 回退

面板只是第二个入口；CLI 的所有能力保持不变，任何一幕都可以用命令行复现：

```powershell
rtk uv run --locked --no-sync rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --trace
rtk uv run --locked --no-sync rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --trace
rtk uv run --locked --no-sync rag-agent rag ask "制度是否允许员工报销宠物寄养费？" --generator local --trace
```

非 panel 命令**不会**导入 NiceGUI，也不要求安装 panel extra。

## 11. 常见问题

- **端口被占用**：按提示换端口（`--port 8081`）。面板不会自动换端口。
- **Run 一直是灰的**：看初始化徽章。`initialization_failed` 表示某个阶段失败，把页面上的阶段名和错误类别对照第 3 节排查。
- **Online 里没有 profile**：`config/providers.toml` 缺失或不可读（页面会显示一条初始化警告），或者 profile 未启用。该文件是 gitignored 的。
- **点了 Run 却弹出确认**：这是 Online 的预期行为，见第 5 节。
- **本地模型没加载**：面板不会下载任何模型；缺少已批准的 GGUF 会让初始化直接失败，而不是静默降级。
