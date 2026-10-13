# 故障排查

先在仓库根目录运行：

```powershell
rtk uv run rag-agent doctor
```

`doctor` 输出 JSON。请读取 `data.overall_status`，不要只看退出码：`warn` 仍返回 0，`fail` 才返回 1。架构状态语义见 [architecture.md](architecture.md)，安全类问题见 [security-and-cost.md](security-and-cost.md)。

## 1. 快速定位

| 现象 | 最可能原因 | 处理 |
|---|---|---|
| `overall_status=warn` | 内存紧张、PyTorch CUDA 不可见或未配在线 Profile | 查看具体 check；离线 Demo 可忽略 Profile warning |
| `local_models=fail` | 模型缺失或关键 digest 不匹配 | 按 [model-manifest.md](model-manifest.md) 恢复精确文件 |
| `index=fail` / `error_category=index` | 索引缺失、文件集合或 digest 不一致 | 重新运行 `kb build` |
| `retrieval_unavailable` | retrieval extra/缓存/remote code 不完整 | 重装锁定依赖并检查缓存 |
| `reranker_unavailable` | Reranker 权重或运行时不可用 | 检查模型清单、RAM 与依赖 |
| `local_missing` | 固定 GGUF 文件不存在 | 核对路径、文件名、大小、SHA-256 |
| `local_resource` | RAM/VRAM 不足或模型加载资源失败 | 关闭高占用程序，重跑 doctor |
| `status=refused` | 安全控制正常拒答 | 看 `refusal_reason`，不要当系统故障 |
| `status=failed` | 运行故障 | 看 `error_category` 与对应章节 |
| online `configuration` | `config/providers.toml` 缺失/非法或 key 配置错误 | 先完成 Profile 审批和校验 |

## 2. Windows、CUDA 与两套运行时

目标机可能出现：

```text
llama.cpp GPU offload: available
PyTorch CUDA: unavailable
```

这不是自相矛盾。Generator 使用 CUDA 12.4 构建的 `llama-cpp-python` wheel；Retriever/Reranker 使用 PyTorch。当前目标机实测 `torch 2.14.1+cpu`，因此 PyTorch CUDA 不可见，而 llama.cpp 能识别 RTX 4060 8 GB。

检查：

```powershell
rtk uv run python -c "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())"
rtk uv run rag-agent doctor
```

若本地 Generator 不能 GPU offload：

1. 确认 NVIDIA driver 正常；
2. 运行 `rtk uv sync --extra local-generator`，确保使用 `pyproject.toml` 固定的 Windows CUDA wheel；
3. 检查是否被其他 Python 环境或全局包覆盖；
4. 不要擅自换 wheel、CUDA 构建或模型，再把结果当作已批准验收。

PyTorch CUDA warning 不会直接让 doctor fail；当前真实 Retriever/Reranker 可在 CPU 路径运行，但延迟和 RAM 压力可能上升。

## 3. 内存压力

`memory=warn` 的条件是可用物理 RAM 少于 4 GiB。目标机只有 16 GB，总模型与 Python 进程容易和浏览器、虚拟显示/串流软件争用内存。

演示前：

1. 关闭多余浏览器标签、IDE language server、游戏启动器、虚拟显示和串流软件；
2. 保留至少 4–6 GiB 可用 RAM；
3. 先运行 `doctor`，再启动模型；
4. 不要同时跑全量 eval 和录屏/虚拟机；
5. 若出现 `local_resource`，停止当前运行、释放内存后重试，不要跳过安全层。

磁盘少于 6 GiB 为 fail，6–15 GiB 为 warn；建议保留至少 15 GiB。

## 4. 模型缓存与 digest

默认位置：

```text
.local/model-cache/
├─ Qwen3-4B-Q5_K_M.gguf
└─ models--Alibaba-NLP--.../snapshots/<revision>/...
```

运行时不会自动下载。`RAG_AGENT_ALLOW_MODEL_DOWNLOAD=true` 当前只会被解析，不改变 `local_files_only=True` 行为。

检查 manifest 结构：

```powershell
rtk uv run python scripts/check_model_manifest.py
```

当前 `doctor` 对 5 个核心工件做存在性和 SHA-256 校验：两份 GTE 权重、两份 audited remote-code 文件和 GGUF。它不是 13 个 manifest 文件的全量 verifier。任何恢复都应严格使用清单中的精确 revision/digest，而不是下载“同名最新版”。

常见类别：

