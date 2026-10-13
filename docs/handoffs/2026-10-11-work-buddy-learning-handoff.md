# Work Buddy 项目学习交接包

## 1. 用途

这份交接包帮助 Work Buddy 准确读懂并讲解当前企业知识库 RAG + Agent 面试 Demo。目标是带用户按数据流逆向学习现有实现，而不是继续开发、重构或替用户声称已经掌握。

仓库绝对路径：

```text
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo
```

核心实现基线为 Git commit `9e7c77e41a157d72fe95a75300c6f62c23992218`，分支为 `master`。交接文档可能位于该基线之后的纯文档提交中；开始学习时应先只读执行 `git status --short --branch` 和 `git log -5 --oneline`，确认实际状态，不要擅自回退或清理工作区。

## 2. 产品定位与已确认边界

这是一个面向面试演示的、可离线运行的企业知识库 RAG + Agent，不是简化问答玩具：

- CLI 是当前唯一完成并验收的交互入口；未来工程演示面板只有设计交接，尚未实现。
- RAG 可以脱离 Agent 独立运行；Agent 必须复用 RAG 的证据、拒答和失败语义。
- Retriever、Reranker 固定为已验收的本地后端；高级用户只可以选择 Generator 为 `local` 或已注册的 `online` Provider。
- 默认测试离线、确定、无密钥、无网络、无费用；Fake 必须明确标为 `fake-backed`，不能冒充真实后端证据。
- 数据均为虚构 Demo 数据。不得把真实个人或企业文档发给外部服务。
- 未经用户明确批准，不下载模型、不调用真实 API、不安装依赖、不推送、不发布、不部署，也不修改代码。

## 3. 当前完成度

### 已实现并验证

- 文档摄取、确定性切块、稳定 chunk ID、原子化索引及摘要校验。
- `Alibaba-NLP/gte-multilingual-base` 同时提供 dense 语义召回与 sparse 词项召回，两路独立排名后以 deterministic RRF 融合。
- `retrieve_k=20`，`rrf_k=60`，固定 `Alibaba-NLP/gte-multilingual-reranker-base` 重排到 `rerank_k=5`。
- 程序化 Evidence Gate、结构化 Generator、程序化 Grounding Validator 与最多一次 repair。
- 本地 `Qwen3-4B-Q5_K_M.gguf` Generator；OpenAI-compatible 在线 Generator 适配和 Provider Profile 约束。
- 最多 6 步的 Plan–Act–Observe Agent，以及 3 个只读业务工具。
- `answered`、`refused`、`failed` 三态结果、稳定错误类别和结构化 execution trace。
- Retriever、Reranker、RAG、Agent 分层评测和默认离线测试。

最近记录的验收证据：默认测试 `547 passed, 5 deselected`；真实 Retriever Recall@20 为 `18/18`，真实 Reranker conditional Hit@5 为 `18/18`；RAG grounded answer 为 `16/18`、无答案识别 `6/6`、citation validity `18/18`；正式三幕演示 cold start `11.101s`、RAG p95 `17.323s`、Agent p95 `30.134s`、拒答 p95 `0.853s`。这些是已有记录，不应替代当前环境的重新验证。

### 尚未完成或必须如实说明

- 工程演示面板尚未实现，当前只有 [面板交接](../panel-handoff.md)。
- 在线 Provider 机制已实现，但没有进行真实付费 API 验收；不能称为“已验证 DeepSeek V4.1 Flash”或任何具体中转站已通过。
- Agent 12-case 长批次 p95 曾为 `76.126s`；正式三幕演示 p95 与长批次 p95 必须分开报告。
- Agent/all 评测包含 4 个 scripted control-flow cases，因此整体 provenance 为 `fake-backed`；A01–A08 才是真实本地链路用例。
- A07 在长批次中出现过一次 `generation_protocol` 波动。
- Grounding Validator 是工程化约束，不是“数学上保证零幻觉”。
- trace 数据结构支持 parent ID，但当前生产链路主要输出顶层阶段；Agent 尚未把内部 RAG 完整嵌套成子树。
- PyTorch 当前是 CPU build；本地 Generator 的 llama.cpp 可使用 GPU offload。WDDM 环境下没有稳定的逐进程 VRAM 证据。

## 4. 核心数据流

### 独立 RAG

```text
Query
  ├─ dense semantic retrieval
  └─ sparse lexical retrieval
            ↓
  deterministic RRF fusion
            ↓ retrieve_k = 20
       GTE Reranker
            ↓ rerank_k = 5
       Evidence Gate
       ├─ 无合格证据 → refused: insufficient_evidence
       └─ 有合格证据 → structured Generator
                              ↓
                     Grounding Validator
                     ├─ 通过 → answered
                     ├─ 不通过 → 最多一次 repair
                     └─ 再失败 → refused: grounding_validation_failed
```

Evidence Gate 在生成前检查来源、身份、文本完整性和 `rerank_score >= 0.20`。Grounding Validator 在生成后检查引用 ID、原文 quote、答案 claim、金额/日期/业务 ID 等关键事实以及 GTE support score（门槛 `0.40`）。因此 Generator 不独自决定可信与否，闭环由程序控制。

### Agent

```text
Task
  → Plan
  → Action
  → Observation
  → 重复（最多 6 步）
  → finish 或 refuse
  → 最终 Grounding Validator
```

允许动作只有：

- `search_knowledge`
- `get_employee_profile`
- `get_expense_claim`
- `get_business_trip`
- `finish`
- `refuse`

三个业务查询工具读取虚构、只读、精确 schema 的 JSON 记录。重复动作会产生 `duplicate_action`；超过步数会产生 `iteration_limit`。最终 Agent 答案至少引用一条知识证据，使用业务观察时还必须引用相应的 `obs:<tool>:<id>`。

