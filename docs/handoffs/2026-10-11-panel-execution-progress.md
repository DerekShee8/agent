# 本地工程面板执行进度交接包

## 1. 交接目的与使用方法

这份文件是**执行阶段**的活文档（设计阶段的对应物是 `2026-10-11-panel-brainstorming-handoff.md`）。

- 它回答的是"**做到哪了、下次从哪接**"；回答"**要做什么**"的是实施计划 `docs/superpowers/plans/2026-10-11-local-engineering-panel.md`。
- **每个计划任务收尾时更新本文件的第 2、3、4、9 节，并与该任务的代码提交放进同一个 commit。**
- 新窗口续接时，先只读核验 Git 实际状态，以 Git 为准，不要凭本文件的记忆回退或清理工作区：

```powershell
rtk git status --short --branch
rtk git log -5 --oneline
```

仓库绝对路径：

```text
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo
```

本仓库是纯本地 git 仓库（`git remote -v` 为空），位于 OneDrive 同步目录内，因此本文件会随 OneDrive 走，但不会随 `C:\Users\EASON\.claude\` 下的对话记录或长期记忆走 —— 这正是它存在的理由。

## 2. 当前断点

| 项 | 值 |
|---|---|
| 分支 | `codex/local-engineering-panel`；**2026-10-13 已快进合并到 `master`（两者同指 `7a01fae`）** |
| 计划版本 | `docs/superpowers/plans/2026-10-11-local-engineering-panel.md`（v2，提交 `47697f3`） |
| 进度 | **计划已完结（2026-10-13，用户决定）。Task 1–16 全部收尾；最终 checklist 13/14 条已满足。唯一未满足项＝真实在线验收（三次尝试全败），永久记为"未通过、未验证"，不再重试。** |
| 工作树 | clean（每个任务提交后应为 clean） |

## 3. 已完成任务

| Task | 内容 | 提交 |
|---|---|---|
| Task 1 | 依赖门禁：锁定并安装 `nicegui==3.18.0` | `ac88b1c` |
| Task 2 | panel 契约 + 协作取消 | `456766d` |
| 计划 v2 | 计划修订 + 本交接文件 | `47697f3` |
| Task 3 | live observer + 真实 dense/sparse/RRF 子事件 + `TraceEvent.run_id` | `1eacbb0` |
| Task 4 | RAG/Agent 可嵌套 trace + 阶段边界协作取消 + 真实终态属性 | `402a13b` |
| Task 5 | 分阶段构建器 + `SharedRuntimeComponents` + `ApplicationTemplate.bind` | `461be52` |
| Task 6 | GeneratorRegistry + 一次性 online 授权 + `PanelRuntime` 六阶段初始化 | `9dbb23e` |
| Task 7 | 公开 trace 白名单/脱敏 + 有界进度缓冲 | `ef5dc05` |
| Task 8 | 全局单 Run coordinator + 取消生命周期 + 最近 5 条历史 | `639d8ef` |
| Task 9 | 五态安全投影（视图模型） | `faede1c` |
| Task 10 | 纯 panel controller + 三预设 + online 确认流程 | `4ed72d1` |
| Task 11 | `rag-agent panel` 懒加载 CLI 入口 + 确定性端口预检 | `857f16d` |
| Task 12a | NiceGUI 页面测试脚手架（根 `conftest.py` + `panel_main.py`） | `9273900` |
| Task 12 | NiceGUI 工程工作台（`create_panel_app` + 15 个页面测试） | `c8526ca` |
| Task 13 | 一致性/五态不变量矩阵 + loopback 与密钥 tripwire + 静态检查 + 离线打包 | `892c949` |
| Task 14 | 操作/架构/安全/Demo 文档 + 实测安装命令 | `39d8f62` |
| Task 15 | 真实本地面板验收（三幕 × 三轮）+ 结构化证据 | `d6543d3` |
| Task 16a | 在线验收代码（离线可验证部分） | `2dbdd9f` |
| Task 16b | 首次真实在线尝试 → `invalid_request`（400） | 见 §9 |
| Task 16c | 第二次尝试（去掉 `[1M]` 后缀）→ **`timeout`** | 见 §9 |
| Task 16d | 第三次尝试（同一 `model_id`）→ RAG `provider_unavailable`(5xx) / Agent `timeout`；仍全败，**如实收尾为未通过、未验证** | 见 §9 |

Task 1 的产物已核实存在：`pyproject.toml` 的 `[project.optional-dependencies].panel`、`uv.lock` 中的 `nicegui` 条目、`docs/dependency-preflight/nicegui.json`、以及 `.venv` 中已安装的 `nicegui 3.18.0`。

**注意**：计划正文里多处写的基线 `547 passed, 5 deselected` 是**面板开工前**的数字，Task 2 之后已不再是实际值。验证时按"默认套件全绿且不比该基线减少"判断，不要按固定数字判断。

## 4. 下一步

**Task 16 现状：真实在线已尝试 3 次 + 1 次直连探测，均失败；结论是"中转站数据面故障"，我们这一侧没有问题。**
**2026-10-12 更新：已按下面的"选择 1"再跑过一次（第三次尝试），仍全败，因此按"选择 2"如实收尾 —— Task 16 记为未通过、未验证，不再重试。**
**2026-10-13 定案：用户决定采用 (c) 永久收尾。Task 16 就此关闭，不再安排重试，也不做换模型诊断。计划文档的最终 checklist 已按事实标注为 13/14 满足，未满足的那一条留空并写明原因。**

（三次尝试都由用户在**自己的 PowerShell 窗口**执行；key 全程只存在于那个窗口的进程内存里，从未进入对话、日志或仓库。）

**首次真实在线尝试的结果（2026-10-12，用户在自己的 PowerShell 窗口执行）：**

- 真实 provider 请求 **2 次**，成本上界 **CNY 0.686**（额度 30 次 / CNY 10，远未用满）。
- RAG 与 Agent 两幕都是 `failed` + **`error_category = "invalid_request"`**；**拒答那幕通过**（它在 Gate 停住、不调生成器）——这证明检索/重排/Gate 链路正常，只有"在线生成"这一腿失败。
- 证据留在机器本地 `.local/benchmarks/panel-online-live.json`（**未**提升为验收 artifact；失败的运行不得当作验收证据）。
- `invalid_request` ⇒ relay 返回 HTTP 400；映射逻辑中 `json_schema_not_supported` 会变成 `protocol` 而非 `invalid_request`，所以不是 schema 不支持。**头号嫌疑是 `model_id = deepseek-v4.1-flash[1M]` 未经核实**。
- 按计划**未盲目重试、未提高上限、未擅自改 profile**。下一步由用户确认 relay 实际提供的模型 id（例如查中转站后台，或带鉴权访问 `/v1/models`）；确认后改 `config/providers.toml` 的**一行**，再跑一次完整的三幕 × 1 轮。**需要用户对"第二次真实调用"点头**（计划要求任何第二次真实调用都要重新授权）。
- 同一轮还修掉两个我方缺陷：在线 SDK 客户端的异步 `close` 未被 await（会漏关连接），以及 `GeneratorRegistry.preflight` 不校验请求携带的 profile 指纹。

**第二次尝试（2026-10-12，`model_id = deepseek-v4.1-flash`）：`invalid_request` 消失，变成 `timeout`**

- RAG `failed` / `timeout` / 52.6 s；Agent `failed` / `timeout` / 48.3 s；拒答 `refused` ✅ / 0.5 s；请求 2 次；成本上界 CNY 0.686。
- **`invalid_request`（HTTP 400）消失** ⇒ `[1M]` 后缀确实就是第一次 400 的原因，`deepseek-v4.1-flash` 已通过**请求校验层**。
- `timeout` 来自 `_mapped_online_error` 的 `isinstance(error, TimeoutError) or class_name == "APITimeoutError"` ⇒ **不是** HTTP 错误码，而是**在 `online_timeout_seconds`（默认 45 s，可用 `RAG_AGENT_ONLINE_TIMEOUT_SECONDS` 覆盖，属配置项非代码）内没收到响应**。52.6 / 48.3 s 正是"撞到 ~45 s 上限 + 编排开销"。
- 本轮负例检查**三项全通过**（`518131d` 的 preflight 指纹硬化生效）。
- 仍**不得**把这两次描述为"已验证/已校验/可生产"；四条 caveat 原样适用。

**第 3 步（直连探测，绕过本项目代码）→ HTTP 502；第 4 步（`GET /v1/models`）→ 模型 id 有效**

| # | 做了什么 | 结果 | 排除了什么 |
|---|---|---|---|
| 1 | 三幕 × 1 轮，`model_id` 带 `[1M]` | RAG/Agent `invalid_request`(400)；拒答 ✅ | — |
| 2 | 同上，`model_id` 去掉 `[1M]` | RAG/Agent `timeout`(45 s)；拒答 ✅ | **`[1M]` 后缀就是第 1 次 400 之因** |
| 3 | 直连探测（**不经本项目代码**、**无 `response_format`**、`max_tokens=8`） | **9.6 s 返回 HTTP 502 Bad Gateway** | **strict `json_schema` 假设**、**"我们超时设太短"假设** |
| 4 | `GET /v1/models`（带鉴权） | **200 + 模型列表**，其中**就有 `deepseek-v4.1-flash`** | **"模型 id 不对"假设** |

**结论：中转站控制面正常（鉴权通过、模型列表可读），数据面故障（生成端点 502 或挂到超时）。** `/v1/models` 200 与 `/v1/chat/completions` 502 的对比是决定性证据。**我们这一侧没有问题。**

**因此**：调大超时**无意义**（它 9.6 s 就返回 502，不是慢是坏）；现在再跑**结果相同**；**不要**盲目重试、不要提高请求上限、不要为了"通过"而换模型。

**第三次尝试（2026-10-12，本次会话，`model_id = deepseek-v4.1-flash`，`RAG_AGENT_ALLOW_NETWORK=true` / `RAG_AGENT_ALLOW_MODEL_DOWNLOAD=false`）→ 仍然全败**

- RAG `failed` / **`provider_unavailable`** / 39.5 s；Agent `failed` / `timeout` / 48.7 s；拒答 `refused` ✅ / 0.5 s；请求 2 次；成本上界 CNY 0.686；负例检查**三项全通过**。
- **唯一新信息**：这是第一次由**我们自己的代码路径**观察到 5xx —— `provider_unavailable` 按 `_mapped_online_error`（`src/enterprise_rag/generation.py:619-622`）**只在 `status_code >= 500`**（或 `APIConnectionError` / `InternalServerError`）时产生。此前两次验收运行只见 400 与超时，5xx 只出现在绕过项目代码的直连探测里；现在**两条独立路径都观察到 5xx**，与"数据面故障"结论一致，没有引入任何新假设。
- 失败运行**未**提升为验收 artifact（`docs/demo-artifacts/` 下**没有** `panel-online-acceptance.json`）；原始证据留在机器本地 `.local/benchmarks/panel-online-live.json`（未跟踪、被 `.gitignore` 忽略）。
- **未**重试、**未**提高请求上限、**未**换模型、**未**改代码绕过 —— 按"选择 2"收尾。

### 三个正当选择

> **2026-10-12 结局：选择 1 已执行（第三次尝试），仍然全败；因此采用选择 2 收尾。选择 3（换模型诊断）未被采用 —— 换模型会改动已批准的 profile，属用户决定。**

1. **等一段时间再跑**（推荐）：502 多为上游临时故障。等几十分钟到几小时，在**同一个窗口**执行下面的 collect 命令；三幕全过即收尾。**（已执行 → 仍全败）**
2. **如实收尾**：Task 16 记为"已尝试 **3 次真实在线 + 1 次直连探测**，未通过，未验证"，并写明**在线路径只有 fake 契约测试覆盖、这家第三方中转站的实际服务未经验证**。**（已采用）**
3. **可选诊断**（一次极小请求）：用列表里另一个模型（如 `glm-5.3-flash`）判断"整个数据面坏了"还是"只有 deepseek 这条路由坏了"。**换模型会改动已批准的 profile，属于用户决定，不得擅自更换。**（未采用）

### 在新窗口执行的确切命令

```powershell
cd "E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo"

