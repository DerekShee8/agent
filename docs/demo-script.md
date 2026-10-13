# 面试 Demo 脚本

目标：8–10 分钟内证明这是一个真实、可测试、会拒答的企业知识库 RAG + Agent，而不是写死 happy path。所有公司、员工、制度和报销记录均为虚构。

演示前先读 [README 快速开始](../README.md)；出现问题按 [故障排查](troubleshooting.md) 处理。指标定义见 [evaluation.md](evaluation.md)。

## 0. 演示前检查（不计入正式时长）

1. 关闭高内存浏览器标签、虚拟显示/串流软件和无关后台程序。
2. 保持网络关闭，不设置任何在线 key。
3. 确认模型与索引已经按批准的清单准备。
4. 在仓库根目录执行：

```powershell
$env:RAG_AGENT_ALLOW_NETWORK = "false"
$env:RAG_AGENT_GENERATOR = "local"
$env:PYTHONUTF8 = "1"
rtk uv run rag-agent doctor
```

讲解一句：`doctor` 不加载模型，但会检查 Python、磁盘、RAM、两套 CUDA 可见性、5 个核心模型工件、索引摘要、Provider Profile 和网络状态。`overall_status=warn` 仍返回 0，所以现场看 JSON，而不是只看退出码。

如果 index 缺失：

```powershell
rtk uv run rag-agent kb build data/sample_docs
```

## 1. 8–10 分钟标准版

### 0:00–1:00 开场：先讲风险边界

建议话术：

> 这是一个完整的企业知识库 RAG + Agent CLI。RAG 可以独立运行，Agent 不能绕过它。默认全离线，数据全部虚构；三个业务工具只读。普通用户只能选择本地或已登记的在线 Generator，Retriever、Reranker、阈值和安全验证不开放任意切换。

快速指向 [architecture.md](architecture.md) 的固定主链：hybrid retrieval → Reranker → Evidence Gate → Generator → Grounding Validator。

### 1:00–3:00 第一幕：RAG 独立回答

```powershell
rtk uv run rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --format json --trace
```

要指出的证据：

- `status=answered`；核心结论包含 `P4` 与 `CNY 600`；
- `sources` 只来自本轮允许的知识 chunk，例如 `travel-policy.md::*`；
- trace 依次显示 `retrieval`、`reranking`、`evidence_gate`、`generation`、`grounding_validation`；
- 每个阶段有 `backend_kind` 与 `duration_ms`；
- dense/sparse/RRF 的分路 score/rank 保存在检索对象与 Retriever 评测中。当前 CLI trace 展示顶层业务阶段，不把三路拆成独立子事件。

建议话术：

> 同一个 GTE multilingual encoder 同时产生 dense 语义表示和 sparse 词项权重。dense 负责改写与语义相似，sparse 负责 P4、政策编号、金额等精确词；RRF 确定性融合到 Top 20，再由 cross-encoder 重排到 Top 5。Gate 不通过时根本不会调用 Generator。

不要声称 Evidence Gate 能证明任意复杂问题完整可回答；当前 Gate 是来源完整性加 rerank threshold，生成后还有独立 grounding 检查。

### 3:00–6:00 第二幕：Agent 组合知识与业务事实

```powershell
rtk uv run rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --format json --trace
```

要指出的事实链：

| 证据 | 事实 |
|---|---|
| `obs:get_expense_claim:CLAIM-1024` | 每晚 CNY 680、状态 rejected、拒绝码 `HOTEL_OVER_LIMIT`，关联 EMP-003 / TRIP-2409-017 |
| `obs:get_employee_profile:EMP-003` | 员工职级 P4 |
| `obs:get_business_trip:TRIP-2409-017` | 上海、A 类城市、无 VP exception |
| `travel-policy.md::*` | P4 去 A 类城市每晚上限 CNY 600，超额需要事前 VP preapproval |

正确业务结论：`680 - 600 = CNY 80`，且记录中没有事前 VP 例外审批，因此拒绝与制度一致。

在 JSON 中展示：

- `tool_trace` 的工具名称、精确参数和状态；
- `observations` 的稳定 ID；
- `claims[].support_ids` 同时绑定知识 chunk 与业务 Observation；
- `sources` 只列知识库 chunk，业务事实应在 `observations` / claim support 中查看；
- `agent_plan`、`agent_action`、`agent_observation` 和最终 grounding 事件。

