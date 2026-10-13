# Enterprise RAG Agent Demo

这是一个面向面试展示的、可离线运行的企业知识库 RAG + Agent 工程。它不是把答案写死在脚本里的玩具：真实主链使用 multilingual dense/sparse hybrid retrieval、Reranker、Evidence Gate、本地结构化 Generator 和生成后 Grounding Validator；Agent 再在这条安全链之上组合三个只读业务工具。

项目默认只使用仓库内的虚构差旅与报销数据，不需要网络、API key 或付费服务。现在有两个入口：CLI 和**只绑定 `127.0.0.1` 的本地工程面板**，两者复用同一个 Application/RagService/AgentService 核心，面板不会改变任何业务结论。

## 能做什么

- 独立 RAG：回答制度问题，保留来源、分数与稳定 chunk 身份；证据不足时程序化拒答。
- 受约束 Agent：查询虚构员工、报销单和行程，结合知识库解释业务结果。
- Grounding 闭环：生成前由 Evidence Gate 决定能否生成；生成后校验每个 claim 的证据，最多 repair 一次。
- 可审计运行：`--trace` 输出阶段、状态、耗时、后端类型和稳定错误类别。
- 分层评测：Retriever、Reranker、端到端 RAG 和 Agent 分开计分，并标明 real / fake-backed provenance。
- 可选 Generator：普通用户只选择 `local` 或登记过的 `online`；Retriever 和 Reranker 不开放任意替换。

系统不会修改报销单、员工或行程，也不提供登录、多租户、云部署、托管向量库或任意工具执行。

## 5 分钟离线启动

以下流程适用于已经完成模型审批、且经批准的模型文件已放入 `.local/model-cache` 的目标机器。首次创建 Python 环境或准备模型不计入 5 分钟。

### 1. 准备环境