# 只有 key 已随旧窗口消失时才需要这三行
$secure = Read-Host -AsSecureString "RAG_AGENT_ONLINE_API_KEY"
$env:RAG_AGENT_ONLINE_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password

$env:RAG_AGENT_ALLOW_NETWORK = "true"
$env:RAG_AGENT_ALLOW_MODEL_DOWNLOAD = "false"

# 1) 真实运行（三幕 × 1 轮）
& ".\.venv\Scripts\python.exe" scripts/collect_panel_online_acceptance.py collect --output ".local/benchmarks/panel-online-live.json"

# 2) 只有三幕全过才做：脱敏提升（不需要 key）
& ".\.venv\Scripts\python.exe" scripts/collect_panel_online_acceptance.py promote --input ".local/benchmarks/panel-online-live.json" --output "docs/demo-artifacts/panel-online-acceptance.json"
```

跑完**把结果告诉后续会话即可**（它可直接读 `.local/benchmarks/panel-online-live.json`）。若三幕全过，还要：把精确值 + 四条 caveat 抄进 `docs/evaluation.md`、更新本文件 §2/§3/§9、再跑一次**关网**默认套件、确认 `config/providers.toml` 与 `.local/` 仍 untracked，然后提交。

**代码侧已就绪**：`scripts/collect_panel_online_acceptance.py`（复用 Task 15 的纯 helper；`collect` 在 `RAG_AGENT_ALLOW_NETWORK != "true"` 时直接拒绝）、`tests/unit/test_collect_panel_online_acceptance.py`、`tests/live/test_panel_online_live.py`（缺 key 时 `pytest.fail` 明确报出，绝不用 fake 顶替）。该脚本必须能**以文件方式**直接运行（内部已加仓库根目录 bootstrap），不要再退回 `from scripts...` 的裸导入。

三幕断言与上限不变：30 次请求 / CNY 10、三幕各 1 轮；成本必须标为"基于**假设单价**的上界"；四条 caveat 逐字保留；**不得**出现"已验证/校验/可生产"字样。**绝不**把 key 写进任何文件、命令文本或对话。

## 5. 用户已批准的决定（新窗口不要重新问）

1. **real-local 验收（Task 15）**：做到 Task 14 后**停下来报告**（模型指纹、确认无需下载、网络硬阻断方案、9 次运行内容、预计耗时、性能上限），用户点头后在**同一会话**内做完 Task 15。**不预先授权。**
2. **Pipeline 主画布**：使用**受控节点卡片链**，不用 Mermaid —— NiceGUI 的 `ui.mermaid` 不会把节点点击回传 Python。
3. **README / 文档里的安装命令**：先实测 `uv sync --locked` 与 `uv build --offline` 在本机的真实行为，**按实测结果**决定写哪条命令（见第 7 节的 wheel 约束）。
4. **真实 online**：**要做**。三幕（RAG / Agent / 拒答）各 1 轮，共约 10–12 次调用。完整 profile 见 `config/providers.toml`（gitignored），关键值：
   - `base_url = https://ai.yangwang.name/v1`（**实测确认**：`/v1/models` 返回 401 鉴权，`/v2/models` 回落 SPA 页面）
   - `model_id = deepseek-v4.1-flash[1M]`（用户提供，**未验证**）
   - key 只走环境变量 `RAG_AGENT_ONLINE_API_KEY`，由用户设置，**绝不读取或回显**
   - `privacy_terms_url = https://ai.yangwang.name/legal/terms`（页面真实存在，**但正文为空**）
   - 单价填 **保守上界 ¥20/百万 token**（输入输出同价）—— 这是**假设，不是该站真实定价**
   - `request_limit = 30`、`budget_limit_cny = 10`、`enabled = true`、`synthetic_data_only = true`
   - 只允许 `data/sample_docs` 与 `data/sample_records` 中的合成样例离开本机