- `local_missing`：GGUF 不存在；
- `local_unavailable`：llama runtime 不可用；
- `local_resource`：文件不匹配、加载资源不足等；
- `retrieval_unavailable` / `reranker_unavailable`：GTE 缓存或依赖不可用。

## 5. 索引缺失或摘要错误

重建：

```powershell
rtk uv run rag-agent kb build data/sample_docs
rtk uv run rag-agent doctor
```

`kb build` 只接受现有目录，递归读取 UTF-8 `.md` / `.txt`。没有支持文件、空文档、不可解码文件或越界链接都会失败。

索引目录默认 `.local/index`。加载时会验证：

- 必需文件恰好存在；
- 每个 artifact digest；
- source digest、行数和 embedding dimension；
- backend kind 必须为 real 才能进入 production composition。

不要手工修改 `dense.npy`、`sparse.jsonl` 或 manifest；从规范虚构语料重建。

## 6. `refused` 不是 `failed`

```text
answered  有回答 + 合法来源
refused   无回答 + refusal_reason，安全控制正常工作，exit 0
failed    无回答 + error_category，运行故障，exit 1
```

常见拒答：

- `insufficient_evidence`：Gate 找不到合格知识证据；
- `grounding_validation_failed`：一次 repair 后仍无法证明 claims；
- `knowledge_evidence_required`：Agent 试图只靠业务事实下制度结论；
- `duplicate_action`：Agent 重复相同 action；
- `iteration_limit`：超过最多 6 步；
- `agent_requested_refusal`：Planner 明确选择拒答。

常见失败：

- `index`, `embedding`, `retrieval_*`, `reranker_*`；
- `local_*`, `generation_protocol`, `grounding_failed`；
- `tool_not_found`, `tool_validation`, `tool_execution`；
- online 的 `authentication`, `permission`, `quota`, `balance`, `rate_limit`, `timeout`, `provider_unavailable`, `invalid_request`, `protocol`。

不要把 `refused` 改成强行生成；它是系统最重要的安全结果之一。

## 7. CLI 参数错误

argparse 错误返回 2。最常见情况：

- `rag ask` / `agent run` 忘记必填 `--generator local|online`；
- online 忘记 `--provider NAME`；
- local 错带 `--provider` 或 `--fallback-local`；
- `eval run` 没有合法 `--layer`。

查看实际帮助：

```powershell
rtk uv run rag-agent --help
rtk uv run rag-agent rag ask --help
rtk uv run rag-agent agent run --help
```

保持全局 `RAG_AGENT_GENERATOR=local`。若设为 `online`，没有 provider CLI 参数的 `kb`、`eval`、`doctor` 也可能在 application composition 阶段失败；在线选择应只通过具体的 `rag ask` / `agent run --generator online --provider ...` 完成。

## 8. Provider 与网络

| 类别 | 说明 |
|---|---|
| `profile_not_allowed` | 网络仍关闭、Profile disabled 或请求上限超出 |
| `privacy_blocked` | Profile 不是 synthetic-only |
| `budget_blocked` | 预授权成本超过当前进程预算 |
| `configuration` | Profile 文件、字段、URL、key 环境变量或价格格式错误 |
| `authentication` / `permission` | 401 / 403 类错误 |
| `quota` / `balance` / `rate_limit` | 额度、余额或 429 |
| `provider_unavailable` / `timeout` | 连接、5xx 或超时 |
| `invalid_request` / `protocol` | 4xx 请求问题、strict schema 不支持或未知响应 |

缺少 `config/providers.toml` 对离线 doctor 只是 warning，但对 `--generator online` 是 configuration failure。真实网络调用前必须先走 [安全与成本审批](security-and-cost.md)。

## 9. 中文乱码

若 PowerShell/日志采集器显示 mojibake，先确认文件本身是 UTF-8，再设置当前会话：

```powershell
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
rtk uv run rag-agent rag ask "P4 员工去上海出差，每晚住宿上限是多少？" --generator local --format json
```

乱码通常是终端解码问题，不等于模型或 JSON 中的数据已损坏。可用 `--format json` 配合 UTF-8-aware viewer 核验。

## 10. 仍无法解决

保存以下不含 secret 的信息：

- `rag-agent doctor` 完整 JSON；
- 命令、退出码、`status/refusal_reason/error_category`；
- Python、torch、llama-cpp 版本；
- 是否刚重建索引；
- 模型文件只提供路径、大小和 SHA-256，不上传权重；
- `--trace` 中的公开事件。

不要附 API key、环境变量值、真实文档或 hidden prompt。
