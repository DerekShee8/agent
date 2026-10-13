# Enterprise RAG Agent Demo — 设计说明

> 状态：已由用户批准，可进入实施计划审阅阶段
>
> 日期：2026-10-08
>
> 项目名：`enterprise-rag-agent-demo`
>
> Python 包：`enterprise_rag`
>
> CLI：`rag-agent`
>
> 当前阶段：仅完成设计；尚未创建、迁移或运行生产代码，尚未安装依赖、下载模型或调用真实 API。

## 1. 项目目标

本项目是一个独立的面试 Demo 工程，最终交付完整的企业知识库 RAG + Agent，而不是功能缩水的玩具。它以差旅报销为业务场景，用虚构的企业制度、员工、报销单和出差信息展示以下能力：

- 企业知识库构建、双路召回、重排、证据判定、带引用回答和安全拒答；
- Agent 在受限工具集内查询业务事实，并将事实与制度证据组合成可审计结论；
- 本地离线完整运行，也可显式选择已登记、已审计的在线 Generator；
- CLI 先行，后续提供复用同一业务核心的本地工程演示面板；
- 分层事件轨迹、失败语义、离线评测和真实后端验收。

面试可能在数日内到来，因此交付顺序强调“先获得可演示且可信的完整闭环，再扩展展示体验”。

## 2. 仓库边界与来源锚点

新项目位于独立兄弟目录，并建立独立 Git 仓库。它不是原项目的子目录，也不通过 worktree、submodule、symlink、`PYTHONPATH` 或指向旧仓库的 editable dependency 运行。

来源仓库只读：

```text
E:\onedrive文件\OneDrive\文档\agent文件\agent项目demo
```

唯一允许的一次性选择性导入锚点：

```text
branch: codex/stage2-task4-contracts
commit: f09229248a354d652ac04ef249be0abb1549fbcd
```

不得从来源仓库当前工作区复制文件。迁移清单必须记录每个导入文件的来源提交、保留原因以及在新仓库中的目标路径。导入后必须在新项目重新执行测试；来源仓库的历史测试结果不构成新项目证据。

## 3. 范围与明确不做的事项

### 3.1 本阶段范围

- 本地虚构差旅报销知识库；
- 可重建的本地索引；
- 独立可运行的 RAG；
- 有界 Agent 及三个只读业务工具；
- 本地 Generator 与在线 Generator 选择；
- 默认离线、确定、无密钥、无网络、无费用的测试；
- Retriever、Reranker、RAG、Agent 分层评测；
- CLI、演示脚本、架构/安全/模型/故障文档；
- CLI 完成后再实现本地工程演示面板。

### 3.2 不在范围内

- 登录、多租户、权限后台；
- 云部署、生产运维后台；
- 托管向量数据库；
- 对真实企业系统或个人数据的访问；
- 写入、审批、付款、删除等副作用工具；
- 任意 URL、任意 shell、任意 SQL 或其他不受约束工具；
- 为网页预先建设独立 HTTP API。

## 4. 用户可见功能

### 4.1 知识库 RAG

用户可以导入 Demo 文档、构建索引并直接提问。系统返回：

- 有制度依据的中文回答；
- 稳定、可验证的来源引用；
- 检索、重排和证据门状态；
- 证据不足时的程序化拒答；
- 阶段失败时清晰、稳定的错误类别。

RAG 不依赖 Agent，可以独立构建、查询和评测。

### 4.2 差旅报销 Agent

Agent 可处理例如：

- “解释员工 EMP-003 的报销单 CLAIM-1024 为什么被拒绝”；
- “这笔住宿费是否超过该员工级别对应的标准”；
- “报销单、出差审批和制度条款之间哪里不一致”；
- “缺少哪些事实或证据，所以当前无法下结论”。

Agent 只做查询、解释和拒答，不执行任何真实业务变更。

### 4.3 Generator 选择

普通用户只选择 Generator：

- `local`：默认，本地离线；
- `online`：仅在已配置、已登记且通过调用前检查时启用；
- `fallback-local`：只在用户明确要求时，允许在线失败后切回本地。

Retriever 与 Reranker 固定为项目验收后端，不在普通 CLI 中暴露任意切换，避免把关键安全链变成无法复现的组合矩阵。

## 5. 总体架构