## 6. 未解决 / 待用户提供

- `RAG_AGENT_ONLINE_API_KEY`：**不落盘**。每次由用户在自己的窗口设为环境变量，只存在于该进程内存；仓库、命令文本与对话里都不出现。**已不再阻塞 Task 16**（用户已在窗口内提供，三次尝试都读到了它）。
- `model_id`：**已核对出现在中转站自己的 `/v1/models` 列表里**，但中转站**是否真的在服务该厂商的模型**仍未验证（caveat 1）。
- **真正未解决的是中转站的数据面**：其生成端点在三次真实调用 + 一次直连探测中从未返回过可用补全。这是外部故障，不是本仓库待办项。

## 7. 已知坑（实施时不要重新踩）

1. **uv 与 llama-cpp-python GitHub wheel**：`uv lock` / `uv sync` 只要需要重新获取 `[tool.uv.sources]` 里指向 GitHub release 的那个 487 MiB wheel 就会卡数小时（本机 GitHub 下载约 40 KB/s）。`.local/` 下已有逐字节相同的本地 wheel 与 lock 编辑流程；`uv lock --check --offline` 不需要该 wheel。**不要裸跑 `uv sync --locked`。**
2. **NiceGUI 测试脚手架并不存在**：仓库里没有任何 `conftest.py`。`nicegui.testing.user_plugin` 的 `user` fixture 默认去找仓库根目录的 `main.py`，找不到直接 `FileNotFoundError`；`pytest_plugins` 在 pytest 9 中**不能**写在非顶层 conftest（`tests/conftest.py` 会报错）。另需与现有 `--import-mode=importlib` 和 `asyncio_mode=auto` 共存，且应使用 `user_plugin` 而非会拉 selenium 的 `plugin`。
3. **`ui.run` 的默认值**：非 native 模式 `host` 默认是 **`0.0.0.0`**（必须显式 `127.0.0.1`）；`reload` 默认是 **`True`**，会起 uvicorn reloader 子进程，导致模型加载两遍并破坏 `aclose` 语义 —— 必须显式 `reload=False`。
4. **中转站的不确定性**：条款页正文为空、model_id 未验证、单价为保守假设。这些必须原样保留在验收证据里，不能标成"已验证"。
5. **`ruff format` 与历史文件**：本仓库历史上只跑 `ruff check`，**从未跑过 `ruff format`**，所以 `evaluation.py`、`retrieval.py`、`execution_trace.py`、`tests/fakes.py` 等文件存在**先于本计划就有的**格式漂移（例如 `_AUDITED_REMOTE_CODE` 的尾逗号、`dense_rows` 的折行）。因此计划 Task 13 Step 5 里"每个 task-touched 文件都通过 `ruff format --check`"这条**当前不可能整体通过**。处理原则：只格式化本计划**新写**的代码，不批量重排历史文件（计划自己的执行边界也禁止这么做）。Task 13 需要把该检查收窄到"新增文件 + 新增代码块"，并在提交信息里说明这一偏差。
6. **`uv build --offline`** 尚未实测（Task 13 才用到）。若 build isolation 离线取不到 `hatchling`，按计划 Task 13 Step 5 的说明处理：报告原始错误，改用 `--no-build-isolation`，或如实记录无法离线验证，**不得**用联网来"修好"它。

## 8. 验证方式

默认离线套件（每次任务收尾都应保持绿）：

```powershell
rtk proxy powershell -NoProfile -Command '$env:RAG_AGENT_ALLOW_NETWORK="false"; & ".\.venv\Scripts\python.exe" -m pytest -q'
```

静态检查：

```powershell
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ruff.exe" check .'
rtk proxy powershell -NoProfile -Command '& ".\.venv\Scripts\ty.exe" check src'
```

## 9. 变更日志

