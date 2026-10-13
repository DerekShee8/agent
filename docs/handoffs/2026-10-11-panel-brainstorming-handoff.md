# 面板 Brainstorming 交接包

## 1. 交接目的

这份交接包用于在同一项目的另一个对话窗口继续“本地工程演示面板”设计。当前仍处于 `brainstorming` 阶段，不是实施阶段。

仓库绝对路径：

```text
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo
```

面板设计前的仓库基线：

- branch：`master`
- commit：`a8b7826`（`docs: add Work Buddy learning handoff`）
- 交接前工作树：clean

本交接包会位于该基线之后的纯文档提交中。新窗口应先只读运行 `git status --short --branch` 和 `git log -5 --oneline`，以当前 Git 状态为准，不要回退或清理用户工作区。

## 2. 当前项目状态

CLI Core 已完成，并有真实本地验收证据。现有记录包括：

- 默认离线测试：`547 passed, 5 deselected`；
- Retriever Recall@20：`18/18`；
- Reranker conditional Hit@5：`18/18`；
- RAG grounded answer：`16/18`；
- 无答案识别：`6/6`；
- citation validity：`18/18`；
- 正式三幕演示：cold start `11.101s`，RAG p95 `17.323s`，Agent p95 `30.134s`，拒答 p95 `0.853s`。

这些是已有验收记录，不等于新窗口已经重新运行。完整证据见 [面板交接](../panel-handoff.md) 与 [评测说明](../evaluation.md)。

当前没有面板生产代码，没有安装 UI framework，没有修改 trace contract，也没有创建实施计划。

## 3. 已固定的产品与架构边界

以下决定来自已批准设计和 CLI 交接，不应在面板 brainstorming 中重新打开：

- 面板是“流水线工作台”，不是聊天产品皮肤。
- 首版仅本地运行，和 CLI 复用同一个 `Application`、`RagService`、`AgentService` 和正式结果契约。
- 首选同一 Python 进程直接调用业务服务，不预先建设独立 HTTP API。
- CLI 和面板不能复制业务判断；Evidence Gate、回答/拒答、来源、工具选择和失败语义仍由业务层决定。
- RAG 必须能脱离 Agent 独立运行；Agent 不得绕过或抹掉 RAG 的证据与失败语义。
- 高级用户只可选择 Generator。Retriever 与 Reranker 不在面板开放替换。
- 默认 network off。Online 必须选择已登记 Provider；不得提供任意 base URL、任意 API key 输入框或绕过 Gate/Validator 的开关。
- 不展示 hidden chain-of-thought、完整 system prompt、secret 或未经批准的真实数据。
- 不做登录、多租户、云部署、生产运维后台或危险工具。

已批准的基本布局：

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

## 4. 本轮已确认的面板决定

本轮对话已经逐项确认：

1. **最高优先级：面试展示效果。** 可以为了清楚展示证据链和控制点多写一些 UI 代码，但不能牺牲本地稳定性。
2. **运行反馈：混合实时。** 运行中实时显示当前顶层阶段与耗时；阶段完成后再显示分数、证据和详细 trace。
3. **默认 trace 密度：分层摘要。** 主画布显示阶段节点；Inspector 展示 dense/sparse 候选、RRF、Reranker Top 5、Gate 决策和引用依据。Agent 的 `search_knowledge` 可展开成内部 RAG 子链。
4. **首版范围：演示运行台。** 支持 RAG/Agent 模式、自定义问题、三幕预设问题、Generator 选择、运行状态、结果、来源、trace 和系统健康状态。索引构建、完整评测和模型管理继续使用 CLI。
5. **可视化伴侣：已同意。** 后续涉及布局、节点层级和交互时可以用浏览器线框图。当前尚未启动伴侣服务器，也没有生成 mockup。

## 5. Framework 研究结论与当前断点

已基于官方资料比较 NiceGUI、Streamlit 和 Gradio Blocks：

### NiceGUI，当前推荐

- async event handler 与现有 async Application 接口匹配；
- row/column/grid/splitter/drawer 足以构造固定 Inspector；
- `ui.mermaid` 原生支持 `on_node_click`，适合可点击流水线；
- 不采用 Streamlit 的整页 rerun 模型；
- 需要显式绑定 `127.0.0.1`，避免默认暴露到局域网；
- 首版建议普通浏览器模式，不启用 `native=True`/pywebview；
- 任何遗漏的同步模型调用都可能阻塞单一 event loop，因此实施计划必须包含阻塞路径审计。

官方资料：

- <https://nicegui.io/documentation/section_action_events>
- <https://nicegui.io/documentation/mermaid>
- <https://nicegui.io/documentation/section_page_layout>
- <https://nicegui.io/documentation/section_configuration_deployment>

### Streamlit，第二选择

优点是上手快，原生状态、columns 和 step timeline 足以做普通 Demo。主要问题是 widget 交互通常触发脚本 rerun；精确的横向可点击 DAG 往往需要自定义 component，长模型运行的状态与副作用管理更绕。