```text
CLI / Future Local Engineering Panel
                 │
                 ▼
          Application Services
      ┌──────────────┬──────────────┐
      │  RagService  │ AgentService │
      └──────┬───────┴──────┬───────┘
             │              │
             │       bounded Plan–Act–Observe
             │              │
             │      ┌───────┴────────────────────┐
             │      │ search_knowledge + fact tools│
             │      └─────────────────────────────┘
             ▼
 Query → Hybrid Retriever → Reranker → Evidence Gate
                                           │
                         ┌─────────────────┴─────────────────┐
                         │ sufficient                         │ insufficient
                         ▼                                    ▼
                    Generator                    Programmatic Refusal
                         │
                         ▼
                 Grounding Validator
                    │             │
                 valid       invalid
                    │      one repair at most
                    ▼             │
                  Answer      validate again
                                  │
                         invalid → Programmatic Refusal
```

CLI 与未来面板只能负责收集参数、调用服务和展示结果。阈值、来源筛选、回答/拒答、工具选择约束和失败语义均由业务服务或领域策略负责。

## 6. 知识库与索引

Demo 数据全部虚构并随仓库提供。制度文档使用易审计的 UTF-8 文本格式作为规范样本；加载器与切分器通过接口隔离，以便后续加入 PDF 等格式而不改变检索和回答契约。

构建索引时，每个 chunk 至少保存：

- `chunk_id`：内容与来源稳定时可重复生成；
- `document_id`、标题、章节、可显示来源；
- 原始文本和规范化文本；
- 文档版本或内容摘要；
- dense vector 与 sparse lexical representation；
- 构建所用模型、revision/digest 和配置摘要。

索引必须可由原始 Demo 数据重建；缓存和索引产物不作为不可追溯的手工资产。

## 7. Retriever：语义与词项双路召回

固定真实 Retriever 为 `Alibaba-NLP/gte-multilingual-base`。同一模型产生：

- dense 语义表示：召回表达不同但含义相近的片段；
- sparse 词项表示：召回编号、金额、制度名、专有词和精确措辞匹配的片段。

执行流程：

```text
Query
  ├─ dense semantic retrieval
  └─ sparse lexical retrieval
           ↓
  candidate union + deterministic RRF
           ↓
        retrieve_k = 20
```

融合使用固定、可记录的 RRF 配置，并以稳定 `chunk_id` 作为最终同分排序键，保证离线评测可重复。结果不得覆盖原始分路信息，必须保留：

- `dense_score` / `dense_rank`；
- `sparse_score` / `sparse_rank`；
- `fusion_score` / `fusion_rank`；
- 作为 Retriever 最终输出的 `retrieval_score` / `retrieval_rank`（对应融合结果）；
- 稳定 chunk 身份与来源。

始终满足 `retrieve_k >= rerank_k > 0`。

## 8. Reranker

固定真实 Reranker 为 `Alibaba-NLP/gte-multilingual-reranker-base`。它对 Query 与候选 chunk 做 cross-encoder 相关性判断，初始参数：

- 输入：融合后的 Top 20；
- 输出：`rerank_k = 5`；
- Query + chunk 最大长度：初始 512 tokens，最终由本机基准和覆盖率验证；
- 保存 `rerank_score`、`rerank_rank` 以及被截断信息。

Reranker 失败不可跳过并直接生成，因为这样会改变已声明的证据安全链。失败返回稳定错误，不伪装成“没有答案”。

## 9. Evidence Gate 与 Grounding 闭环

Generator 不自行决定是否拥有足够证据。闭环由两个独立控制点组成。

### 9.1 生成前：Evidence Gate

Evidence Gate 依据结构化候选判断是否允许生成，至少检查：

- 是否存在合格且来源允许的证据；
- Top-k 相关性是否达到经评测校准的门槛；
- 必要的来源、chunk 身份和文本是否完整；
- 当前问题是否需要却缺失业务事实。

不通过时不调用 Generator，直接返回带原因码的程序化拒答。门槛由 `RagService` 管理，不属于 CLI 参数。

### 9.2 生成后：Grounding Validator

Generator 必须输出符合 JSON Schema 的结构化候选答案，包括结论、引用 claim 和引用 ID。Validator 进行：

- Schema 与引用 ID 存在性检查；
- 每个关键结论是否绑定知识证据或工具 Observation；
- 引用来源是否属于本次允许的证据集合；
- 是否出现无依据新增数字、规则、身份或业务事实；
- 回答状态是否与上游拒答/失败语义一致。

第一次验证失败时，系统只允许一次受约束的 repair generation，并将具体校验错误和原证据重新提供给 Generator。第二次仍失败则程序化拒答。系统不进行无限重试，也不会把失败候选文本作为答案交给用户。