要求 Windows、Python 3.12、[uv](https://docs.astral.sh/uv/)；目标机为 RTX 4060 Laptop 8 GB VRAM、16 GB RAM。

```powershell
rtk uv sync --group dev --extra retrieval --extra local-generator
rtk uv run python --version
```

要在本机使用**本地工程面板**，请用这条**实测通过**的离线命令（约 1 秒；只会重装本仓库自身，不会重新获取任何 wheel）：

```powershell
rtk uv sync --locked --offline --no-python-downloads --extra retrieval --extra local-generator --extra online-generator --extra panel
rtk uv lock --check --offline --no-python-downloads
```

**不要**裸跑 `uv sync --locked`：本机的 `llama-cpp-python` 是指向 GitHub release 的 487 MiB wheel，一旦需要重新获取会以约 40 KB/s 卡数小时。`--offline` 让它只用本地缓存，因此不会触发下载。详见 [面板使用指南](docs/panel-guide.md)。

模型不会在运行时自动下载。首次准备模型前，必须先审阅 [模型供应链清单](docs/model-manifest.md)，确认精确 repository、revision、SHA-256、许可证、大小和缓存位置，并取得明确批准。`RAG_AGENT_ALLOW_MODEL_DOWNLOAD=true` 也不会触发自动下载。

### 2. 构建可重建索引

```powershell
rtk uv run rag-agent kb build data/sample_docs
```

### 3. 运行环境体检

```powershell
rtk uv run rag-agent doctor
```

读取 JSON 中的 `data.overall_status`：`pass` 才是全绿，`warn` 会返回退出码 0，`fail` 返回 1。内存不足 4 GiB、PyTorch CUDA 不可见或尚未配置在线 Profile 都可能产生 warning；离线 Demo 不要求在线 Profile。

### 4. 运行 RAG 和 Agent

```powershell
rtk uv run rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --trace
rtk uv run rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --trace
rtk uv run rag-agent rag ask "制度是否允许员工报销宠物寄养费？" --generator local --trace
```

预期分别看到：有依据的 `CNY 600` 结论；结合员工、报销单、行程和制度的解释；以及 `insufficient_evidence` 正常拒答。

### 5. 启动本地工程面板（可选）

```powershell
$env:PYTHONUTF8 = "1"
$env:RAG_AGENT_ALLOW_NETWORK = "false"
$env:RAG_AGENT_GENERATOR = "local"
rtk uv run --locked --no-sync rag-agent panel
rtk uv run --locked --no-sync rag-agent panel --port 8081 --no-open
```

面板只绑定 `127.0.0.1`（默认端口 8080），**先显示页面**再在后台加载模型；Run 在初始化 READY 之前保持禁用。它提供三个预设 + Custom、RAG/Agent、Local/Online（带一次性确认）、真实的层级流水线画布 + Inspector、Stop 协作式取消、五个终态和最近五条内存历史。端口被占用会直接失败并提示 `--port`，不会静默换端口。完整说明见 [面板使用指南](docs/panel-guide.md)。

## 常用命令

```text
rag-agent kb build PATH
rag-agent rag ask QUESTION --generator local|online [--provider NAME]
    [--fallback-local] [--format text|json] [--trace]
rag-agent agent run TASK --generator local|online [--provider NAME]
    [--fallback-local] [--format text|json] [--trace]
rag-agent eval run --layer retriever|reranker|rag|agent|all
rag-agent doctor
rag-agent panel [--port 8080] [--no-open]
```

- `panel` 是唯一会加载 NiceGUI 的命令；其他命令不要求安装 panel extra。
- `--generator` 对 `rag ask` 和 `agent run` 是必填项。
- `--provider` 与 `--fallback-local` 只允许搭配 `--generator online`。
- `answered`、`refused`、`failed` 是不同业务状态。证据不足的 `refused` 是安全成功路径，退出码为 0；运行故障 `failed` 才返回 1。
- 配置只从当前进程环境变量读取，不自动加载 `.env`。默认值见 [.env.example](.env.example)。
- 在线模式默认禁用；真实调用前必须完成 [安全与成本审批](docs/security-and-cost.md)。计划授权的那一次真实在线验收**已尝试三次，全部失败**（中转站数据面故障），**未通过、未验证**；详见 [docs/evaluation.md](docs/evaluation.md) 的在线小节。除此之外本仓库没有其它真实在线调用。

## 验证

默认测试完全离线、确定、无密钥、无网络、无费用：

```powershell
rtk uv run pytest
rtk uv run ruff check .
rtk uv run ty check src
```

真实模型测试必须显式选择：

```powershell
rtk uv run pytest tests/live -m live -v
rtk uv run rag-agent eval run --layer all
```

评测集与当前实测结果见 [评测说明](docs/evaluation.md)。完整面试流程见 [Demo 脚本](docs/demo-script.md)。

## 文档导航

- [架构与数据流](docs/architecture.md)：固定主链、Agent、契约、trace 和失败语义。
- [本地工程面板使用指南](docs/panel-guide.md)：安装与启动、初始化阶段、预设、Local/Online 确认、Inspector、Stop 语义、五个终态与 CLI 回退。
- [Demo 脚本](docs/demo-script.md)：8–10 分钟、5 分钟压缩版和离线备用方案。
- [评测说明](docs/evaluation.md)：36 个用例、指标、provenance 和实测限制。
- [安全与成本](docs/security-and-cost.md)：密钥、虚构数据、Provider Profile 和调用前审批。
- [模型供应链清单](docs/model-manifest.md)：精确 revision、digest、许可证和缓存位置。
- [故障排查](docs/troubleshooting.md)：Windows CUDA、内存、模型、索引、拒答与错误。
- [面板交接](docs/panel-handoff.md)：CLI 验收矩阵、未来流水线工作台与待选 framework。
- [Work Buddy 学习交接包](docs/handoffs/2026-10-11-work-buddy-learning-handoff.md)：项目事实、代码地图、学习路线与安全启动方式。
- [Work Buddy 启动提示词](docs/handoffs/2026-10-11-work-buddy-start-prompt.md)：复制到新对话即可开始只读导学。
- [面板 Brainstorming 交接包](docs/handoffs/2026-10-11-panel-brainstorming-handoff.md)：已确认决定、framework 断点、trace 事实与后续顺序。
- [面板 Brainstorming 启动提示词](docs/handoffs/2026-10-11-panel-brainstorming-start-prompt.md)：复制到同项目新窗口继续面板设计。
- [设计说明](docs/plans/2026-10-08-enterprise-rag-agent-demo-design.md)：已批准的产品与架构决定。

## 当前边界

- 本地 Qwen3-4B 适合展示结构化控制流，但生成质量仍可能出现冗余；最终答案始终受程序化 evidence/grounding 控制。
- 多步 Agent 的延迟显著高于单次 RAG。当前性能报告不会把 Agent 延迟伪装成“标准单问”预算。
- Provider Profile 的请求数和预算在单次进程内累计，不是跨进程、账户级持久配额。
- trace 现在输出**真实的层级结构**：`retrieval` 下挂着真实的 `dense` / `sparse` / `rrf_fusion` 子事件（含 top_k 排名行与 300 字符文本预览），Agent 的 `search_knowledge` 下挂着完整的检索/重排/Gate 子树。每个事件都带 `run_id`，`RunTrace` 会校验事件与 trace 的 run id 一致。
- 面板历史只在内存里、最多 5 条，进程退出即消失；它不是持久化审计日志。

遇到问题先运行 `rag-agent doctor`，再查 [故障排查](docs/troubleshooting.md)。