建议话术：

> Agent 不是自由执行器。它最多 6 步，只能搜索知识、读取三类虚构记录、finish 或 refuse。相同工具参数不能重复，非法 action 在 schema/allowlist 边界被拒绝。即使工具数据齐全，最终制度结论仍必须引用 RAG 证据。

### 6:00–7:15 第三幕：没有依据就拒答

```powershell
rtk uv run rag-agent rag ask "制度是否允许员工报销宠物寄养费？" --generator local --format json --trace
```

预期：

```text
status=refused
refusal_reason=insufficient_evidence
answer=null
sources=[]
```

trace 应停在 `evidence_gate=refused`，没有 `generation` 事件。

建议话术：

> 这不是异常，而是安全成功路径，退出码为 0。索引损坏、模型超时或协议错误才是 failed/exit 1。Agent 也不能拿业务工具事实补造一个知识库没有的制度。

### 7:15–9:15 第四幕：用评测证明不是写死

```powershell
rtk uv run rag-agent eval run --layer all
```

现场时间不足时，不等待完整长批次；展示 [evaluation.md](evaluation.md) 的已记录报告并说明口径：

- 24 个 knowledge cases：18 可答、6 不可答；
- 12 个 Agent cases：8 个真实业务任务 + 4 个 scripted failure/control-flow；
- Retriever Recall@20 18/18；Reranker conditional Hit@5 18/18；
- RAG grounded correct 16/18，no-answer 6/6，citations 18/18；
- Agent tool/argument 12/12，final status 与 required support 各 11/12；
- forbidden action 0；A07 长批次有一次如实保留的 `generation_protocol` 波动；
- Agent 与 all 报告因为混入 scripted cases，整体 provenance 必须标为 `fake-backed`，不能包装成纯 real。

### 9:15–10:00 收尾

建议话术：

> 当前交付是 CLI-first 的完整业务核心。下一步的本地工程面板不会重写逻辑，而是同进程调用同一个 Application，并把阶段状态、分数、工具轨迹和耗时放到流水线工作台。在线 Generator 已有 adapter 与 preflight，但在供应商、隐私条款和预算单独获批前不会真实调用。

## 2. 5 分钟压缩版

### 0:00–0:40 架构

一句话：双路 Retriever + Reranker + 生成前 Gate + 生成后一次 repair，Agent 只能组合只读工具且必须引用知识证据。

### 0:40–1:50 RAG

运行第一幕命令，只指出 `CNY 600`、合法 sources 和五个顶层 trace 阶段。

### 1:50–3:20 Agent

运行第二幕命令，展示三个 Observation 和政策 citation，解释 `CNY 80` 超额及无 VP 事前审批。

### 3:20–4:05 拒答

运行宠物寄养费问题，指出 Gate 前置拒答、无 Generator event。

### 4:05–5:00 指标与边界

展示已记录评测表；明确本地离线、全虚构、Agent p95 高于单问预算、在线 API 尚未获批/调用。

## 3. 故障备用方案

现场原则：不隐藏失败，不临时改模型，不开网络，不跳过验证。

### 模型无法加载

1. 展示 `rag-agent doctor` 的失败 check；
2. 解释固定 digest 与本地-only 供应链；
3. 使用 `docs/demo-artifacts/` 中预先验收的 JSON 摘要；
4. 展示默认离线测试或 Retriever/Reranker 单层报告，不伪装成刚才 live run。

### 内存不足

关闭浏览器/虚拟显示，确认 available RAM；只重试一次。仍失败则使用已记录 artifact，并明确说“这是 2026-10-11 目标机验收输出摘要”。

### Agent 生成协议波动

Agent 有边界内的一次 final-protocol retry；仍失败会返回 `generation_protocol`。不要循环重试直到成功。展示已记录 A07 限制和 RAG/工具的独立证据。

### 终端中文乱码

设置：