这是一套可测试的风险控制机制，不声称能数学上消除所有幻觉；其效果必须通过 no-answer、错误引用和矛盾证据用例验证。

## 10. Generator 后端

### 10.1 本地默认

- 模型族：`Qwen3-4B-GGUF`；
- 量化目标：`Q5_K_M`；
- 默认 non-thinking 模式；
- 实际上下文目标约 8K，最终按本机内存与延迟基准设定；
- 必须支持受约束的 JSON Schema 输出；
- 若验收质量不足，`Qwen3-8B` 的适配量化仅作为显式 A/B 候选，不自动扩大下载。

### 10.2 在线可选

目标为通过用户选定中转站调用其标称的 DeepSeek V4.1 Flash 兼容接口。因为供应商尚未确定，当前只定义 Provider Profile，不假定它等价于官方服务：

```text
provider_name
base_url
model_id
api_key_env
privacy_terms
pricing
request_limit
budget_limit
synthetic_data_only
```

只有预先登记、审阅过的 Profile 可以被 CLI 选择；CLI 不接受临时任意 `base_url`。密钥只从 Profile 指定的环境变量读取，不写日志、不写 trace、不写 Git。

任何真实调用前，必须单独向用户报告供应商、模型标识、端点、数据条款、价格/额度、预计调用次数、预计成本与硬上限，并取得明确确认。

## 11. Agent 设计

Agent 使用有界 Plan–Act–Observe 循环，最多 6 个 action。允许的 action 只有：

- `search_knowledge`；
- `get_employee_profile`；
- `get_expense_claim`；
- `get_business_trip`；
- `finish`；
- `refuse`。

每个 action 必须通过 Schema 校验和 allowlist 检查；相同工具与相同参数的重复调用被阻止；达到步数上限后程序化终止。

Agent 不能改写或隐藏 RAG 的语义：

- 制度结论必须来自成功的 RAG 结果；
- RAG 拒答时，Agent 不能仅凭业务工具事实推断制度；
- 工具失败时不得猜测返回值；
- 最终答案必须保留知识引用、工具 Observation 和验证状态；
- 最终合成同样经过 Grounding Validator，最多一次 repair。

### 11.1 三个只读事实工具

`get_employee_profile(employee_id)` 返回虚构员工的部门、职级和差旅权益等事实。

`get_expense_claim(claim_id)` 返回虚构报销单的明细、金额、状态、票据标记和拒绝码等事实。

`get_business_trip(trip_id)` 返回虚构出差记录的目的地、日期、审批状态和预算等事实。

工具只返回业务事实，不内置政策结论。每次成功调用产生稳定 `observation_id`，供最终答案引用。不存在、参数非法、数据损坏和工具异常使用不同错误码。

## 12. CLI

首批命令：

```text
rag-agent kb build ./data/sample_docs
rag-agent rag ask "问题" --generator local|online
rag-agent agent run "任务" --generator local|online
rag-agent eval run --layer retriever|reranker|rag|agent|all
rag-agent doctor
```

共同展示选项：

- 默认：适合现场演示的简洁答案、来源和最终状态；
- `--trace`：展示分层事件树；
- `--format json`：输出稳定机器可读契约。

若启用在线后端或显式回退，使用受限配置项选择已登记 Profile，不在命令行接收密钥。CLI 不暴露 Evidence Gate 阈值、来源过滤或“跳过验证”等危险参数。

## 13. 分层事件树

所有模式复用同一套 trace contract：

```text
Run
├─ retrieval
│  ├─ dense
│  ├─ sparse
│  └─ fusion
├─ reranking
├─ evidence_gate
├─ generation
│  ├─ attempt_1
│  ├─ grounding_validation
│  └─ repair_attempt? / final_validation?
└─ agent
   ├─ action
   ├─ tool_call
   ├─ observation
   └─ final_answer
```

事件包含 `event_id`、父子关系、阶段、状态、开始/结束时间、耗时、后端与 revision、允许公开的输入摘要、输出摘要、chunk/observation 引用以及稳定错误码。状态集合至少包括：

```text
started | succeeded | refused | failed | skipped | degraded
```

Trace 明确不记录：API key、完整系统提示词、隐藏 chain-of-thought、未脱敏个人数据。Agent 只展示可审计 action、工具参数、Observation 和判定理由码。

## 14. 错误与受控降级

采用按阶段受控降级，原则是任何降级都不能绕过证据安全链。

