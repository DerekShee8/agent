# 安全、隐私与在线成本

本项目默认是离线系统：`RAG_AGENT_ALLOW_NETWORK=false`、本地 Generator、虚构数据、无密钥、无费用。在线 Generator 是可选扩展，不是完成本地 Demo 的前置条件。

本文是操作门槛，不是对任何中转站安全性的背书。架构见 [architecture.md](architecture.md)，模型供应链见 [model-manifest.md](model-manifest.md)。

## 1. 数据边界

允许进入本项目和任何未来在线调用的只有虚构 Demo 数据：

- `data/sample_docs`：虚构差旅、费用制度与 FAQ；
- `data/sample_records`：虚构员工、报销单与行程；
- `evals`：由上述虚构事实组成的评测集。

真实个人信息、企业制度、报销记录、合同或内部文档不得发送到外部服务。`synthetic_data_only=true` 是 Profile 的硬校验项，但它不能自动识别输入内容；操作者仍负有数据分类责任。

## 2. 密钥规则

- API key 只保存在运行进程的环境变量或本机密钥管理中。
- 不把 key 粘贴到聊天、TOML、`.env`、命令历史、代码、Git、日志或 trace。
- Profile 只保存环境变量名，例如 `api_key_env='RAG_AGENT_RELAY_API_KEY'`。
- trace 会递归脱敏常见敏感字段；这不是把敏感数据交给 trace 的许可。
- `.env.example` 只是字段说明；程序不会自动加载 `.env`。

`doctor` 最多报告 `credential_configured: true|false`，不会输出环境变量值。它只检查变量是否存在；空白或畸形 key 会在真实 preflight 时再次被拒绝。

## 3. Provider Profile

CLI 不接受临时 `base_url`。在线后端必须先写入本机未提交的 `config/providers.toml`：

```toml
[profiles.relay_demo]
enabled = false
base_url = 'https://approved-provider.example/v1'
model_id = 'provider-advertised-model-id'
api_key_env = 'RAG_AGENT_RELAY_API_KEY'
privacy_terms_url = 'https://approved-provider.example/privacy'
input_price_per_million = '0.00'
output_price_per_million = '0.00'
request_limit = 0
budget_limit_cny = '0.00'
synthetic_data_only = true
```

`config/providers.example.toml` 故意使用 `.invalid` URL、0 次请求和 0 预算，并处于 disabled 状态；它不能直接调用。

每个 Profile 必须精确包含以下字段：

| 字段 | 含义 |
|---|---|
| `enabled` | 经人工复核后才可置 true |
| `base_url` | HTTPS public remote endpoint |
| `model_id` | 供应商实际接受的模型标识，不把营销名称当身份保证 |
| `api_key_env` | 只写环境变量名 |
| `privacy_terms_url` | 本次审阅的数据条款链接 |
| `input_price_per_million` | 输入价格，decimal string |
| `output_price_per_million` | 输出价格，decimal string |
| `request_limit` | 当前进程允许的请求次数上限 |
| `budget_limit_cny` | 当前进程预授权预算上限 |
| `synthetic_data_only` | enabled Profile 必须为 true |

URL 校验会拒绝 userinfo、query/fragment、localhost、字面私网/loopback/link-local/reserved IP 和歧义数字主机名。它不做 DNS 解析，因此不能防止 DNS rebinding；若进入更高风险环境，还需要固定 endpoint allowlist、DNS pinning 或出站网络策略。

## 4. 调用前 preflight

每个 online request 在构造网络 client 之前检查：

1. `RAG_AGENT_ALLOW_NETWORK=true`；
2. Profile 存在、唯一、enabled；
3. `synthetic_data_only=true`；
4. 请求序号不超过 `request_limit`；
5. key 环境变量存在、非空且无首尾空格；
6. 预授权成本加已累计成本不超过 `budget_limit_cny`。

预授权公式：

```text
estimated_cost_cny =
    input_token_cap  × input_price_per_million  / 1,000,000
  + output_token_cap × output_price_per_million / 1,000,000
```

当前 CLI 的 input cap 为 16,384，SDK timeout 默认 45 秒、`max_retries=0`、`temperature=0`，并要求 strict JSON schema。