| 日期 | 提交 | 变更 |
|---|---|---|
| 2026-10-11 | 随计划 v2 提交 | 创建本文件；记录 Task 1–2 已完成、用户四项决定、已知坑 |
| 2026-10-11 | 本次提交（Task 4） | `RagService` 与 `AgentService` 新增 keyword-only `cancellation=CancellationToken`，在阶段边界 `_check_cancelled()`；两者都在 broad handler 之前 `except RunCancelled: raise`（取消**不**被转成 failed 业务结果，且不 `recorder.finish()`）。`RagService._search_pipeline` 成为 `ask` 与 Agent 共用的唯一检索/重排/Gate 路径：`search_evidence(question, *, recorder=None, parent_id=None)`，无 recorder 时全程不产生 span；`retrieval` span 打开后把 `TraceScope` 传给 Retriever。Agent 的 `search_knowledge` 现在是 `agent_action -> search_knowledge -> retrieval -> dense/sparse/rrf_fusion` 真实子树（嵌套搜索永不产生 `generation`）。新增终态 `agent_finish` span；为此把 `_finish` 拆成 `_finish`（开/关 span、持有 `recorder.finish`）+ `_evaluate_finish`（返回 `_AnsweredFinish`/`_RefusedFinish`，不再自行结束 recorder），validation/repair/grounding_issue 全部挂在 `agent_finish` 之下。终态属性全部来自真实结构化输出：reranking=`items`（chunk/source/300 字符预览 + retrieval/rerank 双 score/rank）、evidence_gate=`threshold/score/support_ids/decision/reason`、generation/repair=公开 backend/model/revision/digest + 可用 usage/cost、validation=`issue_count/support_ids/backend_kind`。新增 `execution_trace.trace_span(recorder, stage, *, parent_id, attributes)`（`trace_stage` 改为委托它）。`build_application` 新增 keyword-only `trace_factory=`/`cancellation=` 透传。验证：新文件 `tests/integration/test_panel_services.py` 9 个测试（stash 掉 src/ 后 9 个全红，恢复后全绿）；默认套件 577 passed, 5 deselected；`ruff check .`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 4，偏离记录） | 计划 Task 4 的文件清单不完整，以下改动为通过测试与保持诚实性所必需，已在同一提交内完成：(1) `tests/fakes.py` 的 `ScriptedRetriever` 不再断言 `trace_scope is None`（改为记录），否则 `ask()` 一走 traced 路径就报错；(2) `src/enterprise_rag/evaluation.py` 的 `_ScenarioKnowledge.search_evidence` 增加 `recorder`/`parent_id` 形参；(3) `tests/live/test_demo_scenarios_live.py` 与 `tests/live/test_rag_live.py` 改用"顶层 stage"过滤断言 —— 真实 `HybridRetriever` 现在会产出 `dense`/`sparse`/`rrf_fusion` 子事件，原来的精确列表必然失效（**这两个 live 文件本轮无法运行**，Task 15 才会真正执行）；(4) 新增两处极小公开只读属性以便诚实上报：`EvidenceGate.min_score`（Gate 阈值）与 `AnswerGenerator.backend`（取被包裹后端的 model_id/revision/digest）。 |
| 2026-10-11 | 本次提交（Task 5） | `application.py` 新增 `RetrievalRuntimeComponents` / `RerankingRuntimeComponents` / `SharedRuntimeComponents`（冻结，只含 config/index/encoder/retriever/reranker/support_scorer/gate/validator/tools/index_builder，**不含 generator 与任何 run 状态**）；四个独立计时的构建器 `load_runtime_index` / `build_runtime_retrieval` / `build_runtime_reranking` / `load_runtime_tools`，各自保留原有的 real-backend 检查；`assemble_shared_runtime_components` 只校验并组合、**不做任何 I/O**；`aclose_runtime_components` 按 `tools → support_scorer → reranker → encoder → index` 顺序、以 `id()` 去重后每个对象最多关一次（`aclose` 优先 await，同步 `close` 走 `asyncio.to_thread`，无钩子的对象忽略）；`GteIndexBuilder.with_encoder(config, encoder)` 让调用方不再直接写私有 `_encoder`；`ApplicationTemplate`（冻结）+ `.bind(generator_backend=, trace_factory=, cancellation=)` 只创建 `AnswerGenerator`/`RagService`/`AgentPlanner`/`AgentService`/`Application`，重组件全部复用。**`build_application` 保持原样未改**：把它改写成调用这些构建器会把工厂调用顺序从 `index, encoder, reranker, generator, scorer` 变成 `index, encoder, reranker, scorer, generator`，而该顺序被既有测试钉住，属于"会改变语义"的情形，计划也要求只在不会改变语义时才复用。验证：新增 `tests/unit/test_panel_runtime.py`（15 个测试）与 `tests/integration/test_real_composition.py` 的 3 个回归测试；`git stash` 掉 `src/` 后新文件因 `ImportError` 全红，恢复后全绿；默认套件 595 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 5，偏离记录） | `load_runtime_tools` 比计划多一个 keyword-only `tool_loader=ToolRegistry.from_directory` 参数（默认行为与计划完全一致），否则测试无法注入计数用工具工厂；`SharedRuntimeComponents` 里 `gate`/`validator` 由 `build_runtime_reranking` 一并产出（validator 必须绑定 support_scorer，无法与重排阶段分开计时）。 |
| 2026-10-11 | 本次提交（Task 6） | `provider_profiles.py`：新增 `profile_fingerprint(profile)`（SHA-256 over name/base_url/model_id/api_key_env/privacy_terms_url/价格/请求与预算上限/synthetic/enabled 的规范化 JSON，**不含**环境变量值）与冻结的 `OnlineRunPreflight`（只有可展示字段 + 上限/成本），以及 `preflight_online_run(...)`——把整轮 N 次请求逐次交给已有的 `preflight_online_call`，因此 run 级请求上限、密钥检查与保守成本投影不可能与单次调用漂移；超限抛既有的 `ProfileNotAllowedError` / `BudgetBlockedError`，且**不创建任何 client**。`generation.py`：新增 `OnlineUsageSnapshot` 与 `OnlineGenerator.usage_snapshot()`；每次 `generate_json` 的 SDK client 改在 `finally` 里关闭（成功/映射失败/取消都会关，`aclose` 优先）；`LlamaCppGenerator.close()` 幂等且在没有 close 钩子时安全；私有 `_ONLINE_FAILURES` 提升为公开 `ONLINE_FALLBACK_FAILURES`（成员不变）。`panel/runtime.py`：`RunAuthorizationError`（**不是** `ONLINE_FALLBACK_FAILURES` 的成员或子类）、`AuthorizedOnlineGenerator`（每次调用前查 run 级计数）、`AuthorizedLocalFallbackGenerator`（只捕获 `ONLINE_FALLBACK_FAILURES`、首次合格失败后永久锁定到共享 Local、整轮只吐一次切换类别）、`GeneratorRegistry`（`get_local` 用 `asyncio.to_thread` 单飞、`get_online` 按完整 fingerprint 持久复用、`preflight`/`authorize`/`backend_for`/`aclose`）、`PanelRuntime`（六阶段 + 观察者 + `application_for` + `aclose`）。`application.py`：`_aclose_component` 提升为公开 `aclose_runtime_component`，并新增 `runtime_component_resources(product)`——阶段产物是**容器**，部分初始化回滚必须关掉里面的 encoder/reranker/index 而不是容器本身。验证：新增/扩展 5 个测试文件共 51 个新测试；`git stash` 掉 `src/` 后 4 个文件因 ImportError 全红，恢复后全绿；默认套件 629 passed, 5 deselected；`ruff check .`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 6，偏离记录） | (1) `OnlineRunPreflight` 除了可展示字段与上限/成本外，还带 `run_id` / `provider_fingerprint` / `fallback_local`，因为计划的 `authorize(preflight)` 签名没有别的途径拿到这三样；三者都非机密，仍然满足"只含可展示字段"。(2) `PanelRuntime` 的 `index_loader` / `encoder_factory` / `reranker_factory` / `support_scorer_factory` / `tool_loader` / `local_factory` / `online_factory` 都是 keyword-only 注入点（生产默认走真实实现），否则 Task 6 的"阻塞加载器走 `asyncio.to_thread`"和"部分失败只关一次"无法离线验证。(3) `src/enterprise_rag/provider_profiles.py` 仍有**先于本计划**的 `ruff format` 漂移（`_required_cost_precision` 的折行、`preflight_online_call` 里的 `_require_positive_actual_int` 调用），按已知坑 #5 不予重排；本任务新写的代码本身是 format-clean 的。 |
| 2026-10-11 | 本次提交（Task 7） | 新增 `panel/public_trace.py`：`PUBLIC_STAGE_FIELDS`（照计划 Task 9 的表逐字实现，Task 9 必须 import 它而不是再定义一份）、递归凭据键 denylist（`api_key`/`authorization`/`secret`/`password`/`credential` 用子串匹配，`token`/`prompt`/`traceback`/`env` 等用精确匹配，所以 `max_output_tokens` 这类公开计数不会被误杀）、排名行截到 5、字符串截到 300 字符、`Decimal` → 规范化字符串、未知 stage 只保留结构字段，以及 `sanitize_public_trace_event`（输入不被修改）与 `reconcile_terminal_events(progress_prefix, trace)`。新增 `panel/progress.py`：`BoundedProgressBuffer`（一个 deque + 一把锁），`publish` 顺序严格照计划（detach 后忽略 → 同 ID 的 `started` 原地替换 → 未满 append → 满则淘汰最老的 `started` → 全 terminal 时 detach 并依赖权威 `RunTrace`），sanitizer 抛错会被吞并 detach，**绝不**阻塞或抛回业务路径；`drain()` 返回并清空。验证：新增 `tests/unit/test_panel_public_trace.py`（28 个）与 `tests/unit/test_panel_progress.py`（9 个），并在 `tests/integration/test_panel_services.py` 增加最坏情况 Agent 基数测试（六动作 + 每次搜索的完整嵌套检索子树 + 观察 + 一次 fallback + 一次 repair + 全部终态 = 95 个唯一事件；取消前缀 96 个），两者都 < 256 且能被缓冲区完整保留。`git stash push --include-untracked -- src/` 后三个测试文件全红，恢复后全绿；默认套件 667 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 7，偏离记录） | (1) `panel/runtime.py`（Task 6 新写、已随 `9dbb23e` 提交）本轮被 `ruff format` 补正了两处换行——Task 6 收尾时漏跑了该文件的 format 检查，属我方疏漏，非历史文件漂移。(2) 计划说"`Decimal` 成本转规范化字符串"，但 `TraceEvent` 的 `_freeze_value` **结构性拒绝** `Decimal`，所以正常路径上根本到不了那个分支；该分支作为防御性代码保留，测试改为直接调用 `_sanitize_value` 验证，并在注释里写明它服务的是未来的非 `TraceEvent` 调用方。(3) 5 行上限只作用于"元素全是 Mapping 的序列"（即排名行），标量序列（如 `support_ids`）不截断——否则 20 条 support id 会被误砍到 5 条。 |
| 2026-10-11 | 本次提交（Task 8） | 新增 `panel/coordinator.py`：`RunCoordinator(runtime, *, clock=)`，只读 `state`/`active_run_id`/`history`（最新在前）/`stages`；async `initialize`（复用 runtime 的共享初始化任务并把结果落到 `state`）、`start(spec)`（返回 run_id）、`wait(run_id)`、`aclose()`；同步 `begin_authorization(request)`（READY→AUTHORIZING 并预留 run id，不建服务/客户端）、`record_blocked(...)`（只对匹配的 pending request 合法，产出 `spec=None` 的 BLOCKED 记录后回 READY）、`request_cancel(run_id)`（幂等、只作用于当前活动 run）、`drain_progress(run_id)`。每次 Run 新建一个 `BoundedProgressBuffer` 与 `CancellationToken`，把 `buffer.publish` 当 `observer` 传给 `PanelRuntime.application_for`；observer 只在 `active_run_id` 仍等于本 run 时记录，所以迟到事件既进不了活动视图也进不了记录。终态落库统一走 `_store_terminal_locked`（单锁 + `active_run_id` 比对），`deque(maxlen=5)` 存历史，落库后状态回 READY 且不改动已终结的记录。业务 FAILED 记录带 `result.error_category`（`PanelRunRecord._validate_failed` 要求与结果类别一致）；`RunCancelled` → CANCELLED（无 result/完成 trace，前缀 + 一条 `{run_id}:cancel` 的 `cancel_boundary`）；绑定失败 → pre-service FAILED（有 spec、无伪造 result/trace、带 sanitized public_error）。验证：新增 `tests/integration/test_panel_coordinator.py` 17 个测试（全部用 `asyncio.Event` 驱动，无 sleep）；`git stash push --include-untracked -- src/` 后因 ModuleNotFoundError 全红，恢复后全绿；默认套件 684 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 8，偏离记录） | (1) `start(spec)` 的返回值计划未规定，本实现返回 `run_id`（`wait(run_id)` 需要它）。(2) 计划把"重跑相同输入必须产生不同 run id"列在 Task 8，但 run id 由 controller 分配（`RunRequest.run_id`），coordinator 只按 spec 执行；本任务用"相同内容 + 不同 run id → 两条独立记录"覆盖，真正的 run-id 分配测试留给 Task 10。(3) 为了让 5 条历史与进度缓冲有界，终态落库时会淘汰既不在历史、也不在 `_tasks` 里的 run 的 buffer/prefix。 |
| 2026-10-11 | 本次提交（Task 9） | 新增 `panel/projection.py`：`project_run(record, *, selected_event_id=None)`、`project_inspector(nodes, selected_event_id)`、`project_history(records)`，以及冻结的 `PanelRunViewModel` / `TraceNodeViewModel` / `InspectorViewModel` / `PanelHistoryEntryViewModel` / `SourceViewModel` / `ToolCallViewModel` / `ObservationViewModel`。所有显示值只来自 `PanelRunRecord` 的结构化字段（`state`/`result`/`trace`/`progress_events`/`error_category`/`public_error`）；五个状态各有文本 + 图标 + 颜色（颜色只是补充）。trace 节点来自 `record.progress_events`，**再次**经过 `sanitize_public_trace_event`（纵深防御，白名单只有 `panel/public_trace.py` 那一份）。`project_inspector` 只按 event id 选中节点，不改变其它任何字段。验证：新增 `tests/unit/test_panel_projection.py` 20 个测试，其中包含"把 `REFUSED:`、假引用 `policy.md::9999` 与 'ignore the Gate' 塞进 answer 文本，证明状态/来源完全不受影响"、五态文本+图标、以及"公开白名单至少覆盖计划 Task 9 那张表"的逐键核对。`git stash push --include-untracked -- src/` 后因 ModuleNotFoundError 全红，恢复后全绿；默认套件 704 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 9，偏离记录） | `projection.py` 只 import `sanitize_public_trace_event`，**不** import `PUBLIC_STAGE_FIELDS`——白名单由 sanitizer 内部使用，投影侧再 import 一次会变成未使用的死代码；"该表至少覆盖计划列出的键"这条核对放在 `tests/unit/test_panel_projection.py::test_public_stage_fields_cover_the_documented_minimum` 里逐键断言。 |
| 2026-10-11 | 本次提交（Task 10） | 新增 `panel/controller.py`：`ScenarioPreset` + 三个 `PRESETS`（计划里的原文逐字）；冻结的 `PanelViewModel` / `OnlineConfirmationViewModel` / `ProviderOptionViewModel`；`PanelController(coordinator, runtime, *, presets=PRESETS, clock=)` 暴露只读 `presets`/`view`，同步 `select_scenario`/`update_custom_text`/`select_mode`/`select_generator`/`select_provider`/`set_fallback_local`/`reject_online`/`request_stop`/`select_history`/`select_trace_node`，async `prepare_run`/`confirm_online`/`refresh`。`prepare_run` 先校验表单（文本非空、online 必须已选 provider），**先分配 run id 再预检**；local 直接建 `RunSpec` 并 `start` 一次；online 先 `begin_authorization` 再 `preflight`，失败即 `record_blocked`（BLOCKED、零 spec/零应用/零客户端），成功则返回 display-safe 确认视图且**不**建 spec。`confirm_online` 用确认时保存的 preflight 走 `registry.authorize` 建一次性授权、复制成不可变 `RunSpec`、`start` 一次。`select_provider` 记录选择时的 profile 指纹，`prepare_run` 重新计算并比对，不一致即 BLOCKED（对应计划里的"changed profile / fingerprint mismatch"）。`request_stop` 永远取 `coordinator.active_run_id`。`view.run` 由 `select_history` 决定查看哪条记录，但每次新 Run 都会把视图重置回活动 run。验证：新增 `tests/unit/test_panel_controller.py` 21 个测试 + `tests/integration/test_panel_authorization.py` 新增 3 个真实 registry 集成测试（确认流程铸出的授权能被 `backend_for` 消费且只能用一次；关网 → BLOCKED；缺 key → BLOCKED，三者都断言零 `start`、零客户端调用、零授权签发）。`git stash push --include-untracked -- src/` 后因 ModuleNotFoundError 全红，恢复后全绿；默认套件 728 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 10，偏离记录） | (1) "online 已选但未选 provider" 被当作**表单形状错误**（`ValueError`）而不是 BLOCKED——`RunRequest` 对 online 强制要求 provider_name/fingerprint，此时连请求都构造不出来；"已选 profile 随后消失/被改" 才是 BLOCKED（`profile_not_allowed` / `configuration`），两条路径都有测试。(2) `confirm_online` 使用 `prepare_run` 时保存的 preflight 来签发授权（即"用户看到的确认就是被授权的额度"），而不是在确认时重跑预检；这样确认视图与实际授权严格一致。(3) `select_provider` 存储的是选择时刻的指纹，用于发现 profile 文件被改动。 |
| 2026-10-11 | 本次提交（Task 11） | `cli.py`：新增 `PANEL_HOST = "127.0.0.1"`、`PANEL_DEFAULT_PORT = 8080`、`PanelRunner` 类型；`panel` 子命令带 `--port`（int，默认 8080）与 `--no-open`；`main(argv, *, application=None, panel_runner=None)` 在 `build_application` 与异步 dispatch **之前**处理 panel 分支（先 `_probe_panel_port`，再调 runner），异常统一走 `_public_error` 并返回 1；`_validate_panel_options` 把端口限制在 1–65535（越界走 `parser.error` → 退出码 2）；`_probe_panel_port` 只 bind 请求的 `127.0.0.1:port` 并立刻 close，**故意不设** `SO_REUSEADDR`（本平台上设了会让探测 socket 绑上别人正在监听的地址，探测就失效了）；`_load_panel_runner` 是 `cli.py` 里唯一 import `enterprise_rag.panel.app` 的地方。新增 `panel/app.py`：`run_panel(*, host, port, open_browser, native)` —— 校验 host 必须是回环、native 必须为 False、端口在范围内，打印 `http://host:port/` 后调 `ui.run`，并把 `host/port/show/native=False/reload=False/on_air=None/fastapi_docs=False` **全部显式覆盖**；已用 `.venv` 里 NiceGUI 3.18.0 的 `inspect.signature(ui.run)` 逐参数核对过（`show` 而非 `open_browser`、默认 `reload=True`、默认 `native=False`）。验证：新增 `tests/integration/test_panel_cli.py` 17 个测试 + `tests/integration/test_cli.py` 新增 2 个（panel 分支不得触发 `build_application`；doctor 分发不变）；`git stash push --include-untracked -- src/` 后因 ModuleNotFoundError 全红，恢复后全绿；默认套件 747 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 11，偏离记录） | `panel/app.py` 提前在本任务落地（计划把它列在 Task 12 的 Files 里）。原因：`cli.py` 的懒 import 是 `from .panel.app import run_panel`，ty 会静态解析该模块，模块不存在则 `ty check src` 报 `unresolved-import`，而计划要求每个任务收尾时 ty 全绿。因此本任务只落地 `run_panel`（含全部安全覆盖与校验），**页面组合 `create_panel_app` 仍留给 Task 12**；Task 12 对 `panel/app.py` 由"Create"变为"Modify"。 |
| 2026-10-11 | `9273900`（Task 12 Step 0） | 根目录新增 `conftest.py`（`pytest_plugins = ["nicegui.testing.user_plugin"]`，必须放根目录，pytest 9 拒绝非顶层 conftest 里的 `pytest_plugins`；用 `user_plugin` 而不是会拉 selenium 的 `plugin`）；新增 `tests/integration/panel_main.py` 作为 `user` fixture 的 main file（`runpy.run_path` 以 `__main__` 执行，因此页面必须在模块级注册，并且**必须调用 `ui.run()`**，否则 NiceGUI 报 "You must call ui.run() to start the server"）。单独提交前跑过一次全量套件：747 passed，无新增 collection error。 |
| 2026-10-11 | 本次提交（Task 12） | `panel/app.py` 新增 `create_panel_app(*, runtime_factory=PanelRuntime, controller_factory=PanelController)`：一个进程级 runtime + `RunCoordinator` + 每客户端一个 `PanelController`；注册 `/`、先渲染初始化外壳（`ui_available_at` 在任何模型加载**之前**记录）再后台跑共享初始化；`controls` 与 `workbench` 两个 `ui.refreshable`，0.2 s 定时器先 `await controller.refresh()` 再按需刷新控件（只在协调器状态**变化**时重建控件，避免每 tick 重建输入框），另有一个 `once=True` 的立即 tick。布局按计划：`panel-controls`（预设卡片 / custom 输入 / mode 与 generator 选择 / provider 选择 / fallback 开关 / Run 与 Stop）、`pipeline-canvas`（受控节点卡片链，**未**用 `ui.mermaid`）、`inspector-panel`、`panel-result`（state/answer/refusal/error/sources/observations）、`recent-runs`、`online-confirmation`。`app.on_shutdown` 结算活动 Run 并只关一次 runtime。新增 `tests/integration/panel_fakes.py`（FakeRuntime + FakeRegistry + 按脚本产出 answered/refused/failed/blocking 的假 Application，跑的是**真实**的 coordinator 与 controller）与 `tests/integration/test_panel_page.py`（14 个测试，覆盖：初始化阶段与面板状态、初始化失败时 Run 禁用、READY 后启用、三张预设卡片不自动运行、点卡片只填文本、Custom 文本可编辑并用于 Run、Online 只列注册 profile 且 fallback 默认关、确认视图展示 provider/model/endpoint/隐私/请求上限/成本上限/fallback 与 Confirm/Cancel、取消确认产生 BLOCKED、本地 Run 渲染答案/来源/pipeline 节点、选中节点只改 Inspector、历史渲染且选择不改记录、REFUSED 与 FAILED 各自的文本与图标、两个客户端共享初始化器与历史）。验证：`git stash push --include-untracked -- src/` 后 14 个页面测试全 ERROR，恢复后全绿；默认套件 763 passed, 5 deselected；`ruff check .`、`ruff format --check`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 12，偏离记录） | (1) **重要坑**：NiceGUI 的 `nicegui_reset_globals` 在每次 user simulation 结束时会从 `sys.modules` 里删掉"注册了页面的模块**及其所有父模块**"。页面函数定义在 `enterprise_rag.panel.app` 里，所以它会连带删掉 `enterprise_rag` 本身，导致后续测试（如 `tests/test_smoke.py` 的 monkeypatch）报 `module 'enterprise_rag' has no attribute 'cli'`。修法：`panel_main.py` 在建页后把 `Client.page_routes` 里每个页面函数的 `__module__` 改写成该 harness 模块（`__main__`），使清理只影响 `__main__`。(2) `create_panel_app` 的 `runtime_factory`/`controller_factory` 形参放宽为 `Callable[..., object]` 并内部 `cast`，否则 ty 不接受 `PanelRuntime`/`PanelController` 作为默认值。(3) 计划里的 mode/generator 用 `ui.toggle` 会让测试无法驱动（Quasar 的 `q-btn-toggle` 不响应容器上的点击），改成两个显式的 `ui.button`（`mode-rag`/`mode-agent`/`generator-local`/`generator-online`，当前项 `set_enabled(False)`），语义等价且可测。(4) 顺带改了 Task 10 的 `controller.py`：`select_generator("online")` 在**只注册了一个 profile** 时自动预选它（并记录选择时刻的指纹），否则多 profile 场景仍需手动选；这是为可测性与 UX，已补两条单测。(5) 页面用 NiceGUI 的 `.mark(...)`（测试用 `ElementFilter` 按 marker 找元素）而不是 `data-testid` 属性——`mark` 才是这个测试脚手架认的标识。 |
| 2026-10-11 | 本次提交（Task 13） | `tests/integration/test_panel_services.py`：新增参数化的**进度/最终 trace 一致性矩阵**（answered / refused / repair 成功 / repair 拒答 四种形态，用真实 `BoundedProgressBuffer` 当 observer，断言 `reconcile_terminal_events` 后每个最终事件的 `parent_id/stage/status/error_category` 与最终 trace 完全一致且每个 event_id 恰好一次），以及"**observer 缺失 / 已 detach / 抛异常**三种情况下业务结果与最终 trace 与基线完全相同"。`tests/integration/test_panel_coordinator.py`：新增**五终态不变量矩阵**（answered / blocked / failed 三种在真实 coordinator 上产生，cancelled 单独一个测试，断言各自的 spec/result/trace/error_category/public_error 组合，并断言"回到 READY 后终态记录未被改动"）与"cancelled 记录恰好一条 `cancel_boundary` 且所有事件 run_id 一致"。新增 `tests/integration/test_panel_network.py`：本地/fake 的 controller→coordinator→service 路径上硬失败 `socket.connect`/`connect_ex`/`create_connection`/`getaddrinfo` 并断言零尝试；`rag-agent panel` 的端口预检**只** bind `127.0.0.1:请求端口`；把可识别的密钥字符串种进环境变量、profile 与假错误后，序列化进度、历史、确认视图、初始化消息与页面视图模型，断言四类禁用串一个都不出现。`tests/integration/test_panel_page.py` 新增渲染页面的禁用串 tripwire。验证：默认套件 777 passed, 5 deselected；`ruff check .`、`ty check src` 全绿；**本计划新建的 27 个文件全部通过 `ruff format --check`**；`uv build --offline --no-python-downloads` 在本机成功产出 sdist 与 wheel（build isolation 离线可用，无需 `--no-build-isolation`）。 |
| 2026-10-11 | 本次提交（Task 13，格式偏差的实测结论） | 计划 Task 13 Step 5 要求"每个 task-touched 文件都通过 `ruff format --check`"，这条**当前不可能整体满足**。已用脚本把本计划开始前的最后一个提交（`28e0687`）里的各文件取出单独跑 format 检查，结论是：当前不通过的 8 个核心文件（`execution_trace.py`、`retrieval.py`、`rag_service.py`、`agent_service.py`、`application.py`、`generation.py`、`provider_profiles.py`、`cli.py`）**在计划开始前就已经不通过**，即漂移全部是历史遗留、本计划**没有引入任何新漂移**；`ports.py` 一直是干净的；`execution_control.py` 是本计划新建的，也干净。因此把该检查收窄为"本计划新建/新增代码块"，并在本行如实记录这一偏差（计划执行边界本身也禁止批量重排历史文件）。 |
| 2026-10-11 | 本次提交（Task 14） | **先测量再写**：实测 `uv lock --check --offline --no-python-downloads` 成功（`Resolved 101 packages in 7ms`），`uv sync --locked --offline --no-python-downloads --extra retrieval --extra local-generator --extra online-generator --extra panel` **成功且约 1 秒**（只重装了本仓库自身，没有重新获取任何 wheel），因此 README 与 `docs/panel-guide.md` 写的就是这条 known-good 命令，并明确写出**不要**裸跑 `uv sync --locked` 及其原因（GitHub 487 MiB wheel、本机约 40 KB/s）。新增 `docs/panel-guide.md`（安装/启动、初始化阶段、三预设与 Custom、Local/Online 确认与 BLOCKED 矩阵、流水线画布与 Inspector、Stop 语义、五个终态、最近五条内存历史、端口占用、CLI 回退、常见问题）；README 更新为"两个入口"、加入实测安装命令与面板小节、`panel [--port] [--no-open]` 用法、并修正"dense/sparse/RRF 不作为独立 trace 子事件展示"这条**已经过时**的边界说明；`docs/architecture.md` 新增第 9 节（同进程同核心的共享运行时/注册表/模板/协调器/有界进度/投影/取消/离线保证 + 面板模块责任表）；`docs/security-and-cost.md` 新增第 10 节（一次性授权、指纹绑定、run 级上限公式、超限不回退、显式 fallback、合成数据、密钥只报状态、无任意端点、真实在线仍需单独审批）；`docs/evaluation.md` 新增第 8 节 panel 验收小节——**值为待填表格**，明确写出 fake 契约测试不得标成 real-local；`docs/demo-script.md` 新增第 6 节面板版三幕演示（含每幕要点的节点、预期状态/来源/Observation 与 CLI 回退命令）。验证：`Test-Path` 六个文档全为 True；`python -m enterprise_rag panel --help` 退出码 0 且列出 `--port`/`--no-open`；顶层 help 列出 `{kb,rag,agent,eval,doctor,panel}`；默认套件 777 passed, 5 deselected；`ruff check .`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 14，偏离记录） | 计划 Step 5 写的验证命令是 `python -m enterprise_rag.cli --help`，但 `cli.py` **没有 `if __name__ == "__main__"` 保护**，所以 `python -m enterprise_rag.cli` 是**空操作**（退出码 0、零输出），看起来像"通过"其实什么都没验证。改用正确的入口 `python -m enterprise_rag --help` / `python -m enterprise_rag panel --help`（或 `rag-agent` 脚本）后确认两者都正常。后续任务若要跑 CLI，请用这两个入口之一。 |
| 2026-10-11 | 本次提交（Task 15） | 新增 `scripts/collect_panel_acceptance.py`（纯 helper：`nearest_rank` 用 `ceil(p*n)-1`、`latency_summary`、`validate_artifact` 严格 schema、`atomic_write` 原子写、`promote` 递归拒绝密钥样式键值/绝对路径/环境变量值/prompt/response/traceback；`collect`/`run_acceptance` 里真实运行时 import 保持 lazy）、`tests/unit/test_collect_panel_acceptance.py`（23 个纯单测，不加载模型/不开 socket）、`tests/live/test_panel_demo_live.py`（与收集器**共用**同一套 runner 与断言，避免漂移）。**真实本地验收实测结果**：三幕 × 三轮语义断言全部通过；`runtime_ready_seconds` **9.179 s**（上限 60 s）；`ui_available_seconds` 0.0000332 s；RAG p95 **15.239 s**、Agent p95 **24.878 s**、拒答 p95 **0.680 s**（上限均 45 s）；`network_attempts` **0**；index/encoder/reranker/local_generator 加载计数各 **1**；进程峰值 RAM 7,009,529,856 bytes；VRAM 无可靠计数器，如实记 `null`；provenance `real-local`。同时**重跑既有 CLI 真实本地套件**（`test_demo_scenarios_live.py` + `test_rag_live.py`，2 passed，173 s），证明 Task 4 对"顶层 stage"断言的修改是正确的、面板改造没有破坏 CLI 基线。证据经 promotion 写入 `docs/demo-artifacts/panel-demo-acceptance.json`，精确值抄进 `docs/evaluation.md` 的 panel 小节；`docs/demo-artifacts/README.md` 增加重新生成与提升命令。默认套件 800 passed, 6 deselected；`ruff check .`、`ruff format --check`（新文件）、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 15，偏离记录） | (1) 首次运行时我的断言 `backend_kinds == ["real"]` 过严——Evidence Gate 按设计就是 `programmatic`，实际集合是 `["programmatic", "real"]`；改为断言 `"real" in ...` 且 `"fake" not in ...`，**没有**放宽任何实质要求。(2) `peak_working_set_bytes` 第一版因未设 ctypes `argtypes`/`restype` 而静默返回 0（既不真实也不是 `null`），已改为照既有 live test 的写法设好签名并在失败时返回 `None`；`peak_vram_mib` 第一版用 `torch.cuda` 会漏掉 llama.cpp 自己的 CUDA 上下文，改为用 `nvidia-smi` 查本进程，取不到就如实 `null`。(3) promotion 的"长 token"启发式最初误伤 `model_digest`/`model_revision`（内容哈希）与 `PREPARING_DEFAULT_LOCAL_GENERATOR`（全大写阶段名），已收紧为"必须同时含小写与（大写或数字）"，并给内容哈希键加白名单——**收紧的是误报，不是安全边界**，相应负例仍在单测里。 |
| 2026-10-11 | `2dbdd9f`（Task 16a，代码就绪） | 新增 `scripts/collect_panel_online_acceptance.py`（**复用** Task 15 的纯 helper，只新增在线 artifact 的 schema 与运行流程；`collect` 在 `RAG_AGENT_ALLOW_NETWORK != "true"` 时直接拒绝，避免误触）、`tests/unit/test_collect_panel_online_acceptance.py`（21 个纯单测）、`tests/live/test_panel_online_live.py`（`live` 标记；缺 key 时 `pytest.fail` 明确报出"那次运行尚未发生"）。为复用而把 `collect_panel_acceptance.py` 里的 `validate_artifact` 拆出通用 `validate_against(schema, payload)`、并把 `_scan` 公开为 `scan_forbidden_content`。**测试真的抓到了扫描器的一个漏洞**：形如 `Authorization: Bearer sk-...` 的**内联凭据**原先不会被发现（它既不是纯 token 也不匹配环境变量形态），已补 `_INLINE_CREDENTIAL` 规则；同时把"凭据样式的键"判定限定为**非布尔值**，否则 `replayed_authorization_is_refused` 这类描述性布尔标志会被误判（布尔不可能携带凭据）。验证：默认套件 821 passed, 7 deselected；`ruff check .`、`ty check src`、两个新脚本的 `ruff format --check` 全绿。 |
| 2026-10-11 | `00f4902`（Task 16b 前置修复） | 用户首次真实运行时暴露三个我方缺陷，全部修复：(1) **`ModuleNotFoundError: No module named 'scripts'`** —— 以文件方式运行脚本时 Python 只把 `scripts/` 放进 `sys.path`，已加仓库根目录 bootstrap（pytest 下能跑是因为根 `conftest.py` 顺带加了根目录，掩盖了这个问题）。(2) **`RuntimeWarning: coroutine 'AsyncAPIClient.close' was never awaited`** —— 安装的 openai SDK 异步客户端只有**异步** `close`、没有 `aclose`，旧代码把它当同步函数丢进 `asyncio.to_thread`，协程被丢弃、连接根本没关（每次调用泄漏一个客户端）；已改为依次尝试 `aclose`/`close`、可 await 就 await，并补了两个回归测试（异步 close / 同步 close 各一），用 `-W error::RuntimeWarning` 跑过。(3) 失败信息只有 `'round 1: failed'`，看不到类别；收集器现在记录并打印每个场景的 `error_category` 与 `public_error`（两者本身即脱敏，不含 key/prompt/traceback）。 |
| 2026-10-11 | `518131d`（Task 16b 硬化） | 用户那次运行暴露的**第四个**问题：`GeneratorRegistry.preflight` **不校验**请求携带的 `provider_fingerprint` 是否与所命名的 profile 一致（它自己重算指纹、忽略请求里那个），所以"命名 profile A 却携带 B 的指纹"能顺利通过预检。系统最终仍会 fail-closed（`RunSpec` 构造时拒绝），但这层本就该拒绝该输入；已加校验并补离线测试 `test_preflight_refuses_a_request_naming_the_wrong_profile`。同时把失败运行的**脱敏结论**写进 `docs/evaluation.md`（`online-attempted-not-verified` + 三幕结果表 + 四条 caveat 仍适用），并**不**把失败 artifact 提升到 `docs/demo-artifacts/`。验证：默认套件 824 passed, 7 deselected；`ruff check .`、`ty check src` 全绿。 |
| 2026-10-11 | 本次提交（Task 16c） | 第二次真实尝试：`model_id` 去掉 `[1M]` 后 **`invalid_request`（400）消失**，变成 **`timeout`**（45 s 内无响应）。三幕：RAG/Agent 均 `failed`/`timeout`（52.6 / 48.3 s），拒答 `refused` ✅（0.5 s）；请求 2 次、成本上界 CNY 0.686；负例检查三项全通过。结论：`[1M]` 确为首次 400 之因；新失败模式**不是** HTTP 错误码而是响应超时。三个待区分原因（中转站缓慢 / 不支持 strict `json_schema` / 模型 id 仍不对）**尚未**区分。**未**盲目重试、**未**提高上限、**未**改代码绕过。已把两次尝试的脱敏结论写入 `docs/evaluation.md`（`online-attempted-not-verified`，含两列表格与"能确定 / 不能确定"分栏），失败 artifact **未**提升到 `docs/demo-artifacts/`。验证：默认套件 824 passed, 7 deselected；`ruff check .`、`ty check src` 全绿。 |
| 2026-10-12 | 本次提交（Task 16d） | **第三次真实在线尝试**（`model_id = deepseek-v4.1-flash`，`RAG_AGENT_ALLOW_NETWORK=true` / `RAG_AGENT_ALLOW_MODEL_DOWNLOAD=false`，`collect` 三幕 × 1 轮）：RAG `failed` / **`provider_unavailable`** / 39.5 s，Agent `failed` / `timeout` / 48.7 s，拒答 `refused` ✅ / 0.5 s；请求 2 次、成本上界 CNY 0.686；负例检查三项全通过。**唯一新信息**：`provider_unavailable` 按 `_mapped_online_error`（`src/enterprise_rag/generation.py:619-622`）**只在 `status_code >= 500`**（或 `APIConnectionError` / `InternalServerError`）时产生 —— 这是第一次由**我方代码路径**观察到 5xx，与绕过项目代码的直连探测（9.6 s / 502）**相互独立地**指向同一结论：失败发生在中转站与它的上游之间，与我们请求的形状无关。**未**重试、**未**提高上限、**未**换模型、**未**改代码绕过。按 §4"选择 2"**如实收尾**：Task 16 记为"3 次真实在线 + 1 次直连探测，未通过、未验证"；失败 artifact **未**提升（`docs/demo-artifacts/panel-online-acceptance.json` **不存在**），原始证据留在未跟踪的 `.local/benchmarks/panel-online-live.json`；`docs/evaluation.md` 在线小节改写为"三次尝试，均未通过"并补上收尾结论（**在线路径只有 fake 契约测试覆盖、这家第三方中转站的实际服务未经验证**）。验证：**关网**默认套件 **824 passed, 7 deselected**（与基线一致）；`ruff check .`、`ty check src` 全绿；`config/providers.toml` 与 `.local/` 仍 untracked（`git ls-files config .local` 只列出 `config/providers.example.toml`）。 |
| 2026-10-13 | `aa01e58`（在线表述纠错） | 三次真实调用发生后，多份**活文档**里"本仓库迄今没有进行真实在线调用"的断言变成了假话，按事实改正：`README.md`、`docs/security-and-cost.md`（并补上计划 Step 6 要求的"那一次授权**实际**执行了什么"：上限 30 次 vs 实发 2 次、上限 CNY 10 vs 上界 0.686、重放被拒、指纹不符被拒、负例检查期间零请求）、`docs/evaluation.md`（"online live eval：尚未执行" → "已尝试、未通过"）。`docs/demo-artifacts/panel-demo-acceptance.json` 的 `online` 标签是**硬编码常量**而非测量值，已连同 `scripts/collect_panel_acceptance.py` 与 `tests/unit/test_collect_panel_acceptance.py` 一并改为 `online-attempted-not-verified`（即重新生成时该字段会产出的值；该次运行的实测数据未改动）。`docs/panel-handoff.md` 是**带日期的设计阶段快照**，历史数字保持原样，只加日期注记标明那两条"没有真实在线调用"的结论已被取代。`docs/superpowers/` 下的计划与设计文档**未**改动 —— 那里"Record Online as `online-unverified`"是当时的规定，改它等于篡改计划。验证：关网默认套件 824 passed, 7 deselected；`ruff check .`、`ty check src` 全绿；artifact 仍可解析。 |
| 2026-10-13 | 本次提交（计划完结） | **用户决定采用 (c) 永久收尾**，Task 16 就此关闭（不再重试、不做换模型诊断）。计划文档 `docs/superpowers/plans/2026-10-11-local-engineering-panel.md` 的 **Final completion checklist 按事实标注**：13 条已满足的勾上并各附提交与实测依据，**唯一未满足的在线验收条留空**并写明原因（三次尝试全败、从未产出在线答案、无 artifact 提升、失败定位在中转站一侧、记为未验证而非绕过）。本文件 §2 断点与 §4 现状同步改为"计划已完结"。这是**唯一**一次对计划正文的改动，且只改这份用于跟踪进度的 checklist（计划自身即声明用 checkbox 跟踪），计划的目标/边界/任务正文一律未动。 |
| 2026-10-13 | 本次提交（交付合并） | **`codex/local-engineering-panel` 快进合并到 `master`**（`git merge --ff-only`，无冲突、无 merge commit，29 个任务级提交原样保留；`master` 原停在设计阶段的 `422acec`）。合并落地 70 个文件 / 约 15,700 行新增，含 `src/enterprise_rag/panel/` 全部 9 个模块、`conftest.py`、两个收集脚本、Task 15 的验收 artifact 与全部面板测试。合并后在 `master` 上复验：关网默认套件 **824 passed, 7 deselected**；`ruff check .`、`ty check src` 全绿；`python -m enterprise_rag --help` 仍列出 `{kb,rag,agent,eval,doctor,panel}`。仓库**无 remote**，因此不存在 push / PR / 发布；工作分支保留为记录，与 `master` 同指。 |
| 2026-10-13 | 本次提交（补记浏览器 E2E 缺口） | 用户要求把最后一个未记录的真实缺口写进文档：**面板从未在真实浏览器里被打开过并作为验收的一部分**。此前这条只在对话里说过，仓库里没有留痕。已补三处：`docs/evaluation.md` 第 8 节新增"**未做的验证：浏览器端到端**"小节（Task 15 按设计不含浏览器；`ui_available_seconds` 度量的是路由/外壳注册而非首屏；页面行为只由 `tests/integration/test_panel_page.py` 的 14 个 user-simulation 测试覆盖；真实浏览器里的首屏渲染、布局、Quasar 点击行为、`--port`/`--no-open` 体验均**未经验证**；计划 checklist 里两条页面行为项的验证方法正是这套模拟测试）；`docs/panel-guide.md` 第 2 节加"已验证范围"提示框，避免把"面板验收通过"误读为 UI 已验收；计划 checklist 的注记补一句验证方法说明（对计划正文的第二次、也是最后一次改动，仅限该注记）。这不改变 real-local 验收的结论，只是不拿它去覆盖一个真实存在的缺口。 |