## 5. 代码地图

建议从用户可见结果向内逆向阅读：

| 层次 | 主要文件 | 学习重点 |
|---|---|---|
| CLI | `src/enterprise_rag/cli.py`, `cli_rendering.py` | 参数、退出码、text/json/trace 展示；CLI 不承载业务判断 |
| 组合根 | `src/enterprise_rag/application.py`, `config.py` | 后端装配、固定参数、local/online Generator 选择 |
| RAG 核心 | `rag_service.py` | retrieve → rerank → gate → generate → validate/repair 的控制流 |
| 检索与索引 | `ingestion.py`, `index_store.py`, `retrieval.py` | 稳定 chunk、dense/sparse、RRF、模型/索引一致性 |
| 重排 | `reranking.py` | Query–chunk 精排与稳定 rank |
| 生成与证据 | `generation.py`, `grounding.py` | strict schema、Evidence Gate、Grounding Validator、repair |
| Agent | `agent_service.py`, `agent_tools.py` | Plan–Act–Observe、工具白名单、观察引用、循环保护 |
| 契约与失败 | `models.py`, `ports.py`, `errors.py`, `execution_trace.py` | immutable data model、端口、三态结果、错误映射、trace |
| 评测 | `evaluation.py`, `data/eval/`, `tests/` | 分层指标、真实/Fake provenance、failure cases |

## 6. 安全启动与 CLI

第一次接手应先阅读，不要安装或下载。当前机器的环境已经建立时，PowerShell 可使用：

```powershell
Set-Location -LiteralPath "E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo"
$env:PYTHONUTF8 = "1"
$env:RAG_AGENT_ALLOW_NETWORK = "false"
$env:RAG_AGENT_GENERATOR = "local"
rtk uv run --no-sync rag-agent doctor
```

本机曾观察到 `rtk uv run rag-agent doctor` 停在 uv 自动同步阶段，同时连接本地代理 `127.0.0.1:7897`；`--no-sync` 在既有环境中约 8 秒完成。它说明当时是同步/代理路径停滞，并不证明所有机器都会复现。若环境尚未安装，不能盲目使用 `--no-sync` 掩盖缺失依赖，应先报告现状并取得用户许可再安装。

CLI 契约：

```text
rag-agent kb build PATH
rag-agent rag ask QUESTION --generator local|online [--provider NAME] [--fallback-local] [--format text|json] [--trace]
rag-agent agent run TASK --generator local|online [--provider NAME] [--fallback-local] [--format text|json] [--trace]
rag-agent eval run --layer retriever|reranker|rag|agent|all
rag-agent doctor
```

`refused` 是安全、正常的业务结果，退出码为 `0`；`failed` 表示执行失败，退出码为 `1`；CLI 参数错误为 `2`。

## 7. 三幕 Demo

先运行 `doctor`，再逐个执行并观察答案、引用和 trace：

```powershell
rtk uv run --no-sync rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --trace
rtk uv run --no-sync rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --trace
rtk uv run --no-sync rag-agent rag ask "制度是否允许员工报销宠物寄养费？" --generator local --trace
```

预期故事：

1. RAG 从政策证据回答上海 P4 住宿上限为 CNY 600。
2. Agent 组合员工、报销单、出差记录和政策证据，解释 CLAIM-1024 的 680 高于 600，且没有 VP exception。
3. 知识库无宠物寄养费依据，Evidence Gate 触发 `insufficient_evidence`，Generator 不应自由编造。

若实际输出不同，应保留原始命令、状态、错误类别和 trace，再定位原因；不要先改代码。

## 8. 推荐学习路线

每次只学习一个闭环，并由用户亲自复述或操作；AI 写出的解释不能自动算成用户掌握证据。

1. 运行三幕 Demo：先建立“系统能做什么、怎样拒答”的整体感。
2. 逆向跟踪一次 RAG：CLI → Application → RagService → 各后端。
3. 单独理解索引、稳定 chunk ID、dense/sparse 双路召回与 RRF。
4. 理解 Reranker、Evidence Gate 和生成后 Grounding 的职责边界。
5. 跟踪一次 Agent：plan → tool → observation → policy evidence → finish。
6. 学习结果模型、错误类别、退出码和结构化 trace。
7. 阅读分层评测，练习区分 real、fake-backed、已实现、已验收和仅规划。
8. 最后让用户独立画数据流、解释关键失败路径并现场运行 Demo。

每一节建议采用：先给一个具体问题 → 让用户预测 → 打开相关函数 → 跑最小命令或测试 → 用户用自己的话复述 → 指出仍不清楚的点。

## 9. Work Buddy 的首轮交付要求

完整读完本交接包和仓库关键文档后，先给用户：

1. 一段不超过 200 字的项目定位。
2. 一张 RAG + Agent 数据流图。
3. 一份按层次组织的模块地图。
4. “已实现并验证 / 已实现未真实验收 / 仅规划”的状态表。
5. 一条按数据流逆向的学习路径。

然后只问一个问题：先从“运行三幕 Demo”开始，还是从“CLI 入口逆向读一次 RAG”开始。不要在首轮修改文件。

## 10. 必读文档

- [README](../../README.md)
- [架构与数据流](../architecture.md)
- [Demo 脚本](../demo-script.md)
- [评测说明](../evaluation.md)
- [安全与成本](../security-and-cost.md)
- [模型供应链清单](../model-manifest.md)
- [故障排查](../troubleshooting.md)
- [面板交接](../panel-handoff.md)
- [已批准设计](../plans/2026-10-08-enterprise-rag-agent-demo-design.md)
- [Work Buddy 启动提示词](2026-10-11-work-buddy-start-prompt.md)