重要限制：

- 请求数与已花费金额只在当前 `OnlineGenerator` 实例/进程内累计；每次 CLI 启动都会重置，不是账户级持久硬限额。
- 输入大小先用序列化消息的 UTF-8 字节数做本地上界检查，再使用配置 token cap 预估成本；这是保守预授权，不是精确 tokenizer 计费或最终账单。
- Profile 中的供应商模型名称只表示供应商声称的 model ID，不能证明它等价于官方 DeepSeek 服务。

## 5. 真实 API 调用审批单

在任何真实调用前，必须把下表填完整并获得用户针对这一次调用的明确确认：

| 项目 | 必须报告的内容 |
|---|---|
| 供应商 | 法人/站点名称；官方或中转站 |
| 模型 | 请求中的精确 `model_id` 与供应商声称的模型 |
| 端点 | 精确 HTTPS `base_url` |
| 数据条款 | privacy/retention/training/跨境条款链接与摘要 |
| 数据 | 仅虚构数据；说明会发送哪些字段 |
| 单价/额度 | 输入、输出单价，免费额度及有效期 |
| 调用计划 | 预计请求数、每次输入/输出上限 |
| 成本 | 预计成本、最坏成本和 CNY 硬上限 |
| fallback | 是否允许在线失败后显式切回本地 |

没有批准时保持：

```powershell
$env:RAG_AGENT_ALLOW_NETWORK = "false"
```

本仓库**只进行过一次被授权的真实在线验收**，且**未通过**：2026-10-12 对第三方中转站 `https://ai.yangwang.name/v1` 尝试了三次（另加一次绕过本项目代码的直连探测），三次都以失败告终——生成端点从未返回过可用的补全。online adapter 的**离线**完成证据是 fake client 的契约与失败映射测试；真实服务的可用性**未经验证**。完整记录见 [evaluation.md](evaluation.md) 的在线小节。

## 6. 经批准后的最小运行方式

这不是批准，只是批准完成后的操作模板：

```powershell
rtk uv sync --group dev --extra retrieval --extra local-generator --extra online-generator
rtk proxy powershell -NoProfile -Command "Copy-Item -LiteralPath 'config/providers.example.toml' -Destination 'config/providers.toml'"
```

人工填写并复核 Profile 后，在不记录 secret 的受控终端中设置环境变量，然后运行仅含虚构数据的请求：

```powershell
$env:RAG_AGENT_ALLOW_NETWORK = "true"
# 由组织认可的 secret manager 把 RAG_AGENT_RELAY_API_KEY 注入当前进程。
# 不在命令行、聊天或脚本中写出明文值。
rtk uv run rag-agent rag ask "仅含虚构数据的问题" --generator online --provider relay_demo --format json
```

实际执行应使用不落盘、不会进入 shell history 的进程注入方式。不要为了“方便”把 key 改写到 TOML。

## 7. Online failure 与本地 fallback

未带 `--fallback-local` 时，认证、权限、配额、余额、限流、超时、服务不可用、请求错误、协议错误、预算/隐私/Profile 阻断都会作为 online failure 返回。

显式带上 `--fallback-local` 时，这些受控错误允许切换一次本地 Generator：

```powershell
rtk uv run rag-agent rag ask "虚构问题" --generator online --provider relay_demo --fallback-local --trace
```

fallback 会在 composition 阶段同时加载本地模型，所以本地模型缺失时可能在发起在线请求前失败。切换不会绕过 Evidence Gate 或 Grounding Validator，并以 `backend_switch` / `degraded` 记录。

## 8. 模型与依赖供应链

- 模型只接受 [model-manifest.md](model-manifest.md) 中精确 revision、文件名和 SHA-256。
- GTE remote code 只允许固定 `code_revision`，并已审计两个 Python 文件。
- 运行时 `local_files_only=True`；缺少缓存时失败，不静默联网。
- GGUF 和模型缓存位于 `.local/model-cache`，索引位于 `.local/index`；两者均 Git-ignored。
- Python 依赖由 `uv.lock` 固定。Windows `llama-cpp-python` wheel 固定到项目声明的 CUDA 12.4 构建 URL。