### Gradio Blocks，第三选择

优点是 queue、async generator 和逐次 `yield` 很适合 AI 任务进度。主要问题是复杂可点击流水线通常需要自定义 HTML/Svelte；还需要在安装前对当前固定 Transformers/Hugging Face 依赖栈做 resolver dry-run。

**当前断点：用户尚未正式确认 NiceGUI。** 上一窗口最后提出的问题是：

> 是否确认采用 NiceGUI 浏览器模式，不启用 native window、不建立独立 HTTP API，并在实施前锁定精确版本、离线检查依赖冲突？

新窗口的第一项设计动作应只复述必要取舍，然后再次询问这一件事。不得把“推荐 NiceGUI”写成“用户已批准 NiceGUI”。

## 6. Trace 当前事实

源码事实：

- `TraceRecorder` 在运行中累积内部事件，`RunTrace` 是结束后返回的 immutable 结果。
- `TraceEvent` 已有 `event_id`、`parent_id`、`stage`、`status`、耗时、attributes 和 error category。
- Recorder 要求子 span 的 parent 在创建时仍处于 active 状态，contract 已能表达嵌套。
- 当前 `RagService.ask()` 只记录顶层 `retrieval`、`reranking`、`evidence_gate`、`generation`、`grounding_validation`、repair 和 backend switch。
- dense、sparse 与 RRF 的 score/rank 存在检索结果对象和评测中，但没有作为 RAG trace 子事件公开。
- `AgentService` 调用 `search_evidence()` 时，只记录一个 `agent_action`；内部 RAG 检索链没有并入 Agent trace。
- `RagResult` 不直接暴露全部候选列表。
- 当前没有运行中 event subscription、progress callback 或 async event stream，所以仅换 UI framework 不能自动实现“阶段实时亮灯”。

关键文件：

- `src/enterprise_rag/execution_trace.py`
- `src/enterprise_rag/rag_service.py`
- `src/enterprise_rag/agent_service.py`
- `src/enterprise_rag/retrieval.py`
- `src/enterprise_rag/reranking.py`
- `src/enterprise_rag/models.py`
- `src/enterprise_rag/application.py`

## 7. Framework 确认后的下一个设计决定

确认 framework 后，下一步才比较 trace 扩展策略。至少要提出以下三种方案并说明取舍：

1. **服务级嵌套 trace + UI-neutral progress observer（当前推荐）**：让业务层真实产生 dense/sparse/fusion 子事件，并允许只读观察运行中事件；Agent 的知识 action 包含 RAG 子链。面板只做 projection，不伪造层级。
2. **保留现有 trace + 独立 PanelRunView/debug contract**：改动较小，但会形成 trace 与面板数据两个事实来源。
3. **只展示当前平面 trace**：最省事，但无法满足已确认的分层摘要和双路召回解释目标，应明确不推荐。

设计时必须进一步确认：

- progress observer 是同步 callback、异步 callback 还是 bounded queue；
- 运行中 event 与最终 immutable `RunTrace` 如何保持同一 event ID 和终态；
- 候选文本、分数、query、工具参数的公开字段与长度上限；
- Agent 嵌套 RAG 时的 parent/child 归属；
- observer/UI 失败不得改变业务结果；
- 事件背压、重复订阅、取消、超时、脱敏和性能测试。

## 8. 后续 Brainstorming 顺序

严格一次只问一个问题：

1. 确认或否决 NiceGUI。
2. 比较并确认 trace 扩展策略。
3. 确认运行取消与重复点击语义。
4. 用可视化伴侣展示 2–3 个流水线布局/Inspector 交互方案并确认。
5. 分段呈现架构、组件、数据流、状态与错误、测试和完成标准，每段取得批准。
6. 设计全部批准后，写入 `docs/superpowers/specs/2026-10-11-local-engineering-panel-design.md`。
7. 自检占位内容、矛盾、歧义和范围，并提交纯设计文档。
8. 请用户审阅书面设计。
9. 用户批准后才使用 `writing-plans` 生成实施计划。
10. 实施计划再次批准后才允许安装 UI 依赖、扩展 trace 或创建面板生产代码。

## 9. 安全硬门槛

- 当前只做设计，不安装 NiceGUI/Streamlit/Gradio，不运行 dependency resolver，不修改生产代码。
- 下载、真实 API、密钥、外部数据、推送、发布和部署仍需单独明确批准。
- 不修改 CLI 已验收的业务语义。
- 不把旧验收结果冒充为面板完成后的重新验证。
- 不把 UI 自己拼出的节点称为真实 trace。

## 10. 必读材料

- [本地工程演示面板交接](../panel-handoff.md)
- [已批准整体设计](../plans/2026-10-08-enterprise-rag-agent-demo-design.md)
- [架构与数据流](../architecture.md)
- [Demo 脚本](../demo-script.md)
- [评测说明](../evaluation.md)
- [安全与成本](../security-and-cost.md)
- [面板 Brainstorming 启动提示词](2026-10-11-panel-brainstorming-start-prompt.md)
