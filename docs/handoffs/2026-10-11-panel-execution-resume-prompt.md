# 本地工程面板执行阶段新窗口续接提示词

把下面代码块中的完整内容复制到同一项目的新对话窗口中。

```text
我要继续当前“企业知识库 RAG + Agent”项目的本地工程演示面板**实施**（不是设计）。设计阶段和计划都已批准，Task 1 与 Task 2 已经提交完成。

项目绝对路径：
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo

请先只读核验 Git 实际状态，以 Git 为准，不要回退或清理我的工作区：
- rtk git status --short --branch
- rtk git log -5 --oneline

然后完整阅读这两个文件：
- docs/handoffs/2026-10-11-panel-execution-progress.md   ← 进度存档（做到哪、下一步、我的决定、已知坑）
- docs/superpowers/plans/2026-10-11-local-engineering-panel.md   ← 实施计划（v2，含 Task 16）

如果路径不可访问，请让我复制或附加；不要凭记忆重建内容。

必须遵守：
1. 按计划逐任务 TDD（红-绿-重构），每个任务一个小提交，不把失败测试带进下一个任务。
2. 每个任务收尾时更新 docs/handoffs/2026-10-11-panel-execution-progress.md 的第 2、3、4、9 节，并与该任务的提交放进同一个 commit。
3. 不要重新问我已批准的四项决定（见进度文件第 5 节）：real-local 验收方式、Pipeline 画布实现、安装命令先实测、真实 online 的范围与 profile。
4. 执行到 Task 14 后**停下来**向我报告 Task 15 的 real-local 验收细节，等我点头再继续。
5. 不要裸跑 `uv sync --locked`（见进度文件第 7 节第 1 条）。
6. 不要读取、回显或写入任何 API key 值；key 只走环境变量。
7. 真实 online 调用只在 Task 16、且我已设置 RAG_AGENT_ONLINE_API_KEY 之后进行。

你的第一条回复请控制在 6–10 行：说明当前断点在哪个 Task、下一步要做什么、工作树是否干净，然后直接开始该任务的第一个步骤。
```
