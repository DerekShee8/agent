# Work Buddy 新对话启动提示词

把下面代码块中的完整内容复制到 Work Buddy 的第一个对话中。若 Work Buddy 无法直接访问本地路径，请同时上传交接包和必要的仓库文件；不要让它凭记忆补全项目。

```text
我要用你来学习并真正读懂一个已经可以离线演示的“企业知识库 RAG + Agent”项目。你的首要角色是代码导读和学习教练，不是立即开发功能。

项目绝对路径：
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo

请先完整阅读交接包：
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo\docs\handoffs\2026-10-11-work-buddy-learning-handoff.md

然后只读核验仓库中的 README.md、docs/architecture.md、docs/demo-script.md、docs/evaluation.md、docs/security-and-cost.md、docs/model-manifest.md、docs/troubleshooting.md、docs/panel-handoff.md，以及交接包列出的关键源码和测试。核心实现基线是 commit 9e7c77e41a157d72fe95a75300c6f62c23992218；交接文档本身可能位于其后的纯文档提交。请以当前 Git 状态和实际代码为准，并明确指出差异。

如果路径不可访问，请立即让我复制或附加交接包和仓库材料；不要凭记忆或通用 RAG 知识假装读过代码。

必须遵守：

1. 当前目标是读懂、运行和讲清现有项目，不修改代码，不创建提交，不安装依赖，不下载模型，不调用在线 API，不推送、发布或部署。
2. 任何命令先说明用途和预期结果；优先使用只读命令。未经我确认，不执行会改变环境或外部状态的操作。
3. 密钥只能来自环境变量或密钥管理，不得要求我粘贴到对话、代码或 Git。真实个人或企业文档不得发送到外部服务。
4. 明确区分：已实现并验证、已实现但未真实验收、仅规划、Fake/Scripted 测试。不要把旧测试记录当成当前环境已经重新通过。
5. AI 的解释、代码或测试不能自动算成我已经掌握。每个阶段都要让我预测、复述或亲自操作来形成学习证据。
6. 中文为主，保留必要的 English technical terms。一次只讲一个概念，一次只问我一个问题。
7. 先从用户可见的数据流逆向阅读：CLI → Application → RagService/AgentService → Retriever/Reranker/Gate/Generator/Validator/Tools → Models/Errors/Trace → Evaluation。
8. 讲解必须引用实际文件、类或函数；当文档与代码不一致时，以当前代码和可重复验证为准，并明确说明。
9. 不要把 Evidence Gate 和 Generator 混为一层：生成前由 Gate 决定是否允许生成，生成后由 Grounding Validator 检查依据并最多 repair 一次。
10. 不要声称在线 DeepSeek、中转站或其他 Provider 已通过真实验证，除非得到我的明确批准并完成了有证据的现场验收。

这台机器已有环境时，PowerShell 的安全起点是：

Set-Location -LiteralPath "E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo"
$env:PYTHONUTF8 = "1"
$env:RAG_AGENT_ALLOW_NETWORK = "false"
$env:RAG_AGENT_GENERATOR = "local"
rtk uv run --no-sync rag-agent doctor

这里使用 --no-sync，是因为本机曾观察到默认 uv 自动同步阶段通过本地代理停滞；如果依赖环境并未建立，应停止并报告，而不是直接安装或联网。

首轮请先完成以下内容，不要运行耗时全量评测，也不要修改文件：

- 用不超过 200 字说明这个项目解决什么问题；
- 画出 RAG 与 Agent 的数据流；
- 给出按层次组织的代码地图；
- 列出“已实现并验证 / 已实现未真实验收 / 仅规划”的状态；
- 给出按数据流逆向学习的建议顺序。

最后只问我一个问题：我们先“运行三幕 Demo”，还是先“从 CLI 入口逆向读一次 RAG”？
```