```powershell
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

然后用 `--format json` 交给 UTF-8-aware viewer。乱码属于展示层，不改业务结果。

## 4. 备用 artifacts 规则

`docs/demo-artifacts/` 只保存：

- 三幕命令的结构化结果摘要；
- 分层评测摘要；
- 虚构 ID、政策文本、公开分数和耗时。

不得包含 API key、真实个人/企业数据、完整 hidden prompt 或 chain-of-thought。Artifact 必须标注捕获日期、命令、backend/provenance，并与实时输出清楚区分。

## 5. 面试追问速答

**为什么不是让 LLM 自己决定有没有证据？** 生成前 Gate 与生成后 Validator 都是业务层程序化控制；模型不能改变阈值或放行自己。

**为什么双路召回？** dense 对语义改写强，sparse 对编号、金额、专有词强；RRF 让两路互补并可重复。

**Agent 为什么不能只查数据库？** 业务记录说明“发生了什么”，制度文档说明“应该怎样”；最终结论必须同时有事实和政策证据。

**为什么只开放 Generator？** Retriever/Reranker 是已验收证据链，任意替换会扩大不可复现组合；Generator 是明确、受 Gate/Validator 包围的可替换边界。

**系统能彻底消除幻觉吗？** 不能。它用 schema、引用集合、quote/fact 检查、support scoring、一次 repair 和 no-answer eval 降低风险，并诚实记录剩余限制。

## 6. 面板版三幕演示

面板版与上面的 CLI 版讲**同一个**核心，只是把数据流可视化了。启动见 [面板使用指南](panel-guide.md)：

```powershell
$env:RAG_AGENT_ALLOW_NETWORK = "false"
rtk uv run --locked --no-sync rag-agent panel
```

面板打开时可能还在初始化——这本身就是可讲的点：**页面先出现，Run 在 READY 之前保持禁用**，每个阶段有独立耗时。

### 第一幕：P4 上海住宿上限（RAG）

1. 点预设卡片 `rag-shanghai-p4`（只填文本，不启动）。
2. 点 **Run**。
3. 依次点开 `retrieval` → `dense` / `sparse` / `rrf_fusion` / `reranking` → `evidence_gate` → `generation` → `grounding_validation`。
4. 预期：状态 **Answered**（文本 + 图标），答案含 `CNY 600`，来源含 `travel-policy.md::0000`；Inspector 里能看到 dense/sparse 的真实排名行与 RRF 融合结果。

CLI 回退：

```powershell
rtk uv run --locked --no-sync rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --trace
```

### 第二幕：EMP-003 / CLAIM-1024（Agent）

1. 点预设卡片 `agent-claim-1024`。
2. 点 **Run**。
3. 点开 `agent_action` → `search_knowledge`，可以看到**完整的嵌套检索子树**（retrieval/dense/sparse/rrf/reranking/evidence_gate），并且嵌套搜索里**没有** `generation`；再点 `agent_observation` 与 `agent_finish`。
4. 预期：状态 **Answered**，答案同时含 `CLAIM-1024`、`P4`、`680`、`600`，以及三条受支持的 Observation。

CLI 回退：

```powershell
rtk uv run --locked --no-sync rag-agent agent run "解释 EMP-003 的 CLAIM-1024 为什么被拒绝" --generator local --trace
```

### 第三幕：宠物寄养费（Evidence Gate 拒答）

1. 点预设卡片 `rag-pet-boarding-refusal`。
2. 点 **Run**。
3. 看流水线在 `evidence_gate` 处停下：**没有** `generation` 节点。
4. 预期：状态 **Refused**（文本 + 图标），`insufficient_evidence`，无答案、无来源。

CLI 回退：

```powershell
rtk uv run --locked --no-sync rag-agent rag ask "制度是否允许员工报销宠物寄养费？" --generator local --trace
```

### 加分点（如果时间允许）

- **Stop 是协作式的**：Run 后立刻点 Stop，状态先变 `Cancelling`，阻塞中的后端返回后才变 **Cancelled**；记录里只有一条 `cancel_boundary`，**没有**伪造的结果或 trace。
- **在线确认**：切到 Online → Run，先弹出确认视图（provider/model/endpoint/隐私条款/请求上限/成本上限/fallback），点 Cancel 会得到一条 **Blocked**（`authorization_cancelled`），且**没有**发出任何请求。
- **历史只读**：点最近五条历史只会切换查看的记录，不会改动记录，也不会影响正在运行的活动 Run。
