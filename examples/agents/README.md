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

## 节点校验脚本

[validate_input.py](node-scripts/validate_input.py) 用于独立的“脚本处理”节点：一个 `transform(value, context)` 完成入参整理、校验和出参构造。`data.success` 为条件连线依据，`data.inputs` 为整理后的参数，`data.conclusion` 保留失败原因；成功继续下一步，失败可结束或转交。页面“填入校验示例”同时填写脚本和对应出参结构。业务类型、取值范围及必填规则仍需按实际需求补充。

[validate_projects.py](node-scripts/validate_projects.py) 用于全量查询后的脚本处理节点。前置只读工具开启全量分页，脚本入参将 `query` 映射为工具完整出参，`tenant_id` 映射为查询业务租户。每页原始业务数据包含 `tenant_id`、`items`、`count`；项目有非空字符串 `id` 和非空 `specs`，规格有正整数 `cpu_cores`、`memory_gb` 与非负十进制字符串 `price`。平台检查条数和游标，脚本核对所有页面的租户、项目、重复项与规格；规则不通过返回业务失败，不给出最优项。字段和契约须按实际工具调整。

脚本节点只选择渠道获准的隔离 Python 环境，不配置模型、技能或工具。页面未列出环境时先按 [配置说明](../../docs/configuration.md#enhancements) 设置 API 与 Worker 的相同环境；这些示例不会自动授权环境或运行业务查询。
