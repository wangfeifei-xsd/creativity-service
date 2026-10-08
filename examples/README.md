# 接入示例

此目录用于演示后端调用、导入配置和运行受控验收；服务启动不依赖这些文件。

首次接入先看 [后端调用](backend/README.md)。配置 Agent 时只需选择一套完整案例：`text-brief` 用于文本整理，`archive-answer` 用于 MCP 查询与解读。

| 目录 | 用途 |
| --- | --- |
| [backend](backend/README.md) | 后端客户端与命令行调用 |
| [agents](agents/README.md) | 两套 Agent、提示词、依赖清单和输入样本 |
| [skills](skills/README.md) | 对应的技能源码与 ZIP 生成入口 |
| [mcp](mcp/README.md) | 本地受控 MCP 服务 |
| [weather](weather/README.md) | 天气 MCP 服务与初始化数据配套示例 |
| [onboarding](onboarding/README.md) | 多渠道隔离验收，日常接入无需执行 |
| [evaluations](evaluations/README.md) | 评测门禁验收，日常接入无需执行 |

验收配置与样本按需生成，默认写入不提交 Git 的 `.local/examples/`。在服务工程目录执行：

```sh
uv run python -m examples.skills.prepare
uv run python -m examples.onboarding.prepare
uv run python -m examples.evaluations.prepare
```

以上命令均支持 `--output` 指定目录，只生成本地文件。
