# Agent 配置示例

| 配置 | 流程 |
| --- | --- |
| [text-brief.json](text-brief.json) | 文本输入 → 结构化摘要 |
| [archive-answer.json](archive-answer.json) | MCP 查询 → 模型解读 → 对象组装 |

对应技能 ZIP 在 [技能包](../skills/README.md)，提示词、依赖清单、输入样本分别在 `prompts/`、`manifests/`、`cases/`。样本的 `fixture_output` 为受控模型替身输出。

先配置并冻结本渠道依赖，使用依赖清单 `resources` 中的键和实际版本 ID 编写绑定 JSON，再运行：

```sh
uv run python examples/agents/prepare.py \
  examples/agents/archive-answer.json /tmp/archive-bindings.json /tmp/archive-agent.json
```

将生成正文提交到 `POST /admin/v1/agents`，调试、评测并发布后，通过统一运行 API 调用。准备脚本只替换显式绑定并校验结构；工具来源、版本名称和契约仍须满足要求。

完整操作见 [配置交付](../../docs/configuration.md#configuration-delivery)，MCP 样例使用 [档案测试服务](../mcp/README.md)。
