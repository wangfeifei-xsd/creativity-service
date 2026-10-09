# 通用评测装配

本目录为方案 24/26 提供两个不同输入输出结构的 Agent 及 12 条经技术审阅的固定样本。实现边界见 [评测交接](../../docs/operations.md#evaluations)。

在 `creativity-service` 目录执行：

```sh
uv run python -m examples.evaluations.prepare
```

默认写入 `.local/examples/evaluations/`，可用 `--output` 指定其他目录；生成产物不提交 Git。每组包含 Agent 定义、JSONL 样本及固定模型响应；`manifest.json` 登记样本数、标签来源及适用范围。

| 配置 | 输入 | 输出 data | 样本 |
| --- | --- | --- | --- |
| `text_items` | 文本 request | items 列表、source_revision | 正常、边界、缺失、越权、工具变化、故障各 1 条 |
| `numeric_summary` | values 数列 | total 数值、source_revision | 同上，各 1 条 |

正常和边界响应应通过；缺失输入应无效；越权输出、源版本变化和模型故障应失败。两组均应保留整集 6 条分母，并拒绝作为通过的发布证据。工具变化样本模拟响应中的源版本变化；真实工具权限、固定历史返回由独立工具集成用例验证。

Agent 文件尚未绑定接入渠道的模型路由和提示词。先在目标渠道配置并发布这些依赖，再替换 `definition.bindings` 中的版本引用，按管理 API 创建 Agent。样本可从“效果评测 → 样本集 → 导入样本”预览导入；发布用标签需由该渠道审阅人员核对。不要直接把通用框架预期当成正式业务效果目标。

可在真实 MySQL/Redis 的隔离测试环境运行统一执行管线，输出可追溯报告：

```sh
CREATIVITY_EVALUATION_EVIDENCE_DIR=.logs/24 \
  .venv/bin/pytest tests/integration/evaluations -k two_shapes -q
```

测试使用固定模型响应，不访问真实模型供应商。报告带有本次测试渠道、任务、候选、子 run、依赖及数据摘要；测试数据库在结束后清理。26 应用同一份样本契约接入真实模型与 MCP，并保存本轮实际报告及标签审阅依据；运行方式见 [测试指南](../../docs/testing.md)。