| 情况 | 行为 |
|---|---|
| dense、sparse、融合、Retriever 整体或 Reranker 任一失败 | 终止，不进入生成；固定双路主链不退化为未经验收的单路检索。 |
| Evidence Gate 不通过 | 正常拒答，不视为系统故障。 |
| 本地 Generator 失败 | 终止并返回生成错误。 |
| 在线 Generator 失败 | 默认终止；只有用户显式启用时才回退本地，并记录后端切换。 |
| Grounding Validator 首次不通过 | 最多一次 repair；再次失败则程序化拒答。 |
| 工具记录不存在 | 返回结构化 not-found Observation，由 Agent 解释缺失信息。 |
| 工具异常或返回损坏 | 禁止猜测；若结论依赖该事实则拒答。 |
| 重复 action 或超过 6 步 | 程序化终止，返回对应 reason code。 |
| 未登记端点、敏感数据、缺失成本信息或超过预算 | 在网络请求前由 preflight 阻止。 |

受控降级的每一种允许路径都必须有单独测试，不能由宽泛的 `except` 隐式触发。

## 15. 安全、隐私和供应链

- 密钥只存环境变量或本机密钥管理，不进入聊天、代码、配置样例、日志、trace 或 Git；
- 真实个人或企业文档不发送给外部服务，所有 Demo 和在线调用只使用虚构数据；
- 在线调用有请求数和成本硬上限，并在发出请求前完成 preflight；
- 模型加载默认离线优先，禁止运行时静默下载；
- `gte-multilingual-base` 与 `gte-multilingual-reranker-base` 涉及 remote code，下载前必须固定模型 revision 与代码 revision，审阅代码并记录摘要；
- 每个模型在 `docs/model-manifest.md` 记录来源、许可证、精确文件、revision/digest、磁盘占用和缓存路径；
- 下载任何模型前，必须把上述信息报告给用户并获得明确确认；
- Fake 只服务于离线单元/契约测试，输出必须明确标记，不能冒充真实后端验收证据。

未经明确确认，不发布、部署、推送、发送外部消息或执行破坏性操作。

## 16. 硬件与性能预算

目标机器：

- Windows 11 Pro；
- Intel Core i9-13900HX，24 核 32 线程；
- NVIDIA RTX 4060 Laptop GPU，8 GB VRAM；
- 16 GB DDR5 RAM；
- 当前 E 盘约 48 GB 可用，运行演示前需关闭高占用后台程序。

采用“均衡”预算：

- 模型文件目标总量不超过 12 GB，硬上限 15 GB；
- 为 Python 环境、索引、缓存和测试数据预留约 25–30 GB 总空间；
- 冷启动目标不超过 60 秒；
- 标准查询目标不超过 45 秒；
- 完成标准使用标准查询集的 p95，不以单次最佳结果代替。

Retriever、Reranker 和 Generator 需要明确的加载/卸载与 GPU/CPU 放置策略，避免 8 GB VRAM 与 16 GB RAM 同时峰值导致演示失败。实际策略由本机基准确定，不在设计阶段宣称未经测量的性能。

## 17. 测试与分层评测

### 17.1 默认测试

- 无网络、无密钥、无费用；
- 使用 Fake 验证契约、控制流、拒答、错误码和 trace；
- 固定种子、固定语料、稳定排序和可重复断言；
- 网络、GPU 和真实模型测试单独标记，不进入默认快速测试。

### 17.2 评测集

约 36 个平衡用例：

- 24 个知识用例：直接问法、改写、中文夹英文术语、精确编号、干扰片段、不可回答；
- 12 个 Agent 用例：零工具、单工具、多工具、错误 ID、工具失败、重复调用和步数上限。

其中核心验收子集：18 个可回答知识问题和 6 个不可回答问题。

### 17.3 完成指标

- Retriever Recall@20：18 个可回答问题中至少 17 个命中；
- Reranker：在已召回相关 chunk 的条件下，Hit@5 不低于 90%；
- 端到端 RAG：至少 16/18 个回答正确且有依据；
- no-answer：6/6 全部拒答；
- 输出引用 ID：100% 属于本次允许证据；
- Agent：至少 11/12 个任务选择正确工具与参数；
- 未授权调用、写操作、无限循环、密钥泄漏：0；
- 预设失败类别映射正确率：100%；
- 默认离线流程完整运行；
- 模型文件、冷启动和 p95 回答时间满足硬件预算；
- 三个正式 Demo 场景各连续运行 3 次，语义结论一致且引用有效。

在线 live eval 必须显式开启、使用虚构数据、经过成本确认，并与离线结果分开报告。

## 18. 面试 Demo 故事

标准演示控制在 8–10 分钟，并准备 5 分钟压缩版和故障备用脚本。

