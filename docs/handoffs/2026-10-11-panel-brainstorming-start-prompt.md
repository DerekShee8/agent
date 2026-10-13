# 面板 Brainstorming 新窗口启动提示词

把下面代码块中的完整内容复制到同一项目的新对话窗口中。

```text
我要继续当前“企业知识库 RAG + Agent”项目的本地工程演示面板 brainstorming。现在仍是设计阶段，不要立即实现、安装依赖、扩展 trace、创建面板代码、调用 API、下载模型、推送、发布或部署。

项目绝对路径：
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo

请先完整阅读本轮交接包：
E:\onedrive文件\OneDrive\文档\agent文件\agent项目终版demo\docs\handoffs\2026-10-11-panel-brainstorming-handoff.md

然后只读核验：
- docs/panel-handoff.md
- docs/plans/2026-10-08-enterprise-rag-agent-demo-design.md
- docs/architecture.md
- src/enterprise_rag/execution_trace.py
- src/enterprise_rag/rag_service.py
- src/enterprise_rag/agent_service.py
- src/enterprise_rag/application.py

如果路径不可访问，请让我复制或附加交接包；不要凭记忆重建内容。

必须遵守：

1. 使用 brainstorming，一次只问我一个问题。设计全部分段批准前不得实施。
2. CLI Core 已完成验收；不要重新设计 CLI、RAG/Agent 业务语义或已固定安全边界。
3. 面板固定为本地“流水线工作台”，同一 Python 进程直接调用 Application/RagService/AgentService，不预先建设独立 HTTP API。
4. 已确认：面试展示效果优先、混合实时反馈、分层摘要 trace、首版只做演示运行台。
5. 已确认可以使用浏览器可视化伴侣；只在布局、节点层级和交互确实适合视觉比较时使用。
6. NiceGUI 只是当前推荐，尚未得到我的正式确认。不得把它写成已批准决定。
7. 当前没有面板生产代码，没有安装 UI framework，没有修改 trace，也没有实施计划。
8. 真实 trace 必须来自服务层契约；UI 不得伪造 dense/sparse/RRF 或 Agent 内部 RAG 层级。
9. 不展示 hidden chain-of-thought、完整 system prompt、secret 或真实企业/个人数据。
10. 设计完成后先写入 docs/superpowers/specs/2026-10-11-local-engineering-panel-design.md，让我审阅；我批准后才使用 writing-plans。实施计划再次批准后才能写生产代码。

你的第一条回复请控制在 6–10 行：
- 说明这是 CLI 完成后的面板设计阶段；
- 复述四项已确认决定；
- 明确 NiceGUI 尚未批准；
- 明确 trace 目前只有结束后的 immutable RunTrace，没有运行中订阅接口；
- 明确当前不会实施。

然后只问我一个问题：是否确认采用 NiceGUI 浏览器模式，不启用 native window、不建立独立 HTTP API，并在实施前锁定精确版本、离线验证依赖解析？
```