## 9. 明确禁止

- 未经确认调用、测试或探测真实供应商端点；
- 向在线服务发送真实企业/个人材料；
- 在 Profile、聊天或 Git 中保存 secret；
- 绕过 Gate、Reranker、Validator 或工具 allowlist；
- 把进程内预算称为持久账户预算；
- 把中转站的营销模型名当作官方模型身份；
- 未经单独授权 push、publish、deploy 或发送外部消息。

## 10. 面板的一次性授权

面板在 CLI 的 profile 规则之上再加一层**每次 Run 一次性**的授权，而不是"选了 online 就随便用"：

- **确认才授权**：点 Run（online）只做 preflight 并展示确认视图；只有点 Confirm 才铸造 `OnlineRunAuthorization`，点 Cancel 记一条 BLOCKED（`authorization_cancelled`）且**零客户端调用**。
- **指纹绑定**：授权绑定 run id、`profile_fingerprint` 与 fallback 位。profile 在选中之后被改动（指纹不符）会在发出请求前变成 BLOCKED；同一份授权**不能被复用**，`backend_for` 会原子消费它，重放直接失败。
- **run 级上限**：preflight 会把本次 Run 计划中的**每一次**调用逐次交给单次调用 preflight，因此 run 级请求上限、密钥检查与保守成本投影不可能与单次调用的检查漂移。RAG 最多 2 次（一次生成 + 一次 repair），Agent 最多 `agent_max_actions + 2` 次（每个动作一次、可能的最终答案协议重试一次、grounding repair 一次）。
- **超限即失败，不回退**：授权耗尽/重放/不匹配抛出的 `RunAuthorizationError` **不是** fallback 元组的成员，所以它只会变成 FAILED 业务结果，**绝不**静默改用本地模型。
- **显式 fallback**：只有显式打开「失败后回退到本地」时，在线失败才允许切到本地；切换在整轮里**只发生一次**并永久锁定，之后的 repair 不会重试在线。
- **只允许合成数据**：profile 强制 `synthetic_data_only = true`，只有 `data/sample_docs` 与 `data/sample_records` 里的合成样例可以离开本机。
- **密钥只报状态**：确认视图只展示环境变量**名字**与请求/成本上限，不读取、不回显、不写入任何密钥值；面板的进度、历史、Inspector 与页面 HTML 都有密钥 tripwire 测试。
- **没有任意端点**：模型名与 endpoint 只读展示，不能手填任意地址或模型。
- **真实在线仍需单独审批**：面板的存在不改变第 5 节的规则——任何真实中转站调用都要先完成独立审批，本仓库迄今只有计划中明确授权的那一次（见 `docs/handoffs/2026-10-11-panel-execution-progress.md` 第 5 节）。

### 那一次授权**实际**执行了什么（2026-10-12）

上面的规则不是设计意图，而是在真实调用里被观察到生效的（三次尝试中的第三次，也是唯一一次跑完整三幕 × 1 轮）：

| 规则 | 真实运行中的实测 |
|---|---|
| run 级请求上限 | 上限 **30**，本次 Run 实际发出 **2** 次请求 |
| 预算上限 | 上限 **CNY 10**，成本上界 **CNY 0.686**（**基于假设单价的上界**，不是该站真实定价，也不是实测） |
| 一次性消费 | 同一份授权重放被拒（`replayed_authorization_is_refused = true`） |
| 指纹绑定 | 携带错误 `profile_fingerprint` 的请求在发出前被拒（`mismatched_fingerprint_is_refused = true`） |
| 负例检查零请求 | 上述两项负例检查期间**没有**产生任何 provider 请求（`no_provider_request_during_the_negative_checks = true`） |
| 密钥不外泄 | key 只存在于用户窗口的进程内存；仓库、命令文本与对话里都没有出现 |

**仍未验证**：该中转站的**定价**（我们用的是保守假设上界）与**模型身份**（`model_id` 出现在它自己的 `/v1/models` 列表里，但这只证明它**声称**提供）。四条 caveat 原样适用于任何在线结果。