### 第一幕：RAG 独立工作

从一个制度问题开始，展示 dense 与 sparse 如何互补、RRF 如何融合、Reranker 如何改变 Top 5、Evidence Gate 如何放行，以及答案如何绑定具体来源。

### 第二幕：Agent 组合知识与业务事实

询问某员工某张报销单被拒原因。展示 Agent 调用员工、报销单和出差工具，调用 `search_knowledge` 获取制度证据，再生成带知识引用与 Observation 引用的结论。

### 第三幕：安全拒答或受控失败

使用缺失证据、错误业务 ID 或不受支持的在线 Profile，展示系统不会绕过 RAG、猜测工具结果或静默切换后端。

最后运行分层评测命令，以指标证明演示不是预先写死的 happy path。

## 19. 未来本地工程演示面板

面板在 CLI 稳定后开发，与 CLI 直接调用同一个 `RagService`、`AgentService` 和 trace contract，默认在同一 Python 进程中运行，不预先建设独立 HTTP API。

已确认布局为“流水线工作台”：

- 顶部：问题输入、local/online Generator 选择、运行按钮和安全状态；
- 中部：横向阶段链，显示双路召回、融合、重排、Evidence Gate、生成、Validator 与 Agent/工具状态和耗时；
- 主区：最终回答、拒答或错误，以及可点击的来源；
- 固定右侧检查器：点击任一阶段后显示该节点的输入摘要、输出、分数、排名、模型 revision、耗时和错误；
- Agent 区：展示 action、脱敏参数和 Observation，不展示隐藏推理。

面板的首要目的不是模拟聊天产品，而是让面试官在一屏内看清证据链、控制点和工程可观测性。

## 20. 文档交付物

最终至少包含：

- `README.md`：安装、快速开始、常用命令和演示入口；
- `docs/architecture.md`：架构、契约、数据流和安全边界；
- `docs/demo-script.md`：8–10 分钟、5 分钟和失败备用脚本；
- `docs/evaluation.md`：数据集、指标、命令和结果解释；
- `docs/security-and-cost.md`：密钥、隐私、Provider Profile、成本与调用审批；
- `docs/model-manifest.md`：精确模型文件、revision/digest、许可证、大小和缓存；
- `docs/troubleshooting.md`：显存/内存、离线缓存、索引、网络和后端错误。

## 21. 分阶段交付

1. 建立独立仓库骨架、迁移清单与离线测试基线；
2. 实现领域契约、知识库构建和 Fake 后端；
3. 实现真实双路 Retriever 与可重建索引；
4. 实现真实 Reranker、Evidence Gate 和独立 RAG；
5. 实现本地 Generator、结构化输出、Grounding Validator 与一次 repair；
6. 实现三个只读工具和有界 Agent；
7. 完成分层评测、性能基准和三幕 CLI Demo；
8. 在单独确认供应商和预算后实现在线 Generator Profile；
9. CLI 稳定后实现本地流水线工作台。

详细文件级步骤、测试先后顺序和迁移批次将在本设计获批后，使用 writing-plans 单独生成。该实施计划再次获得批准前，不创建或迁移生产代码。

## 22. 仍需在执行点确认的外部事实

以下不是架构未决项，而是必须在发生对应外部动作前确认的安全门：

- 各模型的精确文件名、revision/digest、remote code revision、许可证复核、实际大小与缓存位置；
- 具体中转站、端点、模型 ID、隐私条款、额度/单价、预计调用数、预计成本与硬上限；
- 本机关闭高内存后台程序后的真实冷启动、峰值 RAM/VRAM 和 p95 延迟；
- 评测结果是否支持初始 `retrieve_k=20`、`rerank_k=5`、长度限制与 Evidence Gate 门槛。

这些项目未确认前，不下载模型、不调用 API，也不把估算当作验收结果。

## 23. 设计验收条件

本设计在用户确认以下陈述后视为批准：

1. 最终目标仍是完整企业知识库 RAG + Agent；
2. 新仓库独立，来源只从指定 commit 选择性迁移；
3. RAG 可独立运行，Agent 不绕过证据与失败语义；
4. Generator 前后形成 Evidence Gate + Grounding Validator 闭环；
5. 本地完整离线，在线后端显式、受审计、受成本和隐私门控制；
6. CLI-first，未来流水线工作台复用同一业务核心；
7. 测试、评测、性能和三次稳定演示共同定义“面试可用”；
8. 用户批准本设计后才能编写实施计划，批准实施计划后才能创建或迁移生产代码。
