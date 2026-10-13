# Demo fallback artifacts

本目录保存面试现场的离线备用证据，只含虚构数据。

- `task19-demo-acceptance.json`：2026-10-11 在目标笔记本上连续 3 轮真实本地三幕验收的结构化摘要。
- `panel-demo-acceptance.json`：2026-10-12 本地工程面板（`execution_surface = "panel-coordinator"`）三幕 × 三轮真实本地验收的结构化摘要，由 `scripts/collect_panel_acceptance.py` 生成并经严格 promotion 脱敏。它度量的是 `PanelController → RunCoordinator → PanelRuntime → Application` 路径，**不是**浏览器 E2E；`ui_available_seconds` 只表示路由/外壳注册，不代表浏览器首屏。

这些文件不是实时 CLI transcript，也不替代 live run。使用时应明确说明捕获日期、命令、真实本地 backend 和摘要口径。不得在此目录加入 API key、真实企业/个人数据、hidden prompt 或 chain-of-thought。

重新验收：

```powershell
rtk uv run pytest tests/live/test_demo_scenarios_live.py -m live -v -s
rtk uv run pytest tests/live/test_panel_demo_live.py -m live -v -s
```

新的结果先写入 Git-ignored 的 `.local/benchmarks/`；人工复核不含敏感信息后，才可更新本目录。

面板证据的重新生成与提升（两步都要跑）：

```powershell
rtk uv run python scripts/collect_panel_acceptance.py collect --output ".local/benchmarks/panel-demo-live.json"
rtk uv run python scripts/collect_panel_acceptance.py promote --input ".local/benchmarks/panel-demo-live.json" --output "docs/demo-artifacts/panel-demo-acceptance.json"
```

`promote` 会拒绝任何含密钥样式键值、绝对路径、环境变量值、prompt/response 文本或 traceback 的字段；内容哈希（`model_digest`/`model_revision`/`git_commit`）与全大写的阶段名不属于密钥，会被保留。
